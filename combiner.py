"""
Background worker for combining .flextext files.

A plain QObject moved onto a QThread, so the GUI stays responsive while a large
batch is parsed.  Unlike a single blanket try/except, a file that fails to parse
is recorded and skipped: one bad file among fifty must not lose the whole run.
"""

from pathlib import Path

from PySide6.QtCore import QObject, Signal

import flextext as fx


class CombineResult:
    """What a finished run produced, for the completion dialog."""

    def __init__(self, output_file: str = ""):
        self.output_file = output_file
        self.n_texts = 0
        self.n_paragraphs = 0
        self.n_phrases = 0
        # Texts that could not be parsed, and so are absent from the output.
        self.failures: list[tuple[Path, str]] = []
        # Recordings that could not be read. Kept apart from `failures` because
        # the text still made it into the output — only its timing did not.
        self.audio_failures: list[tuple[Path, str]] = []
        self.audio_warnings: list[str] = []
        self.warnings: list[str] = []
        self.audio_file = ""
        self.audio_ms = 0

    @property
    def n_skipped(self) -> int:
        return len(self.failures)


class CombineWorker(QObject):
    """
    Combines a list of .flextext paths into one output file.

    Signals:
        progress(int, str)  — files done so far, and what is happening now
        success(object)     — a CombineResult
        error(str)          — the run could not produce a file at all
        finished()          — always emitted, success or not
    """

    progress = Signal(int, str)
    success = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, output_file: str, file_paths: list[str], mode: str,
                 options: dict | None = None):
        super().__init__()
        self.output_file = output_file
        self.file_paths = list(file_paths)
        self.mode = mode                      # "corpus" | "combined"
        self.options = options or {}
        self._cancelled = False

    def cancel(self):
        """Ask the run to stop; checked between files."""
        self._cancelled = True

    def run(self):
        try:
            result = self._run()
            if result is not None:
                self.success.emit(result)
        except Exception as exc:              # noqa: BLE001 - last-resort guard
            self.error.emit(f"The merge failed:\n\n{exc}")
        finally:
            self.finished.emit()

    def _handle_audio(self, parsed: list[fx.FlextextFile],
                      result: CombineResult) -> dict[str, int] | None:
        """
        Measure — and optionally join — the recordings matched to each text.

        Returns {flextext path: duration_ms}, or None if the run should stop.
        Joining and measuring happen in one pass so the durations are exactly
        the lengths the files occupy in the output; measuring separately could
        disagree after resampling.
        """
        import audio as au

        pairs: dict = self.options.get("audio_paths") or {}
        matched = [(str(f.path), pairs.get(str(f.path)))
                   for f in parsed if pairs.get(str(f.path))]
        if not matched:
            return {}

        joining = bool(self.options.get("join_audio"))

        # Check once, up front. Without this every recording fails the same way
        # and, when only measuring, the run would quietly fall back to
        # estimating durations from annotations — the very drift the user chose
        # this mode to avoid.
        if not au.have_ffmpeg():
            message = (
                "ffmpeg was not found, so recordings cannot be "
                f"{'joined' if joining else 'measured'}.\n\n"
                "Install ffmpeg system-wide, or place a binary in the bin/ "
                "folder next to this app."
            )
            if joining:
                self.error.emit(message + "\n\nNothing was written.")
                return None
            result.audio_warnings.append(
                message.replace("\n\n", " ")
                + " Text lengths were estimated from each text's last "
                  "annotation instead, which drifts for texts annotated in "
                  "utterances with gaps."
            )
            return {}

        if not joining:
            self.progress.emit(0, "Measuring recordings…")
            durations, failures = au.probe_durations([a for _, a in matched])
            result.audio_failures += failures
            return {flex: durations[aud] for flex, aud in matched
                    if aud in durations}

        output = self.options.get("media_location", "")
        try:
            lengths, actual_gap = au.join_audio(
                [a for _, a in matched], output,
                gap_ms=self.options.get("gap_ms", au.SEPARATOR_MS),
                progress=lambda i, name: self.progress.emit(
                    i, f"Joining audio: {name}…"),
                cancelled=lambda: self._cancelled,
            )
        except au.AudioCancelled as exc:
            self.error.emit(f"{exc} Nothing was written.")
            return None
        except au.AudioError as exc:
            self.error.emit(
                f"The audio could not be joined, so nothing was written.\n\n"
                f"{exc}"
            )
            return None

        # Use the separator's rendered length, not its nominal one, so the
        # offsets land exactly on the audio we just wrote.
        self.options["gap_ms"] = actual_gap
        result.audio_file = output
        return {flex: length for (flex, _), length in zip(matched, lengths)}

    def _collision(self) -> str | None:
        """
        Refuse to write an output over any input.

        The app promises in several places that source files are never
        modified; that must hold even when an output path happens to point at
        a source. resolve() catches the same file reached via a different
        route (relative path, symlink, case-insensitive filesystem).
        """
        def canon(p) -> str:
            try:
                resolved = Path(p).resolve()
            except OSError:
                resolved = Path(p)
            # macOS and Windows filesystems are case-insensitive by default.
            return str(resolved).lower()

        sources = {canon(p): p for p in self.file_paths}
        for a in (self.options.get("audio_paths") or {}).values():
            if a:
                sources[canon(a)] = a

        for label, out in (("output file", self.output_file),
                           ("joined audio", self.options.get("media_location")
                            if self.options.get("join_audio") else None)):
            if out and canon(out) in sources:
                return (f"The {label} path is one of the source files:\n\n"
                        f"{out}\n\nWriting it would destroy that source. "
                        f"Choose a different path.")
        return None

    def _run(self) -> CombineResult | None:
        total = len(self.file_paths)
        if not total:
            self.error.emit("No files to combine.")
            return None

        clash = self._collision()
        if clash:
            self.error.emit(clash)
            return None

        result = CombineResult(self.output_file)
        parsed: list[fx.FlextextFile] = []

        for i, path in enumerate(self.file_paths):
            if self._cancelled:
                self.error.emit(
                    f"Cancelled after reading {i} of {total} file(s). "
                    "Nothing was written."
                )
                return None
            self.progress.emit(i, f"Reading {Path(path).name}…")
            try:
                parsed.append(fx.parse_file(path))
            except fx.FlextextError as exc:
                result.failures.append((Path(path), str(exc)))

        if not parsed:
            self.error.emit(
                "None of the selected files could be read as FLExText.\n\n"
                + "\n".join(f"• {p.name}: {m}" for p, m in result.failures[:10])
            )
            return None

        if self._cancelled:
            self.error.emit("Cancelled before writing. Nothing was written.")
            return None

        durations: dict[str, int] = {}
        if self.mode == "combined" and self.options.get("audio_mode") == fx.AUDIO_SHIFT:
            durations = self._handle_audio(parsed, result)
            if durations is None:
                return None
            if self._cancelled:
                self.error.emit("Cancelled before writing. Nothing was written.")
                return None

        self.progress.emit(total, "Building output…")
        if self.mode == "combined":
            tree, warnings = fx.build_combined(
                parsed,
                title=self.options.get("title", ""),
                title_lang=self.options.get("title_lang", "en"),
                add_title_notes=self.options.get("add_title_notes", True),
                segnum_mode=self.options.get("segnum_mode", fx.SEGNUM_STRIP),
                strip_audio_notes=self.options.get("strip_audio_notes", True),
                audio_mode=self.options.get("audio_mode", fx.AUDIO_DISCARD),
                gap_ms=self.options.get("gap_ms", fx.DEFAULT_GAP_MS),
                media_location=self.options.get("media_location", ""),
                durations=durations,
                distribute_untimed=self.options.get("distribute_untimed", True),
            )
        else:
            tree, warnings = fx.build_corpus(
                parsed, strip_guids=self.options.get("strip_guids", False)
            )
        result.warnings = warnings

        self.progress.emit(total, "Writing file…")
        try:
            fx.write_flextext(tree, self.output_file)
        except OSError as exc:
            self.error.emit(f"Could not write the output file:\n\n{exc}")
            return None

        root = tree.getroot()
        result.audio_ms = sum(durations.values())
        result.n_texts = len(root.findall("interlinear-text"))
        result.n_paragraphs = len(root.findall(".//paragraph"))
        result.n_phrases = len(root.findall(".//phrase"))
        return result
