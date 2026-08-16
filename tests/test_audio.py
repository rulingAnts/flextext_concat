"""
Tests for the audio layer.

These need ffmpeg, since pydub shells out to it for anything but raw WAV.
They skip rather than fail when it is absent, because the rest of the app
works without it.
"""

import math
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audio as au  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(
    not au.have_ffmpeg(), reason="ffmpeg not available")


def make_wav(path: Path, seconds: float, hz: int = 440, rate: int = 44100):
    """A plain mono 16-bit tone of an exact length."""
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(9000 * math.sin(2 * math.pi * hz * i / rate)))
            for i in range(int(rate * seconds))
        ))
    return str(path)


def wav_ms(path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate() * 1000


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_extensions_cover_the_formats_in_use():
    for ext in (".wav", ".mp3", ".m4a", ".flac", ".ogg"):
        assert ext in au.AUDIO_EXTENSIONS


def test_separator_matches_audio_concat():
    """The whole point of the default gap is that the two apps agree."""
    assert au.SEPARATOR_MS == au.SILENCE_MS * 2 + au.CLICK_MS == 1005


def test_configure_without_ffmpeg_raises(monkeypatch):
    monkeypatch.setattr(au, "find_binary", lambda name: None)
    with pytest.raises(au.AudioError, match="ffmpeg was not found"):
        au.configure()


def test_missing_audioop_explains_the_python_version(monkeypatch):
    """
    pydub imports the stdlib `audioop`, removed in Python 3.13. The bare
    ModuleNotFoundError names a third-party dependency and says nothing about
    what to do, so it is translated.
    """
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pydub":
            raise ModuleNotFoundError("No module named 'audioop'",
                                      name="audioop")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(au.AudioError, match="removed the 'audioop' module"):
        au.configure()


def test_missing_pydub_is_reported_separately(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pydub":
            raise ModuleNotFoundError("No module named 'pydub'", name="pydub")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(au.AudioError, match="pydub is not installed"):
        au.configure()


def test_have_ffmpeg_reflects_discovery(monkeypatch):
    monkeypatch.setattr(au, "find_binary", lambda name: None)
    assert au.have_ffmpeg() is False
    monkeypatch.setattr(au, "find_binary", lambda name: "/usr/bin/ffmpeg")
    assert au.have_ffmpeg() is True


# ---------------------------------------------------------------------------
# Durations
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_probe_duration_is_exact(tmp_path):
    assert au.probe_duration_ms(make_wav(tmp_path / "a.wav", 2.0)) == 2000


@needs_ffmpeg
def test_probe_durations_isolates_failures(tmp_path):
    good = make_wav(tmp_path / "good.wav", 1.0)
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio at all")

    durations, failures = au.probe_durations([good, str(bad)])
    assert durations == {good: 1000}
    assert len(failures) == 1
    assert failures[0][0].name == "bad.wav"


def test_probe_durations_skips_empty_entries():
    durations, failures = au.probe_durations([None, ""])
    assert durations == {} and failures == []


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_join_returns_measured_durations_and_gap(tmp_path):
    a = make_wav(tmp_path / "a.wav", 2.0)
    b = make_wav(tmp_path / "b.wav", 3.0)
    out = tmp_path / "joined.wav"

    durations, gap = au.join_audio([a, b], str(out))
    assert durations == [2000, 3000]
    assert gap == au.SEPARATOR_MS
    # the arithmetic the shifted offsets rely on must match the real file
    assert abs((sum(durations) + gap) - wav_ms(out)) < 1.0


@needs_ffmpeg
def test_join_with_zero_gap_is_gapless(tmp_path):
    a = make_wav(tmp_path / "a.wav", 1.0)
    b = make_wav(tmp_path / "b.wav", 1.0)
    out = tmp_path / "joined.wav"

    durations, gap = au.join_audio([a, b], str(out), gap_ms=0, use_click=False)
    assert gap == 0
    assert abs(wav_ms(out) - 2000) < 1.0


@needs_ffmpeg
def test_join_with_plain_silence_gap(tmp_path):
    a = make_wav(tmp_path / "a.wav", 1.0)
    b = make_wav(tmp_path / "b.wav", 1.0)
    out = tmp_path / "joined.wav"

    _, gap = au.join_audio([a, b], str(out), gap_ms=500, use_click=False)
    assert gap == 500
    assert abs(wav_ms(out) - 2500) < 1.0


@needs_ffmpeg
def test_single_file_has_no_trailing_separator(tmp_path):
    a = make_wav(tmp_path / "a.wav", 1.5)
    out = tmp_path / "joined.wav"

    durations, _ = au.join_audio([a], str(out))
    assert durations == [1500]
    assert abs(wav_ms(out) - 1500) < 1.0


@needs_ffmpeg
def test_join_normalises_differing_sample_rates(tmp_path):
    a = make_wav(tmp_path / "a.wav", 1.0, rate=22050)
    b = make_wav(tmp_path / "b.wav", 1.0, rate=44100)
    out = tmp_path / "joined.wav"

    durations, gap = au.join_audio([a, b], str(out))
    assert durations == [1000, 1000]
    assert abs((sum(durations) + gap) - wav_ms(out)) < 2.0
    with wave.open(str(out)) as w:
        assert w.getframerate() == au.STD_RATE


def test_join_with_no_paths_raises():
    with pytest.raises(au.AudioError, match="No audio files"):
        au.join_audio([], "/tmp/never-written.wav")


@needs_ffmpeg
def test_join_reports_the_unreadable_file_by_name(tmp_path):
    good = make_wav(tmp_path / "good.wav", 1.0)
    bad = tmp_path / "broken.wav"
    bad.write_bytes(b"nope")

    with pytest.raises(au.AudioError, match="broken.wav"):
        au.join_audio([good, str(bad)], str(tmp_path / "out.wav"))


# ---------------------------------------------------------------------------
# Cancellation — the Cancel button used to do nothing once joining began
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_cancel_stops_the_join_and_writes_nothing(tmp_path):
    files = [make_wav(tmp_path / f"{i}.wav", 0.5) for i in range(4)]
    out = tmp_path / "joined.wav"
    seen = []

    def cancelled():
        return len(seen) >= 2        # stop partway through

    with pytest.raises(au.AudioCancelled):
        au.join_audio(files, str(out),
                      progress=lambda i, name: seen.append(name),
                      cancelled=cancelled)

    assert not out.exists(), "a cancelled join must leave no output behind"
    assert len(seen) == 2


@needs_ffmpeg
def test_cancel_before_the_first_file(tmp_path):
    files = [make_wav(tmp_path / "a.wav", 0.5)]
    out = tmp_path / "joined.wav"

    with pytest.raises(au.AudioCancelled, match="0 of 1"):
        au.join_audio(files, str(out), cancelled=lambda: True)
    assert not out.exists()


def test_cancelled_is_an_audio_error():
    """Callers that catch AudioError must not miss a cancellation."""
    assert issubclass(au.AudioCancelled, au.AudioError)


# ---------------------------------------------------------------------------
# Memory behaviour
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_join_does_not_reallocate_per_file(tmp_path, monkeypatch):
    """
    Joining used to do `combined += segment` per file, reallocating the whole
    buffer each time — order n squared in bytes copied.  Frames are now
    collected and joined once, so AudioSegment.__add__ should not be used.
    """
    from pydub import AudioSegment

    calls = []
    original = AudioSegment.__add__

    def counting_add(self, other):
        calls.append(1)
        return original(self, other)

    monkeypatch.setattr(AudioSegment, "__add__", counting_add)
    files = [make_wav(tmp_path / f"{i}.wav", 0.3) for i in range(5)]
    au.join_audio(files, str(tmp_path / "out.wav"))

    # only the click separator itself is built with +, never the accumulation
    assert len(calls) <= 2, f"{len(calls)} concatenations for 5 files"
