# bin/ — bundled ffmpeg binary

Place a platform-specific static ffmpeg binary here so `audio.py` finds it at
runtime and PyInstaller bundles it.

| Platform | Required     | Optional      |
|----------|--------------|---------------|
| macOS    | `ffmpeg`     | `ffprobe`     |
| Linux    | `ffmpeg`     | `ffprobe`     |
| Windows  | `ffmpeg.exe` | `ffprobe.exe` |

ffprobe is optional — pydub copes without it, and CI omits it to keep the
download smaller.

The app falls back to a system-wide `ffmpeg` on PATH when nothing is bundled,
which is convenient while developing.

## What needs it

Only combined mode with **Shift onto one concatenated recording**: measuring
each recording's true length, and joining them. Everything else — corpus mode,
combined mode that discards audio, all sorting and matching — is pure XML and
runs without ffmpeg. The app degrades to estimating durations from annotations
rather than failing.

## Where to get static builds

- **macOS / Linux**: <https://www.ffmpeg.org/download.html> → "Static Builds",
  or `brew install ffmpeg` and copy the binary here.
- **Windows**: <https://www.gyan.dev/ffmpeg/builds/> (release-essentials).
