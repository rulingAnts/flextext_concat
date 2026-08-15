#!/usr/bin/env python3
"""
FLExText parsing and merge engine.

Reads SIL FieldWorks interlinear .flextext exports and combines them two ways:

  Corpus         — N <interlinear-text> siblings in one <document>.  Every text
                   keeps its own title, <languages> and <media-files>, so audio
                   segmentation and media links survive untouched.
  Combined text  — one <interlinear-text>, where each source text becomes one
                   <paragraph> and all of its lines become <phrase> children of
                   that paragraph.  Audio segmentation is necessarily discarded.

No Qt dependency — this module is usable as a plain library or from a script.

Format references: Ken Zook, "Technical Notes on FLEx Text Interlinear" (SIL,
2026-05-04); FlexInterlinear.xsd; FieldWorks InterlinearExporter.cs and
BIRDInterlinearImporter.cs.
"""

import copy
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FLEXTEXT_EXTENSIONS = {".flextext", ".xml"}

# Attributes on <phrase> that carry audio segmentation.  All are optional in the
# schema and are written mainly by ELAN and SayMore rather than by FLEx itself.
AUDIO_PHRASE_ATTRS = ("begin-time-offset", "end-time-offset", "media-file", "speaker")

# Note items whose entire content is a human-readable timestamp, e.g.
# "audio ~0:00.000–0:02.421" as written by flextext.app.  Deliberately anchored:
# a note that mixes real commentary with a timestamp is left alone.
_AUDIO_NOTE_RE = re.compile(
    r"""^\s*audio\s*~?\s*
        \d{1,2}(?::\d{2})+(?:\.\d{1,3})?
        \s*[-–—]\s*
        \d{1,2}(?::\d{2})+(?:\.\d{1,3})?
        \s*$""",
    re.VERBOSE | re.IGNORECASE,
)

# What to do with audio segmentation when building a combined text.
#
# AUDIO_SHIFT rebases every phrase's time offsets onto a single concatenated
# recording, so a combined text can be paired in ELAN with audio joined by the
# companion audio_concat app.  Per-text durations are estimated from the last
# annotation in each text (see text_duration), which is exact only when the
# annotation covers the whole recording.
AUDIO_DISCARD = "discard"
AUDIO_SHIFT = "shift"
AUDIO_MODES = (AUDIO_DISCARD, AUDIO_SHIFT)

# Silence + click + silence inserted between files by the audio_concat app
# (500 ms + 5 ms click + 500 ms).  Used as the default inter-text gap so the
# two apps' outputs stay aligned.
DEFAULT_GAP_MS = 1005

# What to do with <item type="segnum"> when building a combined text.
#
# FLEx's importer explicitly discards segnum — BIRDInterlinearImporter's
# AddSegmentItemData has `case "segnum": break;` with the comment that it is
# "not associated to a property, and also not a custom field".  It is written
# on export from the computed on-screen reference, so it is effectively a
# write-only field.  Removing it is therefore the safest default: FLEx numbers
# the text itself, and no other tool is handed stale per-text numbering.
SEGNUM_STRIP = "strip"
SEGNUM_RENUMBER = "renumber"
SEGNUM_KEEP = "keep"
SEGNUM_MODES = (SEGNUM_STRIP, SEGNUM_RENUMBER, SEGNUM_KEEP)

# Attribute order FLEx itself uses on <language>, for normalized output.
_LANGUAGE_ATTR_ORDER = ("lang", "font", "vernacular", "RightToLeft")

_XML_DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'


class FlextextError(Exception):
    """A file could not be read, or is not a FLExText document."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class FlextextFile:
    """One parsed .flextext file, with the counts the GUI needs for display."""

    path: Path
    texts: list[ET.Element] = field(default_factory=list)
    version: str | None = None
    title: str | None = None
    n_paragraphs: int = 0
    n_phrases: int = 0
    n_timed_phrases: int = 0
    n_media_refs: int = 0
    has_runs: bool = False

    @property
    def has_audio(self) -> bool:
        """True if this file carries anything Combined mode would discard."""
        return self.n_timed_phrases > 0 or self.n_media_refs > 0

    @property
    def label(self) -> str:
        """Row text for the file list."""
        parts = [self.path.name]
        if self.title:
            parts.append(f"— {self.title}")
        parts.append(f"({self.n_phrases} phrase{'' if self.n_phrases == 1 else 's'})")
        if self.has_audio:
            parts.append("🔊")
        return "  ".join(parts)


def _item_text(parent: ET.Element, item_type: str) -> str | None:
    """First direct <item> child of the given type, stripped; None if absent."""
    for item in parent.findall("item"):
        if item.get("type") == item_type:
            text = "".join(item.itertext()).strip()
            return text or None
    return None


def parse_file(path) -> FlextextFile:
    """
    Parse one .flextext file.

    Raises FlextextError with a message fit for a dialog, so the caller can skip
    a bad file and keep going rather than aborting the whole run.
    """
    path = Path(path)
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise FlextextError(f"Not valid XML — {exc}") from exc
    except OSError as exc:
        raise FlextextError(f"Could not read the file — {exc}") from exc

    root = tree.getroot()
    if root.tag != "document":
        raise FlextextError(
            f"Root element is <{root.tag}>, expected <document>. "
            "This does not look like a FLExText export."
        )

    texts = root.findall("interlinear-text")
    if not texts:
        raise FlextextError("No <interlinear-text> element found.")

    parsed = FlextextFile(path=path, texts=texts, version=root.get("version"))
    for text in texts:
        if parsed.title is None:
            parsed.title = _item_text(text, "title")
        parsed.n_paragraphs += len(text.findall("paragraphs/paragraph"))
        for phrase in text.iterfind("paragraphs/paragraph/phrases/phrase"):
            parsed.n_phrases += 1
            if any(a in phrase.attrib for a in AUDIO_PHRASE_ATTRS):
                parsed.n_timed_phrases += 1
        parsed.n_media_refs += len(text.findall("media-files/media"))
    parsed.has_runs = any(text.find(".//run") is not None for text in texts)
    return parsed


def parse_files(paths) -> tuple[list[FlextextFile], list[tuple[Path, str]]]:
    """
    Parse many files, isolating failures.

    Returns (parsed, failures) where failures is a list of (path, message).  One
    unreadable file among fifty must never abort the batch.
    """
    parsed: list[FlextextFile] = []
    failures: list[tuple[Path, str]] = []
    for path in paths:
        try:
            parsed.append(parse_file(path))
        except FlextextError as exc:
            failures.append((Path(path), str(exc)))
    return parsed, failures


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _output_version(files: list[FlextextFile]) -> str:
    """
    Pick document/@version for the output.

    Version 3 (FLEx 9.3.5+) adds <run> children inside items for embedded
    writing systems and styles.  Declaring v2 while carrying runs would mislabel
    the data, so any v3 input — or any actual <run> — forces v3.
    """
    if any(f.version == "3" or f.has_runs for f in files):
        return "3"
    numeric = [int(f.version) for f in files if (f.version or "").isdigit()]
    return str(max(numeric)) if numeric else "2"


def collect_languages(files: list[FlextextFile]) -> list[str]:
    """Every distinct lang code across the given files, in first-seen order."""
    seen: list[str] = []
    for f in files:
        for text in f.texts:
            for lang in text.iterfind("languages/language"):
                code = lang.get("lang")
                if code and code not in seen:
                    seen.append(code)
    return seen


def default_title_lang(files: list[FlextextFile]) -> str:
    """
    Best guess for the writing system of a new title.

    A title is analysis-language text, so prefer the language an existing title
    already uses, then any non-vernacular language, then whatever is available.
    """
    vernaculars: set[str] = set()
    for f in files:
        for text in f.texts:
            for item in text.findall("item"):
                if item.get("type") == "title" and item.get("lang"):
                    return item.get("lang")
            for lang in text.iterfind("languages/language"):
                if lang.get("vernacular") == "true" and lang.get("lang"):
                    vernaculars.add(lang.get("lang"))
    for code in collect_languages(files):
        if code not in vernaculars:
            return code
    return next(iter(collect_languages(files)), "en")


def audio_loss_summary(files: list[FlextextFile]) -> tuple[int, int, int]:
    """
    What Combined mode would discard: (files_with_audio, phrases, media_refs).

    Drives the pre-run warning dialog, which stays silent when this is all zero.
    """
    return (
        sum(1 for f in files if f.has_audio),
        sum(f.n_timed_phrases for f in files),
        sum(f.n_media_refs for f in files),
    )


def _phrase_spans(text: ET.Element) -> list[tuple[int, int]]:
    """(begin, end) millisecond spans of every timed phrase, in time order."""
    spans: list[tuple[int, int]] = []
    for phrase in text.iterfind("paragraphs/paragraph/phrases/phrase"):
        begin, end = phrase.get("begin-time-offset"), phrase.get("end-time-offset")
        if begin is None or end is None:
            continue
        try:
            spans.append((int(begin), int(end)))
        except ValueError:          # offsets are xs:string; tolerate junk
            continue
    spans.sort()
    return spans


def text_duration(text: ET.Element) -> tuple[int, bool]:
    """
    Estimate a text's recording length from its annotation, in milliseconds.

    Returns (duration, exact).  The duration is the largest end-time-offset in
    the text.  `exact` is True only when the annotation tiles the recording
    contiguously from zero — the flextext.app style, where every millisecond
    belongs to some phrase and the last end really is the end of the file.

    ELAN-style annotation marks utterances and leaves silence unannotated, so
    it starts late and has gaps; there the true file length is unknowable from
    the XML, and any audio after the last utterance would make later texts in a
    shifted timeline drift.  Callers should surface `exact=False`.
    """
    spans = _phrase_spans(text)
    if not spans:
        return 0, False
    contiguous = spans[0][0] == 0 and all(
        spans[i][0] <= spans[i - 1][1] for i in range(1, len(spans))
    )
    return max(end for _, end in spans), contiguous


def _strip_guids(text: ET.Element) -> None:
    """
    Remove guids so FLEx creates new objects instead of prompting to merge.

    <media guid> is kept deliberately: it is required by the schema and is the
    target of phrase/@media-file, so removing it would break the media linkage.
    """
    text.attrib.pop("guid", None)
    for el in text.iter():
        if el is text or el.tag == "media":
            continue
        el.attrib.pop("guid", None)


def _merge_languages(texts: list[ET.Element]) -> tuple[ET.Element, list[str]]:
    """
    Union the <languages> blocks of several texts, keyed by @lang.

    The schema declares an xs:key uniqueness constraint on @lang, so duplicates
    are not allowed.  vernacular and RightToLeft are sticky: if any source marks
    a language vernacular the merged entry keeps it, because a combined text
    with no vernacular language makes FLEx fall back to guessing on import.
    """
    merged: dict[str, dict[str, str]] = {}
    warnings: list[str] = []

    for text in texts:
        for lang in text.iterfind("languages/language"):
            code = lang.get("lang")
            if not code:
                continue
            attrs = dict(lang.attrib)
            existing = merged.get(code)
            if existing is None:
                merged[code] = attrs
                continue
            for flag in ("vernacular", "RightToLeft"):
                if attrs.get(flag) and not existing.get(flag):
                    existing[flag] = attrs[flag]
            new_font, old_font = attrs.get("font"), existing.get("font")
            if new_font and not old_font:
                existing["font"] = new_font
            elif new_font and old_font and new_font != old_font:
                warnings.append(
                    f"Language '{code}' has conflicting fonts "
                    f"('{old_font}' and '{new_font}'); kept '{old_font}'."
                )

    languages_el = ET.Element("languages")
    for code, attrs in merged.items():
        lang_el = ET.SubElement(languages_el, "language")
        for key in _LANGUAGE_ATTR_ORDER:
            if attrs.get(key):
                lang_el.set(key, attrs[key])
        for key, value in attrs.items():          # carry anything unexpected
            if key not in _LANGUAGE_ATTR_ORDER:
                lang_el.set(key, value)
    return languages_el, warnings


# ---------------------------------------------------------------------------
# Corpus mode
# ---------------------------------------------------------------------------

def build_corpus(files: list[FlextextFile], *,
                 strip_guids: bool = False) -> tuple[ET.ElementTree, list[str]]:
    """
    Combine into a corpus: one <document>, one <interlinear-text> per source text.

    Each text keeps its own <languages> and <media-files>.  Per the schema and
    FLEx's own exporter, <languages> belongs inside each interlinear-text, not
    at document level, and phrase/@media-file → media/@guid is scoped to the
    enclosing text — so nothing needs merging or rewriting here, and no audio
    or media information is lost.
    """
    root = ET.Element("document", {"version": _output_version(files)})
    warnings: list[str] = []

    for f in files:
        for text in f.texts:
            node = copy.deepcopy(text)
            if strip_guids:
                _strip_guids(node)
            root.append(node)

    if not len(root):
        warnings.append("No texts were written — the input list was empty.")
    return ET.ElementTree(root), warnings


# ---------------------------------------------------------------------------
# Combined-text mode
# ---------------------------------------------------------------------------

def _is_audio_note(text: str | None) -> bool:
    """True if an item's whole content is an audio timestamp note."""
    return bool(text) and bool(_AUDIO_NOTE_RE.match(text))


def _clean_phrase(phrase: ET.Element, *, strip_audio_notes: bool,
                  audio_mode: str = AUDIO_DISCARD, shift_ms: int = 0,
                  media_guid: str | None = None) -> None:
    """
    Normalise a phrase for the combined text, in place.

    In AUDIO_DISCARD mode every time/media attribute goes.  In AUDIO_SHIFT mode
    the offsets are rebased onto the concatenated timeline by adding shift_ms,
    speaker is kept (it stays true after concatenation), and media-file is
    repointed at the single combined recording.

    Only removes and appends — never rebuilds the child list.  Phrase content is
    `item* words item*`, with items on both sides of <words>, so reconstructing
    the element would silently reorder the interlinear data.
    """
    if audio_mode == AUDIO_SHIFT:
        for attr in ("begin-time-offset", "end-time-offset"):
            raw = phrase.get(attr)
            if raw is None:
                continue
            try:
                phrase.set(attr, str(int(raw) + shift_ms))
            except ValueError:
                phrase.attrib.pop(attr, None)   # unparseable: drop, don't guess
        if media_guid and phrase.get("begin-time-offset") is not None:
            phrase.set("media-file", media_guid)
        else:
            phrase.attrib.pop("media-file", None)
    else:
        for attr in AUDIO_PHRASE_ATTRS:
            phrase.attrib.pop(attr, None)

    phrase.attrib.pop("guid", None)
    for word in phrase.iter("word"):
        word.attrib.pop("guid", None)

    if strip_audio_notes:
        for item in [c for c in phrase if c.tag == "item"]:
            if item.get("type") in ("note", "notes") and _is_audio_note(
                "".join(item.itertext())
            ):
                phrase.remove(item)


def _segnum_items(phrase: ET.Element) -> list[ET.Element]:
    """Direct segnum <item> children of a phrase (one per writing system)."""
    return [c for c in phrase if c.tag == "item" and c.get("type") == "segnum"]


def _strip_segnum(text_el: ET.Element) -> list[str]:
    """
    Remove every segnum item so FLEx numbers the combined text itself.

    Safe by construction: FLEx's importer explicitly discards segnum —
    BIRDInterlinearImporter.AddSegmentItemData has
    `case "segnum": break; // ... not associated to a property`.  The value is
    written on export from the computed on-screen reference, so there is no
    field for it to import into.  reference-label, which FLEx *does* store on
    Segment.Reference, is deliberately left untouched.
    """
    removed = 0
    for phrase in text_el.iterfind("paragraphs/paragraph/phrases/phrase"):
        for item in _segnum_items(phrase):
            phrase.remove(item)
            removed += 1
    if removed:
        return [f"Removed {removed} segnum item(s); FLEx will number the "
                f"combined text itself on import."]
    return []


def _distribute_offsets(phrases_el: ET.Element, start_ms: int, duration_ms: int,
                        media_guid: str | None) -> int:
    """
    Give every phrase of an unsegmented text an even slice of its recording.

    A text with a recording but no time offsets cannot be represented in ELAN
    at all — an annotation there must have a time slot — so the choice is
    between approximate timing and no usable pairing.  The slices are evenly
    divided and make no claim to match the speech; the caller reports how many
    phrases were treated this way so it is never mistaken for real
    segmentation.
    """
    count = len(phrases_el)
    for i, phrase in enumerate(phrases_el):
        phrase.set("begin-time-offset", str(start_ms + round(i * duration_ms / count)))
        phrase.set("end-time-offset",
                   str(start_ms + round((i + 1) * duration_ms / count)))
        if media_guid:
            phrase.set("media-file", media_guid)
    return count


def _renumber_segnum(text_el: ET.Element) -> list[str]:
    """
    Renumber segnum continuously across the whole combined text.

    One number per phrase, written to every segnum item it has (a phrase may
    carry the same number in several writing systems).  Phrases with no segnum
    item consume a number but do not gain one — inventing segnums could
    contradict the source, and FLEx ignores the field anyway, so the gap is
    reported instead.
    """
    missing = 0
    number = 0
    for phrase in text_el.iterfind("paragraphs/paragraph/phrases/phrase"):
        number += 1
        items = _segnum_items(phrase)
        if not items:
            missing += 1
            continue
        for item in items:
            item.text = str(number)
    if missing:
        return [f"{missing} of {number} phrases had no segnum item, so the "
                f"renumbering skipped them and the sequence has gaps "
                f"({number - missing} numbered). FLEx ignores segnum on import, "
                f"so this only affects other tools reading the file — choose "
                f"'Remove' to avoid it entirely."]
    return []


def build_combined(files: list[FlextextFile], *,
                   title: str,
                   title_lang: str = "en",
                   add_title_notes: bool = True,
                   segnum_mode: str = SEGNUM_STRIP,
                   strip_audio_notes: bool = True,
                   audio_mode: str = AUDIO_DISCARD,
                   gap_ms: int = DEFAULT_GAP_MS,
                   media_location: str = "",
                   durations: dict[str, int] | None = None,
                   distribute_untimed: bool = True,
                   ) -> tuple[ET.ElementTree, list[str]]:
    """
    Combine into a single FLEx text: one <paragraph> per source text.

    Every line of a source text becomes a <phrase> of that text's paragraph,
    flattening sources that already had several paragraphs.

    segnum_mode is one of SEGNUM_STRIP (remove them and let FLEx number the
    text), SEGNUM_RENUMBER (continuous 1…N) or SEGNUM_KEEP (leave each source's
    original numbering).  Stripping is the default because FLEx discards segnum
    on import regardless, so carrying stale per-text numbering only misleads
    other tools that read the file.

    With audio_mode=AUDIO_DISCARD (the default) all audio segmentation goes —
    phrase time offsets, media-file links, speaker names and the <media-files>
    list — because they index into per-text recordings that no longer
    correspond to anything once the texts are merged.

    With audio_mode=AUDIO_SHIFT the offsets are instead rebased onto one
    concatenated recording: each text's phrases are moved forward by the total
    duration of every preceding text plus gap_ms between them.  gap_ms defaults
    to the 1005 ms click separator that the audio_concat app inserts, so a text
    combined here lines up with audio joined there.  Durations are estimated
    from each text's last annotation, which is exact only for contiguously
    annotated texts — the rest are named in the returned warnings.

    Source files are never modified, so re-running always recovers the original.
    """
    warnings: list[str] = []
    contributing: list[ET.Element] = []

    root = ET.Element("document", {"version": _output_version(files)})
    text_el = ET.SubElement(root, "interlinear-text")   # no guid: a new text

    title_item = ET.SubElement(text_el, "item",
                               {"type": "title", "lang": title_lang})
    title_item.text = title

    paragraphs_el = ET.SubElement(text_el, "paragraphs")

    shifting = audio_mode == AUDIO_SHIFT
    media_guid = str(uuid.uuid4()) if (shifting and media_location) else None
    durations = durations or {}
    # When any recording has been measured, the timeline IS that set of
    # recordings.  A text with no recording is simply not in the audio, so it
    # can neither occupy time nor carry offsets that point into it — placing it
    # anyway would push every following text out by a file that isn't there.
    audio_backed = bool(durations)
    cumulative = 0
    estimated: list[str] = []
    synthesized: list[str] = []
    unplaced: list[str] = []
    n_shifted = 0
    n_synthesized = 0

    for f in files:
        for src_text in f.texts:
            contributing.append(src_text)
            source_title = _item_text(src_text, "title") or f.path.stem

            paragraph_el = ET.SubElement(paragraphs_el, "paragraph")
            phrases_el = ET.SubElement(paragraph_el, "phrases")

            # A matched recording gives the true length; without one the only
            # estimate is the end of the last annotation, which misses any
            # trailing audio and makes every later text drift.
            audio_ms = durations.get(str(f.path)) if shifting else None
            # Absent from an audio-backed timeline: drop its timing entirely
            # rather than point it at a stretch of recording that isn't there.
            orphaned = shifting and audio_backed and audio_ms is None
            if orphaned:
                duration = 0
                unplaced.append(source_title)
            elif audio_ms is not None:
                duration = audio_ms
            elif shifting:
                duration, exact = text_duration(src_text)
                if duration and not exact:
                    estimated.append(source_title)
            else:
                duration = 0

            phrase_mode = AUDIO_DISCARD if orphaned else audio_mode
            for src_phrase in src_text.iterfind(
                    "paragraphs/paragraph/phrases/phrase"):
                phrase = copy.deepcopy(src_phrase)
                if (shifting and not orphaned
                        and phrase.get("begin-time-offset") is not None):
                    n_shifted += 1
                _clean_phrase(phrase, strip_audio_notes=strip_audio_notes,
                              audio_mode=phrase_mode, shift_ms=cumulative,
                              media_guid=media_guid)
                phrases_el.append(phrase)

            if shifting:
                already_timed = any(p.get("begin-time-offset") is not None
                                    for p in phrases_el)
                if (audio_ms and not already_timed
                        and distribute_untimed and len(phrases_el)):
                    n_synthesized += _distribute_offsets(
                        phrases_el, cumulative, audio_ms, media_guid)
                    synthesized.append(source_title)

                # Advance by the recording's real length even when the text
                # carried no offsets of its own: the audio still occupies that
                # much of the joined timeline, and skipping it would throw
                # every following text out by the whole file.
                if duration:
                    cumulative += duration + gap_ms

            if not len(phrases_el):
                warnings.append(
                    f"'{source_title}' ({f.path.name}) has no phrases; "
                    "an empty paragraph was written for it."
                )
                continue

            if add_title_notes:
                # Appended last to match FLEx's own ordering (…, gls, note).
                note = ET.SubElement(phrases_el[0], "item",
                                     {"type": "note", "lang": title_lang})
                note.text = source_title

    if segnum_mode == SEGNUM_STRIP:
        warnings.extend(_strip_segnum(text_el))
    elif segnum_mode == SEGNUM_RENUMBER:
        warnings.extend(_renumber_segnum(text_el))

    languages_el, lang_warnings = _merge_languages(contributing)
    warnings.extend(lang_warnings)
    text_el.append(languages_el)        # after <paragraphs>, as FLEx writes it

    if shifting:
        if media_guid:
            media_files = ET.SubElement(text_el, "media-files",
                                        {"offset-type": "milliseconds"})
            ET.SubElement(media_files, "media",
                          {"guid": media_guid, "location": media_location})
        warnings.extend(_shift_warnings(n_shifted, cumulative, gap_ms,
                                        estimated, bool(media_guid),
                                        synthesized, n_synthesized, unplaced))

    if not contributing:
        warnings.append("No texts were written — the input list was empty.")
    return ET.ElementTree(root), warnings


def _name_list(names: list[str], limit: int = 5) -> str:
    shown = ", ".join(f"'{n}'" for n in names[:limit])
    return shown + (f", and {len(names) - limit} more"
                    if len(names) > limit else "")


def _shift_warnings(n_shifted: int, total_ms: int, gap_ms: int,
                    estimated: list[str], has_media: bool,
                    synthesized: list[str], n_synthesized: int,
                    unplaced: list[str]) -> list[str]:
    """Explain what a shifted timeline assumes, and where it may be wrong."""
    if not n_shifted and not n_synthesized:
        return ["Shift offsets was selected, but none of the texts carry time "
                "offsets and none had a matched recording, so nothing was "
                "shifted."]

    out = [f"Built a single {total_ms / 1000:.3f}s timeline with a {gap_ms} ms "
           f"gap between texts: {n_shifted} phrase offset(s) shifted"
           + (f", {n_synthesized} given even slices of their recording."
              if n_synthesized else ".")]
    if not has_media:
        out.append("No combined audio file was named, so the phrases carry "
                   "shifted offsets but no media-file link. Give one to pair "
                   "the text with audio in ELAN.")
    if synthesized:
        out.append(
            f"{len(synthesized)} text(s) had a recording but no segmentation "
            f"({_name_list(synthesized)}), so their lines were spread evenly "
            f"across the recording. That timing is approximate — it keeps the "
            f"text usable in ELAN and keeps every later text correctly "
            f"positioned, but it does not follow the speech."
        )
    if unplaced:
        out.append(
            f"{len(unplaced)} text(s) have no matched recording "
            f"({_name_list(unplaced)}), so they are not in the joined audio. "
            f"Their lines were written without any timing rather than pointing "
            f"at a stretch of recording that does not exist, and they take up "
            f"no room in the timeline, so the texts around them stay aligned."
        )
    if estimated:
        out.append(
            f"{len(estimated)} text(s) have no matched recording, so their "
            f"length was estimated from the last annotation "
            f"({_name_list(estimated)}). Those texts are annotated in "
            f"utterances with gaps, so if the recording continues past the "
            f"final annotation, every later text drifts early by that much. "
            f"Match a recording to them to make this exact."
        )
    return out


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _indent(elem: ET.Element, space: str = "  ", level: int = 0) -> None:
    """
    Pretty-print in place, without ever touching the inside of an <item>.

    ET.indent() would descend into items, and an item is mixed content (text,
    optionally with <run> children in version 3 files).  Adding whitespace in
    there would corrupt the interlinear data itself.
    """
    if elem.tag == "item":
        return
    children = list(elem)
    if not children:
        return

    own_pad = "\n" + space * level
    child_pad = "\n" + space * (level + 1)

    if not (elem.text or "").strip():
        elem.text = child_pad
    for i, child in enumerate(children):
        _indent(child, space, level + 1)
        if not (child.tail or "").strip():
            child.tail = child_pad if i < len(children) - 1 else own_pad


def write_flextext(tree: ET.ElementTree, path) -> None:
    """
    Write a .flextext file.

    The declaration is written by hand because ElementTree emits single quotes
    (<?xml version='1.0' …?>) where FLEx and every real-world export use double.
    """
    _indent(tree.getroot())
    body = ET.tostring(tree.getroot(), encoding="unicode")
    Path(path).write_text(f"{_XML_DECLARATION}\n{body}\n", encoding="utf-8")
