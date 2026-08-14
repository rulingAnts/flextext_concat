"""
Tests for the FLExText merge engine.

Fixtures mirror the three producer dialects found in real corpora: flextext.app
(guids, time offsets, remote media), FLEx/xLingPaper (no guids, fonts, several
phrases per paragraph), and ELAN-annotated (media-files before languages,
alphabetized phrase attributes, no title).
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flextext as fx  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# flextext.app dialect: one paragraph per phrase, time offsets, audio notes.
APP_DIALECT = """<?xml version="1.0" encoding="utf-8"?>
<document version="2">
  <interlinear-text guid="text-aaa">
    <item type="title" lang="id">Cerita Satu</item>
    <paragraphs>
      <paragraph guid="para-a1">
        <phrases>
          <phrase guid="ph-a1" begin-time-offset="0" end-time-offset="2421">
            <item type="txt" lang="fau">Daia bedi</item>
            <item type="segnum" lang="id">1</item>
            <words>
              <word guid="w-a1"><item type="txt" lang="fau">Daia</item></word>
              <word guid="w-a2"><item type="txt" lang="fau">bedi</item></word>
            </words>
            <item type="gls" lang="id">Ini dulu</item>
            <item type="note" lang="id">audio ~0:00.000-0:02.421</item>
          </phrase>
        </phrases>
      </paragraph>
      <paragraph guid="para-a2">
        <phrases>
          <phrase guid="ph-a2" begin-time-offset="2421" end-time-offset="4842">
            <item type="txt" lang="fau">Bete de</item>
            <item type="segnum" lang="id">2</item>
            <words>
              <word guid="w-a3"><item type="txt" lang="fau">Bete</item></word>
            </words>
            <item type="gls" lang="id">Bapak Bete</item>
            <item type="note" lang="id">Ini catatan asli, bukan audio.</item>
          </phrase>
        </phrases>
      </paragraph>
    </paragraphs>
    <languages>
      <language lang="fau" vernacular="true" />
      <language lang="id" />
    </languages>
    <media-files offset-type="milliseconds">
      <media guid="media-a1" location="https://example.invalid/drive?src=1&amp;t=2" />
    </media-files>
  </interlinear-text>
</document>
"""

# FLEx / xLingPaper dialect: no guids anywhere, fonts, one paragraph many phrases.
FLEX_DIALECT = """<?xml version="1.0" encoding="utf-8"?>
<document version="2">
  <interlinear-text>
    <item type="title" lang="en">Crocodile Woman</item>
    <paragraphs>
      <paragraph>
        <phrases>
          <phrase>
            <item type="txt" lang="fau">Aitu waya</item>
            <item type="segnum" lang="en">1</item>
            <words>
              <word><item type="txt" lang="fau">Aitu</item></word>
            </words>
            <item type="gls" lang="en">Long ago</item>
          </phrase>
          <phrase>
            <item type="txt" lang="fau">Kani doko</item>
            <item type="segnum" lang="en">2</item>
            <words>
              <word><item type="txt" lang="fau">Kani</item></word>
              <word><item type="punct" lang="fau">.</item></word>
            </words>
            <item type="gls" lang="en">She went</item>
          </phrase>
          <phrase>
            <item type="txt" lang="fau">Nou tade</item>
            <item type="gls" lang="en">No words element here</item>
          </phrase>
        </phrases>
      </paragraph>
    </paragraphs>
    <languages>
      <language lang="fau" font="Charis SIL" vernacular="true"/>
      <language lang="en" font="Times New Roman"/>
    </languages>
  </interlinear-text>
</document>
"""

# ELAN dialect: no title, media-files BEFORE languages, speaker + media-file.
ELAN_DIALECT = """<?xml version="1.0" encoding="UTF-8"?>
<document version="2">
  <interlinear-text guid="text-ccc">
    <paragraphs>
      <paragraph guid="para-c1">
        <phrases>
          <phrase begin-time-offset="1770" end-time-offset="3870"
                  guid="ph-c1" media-file="media-c1" speaker="">
            <item type="txt" lang="fau">Sokai bede</item>
            <item type="gls" lang="id">Dia bilang</item>
            <words>
              <word guid="w-c1"><item type="txt" lang="fau">Sokai</item></word>
            </words>
            <item type="notes" lang="id">Catatan ELAN</item>
          </phrase>
        </phrases>
      </paragraph>
    </paragraphs>
    <media-files offset-type="">
      <media guid="media-c1" location="file:///tmp/example.wav"/>
    </media-files>
    <languages>
      <language font="" lang="fau" vernacular="true"/>
    </languages>
  </interlinear-text>
</document>
"""

# An "empty shell" export: a paragraph with no phrases at all.
EMPTY_SHELL = """<?xml version="1.0" encoding="utf-8"?>
<document version="2">
  <interlinear-text guid="text-ddd">
    <item type="title" lang="id">Tosokai</item>
    <paragraphs>
      <paragraph guid="para-d1">
        <phrases>
        </phrases>
      </paragraph>
    </paragraphs>
    <languages>
      <language lang="fau" vernacular="true" />
    </languages>
  </interlinear-text>
</document>
"""

# Version 3: embedded <run> children inside an item.
V3_RUNS = """<?xml version="1.0" encoding="utf-8"?>
<document version="3">
  <interlinear-text guid="text-eee">
    <item type="title" lang="en">Runs</item>
    <paragraphs>
      <paragraph>
        <phrases>
          <phrase>
            <item type="txt" lang="fau">plain <run lang="en">embedded</run> tail</item>
            <words>
              <word><item type="txt" lang="fau">plain</item></word>
            </words>
          </phrase>
        </phrases>
      </paragraph>
    </paragraphs>
    <languages>
      <language lang="fau" vernacular="true"/>
    </languages>
  </interlinear-text>
</document>
"""


@pytest.fixture
def corpus_dir(tmp_path):
    """Write the dialect fixtures to disk and return the directory."""
    for name, content in [
        ("app.flextext", APP_DIALECT),
        ("flex.flextext", FLEX_DIALECT),
        ("elan.flextext", ELAN_DIALECT),
        ("shell.flextext", EMPTY_SHELL),
        ("v3.flextext", V3_RUNS),
    ]:
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


def _load(corpus_dir, *names) -> list[fx.FlextextFile]:
    return [fx.parse_file(corpus_dir / n) for n in names]


def _roundtrip(tree, tmp_path, name="out.flextext") -> ET.Element:
    """Write then re-parse, so tests assert on real serialized output."""
    out = tmp_path / name
    fx.write_flextext(tree, out)
    return ET.parse(out).getroot()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_parse_reads_counts_and_title(corpus_dir):
    f = fx.parse_file(corpus_dir / "app.flextext")
    assert f.title == "Cerita Satu"
    assert f.version == "2"
    assert f.n_paragraphs == 2
    assert f.n_phrases == 2
    assert f.n_timed_phrases == 2
    assert f.n_media_refs == 1
    assert f.has_audio is True


def test_parse_flex_dialect_has_no_audio(corpus_dir):
    f = fx.parse_file(corpus_dir / "flex.flextext")
    assert f.n_paragraphs == 1
    assert f.n_phrases == 3
    assert f.has_audio is False


def test_parse_elan_dialect_counts_speaker_and_media_file(corpus_dir):
    f = fx.parse_file(corpus_dir / "elan.flextext")
    assert f.title is None
    assert f.n_timed_phrases == 1
    assert f.n_media_refs == 1


def test_parse_rejects_non_flextext_xml(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<html><body>nope</body></html>", encoding="utf-8")
    with pytest.raises(fx.FlextextError, match="expected <document>"):
        fx.parse_file(bad)


def test_parse_rejects_malformed_xml(tmp_path):
    bad = tmp_path / "truncated.flextext"
    bad.write_text('<?xml version="1.0"?><document><interlinear-t',
                   encoding="utf-8")
    with pytest.raises(fx.FlextextError, match="Not valid XML"):
        fx.parse_file(bad)


def test_parse_files_isolates_failures(corpus_dir):
    bad = corpus_dir / "bad.flextext"
    bad.write_text("<nope/>", encoding="utf-8")
    parsed, failures = fx.parse_files(
        [corpus_dir / "app.flextext", bad, corpus_dir / "flex.flextext"]
    )
    assert [f.path.name for f in parsed] == ["app.flextext", "flex.flextext"]
    assert len(failures) == 1 and failures[0][0].name == "bad.flextext"


# ---------------------------------------------------------------------------
# Corpus mode — must preserve everything
# ---------------------------------------------------------------------------

def test_corpus_keeps_texts_separate(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "flex.flextext", "elan.flextext")
    tree, warnings = fx.build_corpus(files)
    root = _roundtrip(tree, tmp_path)

    assert root.tag == "document"
    assert len(root.findall("interlinear-text")) == 3
    assert warnings == []


def test_corpus_keeps_languages_inside_each_text(corpus_dir, tmp_path):
    """<languages> is per-text, never hoisted to document level."""
    files = _load(corpus_dir, "app.flextext", "flex.flextext")
    root = _roundtrip(fx.build_corpus(files)[0], tmp_path)

    assert root.find("languages") is None
    for text in root.findall("interlinear-text"):
        assert text.find("languages") is not None
    fonts = [l.get("font") for l in root.iterfind(".//languages/language")]
    assert "Charis SIL" in fonts          # per-text fonts survive un-merged


def test_corpus_preserves_audio_and_media_linkage(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "elan.flextext")
    root = _roundtrip(fx.build_corpus(files)[0], tmp_path)

    phrases = root.findall(".//phrase")
    assert any(p.get("begin-time-offset") == "0" for p in phrases)
    assert any(p.get("speaker") == "" for p in phrases)
    assert len(root.findall(".//media-files/media")) == 2

    # every media-file IDREF still resolves inside its OWN text
    for text in root.findall("interlinear-text"):
        guids = {m.get("guid") for m in text.iterfind("media-files/media")}
        for phrase in text.iterfind(".//phrase"):
            ref = phrase.get("media-file")
            if ref:
                assert ref in guids


def test_corpus_strip_guids_keeps_media_guids(corpus_dir, tmp_path):
    """Stripping identity must not break phrase/@media-file → media/@guid."""
    files = _load(corpus_dir, "elan.flextext")
    root = _roundtrip(fx.build_corpus(files, strip_guids=True)[0], tmp_path)

    text = root.find("interlinear-text")
    assert text.get("guid") is None
    assert text.find(".//phrase").get("guid") is None
    assert text.find(".//word").get("guid") is None
    media = text.find("media-files/media")
    assert media.get("guid") == "media-c1"
    assert text.find(".//phrase").get("media-file") == "media-c1"


def test_corpus_order_follows_input_order(corpus_dir, tmp_path):
    files = _load(corpus_dir, "flex.flextext", "app.flextext")
    root = _roundtrip(fx.build_corpus(files)[0], tmp_path)
    titles = [t.findtext("item[@type='title']")
              for t in root.findall("interlinear-text")]
    assert titles == ["Crocodile Woman", "Cerita Satu"]


# ---------------------------------------------------------------------------
# Combined mode — structure
# ---------------------------------------------------------------------------

def test_combined_one_paragraph_per_source_text(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "flex.flextext", "elan.flextext")
    tree, _ = fx.build_combined(files, title="Kumpulan", title_lang="id")
    root = _roundtrip(tree, tmp_path)

    assert len(root.findall("interlinear-text")) == 1
    paragraphs = root.findall(".//paragraphs/paragraph")
    assert len(paragraphs) == 3          # 3 sources → 3 paragraphs

    # app.flextext had 2 paragraphs of 1 phrase; they flatten into one
    assert len(paragraphs[0].findall("phrases/phrase")) == 2
    assert len(paragraphs[1].findall("phrases/phrase")) == 3
    assert len(paragraphs[2].findall("phrases/phrase")) == 1


def test_combined_phrase_count_matches_sum_of_inputs(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "flex.flextext", "elan.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id")[0], tmp_path
    )
    assert len(root.findall(".//phrase")) == sum(f.n_phrases for f in files)


def test_combined_uses_supplied_title(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="Kumpulan Cerita", title_lang="id")[0],
        tmp_path,
    )
    text = root.find("interlinear-text")
    assert text.findtext("item[@type='title']") == "Kumpulan Cerita"
    assert text.find("item[@type='title']").get("lang") == "id"
    assert text.get("guid") is None      # a new FLEx object


def test_combined_child_order_matches_flex(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id")[0], tmp_path
    )
    tags = [c.tag for c in root.find("interlinear-text")]
    assert tags == ["item", "paragraphs", "languages"]


# ---------------------------------------------------------------------------
# Combined mode — audio segmentation removal
# ---------------------------------------------------------------------------

def test_combined_strips_all_audio_phrase_attributes(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "elan.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id")[0], tmp_path
    )
    for phrase in root.iterfind(".//phrase"):
        for attr in fx.AUDIO_PHRASE_ATTRS:
            assert attr not in phrase.attrib
        assert "guid" not in phrase.attrib


def test_combined_drops_media_files(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "elan.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id")[0], tmp_path
    )
    assert root.find(".//media-files") is None
    assert root.find(".//media") is None


def test_combined_strips_only_timestamp_notes(corpus_dir, tmp_path):
    """A note that is entirely a timestamp goes; real commentary stays."""
    files = _load(corpus_dir, "app.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id",
                          add_title_notes=False)[0],
        tmp_path,
    )
    notes = [i.text for i in root.iterfind(".//item[@type='note']")]
    assert "Ini catatan asli, bukan audio." in notes
    assert not any("audio ~" in (n or "") for n in notes)


def test_combined_keeps_timestamp_notes_when_disabled(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id",
                          add_title_notes=False, strip_audio_notes=False)[0],
        tmp_path,
    )
    notes = [i.text for i in root.iterfind(".//item[@type='note']")]
    assert any("audio ~" in (n or "") for n in notes)


@pytest.mark.parametrize("text,expected", [
    ("audio ~0:00.000-0:02.421", True),
    ("audio ~0:00.000–0:02.421", True),        # en dash
    ("audio 1:02:03.400 — 1:02:05.900", True),
    ("  AUDIO ~0:01-0:02  ", True),
    ("Ini catatan asli, bukan audio.", False),
    ("audio starts at 0:00.000-0:02.421 and is unclear", False),
    ("", False),
    (None, False),
])
def test_audio_note_detection(text, expected):
    assert fx._is_audio_note(text) is expected


def test_audio_loss_summary(corpus_dir):
    files = _load(corpus_dir, "app.flextext", "flex.flextext", "elan.flextext")
    n_files, n_phrases, n_media = fx.audio_loss_summary(files)
    assert (n_files, n_phrases, n_media) == (2, 3, 2)


def test_audio_loss_summary_is_zero_without_audio(corpus_dir):
    files = _load(corpus_dir, "flex.flextext")
    assert fx.audio_loss_summary(files) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Combined mode — shifted timeline
# ---------------------------------------------------------------------------

def test_text_duration_exact_when_annotation_tiles_from_zero(corpus_dir):
    """The flextext.app style: contiguous spans starting at 0."""
    f = fx.parse_file(corpus_dir / "app.flextext")
    duration, exact = fx.text_duration(f.texts[0])
    assert (duration, exact) == (4842, True)


def test_text_duration_inexact_when_annotation_has_gaps(corpus_dir):
    """The ELAN style: starts late, so the true file length is unknowable."""
    f = fx.parse_file(corpus_dir / "elan.flextext")
    duration, exact = fx.text_duration(f.texts[0])
    assert duration == 3870
    assert exact is False


def test_text_duration_zero_without_offsets(corpus_dir):
    f = fx.parse_file(corpus_dir / "flex.flextext")
    assert fx.text_duration(f.texts[0]) == (0, False)


def test_shift_rebases_offsets_onto_one_timeline(corpus_dir, tmp_path):
    """Second text starts after the first's duration plus the gap."""
    files = _load(corpus_dir, "app.flextext", "elan.flextext")
    tree, _ = fx.build_combined(
        files, title="X", title_lang="id",
        audio_mode=fx.AUDIO_SHIFT, gap_ms=1005,
    )
    root = _roundtrip(tree, tmp_path)
    spans = [(int(p.get("begin-time-offset")), int(p.get("end-time-offset")))
             for p in root.iterfind(".//phrase")
             if p.get("begin-time-offset") is not None]

    # app.flextext: unchanged (first text, shift 0)
    assert spans[0] == (0, 2421)
    assert spans[1] == (2421, 4842)
    # elan.flextext: shifted by 4842 + 1005 = 5847
    assert spans[2] == (1770 + 5847, 3870 + 5847)


def test_shift_uses_default_gap_matching_audio_concat(corpus_dir, tmp_path):
    """500 ms silence + 5 ms click + 500 ms silence."""
    assert fx.DEFAULT_GAP_MS == 1005
    files = _load(corpus_dir, "app.flextext", "elan.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id",
                          audio_mode=fx.AUDIO_SHIFT)[0],
        tmp_path,
    )
    spans = [int(p.get("begin-time-offset"))
             for p in root.iterfind(".//phrase")
             if p.get("begin-time-offset") is not None]
    assert spans[2] == 1770 + 4842 + 1005


def test_shift_keeps_speaker(corpus_dir, tmp_path):
    """Speaker names stay true after concatenation, unlike offsets."""
    files = _load(corpus_dir, "elan.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id",
                          audio_mode=fx.AUDIO_SHIFT)[0],
        tmp_path,
    )
    assert root.find(".//phrase").get("speaker") == ""


def test_shift_points_all_phrases_at_one_media_file(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "elan.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id",
                          audio_mode=fx.AUDIO_SHIFT,
                          media_location="file:///tmp/joined.wav")[0],
        tmp_path,
    )
    medias = root.findall(".//media-files/media")
    assert len(medias) == 1
    assert medias[0].get("location") == "file:///tmp/joined.wav"

    guid = medias[0].get("guid")
    refs = {p.get("media-file") for p in root.iterfind(".//phrase")
            if p.get("begin-time-offset") is not None}
    assert refs == {guid}


def test_shift_without_media_location_drops_media_file_refs(corpus_dir, tmp_path):
    """A stale per-text media guid would dangle; better to have none."""
    files = _load(corpus_dir, "elan.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id",
                          audio_mode=fx.AUDIO_SHIFT)[0],
        tmp_path,
    )
    assert root.find(".//media-files") is None
    assert all(p.get("media-file") is None for p in root.iterfind(".//phrase"))


def test_shift_warns_about_estimated_durations(corpus_dir):
    files = _load(corpus_dir, "app.flextext", "elan.flextext")
    _, warnings = fx.build_combined(files, title="X", title_lang="id",
                                    audio_mode=fx.AUDIO_SHIFT)
    text = " ".join(warnings)
    assert "drifts early" in text
    assert "'elan'" in text            # named as the risky one
    assert "Cerita Satu" not in text   # contiguous, so not flagged


def test_shift_reports_nothing_to_shift(corpus_dir):
    files = _load(corpus_dir, "flex.flextext")
    _, warnings = fx.build_combined(files, title="X", title_lang="en",
                                    audio_mode=fx.AUDIO_SHIFT)
    assert any("none of the texts carry time offsets" in w for w in warnings)


def test_untimed_text_does_not_advance_the_timeline(corpus_dir, tmp_path):
    """A text with no audio contributes nothing to concatenate."""
    files = _load(corpus_dir, "app.flextext", "flex.flextext", "elan.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id",
                          audio_mode=fx.AUDIO_SHIFT, gap_ms=1005)[0],
        tmp_path,
    )
    spans = [int(p.get("begin-time-offset"))
             for p in root.iterfind(".//phrase")
             if p.get("begin-time-offset") is not None]
    # flex.flextext sits between them but has no offsets, so elan still
    # starts at app's duration + one gap
    assert spans[-1] == 1770 + 4842 + 1005


def test_shift_tolerates_unparseable_offsets(tmp_path):
    src = tmp_path / "junk.flextext"
    src.write_text(
        APP_DIALECT.replace('begin-time-offset="0"', 'begin-time-offset="abc"'),
        encoding="utf-8",
    )
    root = _roundtrip(
        fx.build_combined([fx.parse_file(src)], title="X", title_lang="id",
                          audio_mode=fx.AUDIO_SHIFT)[0],
        tmp_path, "out2.flextext",
    )
    first = root.find(".//phrase")
    assert first.get("begin-time-offset") is None   # dropped, not guessed
    assert first.findtext("item[@type='txt']") == "Daia bedi"


def test_discard_remains_the_default(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id")[0], tmp_path
    )
    assert all(p.get("begin-time-offset") is None
               for p in root.iterfind(".//phrase"))


# ---------------------------------------------------------------------------
# Combined mode — content preservation
# ---------------------------------------------------------------------------

def test_combined_preserves_item_words_item_ordering(corpus_dir, tmp_path):
    """Items appear on both sides of <words>; that order must survive."""
    files = _load(corpus_dir, "app.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id",
                          add_title_notes=False,
                          segnum_mode=fx.SEGNUM_KEEP)[0],
        tmp_path,
    )
    first = root.find(".//phrase")
    tags = [(c.tag, c.get("type")) for c in first]
    assert tags == [
        ("item", "txt"),
        ("item", "segnum"),
        ("words", None),
        ("item", "gls"),
    ]


def test_combined_preserves_words_and_punct(corpus_dir, tmp_path):
    files = _load(corpus_dir, "flex.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="en")[0], tmp_path
    )
    assert len(root.findall(".//word")) == 3
    assert root.findtext(".//item[@type='punct']") == "."


def test_combined_tolerates_phrase_without_words(corpus_dir, tmp_path):
    """ELAN omits <words> even though the XSD says minOccurs=1."""
    files = _load(corpus_dir, "flex.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="en")[0], tmp_path
    )
    phrases = root.findall(".//phrase")
    assert phrases[2].find("words") is None
    assert phrases[2].findtext("item[@type='txt']") == "Nou tade"


def test_combined_strips_word_guids(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id")[0], tmp_path
    )
    assert all("guid" not in w.attrib for w in root.iterfind(".//word"))


# ---------------------------------------------------------------------------
# Combined mode — title notes and segnum
# ---------------------------------------------------------------------------

def test_title_notes_land_on_first_phrase_of_each_paragraph(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "flex.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id")[0], tmp_path
    )
    paragraphs = root.findall(".//paragraph")
    for para, expected in zip(paragraphs, ["Cerita Satu", "Crocodile Woman"]):
        phrases = para.findall("phrases/phrase")
        notes = [i.text for i in phrases[0].iterfind("item[@type='note']")]
        assert expected in notes
        assert phrases[0][-1].get("type") == "note"      # appended last
        for later in phrases[1:]:
            assert expected not in [i.text for i in
                                    later.iterfind("item[@type='note']")]


def test_title_note_falls_back_to_filename(corpus_dir, tmp_path):
    """The ELAN dialect has no title item."""
    files = _load(corpus_dir, "elan.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id")[0], tmp_path
    )
    notes = [i.text for i in root.iterfind(".//item[@type='note']")]
    assert "elan" in notes


def test_title_notes_can_be_disabled(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id",
                          add_title_notes=False)[0],
        tmp_path,
    )
    notes = [i.text for i in root.iterfind(".//item[@type='note']")]
    assert "Cerita Satu" not in notes


def test_segnum_is_stripped_by_default(corpus_dir, tmp_path):
    """
    FLEx discards segnum on import (BIRDInterlinearImporter: `case "segnum":
    break;`), so the default is to remove it and let FLEx number the text.
    """
    files = _load(corpus_dir, "app.flextext", "flex.flextext")
    tree, warnings = fx.build_combined(files, title="X", title_lang="id")
    root = _roundtrip(tree, tmp_path)

    assert root.findall(".//item[@type='segnum']") == []
    assert any("FLEx will number" in w for w in warnings)
    # the rest of each phrase is untouched
    assert len(root.findall(".//phrase")) == 5
    assert root.find(".//phrase").findtext("item[@type='txt']") == "Daia bedi"


def test_segnum_strip_leaves_reference_label_alone(tmp_path):
    """reference-label maps to Segment.Reference and IS stored by FLEx."""
    src = tmp_path / "ref.flextext"
    src.write_text(
        FLEX_DIALECT.replace(
            '<item type="segnum" lang="en">1</item>',
            '<item type="segnum" lang="en">1</item>'
            '<item type="reference-label" lang="en">1.1</item>',
        ),
        encoding="utf-8",
    )
    root = _roundtrip(
        fx.build_combined([fx.parse_file(src)], title="X", title_lang="en")[0],
        tmp_path,
    )
    assert root.findall(".//item[@type='segnum']") == []
    assert root.findtext(".//item[@type='reference-label']") == "1.1"


def test_segnum_renumbers_continuously_across_paragraphs(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "flex.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id",
                          segnum_mode=fx.SEGNUM_RENUMBER)[0],
        tmp_path,
    )
    segnums = [i.text for i in root.iterfind(".//item[@type='segnum']")]
    assert segnums == ["1", "2", "3", "4"]     # 2 from app + 2 of 3 from flex


def test_segnum_gap_is_reported_when_renumbering(corpus_dir):
    """flex.flextext has a third phrase with no segnum item."""
    files = _load(corpus_dir, "flex.flextext")
    _, warnings = fx.build_combined(files, title="X", title_lang="en",
                                    segnum_mode=fx.SEGNUM_RENUMBER)
    assert any("1 of 3 phrases had no segnum" in w for w in warnings)


def test_segnum_kept_verbatim(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "flex.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id",
                          segnum_mode=fx.SEGNUM_KEEP)[0],
        tmp_path,
    )
    segnums = [i.text for i in root.iterfind(".//item[@type='segnum']")]
    assert segnums == ["1", "2", "1", "2"]     # original per-text numbering


# ---------------------------------------------------------------------------
# Combined mode — language union
# ---------------------------------------------------------------------------

def test_languages_union_is_keyed_by_lang(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "flex.flextext", "elan.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id")[0], tmp_path
    )
    langs = root.findall(".//languages/language")
    codes = [l.get("lang") for l in langs]
    assert sorted(codes) == ["en", "fau", "id"]
    assert len(codes) == len(set(codes))       # xs:key uniqueness


def test_vernacular_flag_is_sticky(corpus_dir, tmp_path):
    """Without a vernacular language FLEx guesses on import."""
    files = _load(corpus_dir, "app.flextext", "flex.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="id")[0], tmp_path
    )
    fau = root.find(".//languages/language[@lang='fau']")
    assert fau.get("vernacular") == "true"


def test_empty_font_does_not_overwrite_a_real_one(corpus_dir, tmp_path):
    """The ELAN dialect carries font=""; it must not clobber Charis SIL."""
    files = _load(corpus_dir, "flex.flextext", "elan.flextext")
    root = _roundtrip(
        fx.build_combined(files, title="X", title_lang="en")[0], tmp_path
    )
    fau = root.find(".//languages/language[@lang='fau']")
    assert fau.get("font") == "Charis SIL"


def test_conflicting_fonts_warn_and_keep_first(corpus_dir, tmp_path):
    other = corpus_dir / "other.flextext"
    other.write_text(
        FLEX_DIALECT.replace('font="Charis SIL"', 'font="Doulos SIL"'),
        encoding="utf-8",
    )
    files = _load(corpus_dir, "flex.flextext", "other.flextext")
    tree, warnings = fx.build_combined(files, title="X", title_lang="en")
    root = _roundtrip(tree, tmp_path)
    assert any("conflicting fonts" in w for w in warnings)
    assert root.find(".//languages/language[@lang='fau']").get("font") \
        == "Charis SIL"


def test_language_attribute_order_is_normalized(corpus_dir, tmp_path):
    """ELAN writes font before lang; output uses FLEx's order."""
    files = _load(corpus_dir, "elan.flextext")
    out = tmp_path / "out.flextext"
    fx.write_flextext(fx.build_combined(files, title="X",
                                        title_lang="id")[0], out)
    text = out.read_text(encoding="utf-8")
    assert '<language lang="fau" vernacular="true" />' in text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_shell_produces_empty_paragraph_and_warning(corpus_dir, tmp_path):
    files = _load(corpus_dir, "shell.flextext")
    tree, warnings = fx.build_combined(files, title="X", title_lang="id")
    root = _roundtrip(tree, tmp_path)

    assert len(root.findall(".//paragraph")) == 1
    assert root.findall(".//phrase") == []
    assert any("no phrases" in w for w in warnings)


def test_empty_input_warns_in_both_modes():
    _, corpus_warnings = fx.build_corpus([])
    _, combined_warnings = fx.build_combined([], title="X", title_lang="en")
    assert any("empty" in w for w in corpus_warnings)
    assert any("empty" in w for w in combined_warnings)


def test_version_3_wins_over_version_2(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "v3.flextext")
    for tree, _ in (fx.build_corpus(files),
                    fx.build_combined(files, title="X", title_lang="en")):
        root = _roundtrip(tree, tmp_path)
        assert root.get("version") == "3"


def test_version_defaults_to_2(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext", "flex.flextext")
    root = _roundtrip(fx.build_corpus(files)[0], tmp_path)
    assert root.get("version") == "2"


def test_runs_inside_items_survive_serialization(corpus_dir, tmp_path):
    """Indenting must never inject whitespace into mixed item content."""
    files = _load(corpus_dir, "v3.flextext")
    out = tmp_path / "out.flextext"
    fx.write_flextext(fx.build_combined(files, title="X",
                                        title_lang="en")[0], out)
    raw = out.read_text(encoding="utf-8")
    assert '<item type="txt" lang="fau">plain <run lang="en">embedded</run> tail</item>' in raw

    root = ET.parse(out).getroot()
    item = root.find(".//item[@type='txt']")
    assert item.text == "plain "
    assert item.find("run").text == "embedded"
    assert item.find("run").tail == " tail"


def test_multiple_interlinear_texts_in_one_file(tmp_path):
    """A corpus file used as input yields one paragraph per contained text."""
    merged = tmp_path / "already_corpus.flextext"
    body = APP_DIALECT.split("\n", 1)[1].strip()
    inner = body[len("<document version=\"2\">"):-len("</document>")]
    merged.write_text(
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f'<document version="2">{inner}{inner}</document>\n',
        encoding="utf-8",
    )
    f = fx.parse_file(merged)
    assert len(f.texts) == 2

    root = _roundtrip(
        fx.build_combined([f], title="X", title_lang="id")[0], tmp_path
    )
    assert len(root.findall(".//paragraph")) == 2


def test_output_declaration_uses_double_quotes(corpus_dir, tmp_path):
    files = _load(corpus_dir, "app.flextext")
    out = tmp_path / "out.flextext"
    fx.write_flextext(fx.build_corpus(files)[0], out)
    assert out.read_text(encoding="utf-8").startswith(
        '<?xml version="1.0" encoding="utf-8"?>\n<document'
    )


def test_source_files_are_never_modified(corpus_dir, tmp_path):
    before = (corpus_dir / "app.flextext").read_bytes()
    files = _load(corpus_dir, "app.flextext")
    fx.build_combined(files, title="X", title_lang="id")
    fx.build_corpus(files, strip_guids=True)
    assert (corpus_dir / "app.flextext").read_bytes() == before


def test_default_title_lang_prefers_existing_title_language(corpus_dir):
    assert fx.default_title_lang(_load(corpus_dir, "app.flextext")) == "id"
    assert fx.default_title_lang(_load(corpus_dir, "flex.flextext")) == "en"


def test_default_title_lang_avoids_vernacular(corpus_dir):
    """elan.flextext has no title and only a vernacular language."""
    assert fx.default_title_lang(_load(corpus_dir, "elan.flextext")) == "fau"


def test_collect_languages(corpus_dir):
    files = _load(corpus_dir, "app.flextext", "flex.flextext")
    assert fx.collect_languages(files) == ["fau", "id", "en"]
