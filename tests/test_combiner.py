"""
Tests for the worker's safety guards.

Qt-dependent but headless: CombineWorker is a QObject, so a QApplication (or
QCoreApplication) must exist, but no window is shown and run() is called
synchronously.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication  # noqa: E402

import flextext as fx  # noqa: E402
from combiner import CombineWorker  # noqa: E402
from test_flextext import APP_DIALECT, FLEX_DIALECT  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def sources(tmp_path):
    a = tmp_path / "a.flextext"
    b = tmp_path / "b.flextext"
    a.write_text(APP_DIALECT, encoding="utf-8")
    b.write_text(FLEX_DIALECT, encoding="utf-8")
    return [str(a), str(b)]


def run_worker(output, paths, mode="corpus", options=None):
    result = {}
    worker = CombineWorker(str(output), paths, mode, options or {})
    worker.success.connect(lambda r: result.update(ok=r))
    worker.error.connect(lambda m: result.update(err=m))
    worker.run()
    return result


# ---------------------------------------------------------------------------
# Output-over-input protection — the app promises sources are never modified
# ---------------------------------------------------------------------------

def test_output_over_a_source_text_is_refused(tmp_path, sources):
    before = Path(sources[0]).read_bytes()
    result = run_worker(sources[0], sources)

    assert "would destroy that source" in result.get("err", "")
    assert "ok" not in result
    assert Path(sources[0]).read_bytes() == before


def test_output_over_a_source_via_relative_path_is_refused(tmp_path, sources):
    """The same file reached by a different route must still be caught."""
    sneaky = str(Path(sources[0]).parent / "." / Path(sources[0]).name)
    result = run_worker(sneaky, sources)
    assert "would destroy that source" in result.get("err", "")


def test_joined_audio_over_a_source_recording_is_refused(tmp_path, sources):
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"RIFF")             # never read: refused before ffmpeg
    result = run_worker(
        tmp_path / "out.flextext", sources, mode="combined",
        options={
            "title": "X", "title_lang": "id",
            "audio_mode": fx.AUDIO_SHIFT, "join_audio": True,
            "media_location": str(wav),
            "audio_paths": {sources[0]: str(wav)},
        },
    )
    assert "would destroy that source" in result.get("err", "")
    assert wav.read_bytes() == b"RIFF"


def test_audio_output_not_checked_when_not_joining(tmp_path, sources):
    """
    Without joining, media_location is only recorded in the XML — pointing it
    at an existing recording is legitimate, not a collision.
    """
    wav = tmp_path / "existing.wav"
    wav.write_bytes(b"RIFF")
    result = run_worker(
        tmp_path / "out.flextext", sources, mode="combined",
        options={
            "title": "X", "title_lang": "id",
            "audio_mode": fx.AUDIO_SHIFT, "join_audio": False,
            "media_location": str(wav),
            "audio_paths": {},
        },
    )
    assert "ok" in result


def test_distinct_output_path_is_accepted(tmp_path, sources):
    result = run_worker(tmp_path / "out.flextext", sources)
    assert "ok" in result
    assert result["ok"].n_texts == 2
