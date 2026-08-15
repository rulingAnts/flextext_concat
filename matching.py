"""
Match .flextext files to their audio recordings by filename.

Exact stem matches are rare in practice.  Real pairs in a working corpus look
like this:

    Crocodile Woman.flextext          <- 2021-06-21 Barnabas Doi Crocodile Woman.m4a
    Edo gets bit by a snake.flextext  <- Edo Gets Bit by a Snake.m4a
    Video_kegiatan_2b.flextext        <- video_kegiatan_2b (concatenated, 24 to 32 bit upscaled).wav
    Matius Gets Lost in the Jungle (includes comments)-xLingPaper.flextext
                                      <- Matius Gets Lost in the Jungle.MP3

so matching is fuzzy: strip the export timestamps and tool suffixes FLEx and
flextext.app append, fold case and punctuation, then score on shared words.
The result is a suggestion — the pairing table lets the user correct it.

The <media location> attribute is deliberately not used as a hint: in real
exports it is overwhelmingly a remote URL carrying no filename.
"""

import re
from pathlib import Path

# A parenthetical describing processing is noise as a whole — dropping it
# wholesale avoids leaving fragments like "24 to" behind from
# "(concatenated, 24 to 32 bit upscaled)".  Parentheticals without one of these
# words are left alone, because they are often speaker names that carry real
# matching signal: "Otodemo, pindahan ke Dairi ( Pilipus Sata )".
_NOISE_PARENTHETICAL = re.compile(
    r"\([^)]*\b(?:concatenated|upscaled|enhanced|converted|archival|bit|"
    r"khz|mono|stereo|normali[sz]ed|version|copy|draft|includes?\s+comments?|"
    r"periksa|template)\b[^)]*\)",
    re.IGNORECASE,
)

# Export-time decorations that carry no identity, stripped before comparison.
_NOISE_PATTERNS = [
    r"\d{4}-\d{2}-\d{2}-\d{4}",             # flextext.app: 2026-07-20-1803
    r"\d{4}-\d{2}-\d{2}",                   # plain dates
    r"\bxlingpaper\b",
    r"\bannotated\b",
    r"\bconcatenated\b",
    r"\bincludes?\s+comments?\b",
    r"\bcopy\b",
    r"\bfinal\b",
    r"\bdone\b",
    r"\bv?\d+\s*bit\b",
    r"\bupscaled\b",
    r"\bdikirim(\s+lagi)?\b",               # Indonesian: "sent (again)"
    r"\(\s*\d+\s*\)",                       # trailing "(1)" duplicates
]

# Words too common in this corpus to carry matching signal on their own.
_STOPWORDS = {
    "the", "a", "an", "of", "in", "to", "and", "for", "on", "at", "by",
    "dan", "di", "ke", "yang", "pu", "dengan",
}

_MIN_SCORE = 0.34          # below this, no suggestion is offered


def normalize(name: str) -> str:
    """Fold a filename stem to comparable text."""
    text = _NOISE_PARENTHETICAL.sub(" ", name.lower())
    for pattern in _NOISE_PATTERNS:
        text = re.sub(pattern, " ", text)
    text = re.sub(r"[_\-–—.,'’\"()\[\]{}!?:;/\\+]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(name: str) -> set[str]:
    """Meaningful words in a filename, for overlap scoring."""
    words = {w for w in normalize(name).split() if len(w) > 1}
    meaningful = words - _STOPWORDS
    return meaningful or words        # never return empty on a stopword-only name


def score(flextext_stem: str, audio_stem: str) -> float:
    """
    0.0–1.0 similarity between two filename stems.

    Containment scores high on purpose: audio filenames in the field routinely
    carry a date and speaker prefix around the text's name, so the flextext
    name being a subset of the audio name is the single strongest signal.
    """
    a, b = tokens(flextext_stem), tokens(audio_stem)
    if not a or not b:
        return 0.0
    overlap = len(a & b)
    if not overlap:
        return 0.0
    if a <= b or b <= a:                       # one name contains the other
        return 0.9 + 0.1 * (overlap / max(len(a), len(b)))
    return overlap / len(a | b)                # Jaccard


def best_match(flextext_path: str, audio_paths: list[str],
               *, prefer_same_folder: bool = True) -> tuple[str | None, float]:
    """Highest-scoring audio candidate for one .flextext file, and its score."""
    fstem = Path(flextext_path).stem
    fdir = Path(flextext_path).parent

    best: str | None = None
    best_score = 0.0
    for aud in audio_paths:
        value = score(fstem, Path(aud).stem)
        if value <= 0:
            continue
        if prefer_same_folder and Path(aud).parent == fdir:
            value += 0.05
        if value > best_score:
            best, best_score = aud, value
    return (best, best_score) if best_score >= _MIN_SCORE else (None, best_score)


BY_NAME = "name"        # filenames genuinely resemble each other
BY_FOLDER = "folder"    # names differ, but the audio sits beside the text


def match_one(flextext_path: str, audio_paths: list[str],
              *, prefer_same_folder: bool = True) -> tuple[str | None, str | None]:
    """
    Suggest an audio file for one text, with how confident the suggestion is.

    Falls back to folder layout when names do not resemble each other, because
    in real corpora the audio is often titled in a different language from the
    text — `Eti Makan Sabun.flextext` beside `Eti eats soap.wav`, or
    `Tosokai.flextext` beside `2021-07-22 Yohanes Suhu Tokosau's death and
    aftermath.wav`.  No amount of string similarity finds those, but sitting in
    the same folder does.  Folder matches are flagged so the UI can ask the
    user to check them.
    """
    found, _ = best_match(flextext_path, audio_paths,
                          prefer_same_folder=prefer_same_folder)
    if found:
        return found, BY_NAME

    siblings = [a for a in audio_paths
                if Path(a).parent == Path(flextext_path).parent]
    if not siblings:
        return None, None
    if len(siblings) == 1:
        return siblings[0], BY_FOLDER
    # Several candidates beside the text: take the closest by name, still weak.
    best = max(siblings,
               key=lambda a: score(Path(flextext_path).stem, Path(a).stem))
    return best, BY_FOLDER


def match_audio(flextext_paths: list[str], audio_paths: list[str],
                *, prefer_same_folder: bool = True,
                with_confidence: bool = False):
    """
    Suggest one audio file per .flextext file.

    Each file is matched independently, and **one recording may be suggested
    for several texts**.  That is deliberate: a working corpus holds many dated
    export snapshots of the same text (a dozen `Tosokai 2026-07-…flextext` all
    describing one recording), and making audio exclusive would let the first
    snapshot claim it and starve the rest.  The user includes whichever
    snapshot they want.

    Genuinely including two texts that share a recording would double-count it
    in the timeline, so the caller warns about duplicates at combine time
    rather than the matcher silently preventing it here.

    With with_confidence=True, values are (path, BY_NAME|BY_FOLDER|None).
    """
    pairs = {
        flex: match_one(flex, audio_paths, prefer_same_folder=prefer_same_folder)
        for flex in flextext_paths
    }
    if with_confidence:
        return pairs
    return {flex: found for flex, (found, _) in pairs.items()}
