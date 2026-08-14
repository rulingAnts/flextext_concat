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
        self.failures: list[tuple[Path, str]] = []
        self.warnings: list[str] = []

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

    def _run(self) -> CombineResult | None:
        total = len(self.file_paths)
        if not total:
            self.error.emit("No files to combine.")
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
        result.n_texts = len(root.findall("interlinear-text"))
        result.n_paragraphs = len(root.findall(".//paragraph"))
        result.n_phrases = len(root.findall(".//phrase"))
        return result
