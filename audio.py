"""
Audio support: ffmpeg discovery, duration probing, and concatenation.

Only needed when combining in "shift offsets" mode with matched audio.  The
rest of the app has no audio dependency at all, so this module is imported
lazily and its absence degrades to estimating durations from annotations.

Concatenation mirrors the companion audio_concat app exactly — same 44.1 kHz
mono normalisation and the same 500 ms + 5 ms click + 500 ms separator — so a
timeline built here lines up with audio joined there.
"""

import shutil
import sys
from pathlib import Path

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".aif", ".aiff", ".ogg",
                    ".m4a", ".mp4", ".wma", ".aac"}

# The separator audio_concat inserts between files.
SILENCE_MS = 500
CLICK_MS = 5
CLICK_HZ = 2000
SEPARATOR_MS = SILENCE_MS * 2 + CLICK_MS      # 1005

STD_RATE = 44100
STD_CHANNELS = 1


class AudioError(Exception):
    """Audio could not be read, joined, or ffmpeg is unavailable."""


class AudioCancelled(AudioError):
    """The caller asked to stop partway through; nothing was written."""


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------

def _app_root() -> Path:
    """
    Root directory for binary lookup.

    - PyInstaller onefile   → sys._MEIPASS (extraction temp dir)
    - PyInstaller onefolder → directory containing sys.executable
    - Plain script          → directory containing this file
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).parent
    return Path(__file__).parent


def find_binary(name: str) -> str | None:
    """Locate 'ffmpeg' or 'ffprobe': bundled bin/ first, then PATH."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    bundled = _app_root() / "bin" / exe
    if bundled.is_file():
        return str(bundled)
    return shutil.which(name)


def have_ffmpeg() -> bool:
    return find_binary("ffmpeg") is not None


def configure() -> tuple[str | None, str | None]:
    """
    Point pydub at the best available binaries.

    Returns (ffmpeg, ffprobe); ffprobe may be None (pydub copes without it).
    Raises AudioError if ffmpeg cannot be found at all.
    """
    try:
        from pydub import AudioSegment
    except ImportError as exc:
        raise AudioError(
            "pydub is not installed, so audio cannot be read or joined.\n\n"
            "Install it with:  pip install pydub"
        ) from exc

    ffmpeg = find_binary("ffmpeg")
    ffprobe = find_binary("ffprobe")
    if ffmpeg is None:
        raise AudioError(
            "ffmpeg was not found.\n\n"
            "Either place an ffmpeg binary in the bin/ folder next to this "
            "app, or install ffmpeg system-wide so it is on your PATH."
        )

    AudioSegment.converter = ffmpeg
    if ffprobe:
        AudioSegment.ffprobe = ffprobe
    return ffmpeg, ffprobe


# ---------------------------------------------------------------------------
# Durations
# ---------------------------------------------------------------------------

def probe_duration_ms(path) -> int:
    """
    True length of an audio file in milliseconds.

    This is what makes shifted offsets exact: a .flextext file never records
    how long its recording is, so without the audio the only estimate is the
    end of the last annotation, which misses any trailing silence.
    """
    configure()
    from pydub import AudioSegment
    try:
        return len(AudioSegment.from_file(str(path)))
    except Exception as exc:                      # pydub raises many types
        raise AudioError(f"Could not read '{Path(path).name}': {exc}") from exc


def probe_durations(paths) -> tuple[dict[str, int], list[tuple[Path, str]]]:
    """
    Probe many files, isolating failures.

    Returns ({path: duration_ms}, [(path, message)]) so one unreadable
    recording does not abort the batch.
    """
    durations: dict[str, int] = {}
    failures: list[tuple[Path, str]] = []
    for path in paths:
        if not path:
            continue
        try:
            durations[str(path)] = probe_duration_ms(path)
        except AudioError as exc:
            failures.append((Path(path), str(exc)))
    return durations, failures


# ---------------------------------------------------------------------------
# Concatenation
# ---------------------------------------------------------------------------

def _click_segment():
    """Silence + a short decaying 2 kHz click + silence, as audio_concat makes it."""
    import numpy as np
    from pydub import AudioSegment

    t = np.linspace(0, CLICK_MS / 1000,
                    int(STD_RATE * CLICK_MS / 1000), endpoint=False)
    wave = np.sin(2 * np.pi * CLICK_HZ * t) * np.linspace(1, 0, len(t))
    data = (wave * 0.5 * (2 ** 15 - 1)).astype(np.int16).tobytes()
    click = AudioSegment(data=data, sample_width=2,
                         frame_rate=STD_RATE, channels=1)
    silence = AudioSegment.silent(duration=SILENCE_MS, frame_rate=STD_RATE)
    return silence + click + silence


def join_audio(paths: list[str], output: str, *, gap_ms: int = SEPARATOR_MS,
               use_click: bool = True, sample_width: int = 2,
               progress=None, cancelled=None) -> tuple[list[int], int]:
    """
    Concatenate audio files, returning (durations_ms, actual_gap_ms).

    Both numbers are measured from the rendered audio rather than assumed, so
    offsets computed from them land exactly.  The separator in particular is
    not always its nominal length — a 5 ms click at 44.1 kHz is 220 frames —
    and using the nominal figure would push every text slightly past where the
    audio really goes.

    Raw frames are accumulated in a list and joined once at the end.  Repeatedly
    doing `combined += segment` reallocates the whole buffer per file, which
    copies on the order of n²: fifty four-minute texts would move about 27 GB
    to produce a 1 GB result.

    `progress(i, name)` is called before each file; `cancelled()` is polled
    between files and raises AudioCancelled without writing anything.
    """
    configure()
    from pydub import AudioSegment

    if not paths:
        raise AudioError("No audio files to join.")

    if use_click and gap_ms == SEPARATOR_MS:
        separator = _click_segment()
    elif gap_ms > 0:
        separator = AudioSegment.silent(duration=gap_ms, frame_rate=STD_RATE)
    else:
        separator = None
    if separator is not None:
        separator = separator.set_sample_width(sample_width) \
                             .set_channels(STD_CHANNELS)

    chunks: list[bytes] = []
    durations: list[int] = []
    for i, path in enumerate(paths):
        if cancelled and cancelled():
            raise AudioCancelled(
                f"Cancelled after {i} of {len(paths)} recording(s).")
        if progress:
            progress(i, Path(path).name)
        try:
            seg = AudioSegment.from_file(str(path))
        except Exception as exc:
            raise AudioError(f"Could not read '{Path(path).name}': {exc}") from exc
        if seg.frame_rate != STD_RATE:
            seg = seg.set_frame_rate(STD_RATE)
        if seg.channels != STD_CHANNELS:
            seg = seg.set_channels(STD_CHANNELS)
        if seg.sample_width != sample_width:
            seg = seg.set_sample_width(sample_width)

        durations.append(len(seg))
        chunks.append(seg.raw_data)
        if separator is not None and i < len(paths) - 1:
            chunks.append(separator.raw_data)

    if cancelled and cancelled():
        raise AudioCancelled("Cancelled before writing.")

    combined = AudioSegment(data=b"".join(chunks), sample_width=sample_width,
                            frame_rate=STD_RATE, channels=STD_CHANNELS)
    try:
        combined.export(output, format="wav")
    except Exception as exc:
        raise AudioError(f"Could not write '{output}': {exc}") from exc
    return durations, (len(separator) if separator is not None else 0)
