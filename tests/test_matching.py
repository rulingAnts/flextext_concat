"""
Tests for audio<->flextext filename matching.

Cases are taken from real filename pairs in a working Fayu corpus, which is
where all the awkwardness comes from: export timestamps, speaker and date
prefixes, processing suffixes, spelling drift, and audio titled in a different
language from the text.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matching as m  # noqa: E402


def _mk(tmp_path, rel: str) -> str:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return str(p)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Tosokai 2026-07-20-1803", "tosokai"),
    ("Soni Uu pu Mimpi 2026-08-01-0936 (1)", "soni uu pu mimpi"),
    ("Matius Gets Lost in the Jungle (includes comments)-xLingPaper",
     "matius gets lost in the jungle"),
    ("video_kegiatan_2b (concatenated, 24 to 32 bit upscaled)",
     "video kegiatan 2b"),
    ("Tautua Do (dikirim lagi) 2026-07-20-1216", "tautua do"),
    ("Permasalahan Ayub dengan Ibu--annotated", "permasalahan ayub dengan ibu"),
])
def test_normalize_strips_export_decoration(raw, expected):
    assert m.normalize(raw) == expected


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flex,aud", [
    # audio carries a date and speaker prefix around the text's name
    ("Crocodile Woman", "2021-06-21 Barnabas Doi Crocodile Woman"),
    ("Boa Constrictor Attack", "2021-07-21 Boa Constrictor Attack"),
    # case differs
    ("Edo gets bit by a snake", "Edo Gets Bit by a Snake"),
    # processing suffix on the audio
    ("Soni Uu pu Mimpi 2026-07-20-1803", "Soni Uu pu mimpi.enhanced.converted"),
    ("Pantat Kaskado 2026-07-30-1630", "pantat kaskado.NON-ARCHIVAL"),
    # tool suffix on the flextext
    ("Matius Gets Lost in the Jungle (includes comments)-xLingPaper",
     "Matius Gets Lost in the Jungle"),
])
def test_real_pairs_score_high(flex, aud):
    assert m.score(flex, aud) >= m._MIN_SCORE


@pytest.mark.parametrize("flex,aud", [
    ("Akulah War Story", "Akuila War Story"),        # spelling drift
    ("Crocodile Hunters", "Crocodile Huters"),       # typo in the audio
    ("Motor lepas di kali kirihi", "Motor Lepas Di Kali Kilihi"),
])
def test_near_misses_still_match(flex, aud):
    assert m.score(flex, aud) > 0.3


def test_unrelated_names_do_not_match():
    assert m.score("Crocodile Woman", "Airplane Accident in Derapos") == 0.0


def test_stopwords_alone_do_not_create_a_match():
    assert m.score("the a of in", "to and for on") == 0.0


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def test_one_recording_can_serve_many_snapshots(tmp_path):
    """
    A corpus holds many dated exports of one text; each must suggest the same
    recording rather than the first claiming it.
    """
    flex = [_mk(tmp_path, f"Tosokai/Soni Uu pu Mimpi 2026-07-{d}-1803.flextext")
            for d in ("20", "23", "24", "28")]
    aud = [_mk(tmp_path, "Tosokai/Soni Uu pu mimpi.enhanced.wav")]

    result = m.match_audio(flex, aud)
    assert all(result[f] == aud[0] for f in flex)


def test_falls_back_to_the_folder_when_names_differ(tmp_path):
    """Audio titled in another language: only the folder relates them."""
    flex = _mk(tmp_path, "Tosokai/Tosokai 2026-07-20-1803.flextext")
    aud = _mk(tmp_path, "Tosokai/2021-07-22 Yohanes Suhu death and aftermath.wav")

    found, confidence = m.match_one(flex, [aud])
    assert found == aud
    assert confidence == m.BY_FOLDER


def test_name_match_is_preferred_over_folder_match(tmp_path):
    flex = _mk(tmp_path, "d/Crocodile Woman.flextext")
    named = _mk(tmp_path, "d/2021-06-21 Barnabas Doi Crocodile Woman.m4a")
    other = _mk(tmp_path, "d/something entirely different.wav")

    found, confidence = m.match_one(flex, [other, named])
    assert found == named
    assert confidence == m.BY_NAME


def test_no_audio_in_folder_means_no_match(tmp_path):
    flex = _mk(tmp_path, "empty/James Impales His Junk.flextext")
    aud = _mk(tmp_path, "elsewhere/unrelated recording.wav")

    found, confidence = m.match_one(flex, [aud])
    assert found is None
    assert confidence is None


def test_same_folder_wins_a_tie(tmp_path):
    flex = _mk(tmp_path, "here/Crocodile Woman.flextext")
    near = _mk(tmp_path, "here/Crocodile Woman.m4a")
    far = _mk(tmp_path, "there/Crocodile Woman.m4a")

    assert m.match_one(flex, [far, near])[0] == near


def test_several_unrelated_candidates_yield_no_match(tmp_path):
    """
    Two recordings beside the text, neither resembling its name: choosing one
    would be arbitrary, so both are left for the user to assign by hand.
    """
    flex = _mk(tmp_path, "d/Eti Makan Sabun.flextext")
    a = _mk(tmp_path, "d/Eti eats soap.enhanced.wav")
    b = _mk(tmp_path, "d/completely unrelated thing.wav")

    found, confidence = m.match_one(flex, [b, a])
    assert found is None
    assert confidence is None


def test_name_match_still_wins_among_several_candidates(tmp_path):
    """Ambiguity only blocks the fallback, never a genuine name match."""
    flex = _mk(tmp_path, "d/Crocodile Woman.flextext")
    named = _mk(tmp_path, "d/2021 Barnabas Doi Crocodile Woman.m4a")
    other = _mk(tmp_path, "d/something else entirely.wav")

    found, confidence = m.match_one(flex, [other, named])
    assert found == named
    assert confidence == m.BY_NAME


def test_match_audio_with_confidence_shape(tmp_path):
    flex = _mk(tmp_path, "d/Crocodile Woman.flextext")
    aud = _mk(tmp_path, "d/Crocodile Woman.m4a")

    plain = m.match_audio([flex], [aud])
    rich = m.match_audio([flex], [aud], with_confidence=True)
    assert plain == {flex: aud}
    assert rich == {flex: (aud, m.BY_NAME)}


def test_empty_audio_pool_matches_nothing(tmp_path):
    flex = _mk(tmp_path, "d/Anything.flextext")
    assert m.match_audio([flex], []) == {flex: None}
