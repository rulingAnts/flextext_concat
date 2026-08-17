#!/usr/bin/env python3
"""
FLExText Concatenator
GUI for combining SIL FieldWorks .flextext interlinear exports, either as a
corpus of separate texts or as one combined text.
"""

import re
import sys
from pathlib import Path
from typing import Optional

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import audio
import flextext as fx
import matching
from combiner import CombineWorker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FLEXTEXT_EXTENSIONS = fx.FLEXTEXT_EXTENSIONS

SORT_FIELDS = [
    ("Name (alphabetical)", "name"),
    ("Name (numerical)",    "num"),
    ("Text title",          "title"),
    ("Date created",        "ctime"),
    ("Date modified",       "mtime"),
    ("Date accessed",       "atime"),
]

SORT_DIRECTIONS = [
    ("Ascending",  False),
    ("Descending", True),
]

OUTPUT_MODES = [
    ("Corpus (separate texts)", "corpus"),
    ("Combined Text (one text)", "combined"),
]

# FLEx discards segnum on import, so removing it is the safe default; the other
# two exist for tools that do read it.  See flextext.SEGNUM_* for the detail.
SEGNUM_CHOICES = [
    ("Remove (FLEx numbers it)", fx.SEGNUM_STRIP),
    ("Renumber 1…N",            fx.SEGNUM_RENUMBER),
    ("Keep original numbers",   fx.SEGNUM_KEEP),
]

# Discarding is the default because it needs nothing from the user; shifting
# assumes the source audio really will be concatenated in the same order.
AUDIO_CHOICES = [
    ("Discard segmentation",        fx.AUDIO_DISCARD),
    ("Shift onto joined recording", fx.AUDIO_SHIFT),
]

REGEX_SORT_MODES = [
    ("Natural text", "natural"),
    ("Numeric",      "numeric"),
    ("Alphabetical", "alpha"),
]

_REGEX_TOOLTIP = (
    "Enter a Python regex with at least one capture group  ( ) .\n"
    "The text captured by the group is used as this layer's sort key.\n\n"
    "Examples:\n"
    "  ^(\\d+)              captures a leading number\n"
    "  (\\d{4}-\\d{2}-\\d{2})  captures a date like '2026-07-20'\n"
    "  ^([^-]+)-           captures everything before the first hyphen\n\n"
    "Tip: Not sure how to write a regex? Describe what you need to an\n"
    "AI assistant (e.g. Claude or ChatGPT) and ask it to generate the\n"
    "pattern for you."
)

_WARN_STYLE = (
    "background: #fef3c7; border: 1px solid #f59e0b; "
    "border-radius: 4px; padding: 6px; color: #78350f;"
)
_OK_STYLE = (
    "background: #ecfdf5; border: 1px solid #10b981; "
    "border-radius: 4px; padding: 6px; color: #064e3b;"
)

# ---------------------------------------------------------------------------
# Sort helpers — pure list[str] -> list[str], no Qt
# ---------------------------------------------------------------------------

def _natural_key(s: str) -> list:
    """Sort key that orders 'text2' before 'text10'."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


def _ctime(path: str) -> float:
    """Creation time: st_birthtime on macOS, st_ctime elsewhere."""
    stat = Path(path).stat()
    return getattr(stat, "st_birthtime", stat.st_ctime)


def _sorted_paths(paths: list[str], field: str, reverse: bool,
                  titles: dict[str, str] | None = None) -> list[str]:
    if field == "name":
        return sorted(paths, key=lambda p: Path(p).name.lower(), reverse=reverse)
    if field == "num":
        return sorted(paths, key=lambda p: _natural_key(Path(p).name), reverse=reverse)
    if field == "title":
        # Files with no <item type="title"> fall back to the filename stem, so
        # an untitled export still lands somewhere predictable.
        lookup = titles or {}
        return sorted(
            paths,
            key=lambda p: _natural_key(lookup.get(p) or Path(p).stem),
            reverse=reverse,
        )
    if field == "ctime":
        return sorted(paths, key=_ctime, reverse=reverse)
    if field == "mtime":
        return sorted(paths, key=lambda p: Path(p).stat().st_mtime, reverse=reverse)
    if field == "atime":
        return sorted(paths, key=lambda p: Path(p).stat().st_atime, reverse=reverse)
    return paths



def _apply_single_regex_layer(paths: list[str], pattern: str, group: int,
                               mode: str, reverse: bool) -> list[str]:
    """Single-layer regex sort. Unmatched files sort after matched ones."""
    try:
        rx = re.compile(pattern)
    except re.error:
        return paths

    matched: list[tuple[str, str]] = []
    unmatched: list[str] = []
    for path in paths:
        m = rx.search(Path(path).name)
        if m:
            try:
                captured = m.group(group)
                matched.append((captured, path))
                continue
            except IndexError:
                pass
        unmatched.append(path)

    if mode == "numeric":
        def key(item: tuple[str, str]):
            try:
                return float(item[0])
            except (ValueError, TypeError):
                return 0.0
    elif mode == "natural":
        def key(item: tuple[str, str]):
            return _natural_key(item[0])
    else:  # alpha
        def key(item: tuple[str, str]):
            return item[0].lower()

    matched.sort(key=key, reverse=reverse)
    return [p for _, p in matched] + unmatched


def _apply_multilayer_regex_sort(paths: list[str], layers: list[dict]) -> list[str]:
    """
    Multi-level stable sort applied from least-significant to most-significant
    layer, so Layer 1 (top of the list) acts as the primary sort key.
    """
    result = list(paths)
    for layer in reversed(layers):
        pat = layer.get("pattern", "")
        if pat:
            result = _apply_single_regex_layer(
                result, pat, layer["group"], layer["mode"], layer["reverse"]
            )
    return result


# ---------------------------------------------------------------------------
# DraggableListWidget — shared by the file list and the suffix order list
# ---------------------------------------------------------------------------

class DraggableListWidget(QListWidget):
    """
    QListWidget with multi-select drag-and-drop reordering.

    Shift+Click extends contiguous selection.
    Ctrl/Cmd+Click toggles individual items.
    Drag a selection to a new position — relative order is preserved.

    With accept_external=True the widget also takes files dropped from the
    desktop file manager and reports them via files_dropped.
    """

    files_dropped = Signal(list)

    def __init__(self, parent=None, accept_external: bool = False):
        super().__init__(parent)
        self._accept_external = accept_external
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(
            QAbstractItemView.DragDropMode.DragDrop if accept_external
            else QAbstractItemView.DragDropMode.InternalMove
        )

    def _is_external(self, event) -> bool:
        return (self._accept_external
                and event.source() is not self
                and event.mimeData().hasUrls())

    def dragEnterEvent(self, event):
        if self._is_external(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._is_external(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self._is_external(event):
            paths = [u.toLocalFile() for u in event.mimeData().urls()
                     if u.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
            else:
                event.ignore()
            return

        if event.source() is not self:
            event.ignore()
            return

        target_item = self.itemAt(event.position().toPoint())
        if target_item is None:
            target_row = self.count()
        else:
            target_row = self.row(target_item)
            if self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.BelowItem:
                target_row += 1

        selected_rows = sorted(self.row(item) for item in self.selectedItems())
        if not selected_rows:
            event.ignore()
            return

        items_data = [
            (self.item(r).text(),
             self.item(r).data(Qt.ItemDataRole.UserRole),
             self.item(r).toolTip())
            for r in selected_rows
        ]
        rows_above = sum(1 for r in selected_rows if r < target_row)
        adjusted = target_row - rows_above

        for row in reversed(selected_rows):
            self.takeItem(row)

        for offset, (text, data, tip) in enumerate(items_data):
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, data)
            item.setToolTip(tip)
            self.insertItem(adjusted + offset, item)

        self.clearSelection()
        for offset in range(len(items_data)):
            self.item(adjusted + offset).setSelected(True)

        event.accept()


# ---------------------------------------------------------------------------
# Unmatched-audio pane
# ---------------------------------------------------------------------------

class UnmatchedAudioList(QListWidget):
    """
    Recordings found in the audio folder(s) that no text currently uses.

    Dragging an entry onto a row of the pairing table assigns it — the drag
    carries a file URL, so the table's normal external-drop path handles it
    and nothing here needs to know about rows.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setToolTip(
            "Recordings not assigned to any text.\n"
            "Drag one onto a text's row to pair them.")

    def mimeData(self, items):
        from PySide6.QtCore import QMimeData, QUrl
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(i.data(Qt.ItemDataRole.UserRole))
                      for i in items])
        return data

    def set_paths(self, paths: list[str]):
        self.clear()
        for path in paths:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.addItem(item)


# ---------------------------------------------------------------------------
# Pairing table — texts on the left, their recordings on the right
# ---------------------------------------------------------------------------

class PairingTable(QTableWidget):
    """
    Two columns: the .flextext file, which keys the row, and the audio matched
    to it.

    The row order is the order texts are written and audio is joined, so the
    two can never disagree.  Dragging in the left column moves whole rows,
    keeping each text with its recording; dragging in the right column moves
    just the audio, to correct a mismatched pairing.  Files dropped from the
    desktop are routed by extension — audio onto the row it lands on, texts
    appended to the list.
    """

    TEXT_COL = 0
    AUDIO_COL = 1

    texts_dropped = Signal(list)      # .flextext paths from an external drop
    pairing_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["Text (.flextext)", "Audio"])
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setWordWrap(False)

        header = self.horizontalHeader()
        header.setSectionResizeMode(self.TEXT_COL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.AUDIO_COL, QHeaderView.ResizeMode.Stretch)

        self._drag_col = self.TEXT_COL
        self._drag_row: int | None = None
        self.itemDoubleClicked.connect(self._on_double_click)

    # ── Row data ─────────────────────────────────────────────────────────────

    def text_path(self, row: int) -> str:
        return self.item(row, self.TEXT_COL).data(Qt.ItemDataRole.UserRole)

    def audio_path(self, row: int) -> str | None:
        item = self.item(row, self.AUDIO_COL)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def text_paths(self) -> list[str]:
        return [self.text_path(r) for r in range(self.rowCount())]

    def pairs(self) -> list[tuple[str, str | None]]:
        return [(self.text_path(r), self.audio_path(r))
                for r in range(self.rowCount())]

    def set_rows(self, rows: list[tuple[str, str | None, str | None]],
                 labels: dict[str, str] | None = None):
        """rows = [(text_path, audio_path, confidence)]."""
        labels = labels or {}
        self.setRowCount(0)
        for text_path, audio_path, confidence in rows:
            self._append(text_path, audio_path, confidence, labels)
        self.pairing_changed.emit()

    def _append(self, text_path, audio_path, confidence, labels):
        row = self.rowCount()
        self.insertRow(row)

        left = QTableWidgetItem(labels.get(text_path) or Path(text_path).name)
        left.setData(Qt.ItemDataRole.UserRole, text_path)
        left.setToolTip(text_path)
        self.setItem(row, self.TEXT_COL, left)
        self.set_audio(row, audio_path, confidence)

    def set_audio(self, row: int, audio_path: str | None,
                  confidence: str | None = None):
        if audio_path:
            label = Path(audio_path).name
            tip = audio_path
            if confidence == matching.BY_FOLDER:
                # Named differently from the text; only the folder relates them,
                # so mark it for the user to confirm rather than trust silently.
                label = "? " + label
                tip = ("Matched because it sits in the same folder as the "
                       f"text, not by name — please check.\n\n{audio_path}")
        else:
            label, tip = "— none —", "Double-click to choose an audio file."

        item = QTableWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, audio_path)
        item.setData(Qt.ItemDataRole.UserRole + 1, confidence)
        item.setToolTip(tip)
        if not audio_path:
            item.setForeground(Qt.GlobalColor.gray)
        self.setItem(row, self.AUDIO_COL, item)

    def clear_audio(self, rows):
        for row in rows:
            self.set_audio(row, None)
        self.pairing_changed.emit()

    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.selectedIndexes()})

    # ── Interaction ──────────────────────────────────────────────────────────

    def _on_double_click(self, item):
        if item.column() != self.AUDIO_COL:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the audio for this text", "",
            "Audio files (*" + " *".join(sorted(audio.AUDIO_EXTENSIONS))
            + ");;All files (*)",
        )
        if path:
            self.set_audio(item.row(), path)
            self.pairing_changed.emit()

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if index.isValid():
            self._drag_col = index.column()
            self._drag_row = index.row()
        else:
            self._drag_row = None
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self._drop_external(event)
            return
        if event.source() is not self:
            event.ignore()
            return
        if self._drag_col == self.AUDIO_COL:
            self._drop_audio(event)
        else:
            self._drop_rows(event)

    def _target_row(self, event) -> int:
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            return self.rowCount()
        row = index.row()
        if (self.dropIndicatorPosition()
                == QAbstractItemView.DropIndicatorPosition.BelowItem):
            row += 1
        return row

    def _drop_external(self, event):
        """Audio lands on the row under the cursor; texts join the list."""
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        sounds = [p for p in paths
                  if Path(p).suffix.lower() in audio.AUDIO_EXTENSIONS]
        others = [p for p in paths if p not in sounds]

        index = self.indexAt(event.position().toPoint())
        if sounds and index.isValid():
            for offset, path in enumerate(sounds):
                row = index.row() + offset
                if row < self.rowCount():
                    self.set_audio(row, path)
            self.pairing_changed.emit()
        elif sounds:
            others += sounds          # dropped past the last row: treat as files

        if others:
            self.texts_dropped.emit(others)
        event.acceptProposedAction()

    def _drop_audio(self, event):
        """
        Swap an audio assignment between two rows.

        Swapping rather than moving: dropping onto a row that already has a
        recording would otherwise discard it silently, and the row you dragged
        from is the obvious place for it to go.  The dragged row is the one the
        drag started on, not merely the first selected, so a stray multi-row
        selection cannot move the wrong pairing.
        """
        source = self._drag_row
        target = min(self._target_row(event), self.rowCount() - 1)
        if source is None or target < 0 or source == target:
            event.ignore()
            return

        def read(row):
            item = self.item(row, self.AUDIO_COL)
            return (item.data(Qt.ItemDataRole.UserRole),
                    item.data(Qt.ItemDataRole.UserRole + 1))

        source_audio, source_conf = read(source)
        target_audio, target_conf = read(target)
        self.set_audio(target, source_audio, source_conf)
        self.set_audio(source, target_audio, target_conf)
        self.pairing_changed.emit()
        event.accept()

    def _drop_rows(self, event):
        """Move whole rows, so each text keeps its recording."""
        source_rows = self.selected_rows()
        if not source_rows:
            event.ignore()
            return
        target = self._target_row(event)
        captured = [
            (self.item(r, self.TEXT_COL).text(), self.text_path(r),
             self.audio_path(r),
             self.item(r, self.AUDIO_COL).data(Qt.ItemDataRole.UserRole + 1))
            for r in source_rows
        ]
        above = sum(1 for r in source_rows if r < target)
        target -= above

        for r in reversed(source_rows):
            self.removeRow(r)
        for offset, (label, text_path, audio_path, confidence) in enumerate(captured):
            row = target + offset
            self.insertRow(row)
            left = QTableWidgetItem(label)
            left.setData(Qt.ItemDataRole.UserRole, text_path)
            left.setToolTip(text_path)
            self.setItem(row, self.TEXT_COL, left)
            self.set_audio(row, audio_path, confidence)

        self.clearSelection()
        for offset in range(len(captured)):
            self.selectRow(target + offset)
        self.pairing_changed.emit()
        event.accept()


# ---------------------------------------------------------------------------
# Simple (GUI) sort panel
# ---------------------------------------------------------------------------


class GuiSortPanel(QWidget):
    """The Simple sort panel: sort field and direction."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Sort by:"))
        self.field_combo = QComboBox()
        for label, _ in SORT_FIELDS:
            self.field_combo.addItem(label)
        sort_row.addWidget(self.field_combo)
        self.dir_combo = QComboBox()
        for label, _ in SORT_DIRECTIONS:
            self.dir_combo.addItem(label)
        sort_row.addWidget(self.dir_combo)
        self.apply_sort_btn = QPushButton("Apply Sort")
        sort_row.addWidget(self.apply_sort_btn)
        sort_row.addStretch()
        layout.addLayout(sort_row)

    @property
    def sort_field(self) -> str:
        return SORT_FIELDS[self.field_combo.currentIndex()][1]

    @property
    def sort_reverse(self) -> bool:
        return SORT_DIRECTIONS[self.dir_combo.currentIndex()][1]


# ---------------------------------------------------------------------------
# Advanced (Regex) sort panel
# ---------------------------------------------------------------------------

class RegexLayerWidget(QWidget):
    """One row in the Advanced sort panel representing a single sort layer."""

    remove_requested    = Signal()
    move_up_requested   = Signal()
    move_down_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(4)

        up_btn = QPushButton("▲")
        up_btn.setFixedWidth(26)
        up_btn.setToolTip("Move this layer up (higher priority)")
        up_btn.clicked.connect(self.move_up_requested)

        down_btn = QPushButton("▼")
        down_btn.setFixedWidth(26)
        down_btn.setToolTip("Move this layer down (lower priority)")
        down_btn.clicked.connect(self.move_down_requested)

        self.pattern_edit = QLineEdit()
        self.pattern_edit.setPlaceholderText(
            "Regex with capture group, e.g.  ^(\\d+)  or  (\\d{4}-\\d{2}-\\d{2})"
        )
        self.pattern_edit.setToolTip(_REGEX_TOOLTIP)
        self.pattern_edit.textChanged.connect(self._validate)

        self.status_lbl = QLabel()
        self.status_lbl.setFixedWidth(16)

        self.group_spin = QSpinBox()
        self.group_spin.setRange(1, 9)
        self.group_spin.setValue(1)
        self.group_spin.setFixedWidth(44)
        self.group_spin.setToolTip(
            "Which capture group to sort by.\n"
            "Group 1 = first  ( ),  group 2 = second  ( ),  etc."
        )

        self.mode_combo = QComboBox()
        for label, _ in REGEX_SORT_MODES:
            self.mode_combo.addItem(label)
        self.mode_combo.setToolTip(
            "Natural text:  '2' sorts before '10'  (recommended for most names)\n"
            "Numeric:       parse the captured text as a number\n"
            "Alphabetical:  plain A–Z string comparison"
        )

        self.dir_combo = QComboBox()
        for label, _ in SORT_DIRECTIONS:
            self.dir_combo.addItem(label)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(26)
        remove_btn.setToolTip("Remove this layer")
        remove_btn.clicked.connect(self.remove_requested)

        layout.addWidget(up_btn)
        layout.addWidget(down_btn)
        layout.addWidget(self.pattern_edit, 1)
        layout.addWidget(self.status_lbl)
        layout.addWidget(QLabel("Grp:"))
        layout.addWidget(self.group_spin)
        layout.addWidget(self.mode_combo)
        layout.addWidget(self.dir_combo)
        layout.addWidget(remove_btn)

    def _validate(self, text: str):
        if not text:
            self.status_lbl.setText("")
            self.status_lbl.setToolTip("")
            return
        try:
            re.compile(text)
            self.status_lbl.setText("✓")
            self.status_lbl.setStyleSheet("color: green; font-weight: bold;")
            self.status_lbl.setToolTip("Valid regex pattern")
        except re.error as exc:
            self.status_lbl.setText("✗")
            self.status_lbl.setStyleSheet("color: red; font-weight: bold;")
            self.status_lbl.setToolTip(f"Invalid pattern: {exc}")

    def layer_config(self) -> dict:
        return {
            "pattern": self.pattern_edit.text().strip(),
            "group":   self.group_spin.value(),
            "mode":    REGEX_SORT_MODES[self.mode_combo.currentIndex()][1],
            "reverse": SORT_DIRECTIONS[self.dir_combo.currentIndex()][1],
        }

    def set_config(self, config: dict):
        self.pattern_edit.setText(config.get("pattern", ""))
        self.group_spin.setValue(int(config.get("group", 1)))
        mode = config.get("mode", "natural")
        mode_idx = next((i for i, (_, v) in enumerate(REGEX_SORT_MODES) if v == mode), 0)
        self.mode_combo.setCurrentIndex(mode_idx)
        reverse = config.get("reverse", False)
        self.dir_combo.setCurrentIndex(1 if reverse else 0)


class RegexSortPanel(QWidget):
    """
    Advanced sort panel: an ordered stack of regex sort layers.

    Layer 1 (top) is the primary sort key; each subsequent layer breaks ties
    within groups that are equal under all higher-priority layers.
    """

    _HELP = (
        "LAYER PRIORITY\n"
        "Layer 1 (top) = primary sort.  Layer 2 breaks ties within equal\n"
        "Layer-1 groups.  Layer 3 breaks ties within equal Layer-2 groups,\n"
        "and so on.  Use ▲ / ▼ to reorder layers.\n\n"
        "UNMATCHED FILES\n"
        "Files that don't match a layer's pattern are sorted after all\n"
        "matching files for that layer, but remain grouped correctly by any\n"
        "higher-priority layers they did match.\n\n"
        "EXAMPLE — texts organised by number then export date\n"
        "  Layer 1:  ^(\\d+)                  Numeric    Asc\n"
        "            → groups files by their leading number\n\n"
        "  Layer 2:  (\\d{4}-\\d{2}-\\d{2})     Natural    Desc\n"
        "            → newest export of each text first\n\n"
        "AI TIP\n"
        "Not sure how to write a regex?  Paste a few example filenames into\n"
        "Claude, ChatGPT, or another AI assistant and ask it to write a\n"
        "Python regex that captures the part you want to sort by."
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(
            "<b>Sort layers</b> — Layer 1 is primary; lower layers break ties"))
        help_btn = QPushButton("?")
        help_btn.setFixedWidth(28)
        help_btn.setToolTip(self._HELP)
        help_btn.clicked.connect(
            lambda: QMessageBox.information(self, "Advanced Sort Help", self._HELP)
        )
        hdr.addStretch()
        hdr.addWidget(help_btn)
        root.addLayout(hdr)

        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.layers_layout = QVBoxLayout(frame)
        self.layers_layout.setContentsMargins(4, 4, 4, 4)
        self.layers_layout.setSpacing(2)
        root.addWidget(frame)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Layer")
        add_btn.clicked.connect(self._add_layer)
        self.apply_btn = QPushButton("Apply Regex Sort")
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.apply_btn)
        root.addLayout(btn_row)

        self._add_layer()  # always start with one layer

    def _add_layer(self):
        layer = RegexLayerWidget()
        layer.remove_requested.connect(lambda l=layer: self._remove_layer(l))
        layer.move_up_requested.connect(lambda l=layer: self._move_layer(l, -1))
        layer.move_down_requested.connect(lambda l=layer: self._move_layer(l, 1))
        self.layers_layout.addWidget(layer)

    def _remove_layer(self, layer: RegexLayerWidget):
        if self.layers_layout.count() <= 1:
            return  # always keep at least one row
        self.layers_layout.removeWidget(layer)
        layer.deleteLater()

    def _move_layer(self, layer: RegexLayerWidget, delta: int):
        idx = self.layers_layout.indexOf(layer)
        new_idx = idx + delta
        if 0 <= new_idx < self.layers_layout.count():
            self.layers_layout.removeWidget(layer)
            self.layers_layout.insertWidget(new_idx, layer)

    def get_layers(self) -> list[dict]:
        layers = []
        for i in range(self.layers_layout.count()):
            item = self.layers_layout.itemAt(i)
            if item and isinstance(item.widget(), RegexLayerWidget):
                layers.append(item.widget().layer_config())
        return layers

    def set_layers(self, layer_configs: list[dict]):
        while self.layers_layout.count():
            item = self.layers_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        for cfg in layer_configs:
            layer = RegexLayerWidget()
            layer.remove_requested.connect(lambda l=layer: self._remove_layer(l))
            layer.move_up_requested.connect(lambda l=layer: self._move_layer(l, -1))
            layer.move_down_requested.connect(lambda l=layer: self._move_layer(l, 1))
            layer.set_config(cfg)
            self.layers_layout.addWidget(layer)
        if self.layers_layout.count() == 0:
            self._add_layer()


# ---------------------------------------------------------------------------
# Output mode panels
# ---------------------------------------------------------------------------

class CorpusOptionsPanel(QWidget):
    """Options for corpus mode — a file of separate texts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        note = QLabel(
            "Each text stays a separate text in FLEx, keeping its own title, "
            "writing systems and media list. <b>Audio segmentation and media "
            "information are preserved exactly.</b>"
        )
        note.setWordWrap(True)
        note.setStyleSheet(_OK_STYLE)
        layout.addWidget(note)

        self.strip_guids_cb = QCheckBox("Strip text GUIDs (import as new)")
        self.strip_guids_cb.setToolTip(
            "FLEx uses the GUID of each text to detect duplicates on import.\n"
            "Leave this off to keep GUIDs, so re-importing updates the existing\n"
            "texts. Turn it on to import everything as brand-new texts and avoid\n"
            "a 'Duplicate text found' prompt for every text in the file.\n\n"
            "Media GUIDs are always kept — phrases reference them by GUID."
        )
        layout.addWidget(self.strip_guids_cb)
        layout.addStretch()


class CombinedOptionsPanel(QWidget):
    """Options for combined mode — one FLEx text, one paragraph per source."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        warning = QLabel(
            "⚠ Combining into one text <b>permanently removes audio time "
            "offsets, media-file links, speaker names, and the media-files "
            "list</b>. Use Corpus mode to keep them. Your source files are "
            "never modified."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(_WARN_STYLE)
        self.audio_note = warning
        layout.addWidget(warning)

        # Two columns — text options left, audio options right — so combined
        # mode stays short enough to fit on laptop screens. A single column
        # crushed the shift options into overlapping rows on smaller displays.
        columns = QHBoxLayout()
        columns.setSpacing(18)
        text_col = QVBoxLayout()
        text_col.setSpacing(6)
        audio_col = QVBoxLayout()
        audio_col.setSpacing(6)
        columns.addLayout(text_col, 1)
        columns.addLayout(audio_col, 1)
        layout.addLayout(columns)

        # ── Audio segmentation (right column) ───────────────────────────────
        audio_row = QHBoxLayout()
        audio_row.addWidget(QLabel("Audio:"))
        self.audio_combo = QComboBox()
        for label, _ in AUDIO_CHOICES:
            self.audio_combo.addItem(label)
        self.audio_combo.setToolTip(
            "Discard: remove all time offsets, media links and speakers.\n"
            "  Merging separate recordings leaves them pointing at nothing.\n\n"
            "Shift: rebase every offset onto one concatenated recording, so\n"
            "  the combined text pairs with audio joined in the same order.\n"
            "  Each text is moved forward by the total length of the texts\n"
            "  before it, plus the gap below."
        )
        self.audio_combo.currentIndexChanged.connect(self._on_audio_mode_changed)
        audio_row.addWidget(self.audio_combo)
        audio_row.addStretch()
        audio_col.addLayout(audio_row)

        self.shift_box = QWidget()
        shift_layout = QVBoxLayout(self.shift_box)
        shift_layout.setContentsMargins(18, 0, 0, 0)
        shift_layout.setSpacing(4)

        gap_row = QHBoxLayout()
        gap_row.addWidget(QLabel("Gap:"))
        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(0, 600000)
        self.gap_spin.setValue(fx.DEFAULT_GAP_MS)
        self.gap_spin.setSuffix(" ms")
        self.gap_spin.setFixedWidth(110)
        self.gap_spin.setToolTip(
            f"{fx.DEFAULT_GAP_MS} ms inserts the click marker: 500 ms silence "
            f"+ 5 ms click\n+ 500 ms silence — the same separator the Audio "
            f"Concatenator uses.\nAny other value inserts plain silence of "
            f"that length. 0 is gapless.\n\n"
            f"The click is 220 samples at 44.1 kHz, so the marker really runs\n"
            f"1004.99 ms rather than a round 1005. Offsets are shifted by the\n"
            f"measured length, not the nominal one, so this never accumulates."
        )
        gap_row.addWidget(self.gap_spin)
        reset_btn = QPushButton("Click marker")
        reset_btn.setToolTip(
            f"Set the gap to {fx.DEFAULT_GAP_MS} ms — the click marker: "
            f"500 ms silence + 5 ms click + 500 ms silence.")
        reset_btn.clicked.connect(
            lambda: self.gap_spin.setValue(fx.DEFAULT_GAP_MS))
        gap_row.addWidget(reset_btn)
        gap_row.addStretch()
        shift_layout.addLayout(gap_row)

        self.gap_note = QLabel()
        self.gap_note.setWordWrap(True)
        self.gap_note.setStyleSheet("color: palette(mid); font-size: 11px;")
        shift_layout.addWidget(self.gap_note)

        self.join_audio_cb = QCheckBox(
            "Join recordings into one audio file")
        self.join_audio_cb.setChecked(True)
        self.join_audio_cb.setToolTip(
            "Writes the combined audio alongside the combined text, in the same\n"
            "order, so the two cannot disagree. Each recording's true length is\n"
            "measured while joining, which makes the shifted offsets exact\n"
            "instead of estimated.\n\n"
            "Turn this off to combine the text only — offsets are then estimated\n"
            "from each text's last annotation."
        )
        self.join_audio_cb.toggled.connect(self._on_join_audio_toggled)
        shift_layout.addWidget(self.join_audio_cb)

        self.distribute_cb = QCheckBox(
            "Spread lines evenly where audio has no segmentation")
        self.distribute_cb.setChecked(True)
        self.distribute_cb.setToolTip(
            "ELAN cannot show an annotation without a time slot, so a text with\n"
            "a recording but no segmentation would be unusable there.\n"
            "Its lines are given even slices of its recording — approximate\n"
            "timing that does not follow the speech, reported in the summary.\n\n"
            "Either way the timeline still advances by that recording's full\n"
            "length, so the texts after it stay aligned."
        )
        shift_layout.addWidget(self.distribute_cb)

        media_row = QHBoxLayout()
        self.media_label = QLabel("Audio out:")
        media_row.addWidget(self.media_label)
        self.media_edit = QLineEdit()
        self.media_edit.setPlaceholderText(
            "Where to write the joined recording…")
        self.media_edit.setToolTip(
            "The single audio file the shifted offsets point into.\n"
            "Written as one <media> entry that every phrase references, which\n"
            "is what lets ELAN open the text against the audio.\n\n"
            "When joining, this is where the audio is written. When not "
            "joining,\nit is just the path recorded in the text — leave it "
            "empty to shift\noffsets without linking any media."
        )
        media_browse = QPushButton("Browse…")
        media_browse.clicked.connect(self._browse_media)
        media_row.addWidget(self.media_edit, 1)
        media_row.addWidget(media_browse)
        shift_layout.addLayout(media_row)

        audio_col.addWidget(self.shift_box)
        audio_col.addStretch()

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("Title:"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Title for the combined text (required)…")
        title_row.addWidget(self.title_edit, 1)
        title_row.addWidget(QLabel("Language:"))
        self.lang_combo = QComboBox()
        self.lang_combo.setEditable(True)
        self.lang_combo.setFixedWidth(90)
        self.lang_combo.setToolTip(
            "Writing system for the title and for any source-title notes.\n"
            "Populated from the languages found in the loaded files."
        )
        title_row.addWidget(self.lang_combo)
        text_col.addLayout(title_row)

        self.title_notes_cb = QCheckBox(
            "Source title as a note on the first line")
        self.title_notes_cb.setChecked(True)
        self.title_notes_cb.setToolTip(
            "Records where each original text begins. FLEx shows this on the\n"
            "Note line. A paragraph cannot carry a title of its own, so the\n"
            "note is attached to the paragraph's first phrase."
        )
        text_col.addWidget(self.title_notes_cb)

        segnum_row = QHBoxLayout()
        segnum_row.addWidget(QLabel("Line numbers:"))
        self.segnum_combo = QComboBox()
        for label, _ in SEGNUM_CHOICES:
            self.segnum_combo.addItem(label)
        self.segnum_combo.setToolTip(
            "FLEx ignores segnum when importing — its importer has an explicit\n"
            "'case \"segnum\": break;' because there is no field to store it in.\n"
            "It is written on export from the reference FLEx computes on screen.\n\n"
            "Remove (recommended): drop them and let FLEx number the combined\n"
            "  text itself. No stale numbering is left for other tools to read.\n"
            "Renumber: continuous 1…N across the whole text, for tools other\n"
            "  than FLEx that do read segnum. Lines that have none are counted\n"
            "  but not given one, so the sequence can have gaps.\n"
            "Keep: leave each source text's original numbering, restarting at 1\n"
            "  for every paragraph."
        )
        segnum_row.addWidget(self.segnum_combo)
        segnum_row.addStretch()
        text_col.addLayout(segnum_row)

        self.strip_notes_cb = QCheckBox(
            "Strip audio timestamp notes")
        self.strip_notes_cb.setChecked(True)
        self.strip_notes_cb.setToolTip(
            "Removes note items whose entire content is an audio timestamp.\n"
            "Notes that mix real commentary with a timestamp are left alone."
        )
        text_col.addWidget(self.strip_notes_cb)
        text_col.addStretch()

        self._on_audio_mode_changed()
        self._on_join_audio_toggled(self.join_audio_cb.isChecked())

    # ── Audio segmentation ───────────────────────────────────────────────────

    @property
    def audio_mode(self) -> str:
        return AUDIO_CHOICES[self.audio_combo.currentIndex()][1]

    def set_audio_mode(self, mode: str) -> bool:
        idx = next((i for i, (_, v) in enumerate(AUDIO_CHOICES) if v == mode), None)
        if idx is None:
            return False
        self.audio_combo.setCurrentIndex(idx)
        return True

    def _on_audio_mode_changed(self, *_):
        """Only the discarding path destroys data, so only it gets a warning."""
        shifting = self.audio_mode == fx.AUDIO_SHIFT
        self.shift_box.setVisible(shifting)
        if shifting:
            self.audio_note.setText(
                "Time offsets are rebased onto one concatenated recording. "
                "Join the source audio <b>in this same order</b> for the result "
                "to line up. Each text's length is estimated from its last "
                "annotation, so texts annotated in utterances with gaps may "
                "drift — you'll be told which ones."
            )
            self.audio_note.setStyleSheet(_OK_STYLE)
        else:
            self.audio_note.setText(
                "⚠ Combining into one text <b>permanently removes audio time "
                "offsets, media-file links, speaker names, and the media-files "
                "list</b>. Use Corpus mode, or Shift above, to keep them. Your "
                "source files are never modified."
            )
            self.audio_note.setStyleSheet(_WARN_STYLE)

    def _on_join_audio_toggled(self, joining: bool):
        self.distribute_cb.setEnabled(joining)
        # The gap means two different things. When this app writes the audio it
        # IS the separator, so it is exact and needs no matching. When someone
        # else joins the audio it is a claim about their tool, and a wrong
        # value silently desyncs everything.
        if joining:
            self.gap_note.setText(
                "This app inserts this gap itself, so it is exact — offsets are "
                "shifted by the length actually written."
            )
        else:
            self.gap_note.setText(
                "⚠ Must match the gap your joining tool inserts. Audio "
                "Concatenator uses 1005 ms; plain concatenation is 0."
            )
        self.media_label.setText(
            "Audio out:" if joining else "Media path:")
        self.media_edit.setPlaceholderText(
            "Where to write the joined recording…" if joining
            else "Path or URL of the joined recording (optional)…")

    def _browse_media(self):
        if self.join_audio_cb.isChecked():
            path, _ = QFileDialog.getSaveFileName(
                self, "Write the joined audio to", "", "WAV files (*.wav)")
            if path and not path.lower().endswith(".wav"):
                path += ".wav"
            if path:
                self.media_edit.setText(path)
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select the combined audio file", "",
            "Audio files (*.wav *.mp3 *.m4a *.flac *.ogg *.aif *.aiff);;All files (*)",
        )
        if path:
            self.media_edit.setText(Path(path).as_uri())

    @property
    def segnum_mode(self) -> str:
        return SEGNUM_CHOICES[self.segnum_combo.currentIndex()][1]

    def set_segnum_mode(self, mode: str) -> bool:
        idx = next((i for i, (_, v) in enumerate(SEGNUM_CHOICES) if v == mode), None)
        if idx is None:
            return False
        self.segnum_combo.setCurrentIndex(idx)
        return True

    def set_languages(self, codes: list[str], default: str):
        """Repopulate the language combo, keeping any hand-typed value."""
        current = self.lang_combo.currentText().strip()
        self.lang_combo.clear()
        self.lang_combo.addItems(codes)
        if current and current in codes:
            self.lang_combo.setCurrentText(current)
        elif default:
            self.lang_combo.setCurrentText(default)


# ---------------------------------------------------------------------------
# Settings serialisation helpers
# ---------------------------------------------------------------------------

_VALID_SORT_MODES   = {"simple", "advanced"}
_VALID_SORT_FIELDS  = {v for _, v in SORT_FIELDS}
_VALID_DIRECTIONS   = {"ascending", "descending"}
_VALID_SORT_AS      = {"natural", "numeric", "alpha"}
_VALID_OUTPUT_MODES = {v for _, v in OUTPUT_MODES}
_VALID_SEGNUM_MODES = {v for _, v in SEGNUM_CHOICES}
_VALID_AUDIO_MODES  = {v for _, v in AUDIO_CHOICES}

_BOOL_SETTINGS = [
    # (yaml key, attribute path on MainWindow, comment)
    ("recursive",         "recursive_cb",                   "include subfolders when loading a folder"),
    ("strip_guids",       "corpus_panel.strip_guids_cb",    "corpus mode: import as new texts"),
    ("add_title_notes",   "combined_panel.title_notes_cb",  "combined mode: source title as a note"),
    ("strip_audio_notes", "combined_panel.strip_notes_cb",  "combined mode: drop audio timestamp notes"),
    ("join_audio",        "combined_panel.join_audio_cb",   "shift mode: also write the joined recording"),
    ("distribute_untimed", "combined_panel.distribute_cb",  "shift mode: even slices for unsegmented texts"),
]


def _ys(value) -> str:
    """
    Serialize a scalar to a YAML-safe inline string.

    Uses pyyaml to handle quoting automatically, then takes only the first
    output line — pyyaml sometimes appends a '...' document-end marker on a
    second line that would corrupt inline comments.
    """
    if not _HAS_YAML:
        return str(value)
    raw = _yaml.dump(value, default_flow_style=True, allow_unicode=True)
    return raw.splitlines()[0] if raw.strip() else ""


def _build_settings_yaml(settings: dict) -> str:
    """
    Build a human-readable YAML string with per-field inline comments
    explaining every valid option.  Values with a closed set of allowed
    options include the full option list in their comment.
    """
    lines = [
        "# FLExText Concatenator — settings",
        "# Edit freely with any text editor.",
        "",
        f"output_mode: {settings['output_mode']}  # corpus | combined",
        f"sort_mode: {settings['sort_mode']}  # simple | advanced",
        f"sort_field: {settings['sort_field']}  # name | num | title | ctime | mtime | atime",
        f"sort_direction: {settings['sort_direction']}  # ascending | descending",
        "",
        "# Combined-text mode",
        f"combined_title: {_ys(settings['combined_title'])}"
        "  # title of the single combined text",
        f"combined_title_lang: {_ys(settings['combined_title_lang'])}"
        "  # writing system code for that title",
        f"segnum_mode: {settings['segnum_mode']}  # strip | renumber | keep —",
        "                    # FLEx ignores segnum on import, so 'strip' lets it",
        "                    # number the text itself; the others suit tools that",
        "                    # do read segnum.",
        f"audio_mode: {settings['audio_mode']}  # discard | shift —",
        "                    # 'shift' rebases time offsets onto one concatenated",
        "                    # recording instead of throwing them away.",
        f"gap_ms: {settings['gap_ms']}"
        f"  # silence between texts in the shifted timeline;",
        f"                    # {fx.DEFAULT_GAP_MS} matches the Audio Concatenator's click separator",
        f"combined_media: {_ys(settings['combined_media'])}"
        "  # path/URL of the joined recording",
        "",
    ]

    for key, _, comment in _BOOL_SETTINGS:
        lines.append(f"{key}: {str(settings[key]).lower()}  # true | false — {comment}")

    lines += [
        f"warn_audio_loss: {str(settings['warn_audio_loss']).lower()}"
        "  # true | false — confirm before combined mode discards audio data",
        "",
        "regex_layers:  # Advanced sort only — list of sort layers.",
        "               # Layer 1 (top) is the primary sort key; lower layers break ties.",
        "               # Delete all entries (or write []) when using Simple (GUI) sort.",
    ]

    layers = settings.get("regex_layers") or []
    if layers:
        for layer in layers:
            lines += [
                f"  - pattern: {_ys(layer['pattern'])}"
                "  # Python regex — must contain at least one capture group  ( )",
                f"    group: {layer['group']}"
                "  # which capture group to sort by: 1, 2, 3, …",
                f"    sort_as: {layer['sort_as']}"
                "  # natural | numeric | alpha",
                f"    direction: {layer['direction']}"
                "  # ascending | descending",
            ]
    else:
        lines.append("  []")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FLExText Concatenator")
        # Small minimum + generous default: the content sits in a scroll area,
        # so a cramped window (or a high-DPI laptop at 125 % scaling) grows
        # scrollbars instead of crushing widgets into each other.
        self.setMinimumSize(640, 420)
        self.resize(1040, 760)
        self._worker: Optional[CombineWorker] = None
        self._thread: Optional[QThread] = None
        # Parsed metadata per path, for list labels, title sorting and the
        # audio-loss warning.  Populated whenever files are added.
        self._info: dict[str, fx.FlextextFile] = {}
        self._warn_audio_loss = True
        # Empty means "look beside each text", which is how these corpora are
        # normally laid out; set only when the audio lives somewhere else.
        self._audio_folder = ""
        # Recordings the user added by hand via "Add Audio Files…" — kept
        # apart from the folder scan so a rescan cannot silently drop them.
        self._extra_audio: set[str] = set()
        self._build_ui()

    # ── Construction ─────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        scroll = QScrollArea()
        scroll.setWidget(central)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        # The output path and Combine button live OUTSIDE the scroll area, so
        # the primary action can never scroll out of reach on a small window.
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll, 1)
        footer = QWidget()
        self._footer = QVBoxLayout(footer)
        self._footer.setContentsMargins(10, 6, 10, 8)
        self._footer.setSpacing(6)
        outer.addWidget(footer)
        self.setCentralWidget(container)

        # ── Load row ────────────────────────────────────────────────────────
        load_row = QHBoxLayout()
        self.load_btn = QPushButton("Load Folder…")
        self.load_btn.clicked.connect(self._on_load_folder)
        self.add_btn = QPushButton("Add Files…")
        self.add_btn.clicked.connect(self._on_add_files)
        self.recursive_cb = QCheckBox("Include subfolders")
        self.recursive_cb.setChecked(True)
        self.recursive_cb.setToolTip(
            "Search the selected folder and everything inside it.")
        self.folder_label = QLabel("No files loaded")
        self.folder_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        load_row.addWidget(self.load_btn)
        load_row.addWidget(self.add_btn)
        load_row.addWidget(self.recursive_cb)
        load_row.addWidget(self.folder_label, 1)
        root.addLayout(load_row)

        # ── Audio folder row ─────────────────────────────────────────────────
        audio_row = QHBoxLayout()
        self.audio_folder_btn = QPushButton("Audio Folder…")
        self.audio_folder_btn.setToolTip(
            "Where to look for recordings. By default this is the same folder "
            "as the texts —\nset it only if your audio lives somewhere else.")
        self.audio_folder_btn.clicked.connect(self._on_choose_audio_folder)
        self.audio_folder_label = QLabel("Audio: same folder as the texts")
        self.audio_folder_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.rematch_btn = QPushButton("Match Audio")
        self.rematch_btn.setToolTip(
            "Suggest a recording for every text, by filename and then by "
            "folder.\nSuggestions made on folder alone are marked '?' — check "
            "those.")
        self.rematch_btn.clicked.connect(self._on_rematch)
        self.clear_audio_btn = QPushButton("Clear Audio")
        self.clear_audio_btn.setToolTip(
            "Unassign the audio from the selected rows.")
        self.clear_audio_btn.clicked.connect(self._on_clear_audio)
        audio_row.addWidget(self.audio_folder_btn)
        audio_row.addWidget(self.audio_folder_label, 1)
        audio_row.addWidget(self.rematch_btn)
        audio_row.addWidget(self.clear_audio_btn)
        root.addLayout(audio_row)

        # ── Pairing table + unmatched-audio pane ─────────────────────────────
        self.table = PairingTable()
        self.table.texts_dropped.connect(self._on_files_dropped)
        self.table.pairing_changed.connect(self._refresh_counts)
        self.table.setToolTip(
            "Drag files here from your file manager.\n"
            "Drag in the left column to reorder texts (the audio follows).\n"
            "Drag in the right column to swap a recording with another text's.\n"
            "Drag from the Unmatched audio pane to assign a recording.\n"
            "Double-click a recording to pick a different file."
        )

        self.unmatched_panel = QWidget()
        unmatched_layout = QVBoxLayout(self.unmatched_panel)
        unmatched_layout.setContentsMargins(0, 0, 0, 0)
        unmatched_layout.setSpacing(4)
        self.unmatched_label = QLabel("<b>Unmatched audio</b>")
        unmatched_layout.addWidget(self.unmatched_label)
        self.unmatched_list = UnmatchedAudioList()
        unmatched_layout.addWidget(self.unmatched_list, 1)
        self.add_audio_btn = QPushButton("Add Audio Files…")
        self.add_audio_btn.setToolTip(
            "Add recordings from anywhere on disk to this pane,\n"
            "then drag them onto texts to pair them.")
        self.add_audio_btn.clicked.connect(self._on_add_audio_files)
        unmatched_layout.addWidget(self.add_audio_btn)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self.table)
        self._splitter.addWidget(self.unmatched_panel)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setChildrenCollapsible(False)
        # Keep the table usable even when the scroll area is doing the work.
        self.table.setMinimumHeight(120)
        self.unmatched_list.setMinimumWidth(140)
        self._splitter.setSizes([700, 240])
        root.addWidget(self._splitter, 1)

        list_btns = QHBoxLayout()
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._on_remove_selected)
        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self._on_clear)
        self.count_label = QLabel("")
        list_btns.addWidget(remove_btn)
        list_btns.addWidget(clear_btn)
        list_btns.addStretch()
        list_btns.addWidget(self.count_label)
        root.addLayout(list_btns)

        # ── Options tabs ─────────────────────────────────────────────────────
        # Sorting and output options are separate decisions and were stacked
        # vertically, so both competed for height with the file table. As tabs
        # only one is on screen at a time, which is what actually fixes the
        # crowding — scrolling alone just made a tall wall scrollable.
        self._options_tabs = QTabWidget()

        # -- Sort tab --
        sort_tab = QWidget()
        sort_layout = QVBoxLayout(sort_tab)
        sort_layout.setSpacing(6)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Sort mode:"))
        self._mode_group = QButtonGroup(self)
        self._radio_simple = QRadioButton("Simple (GUI)")
        self._radio_adv    = QRadioButton("Advanced (Regex)")
        self._radio_simple.setChecked(True)
        self._mode_group.addButton(self._radio_simple, 0)
        self._mode_group.addButton(self._radio_adv,    1)
        self._mode_group.idToggled.connect(self._on_mode_toggle)
        mode_row.addWidget(self._radio_simple)
        mode_row.addWidget(self._radio_adv)
        mode_row.addStretch()
        sort_layout.addLayout(mode_row)

        self._sort_stack = QStackedWidget()
        self.gui_panel = GuiSortPanel()
        self.gui_panel.apply_sort_btn.clicked.connect(self._on_apply_sort)
        self._sort_stack.addWidget(self.gui_panel)    # index 0
        self.regex_panel = RegexSortPanel()
        self.regex_panel.apply_btn.clicked.connect(self._on_apply_regex_sort)
        self._sort_stack.addWidget(self.regex_panel)  # index 1
        sort_layout.addWidget(self._sort_stack)
        sort_layout.addStretch()
        self._options_tabs.addTab(sort_tab, "Sort")

        # -- Combine as tab --
        combine_tab = QWidget()
        out_mode_layout = QVBoxLayout(combine_tab)
        out_mode_layout.setSpacing(6)

        out_row = QHBoxLayout()
        self._out_group = QButtonGroup(self)
        self._radio_corpus   = QRadioButton("Corpus — separate texts")
        self._radio_combined = QRadioButton("Combined Text — one text")
        self._radio_corpus.setChecked(True)
        self._out_group.addButton(self._radio_corpus,   0)
        self._out_group.addButton(self._radio_combined, 1)
        self._out_group.idToggled.connect(self._on_output_mode_toggle)
        out_row.addWidget(self._radio_corpus)
        out_row.addWidget(self._radio_combined)
        out_row.addStretch()
        load_settings_btn = QPushButton("Load Settings…")
        load_settings_btn.setToolTip("Load settings from a YAML file")
        load_settings_btn.clicked.connect(self._load_settings)
        save_settings_btn = QPushButton("Save Settings…")
        save_settings_btn.setToolTip("Save current settings to a YAML file")
        save_settings_btn.clicked.connect(self._save_settings)
        out_row.addWidget(load_settings_btn)
        out_row.addWidget(save_settings_btn)
        out_mode_layout.addLayout(out_row)

        self._out_stack = QStackedWidget()
        self.corpus_panel = CorpusOptionsPanel()
        self.combined_panel = CombinedOptionsPanel()
        self._out_stack.addWidget(self.corpus_panel)    # index 0
        self._out_stack.addWidget(self.combined_panel)  # index 1
        out_mode_layout.addWidget(self._out_stack)
        out_mode_layout.addStretch()
        self._options_tabs.addTab(combine_tab, "Combine as")
        self._options_tabs.setCurrentIndex(1)   # the decision users make first

        root.addWidget(self._options_tabs)

        # ── Output row ───────────────────────────────────────────────────────
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output:"))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Choose output file…")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_output)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse_btn)
        self._footer.addLayout(output_row)

        # ── Progress + Combine row ───────────────────────────────────────────
        run_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.combine_btn = QPushButton("Combine Files")
        self.combine_btn.setDefault(True)
        self.combine_btn.clicked.connect(self._on_combine)
        run_row.addWidget(self.progress, 1)
        run_row.addWidget(self.cancel_btn)
        run_row.addStretch()
        run_row.addWidget(self.combine_btn)
        self._footer.addLayout(run_row)

        # Establish the corpus/combined enabled-state; setChecked() during
        # construction fires no toggle, so the initial pass is explicit.
        self._on_output_mode_toggle(self._out_group.checkedId(), True)

        self.statusBar().showMessage("Ready")

        # Ctrl+Shift+D (⌘+Shift+D on macOS) → environment diagnostic
        shortcut = QShortcut(QKeySequence("Ctrl+Shift+D"), self)
        shortcut.activated.connect(self._show_diagnostics)

    # ── Settings save / load ─────────────────────────────────────────────────

    def _widget_for(self, path: str):
        """Resolve a dotted attribute path like 'combined_panel.title_notes_cb'."""
        obj = self
        for part in path.split("."):
            obj = getattr(obj, part)
        return obj

    def _collect_settings(self) -> dict:
        """Collect current settings into a plain dict."""
        raw_layers = self.regex_panel.get_layers()
        layers_out = [
            {
                "pattern":   l["pattern"],
                "group":     l["group"],
                "sort_as":   l["mode"],
                "direction": "descending" if l["reverse"] else "ascending",
            }
            for l in raw_layers
        ]

        settings = {
            "output_mode":         self._output_mode(),
            "sort_mode":           "simple" if self._radio_simple.isChecked() else "advanced",
            "sort_field":          self.gui_panel.sort_field,
            "sort_direction":      "descending" if self.gui_panel.sort_reverse else "ascending",
            "combined_title":      self.combined_panel.title_edit.text().strip(),
            "combined_title_lang": self.combined_panel.lang_combo.currentText().strip(),
            "segnum_mode":         self.combined_panel.segnum_mode,
            "audio_mode":          self.combined_panel.audio_mode,
            "gap_ms":              self.combined_panel.gap_spin.value(),
            "combined_media":      self.combined_panel.media_edit.text().strip(),
            "warn_audio_loss":     self._warn_audio_loss,
            "regex_layers":        layers_out,
        }
        for key, widget_path, _ in _BOOL_SETTINGS:
            settings[key] = self._widget_for(widget_path).isChecked()
        return settings

    def _apply_settings(self, data: dict):
        """
        Apply a settings dict to the UI.

        Each field is applied independently.  Invalid values are skipped and
        collected into a warning shown at the end, so a single bad entry never
        prevents the rest of the file from loading.
        """
        warnings: list[str] = []

        # ── output_mode ──────────────────────────────────────────────────────
        out_mode = data.get("output_mode")
        if out_mode is not None:
            if out_mode in _VALID_OUTPUT_MODES:
                if out_mode == "combined":
                    self._radio_combined.setChecked(True)
                else:
                    self._radio_corpus.setChecked(True)
            else:
                warnings.append(
                    f"output_mode: '{out_mode}' is not valid — "
                    "must be 'corpus' or 'combined'. Skipped."
                )

        # ── sort_mode ────────────────────────────────────────────────────────
        mode = data.get("sort_mode")
        if mode is not None:
            if mode in _VALID_SORT_MODES:
                if mode == "advanced":
                    self._radio_adv.setChecked(True)
                else:
                    self._radio_simple.setChecked(True)
            else:
                warnings.append(
                    f"sort_mode: '{mode}' is not valid — "
                    "must be 'simple' or 'advanced'. Skipped."
                )

        # ── sort_field ───────────────────────────────────────────────────────
        field = data.get("sort_field")
        if field is not None:
            if field in _VALID_SORT_FIELDS:
                idx = next((i for i, (_, v) in enumerate(SORT_FIELDS) if v == field), None)
                if idx is not None:
                    self.gui_panel.field_combo.setCurrentIndex(idx)
            else:
                warnings.append(
                    f"sort_field: '{field}' is not valid — "
                    f"must be one of: {', '.join(v for _, v in SORT_FIELDS)}. Skipped."
                )

        # ── sort_direction ───────────────────────────────────────────────────
        direction = data.get("sort_direction")
        if direction is not None:
            if direction in _VALID_DIRECTIONS:
                self.gui_panel.dir_combo.setCurrentIndex(
                    1 if direction == "descending" else 0)
            else:
                warnings.append(
                    f"sort_direction: '{direction}' is not valid — "
                    "must be 'ascending' or 'descending'. Skipped."
                )

        # ── combined-text text fields ────────────────────────────────────────
        title = data.get("combined_title")
        if title is not None:
            self.combined_panel.title_edit.setText(str(title))
        title_lang = data.get("combined_title_lang")
        if title_lang is not None:
            self.combined_panel.lang_combo.setCurrentText(str(title_lang))
        media = data.get("combined_media")
        if media is not None:
            self.combined_panel.media_edit.setText(str(media))

        segnum_mode = data.get("segnum_mode")
        if segnum_mode is not None:
            if not self.combined_panel.set_segnum_mode(str(segnum_mode)):
                warnings.append(
                    f"segnum_mode: '{segnum_mode}' is not valid — must be one "
                    f"of: {', '.join(sorted(_VALID_SEGNUM_MODES))}. Skipped."
                )

        audio_mode = data.get("audio_mode")
        if audio_mode is not None:
            if not self.combined_panel.set_audio_mode(str(audio_mode)):
                warnings.append(
                    f"audio_mode: '{audio_mode}' is not valid — must be one "
                    f"of: {', '.join(sorted(_VALID_AUDIO_MODES))}. Skipped."
                )

        gap = data.get("gap_ms")
        if gap is not None:
            try:
                self.combined_panel.gap_spin.setValue(int(gap))
            except (ValueError, TypeError):
                warnings.append(
                    f"gap_ms: '{gap}' is not a whole number of milliseconds. "
                    f"Using {self.combined_panel.gap_spin.value()}."
                )

        # ── booleans ─────────────────────────────────────────────────────────
        for key, widget_path, _ in _BOOL_SETTINGS:
            value = data.get(key)
            if value is None:
                continue
            if isinstance(value, bool):
                self._widget_for(widget_path).setChecked(value)
            else:
                warnings.append(
                    f"{key}: expected true or false, got '{value}'. Skipped.")

        warn = data.get("warn_audio_loss")
        if warn is not None:
            if isinstance(warn, bool):
                self._warn_audio_loss = warn
            else:
                warnings.append(
                    f"warn_audio_loss: expected true or false, got '{warn}'. Skipped.")

        # ── regex_layers ─────────────────────────────────────────────────────
        layers = data.get("regex_layers")
        if layers is not None:
            if not isinstance(layers, list):
                warnings.append(
                    f"regex_layers: expected a list, got {type(layers).__name__}. Skipped."
                )
            else:
                converted: list[dict] = []
                for i, raw in enumerate(layers):
                    if not isinstance(raw, dict):
                        warnings.append(f"regex_layers[{i}]: expected a dict. Skipped.")
                        continue
                    cfg: dict = {}

                    # pattern — accept any string; warn if it won't compile
                    pat = raw.get("pattern", "")
                    pat = str(pat) if pat is not None else ""
                    try:
                        re.compile(pat)
                    except re.error as exc:
                        warnings.append(
                            f"regex_layers[{i}].pattern: '{pat}' is not a valid regex "
                            f"({exc}). Loaded anyway — fix before applying sort."
                        )
                    cfg["pattern"] = pat

                    # group — integer 1–9
                    raw_group = raw.get("group", 1)
                    try:
                        g = int(raw_group)
                        if not (1 <= g <= 9):
                            raise ValueError("out of range 1–9")
                        cfg["group"] = g
                    except (ValueError, TypeError):
                        warnings.append(
                            f"regex_layers[{i}].group: '{raw_group}' is not valid — "
                            "must be an integer 1–9. Using 1."
                        )
                        cfg["group"] = 1

                    # sort_as
                    sort_as = str(raw.get("sort_as", "natural"))
                    if sort_as in _VALID_SORT_AS:
                        cfg["mode"] = sort_as
                    else:
                        warnings.append(
                            f"regex_layers[{i}].sort_as: '{sort_as}' is not valid — "
                            "must be 'natural', 'numeric', or 'alpha'. Using 'natural'."
                        )
                        cfg["mode"] = "natural"

                    # direction
                    dir_val = str(raw.get("direction", "ascending"))
                    if dir_val in _VALID_DIRECTIONS:
                        cfg["reverse"] = dir_val == "descending"
                    else:
                        warnings.append(
                            f"regex_layers[{i}].direction: '{dir_val}' is not valid — "
                            "must be 'ascending' or 'descending'. Using 'ascending'."
                        )
                        cfg["reverse"] = False

                    converted.append(cfg)

                if converted:
                    self.regex_panel.set_layers(converted)

        if warnings:
            QMessageBox.warning(
                self, "Settings loaded with warnings",
                "Some values were skipped or substituted:\n\n"
                + "\n".join(f"  • {w}" for w in warnings),
            )

    def _save_settings(self):
        if not _HAS_YAML:
            QMessageBox.warning(
                self, "PyYAML not installed",
                "Install PyYAML to save/load settings:\n\n  pip install pyyaml",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save settings", "flextext_concat_settings.yaml",
            "YAML files (*.yaml *.yml);;All files (*)",
        )
        if not path:
            return
        if not path.lower().endswith((".yaml", ".yml")):
            path += ".yaml"

        try:
            Path(path).write_text(
                _build_settings_yaml(self._collect_settings()), encoding="utf-8")
            self.statusBar().showMessage(f"Settings saved: {path}")
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    def _load_settings(self):
        if not _HAS_YAML:
            QMessageBox.warning(
                self, "PyYAML not installed",
                "Install PyYAML to save/load settings:\n\n  pip install pyyaml",
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Load settings", "",
            "YAML files (*.yaml *.yml);;All files (*)",
        )
        if not path:
            return
        try:
            data = _yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, _yaml.YAMLError) as exc:
            QMessageBox.critical(self, "Load failed", str(exc))
            return
        if not isinstance(data, dict):
            QMessageBox.warning(
                self, "Invalid file",
                "The file does not contain a valid settings dict.")
            return
        self._apply_settings(data)
        self.statusBar().showMessage(f"Settings loaded: {path}")

    # ── File list plumbing ───────────────────────────────────────────────────

    def _current_paths(self) -> list[str]:
        return self.table.text_paths()

    def _titles(self) -> dict[str, str]:
        return {p: info.title for p, info in self._info.items() if info.title}

    def _labels(self) -> dict[str, str]:
        return {p: info.label for p, info in self._info.items()}

    def _set_paths(self, paths: list[str]):
        """Reorder or replace the rows, keeping each text's audio with it."""
        existing = dict(self.table.pairs())
        confidence = {
            self.table.text_path(r):
                self.table.item(r, PairingTable.AUDIO_COL)
                    .data(Qt.ItemDataRole.UserRole + 1)
            for r in range(self.table.rowCount())
        }
        self.table.set_rows(
            [(p, existing.get(p), confidence.get(p)) for p in paths],
            self._labels(),
        )

    def _refresh_counts(self):
        rows = self.table.pairs()
        infos = [self._info[p] for p, _ in rows if p in self._info]
        phrases = sum(i.n_phrases for i in infos)
        texts = sum(len(i.texts) for i in infos)
        with_audio = sum(1 for _, a in rows if a)
        self.count_label.setText(
            f"{len(rows)} file(s) · {texts} text(s) · {phrases} phrase(s) · "
            f"{with_audio} with audio" if rows else ""
        )
        self.combined_panel.set_languages(
            fx.collect_languages(infos), fx.default_title_lang(infos)
        )
        self._refresh_unmatched()

    def _refresh_unmatched(self):
        """The pane shows every known recording no text is currently using."""
        assigned = {a for _, a in self.table.pairs() if a}
        available = sorted(set(self._audio_pool()) - assigned,
                           key=lambda p: Path(p).name.lower())
        self.unmatched_list.set_paths(available)
        self.unmatched_label.setText(
            f"<b>Unmatched audio</b> ({len(available)})" if available
            else "<b>Unmatched audio</b>")

    def _audio_pool(self) -> list[str]:
        """Every candidate recording, from the audio folder or beside the texts."""
        roots: list[Path] = []
        if self._audio_folder:
            roots.append(Path(self._audio_folder))
        else:
            # Many texts usually share a folder, so walk each one once.
            seen_roots: set[str] = set()
            for path in self._current_paths():
                parent = Path(path).parent
                if str(parent) not in seen_roots:
                    seen_roots.add(str(parent))
                    roots.append(parent)

        recursive = self.recursive_cb.isChecked()
        found: list[str] = []
        seen: set[str] = set()
        for root in roots:
            if not root.is_dir():
                continue
            walker = root.rglob("*") if recursive else root.iterdir()
            for path in walker:
                key = str(path)
                if (key not in seen and path.is_file()
                        and path.suffix.lower() in audio.AUDIO_EXTENSIONS):
                    seen.add(key)
                    found.append(key)
        found += [p for p in self._extra_audio if p not in seen]
        return sorted(found)

    def _on_add_audio_files(self):
        """Bring recordings from anywhere on disk into the unmatched pane."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add recordings to the unmatched pane", "",
            "Audio files (*" + " *".join(sorted(audio.AUDIO_EXTENSIONS))
            + ");;All files (*)",
        )
        if not paths:
            return
        self._extra_audio.update(paths)
        self._refresh_unmatched()
        self.statusBar().showMessage(
            f"Added {len(paths)} recording(s) to the unmatched pane — "
            "drag them onto texts to pair them.")

    def _automatch(self, paths: list[str]):
        """Suggest audio for the given texts, leaving manual choices alone."""
        pool = self._audio_pool()
        if not pool:
            return 0
        suggestions = matching.match_audio(paths, pool, with_confidence=True)
        matched = 0
        for row in range(self.table.rowCount()):
            text_path = self.table.text_path(row)
            if text_path not in suggestions or self.table.audio_path(row):
                continue
            found, confidence = suggestions[text_path]
            if found:
                self.table.set_audio(row, found, confidence)
                matched += 1
        return matched

    def _add_paths(self, new_paths: list[str], *, replace: bool = False):
        """
        Parse and add files, keeping the existing order and skipping duplicates.

        Files that cannot be parsed are reported and left out rather than
        silently added, so the list only ever contains usable texts.
        """
        existing = self.table.pairs() if not replace else []
        if replace:
            self._info.clear()

        seen = {p for p, _ in existing}
        to_parse = [p for p in new_paths if p not in seen]
        parsed, failures = fx.parse_files(to_parse)
        for info in parsed:
            self._info[str(info.path)] = info

        added = [str(i.path) for i in parsed]
        self.table.set_rows(
            [(p, a, None) for p, a in existing] + [(p, None, None) for p in added],
            self._labels(),
        )
        self._automatch(added)
        self._refresh_counts()

        if failures:
            shown = "\n".join(f"  • {p.name}: {m}" for p, m in failures[:10])
            more = (f"\n\n…and {len(failures) - 10} more."
                    if len(failures) > 10 else "")
            QMessageBox.warning(
                self, "Some files could not be read",
                f"{len(failures)} file(s) were not added:\n\n{shown}{more}",
            )
        return added, failures

    # ── Slots ────────────────────────────────────────────────────────────────

    def _show_diagnostics(self):
        """Ctrl+Shift+D — report the runtime environment."""
        import platform
        import xml.etree.ElementTree as ET
        from PySide6 import __version__ as pyside_version

        lines = [
            f"Frozen (PyInstaller):  {'yes' if getattr(sys, 'frozen', False) else 'no'}",
            f"Python:   {platform.python_version()} ({sys.executable})",
            f"Platform: {platform.platform()}",
            f"PySide6:  {pyside_version}",
            f"ElementTree: {ET.VERSION}",
            f"PyYAML:   {'installed' if _HAS_YAML else 'NOT installed'}",
            "",
            f"Files loaded: {self.table.rowCount()}",
            f"Texts:        {sum(len(i.texts) for i in self._info.values())}",
            f"With audio:   {fx.audio_loss_summary(list(self._info.values()))[0]}",
        ]
        QMessageBox.information(
            self, "Diagnostics  (Ctrl+Shift+D)", "\n".join(lines))

    def _on_mode_toggle(self, btn_id: int, checked: bool):
        if checked:
            self._sort_stack.setCurrentIndex(btn_id)

    def _on_output_mode_toggle(self, btn_id: int, checked: bool):
        if checked:
            self._out_stack.setCurrentIndex(btn_id)
            self._retarget_output_suffix()
            # Corpus mode passes every text through verbatim: no audio is
            # joined, no offsets are shifted, nothing is synthesized. The
            # pairing controls would do nothing, so they are disabled rather
            # than left looking live.
            combining = self._output_mode() == "combined"
            for widget in (self.audio_folder_btn, self.rematch_btn,
                           self.clear_audio_btn):
                widget.setEnabled(combining)
            self.table.setColumnHidden(PairingTable.AUDIO_COL, not combining)
            self.unmatched_panel.setVisible(combining)

    def _output_mode(self) -> str:
        return "combined" if self._radio_combined.isChecked() else "corpus"

    def _retarget_output_suffix(self):
        """Keep the suggested output filename in step with the chosen mode."""
        current = self.output_edit.text().strip()
        if not current:
            return
        path = Path(current)
        for old, new in (("_corpus", "_combined"), ("_combined", "_corpus")):
            if path.stem.endswith(old) and self._output_mode() in new:
                self.output_edit.setText(
                    str(path.with_name(path.stem[: -len(old)] + new + path.suffix))
                )
                return

    def _on_load_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select folder of .flextext files")
        if not folder:
            return
        root = Path(folder)
        walker = root.rglob("*") if self.recursive_cb.isChecked() else root.iterdir()
        paths = sorted(
            str(p) for p in walker
            if p.is_file() and p.suffix.lower() in FLEXTEXT_EXTENSIONS
        )
        if not paths:
            QMessageBox.information(
                self, "Nothing found",
                f"No .flextext files found in:\n{folder}\n\n"
                + ("" if self.recursive_cb.isChecked()
                   else "Try turning on 'Include subfolders'."),
            )
            return

        self.folder_label.setText(folder)
        added, _ = self._add_paths(
            _sorted_paths(paths, "name", False), replace=True)
        suffix = "_combined" if self._output_mode() == "combined" else "_corpus"
        self.output_edit.setText(str(root.parent / f"{root.name}{suffix}.flextext"))
        self.statusBar().showMessage(
            f"Loaded {len(added)} file(s) from {folder}")

    def _on_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add .flextext files", "",
            "FLExText files (*.flextext);;All files (*)",
        )
        if paths:
            added, _ = self._add_paths(paths)
            self.statusBar().showMessage(f"Added {len(added)} file(s).")

    def _on_files_dropped(self, paths: list[str]):
        """Files dragged in from the desktop; folders are expanded."""
        collected: list[str] = []
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                walker = p.rglob("*") if self.recursive_cb.isChecked() else p.iterdir()
                collected += sorted(
                    str(c) for c in walker
                    if c.is_file() and c.suffix.lower() in FLEXTEXT_EXTENSIONS
                )
            elif p.suffix.lower() in FLEXTEXT_EXTENSIONS:
                collected.append(str(p))

        if not collected:
            self.statusBar().showMessage("Nothing dropped that looks like a .flextext file.")
            return
        added, _ = self._add_paths(collected)
        self.statusBar().showMessage(f"Added {len(added)} file(s).")

    def _on_choose_audio_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Folder containing the recordings (Cancel to use each text's own folder)")
        self._audio_folder = folder
        self.audio_folder_label.setText(
            f"Audio: {folder}" if folder else "Audio: same folder as the texts")
        if self.table.rowCount():
            self._on_rematch()

    def _on_rematch(self):
        """Re-suggest audio for every row, including ones already assigned."""
        if self.table.rowCount() == 0:
            QMessageBox.information(
                self, "No texts", "Load some .flextext files first.")
            return
        pool = self._audio_pool()
        if not pool:
            where = self._audio_folder or "the texts' own folders"
            QMessageBox.information(
                self, "No audio found",
                f"No audio files were found in {where}.\n\n"
                "Choose an audio folder, or turn on 'Include subfolders'.",
            )
            return

        for row in range(self.table.rowCount()):
            self.table.set_audio(row, None)
        matched = self._automatch(self.table.text_paths())
        self._refresh_counts()

        weak = sum(
            1 for r in range(self.table.rowCount())
            if self.table.item(r, PairingTable.AUDIO_COL)
                   .data(Qt.ItemDataRole.UserRole + 1) == matching.BY_FOLDER
        )
        note = (f"  {weak} matched on folder alone (marked ?) — please check."
                if weak else "")
        self.statusBar().showMessage(
            f"Matched {matched} of {self.table.rowCount()} text(s) "
            f"from {len(pool)} recording(s).{note}"
        )

    def _on_clear_audio(self):
        rows = self.table.selected_rows()
        if not rows:
            # Clearing everything by accident would throw away all the manual
            # pairing, so an empty selection asks rather than assuming.
            paired = sum(1 for _, a in self.table.pairs() if a)
            if not paired:
                self.statusBar().showMessage("No audio assigned.")
                return
            answer = QMessageBox.question(
                self, "Clear all audio?",
                f"Nothing is selected. Unassign the audio from all {paired} "
                f"paired row(s)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            rows = list(range(self.table.rowCount()))
        self.table.clear_audio(list(rows))
        self.statusBar().showMessage(f"Audio unassigned from {len(rows)} row(s).")

    def _on_remove_selected(self):
        for row in reversed(self.table.selected_rows()):
            self._info.pop(self.table.text_path(row), None)
            self.table.removeRow(row)
        self._refresh_counts()

    def _on_clear(self):
        self.table.setRowCount(0)
        self._info.clear()
        self.folder_label.setText("No files loaded")
        self._refresh_counts()
        self.statusBar().showMessage("Cleared.")

    def _on_apply_sort(self):
        if self.table.rowCount() == 0:
            return
        self._set_paths(
            _sorted_paths(
                self._current_paths(),
                self.gui_panel.sort_field,
                self.gui_panel.sort_reverse,
                self._titles(),
            )
        )
        self.statusBar().showMessage("Sort applied.")

    def _on_apply_regex_sort(self):
        layers = self.regex_panel.get_layers()
        active = [l for l in layers if l["pattern"]]
        if not active:
            QMessageBox.warning(
                self, "No patterns", "Enter at least one regex pattern.")
            return
        for layer in active:
            try:
                re.compile(layer["pattern"])
            except re.error as exc:
                QMessageBox.critical(
                    self, "Invalid pattern",
                    f"Pattern: {layer['pattern']!r}\nError: {exc}",
                )
                return
        if self.table.rowCount() == 0:
            return
        self._set_paths(_apply_multilayer_regex_sort(self._current_paths(), active))
        n = len(active)
        self.statusBar().showMessage(
            f"Regex sort applied ({n} layer{'s' if n != 1 else ''})."
        )

    def _on_browse_output(self):
        initial = self.output_edit.text() or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(
            self, "Save combined file as", initial, "FLExText files (*.flextext)"
        )
        if path:
            if not path.lower().endswith(".flextext"):
                path += ".flextext"
            self.output_edit.setText(path)

    # ── Running the merge ────────────────────────────────────────────────────

    def _confirm_audio_loss(self) -> str | None:
        """
        Warn before combined mode discards audio data.

        Returns "go", "corpus" (the user switched modes) or None (cancelled).
        Stays silent when nothing would actually be lost.
        """
        # Shifting keeps the data, so there is nothing to warn about.
        if self.combined_panel.audio_mode == fx.AUDIO_SHIFT:
            return "go"

        infos = [self._info[p] for p in self._current_paths() if p in self._info]
        n_files, n_phrases, n_media = fx.audio_loss_summary(infos)
        if not self._warn_audio_loss or (n_phrases == 0 and n_media == 0):
            return "go"

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Audio segmentation will be discarded")
        box.setText(
            f"<b>{n_files} of {len(infos)} file(s) carry audio segmentation.</b>"
        )
        box.setInformativeText(
            f"Combining into one text will permanently discard:\n\n"
            f"  • time offsets on {n_phrases} phrase(s)\n"
            f"  • {n_media} media-file reference(s) and the media-files list\n"
            f"  • speaker names\n\n"
            f"This cannot be recovered from the output file. Your source files "
            f"are not modified, so you can re-run in Corpus mode to keep all of "
            f"it."
        )
        combine_btn = box.addButton("Combine anyway",
                                    QMessageBox.ButtonRole.DestructiveRole)
        shift_btn = box.addButton("Shift offsets instead",
                                  QMessageBox.ButtonRole.ActionRole)
        corpus_btn = box.addButton("Switch to Corpus mode",
                                   QMessageBox.ButtonRole.ActionRole)
        cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel_btn)

        suppress = QCheckBox("Don't warn me again")
        box.setCheckBox(suppress)
        box.exec()

        clicked = box.clickedButton()
        if suppress.isChecked():
            self._warn_audio_loss = False
        if clicked is combine_btn:
            return "go"
        if clicked is shift_btn:
            self.combined_panel.set_audio_mode(fx.AUDIO_SHIFT)
            return "shift"
        if clicked is corpus_btn:
            self._radio_corpus.setChecked(True)
            return "corpus"
        return None

    def _output_collision(self, output: str) -> str | None:
        """Name the source file an output path would overwrite, if any."""
        def canon(p) -> str:
            try:
                return str(Path(p).resolve()).lower()
            except OSError:
                return str(p).lower()

        sources = {canon(t) for t, _ in self.table.pairs()}
        sources |= {canon(a) for _, a in self.table.pairs() if a}

        candidates = [("The output file", output)]
        if (self._output_mode() == "combined"
                and self.combined_panel.audio_mode == fx.AUDIO_SHIFT
                and self.combined_panel.join_audio_cb.isChecked()):
            candidates.append(
                ("The joined-audio file",
                 self.combined_panel.media_edit.text().strip()))

        for label, path in candidates:
            if path and canon(path) in sources:
                return (f"{label} is one of the loaded source files:\n\n{path}"
                        f"\n\nWriting it would destroy that source. Choose a "
                        f"different path.")
        return None

    def _confirm_unmatched(self) -> bool:
        """
        Ask before combining when some texts have no recording, or when one
        recording is used twice.

        Both are legitimate — a text may simply have no audio — but both change
        the timeline, so they should be a decision rather than a surprise.
        """
        rows = self.table.pairs()
        unmatched = [Path(p).name for p, a in rows if not a]

        used: dict[str, list[str]] = {}
        for text_path, audio_path in rows:
            if audio_path:
                used.setdefault(audio_path, []).append(Path(text_path).name)
        duplicates = {a: names for a, names in used.items() if len(names) > 1}

        if not unmatched and not duplicates:
            return True

        parts: list[str] = []
        if unmatched:
            shown = "\n".join(f"  • {n}" for n in unmatched[:8])
            more = (f"\n  …and {len(unmatched) - 8} more"
                    if len(unmatched) > 8 else "")
            parts.append(
                f"{len(unmatched)} text(s) have no recording:\n{shown}{more}\n\n"
                "Their lines will carry no timing, and they will take up no "
                "room in the joined audio."
            )
        if duplicates:
            shown = "\n".join(
                f"  • {Path(a).name} → {', '.join(n[:40] for n in names)}"
                for a, names in list(duplicates.items())[:5]
            )
            parts.append(
                f"{len(duplicates)} recording(s) are used by more than one "
                f"text:\n{shown}\n\n"
                "That audio will be joined once per text, so the timeline will "
                "contain it several times. If these are different exports of "
                "the same text, keep only one."
            )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Check the pairing")
        box.setText("<b>Some texts are not paired one-to-one with audio.</b>")
        box.setInformativeText("\n\n".join(parts))
        go = box.addButton("Combine anyway", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is go

    def _on_combine(self):
        if self.table.rowCount() == 0:
            QMessageBox.warning(
                self, "No files", "Load or add some .flextext files first.")
            return
        output = self.output_edit.text().strip()
        if not output:
            QMessageBox.warning(
                self, "No output path", "Choose an output file path first.")
            return

        # The worker refuses these too, but catching it here puts the error
        # next to the field the user needs to change.
        clash = self._output_collision(output)
        if clash:
            QMessageBox.warning(self, "Output would overwrite a source", clash)
            return

        mode = self._output_mode()
        options: dict = {}

        if mode == "combined":
            title = self.combined_panel.title_edit.text().strip()
            if not title:
                QMessageBox.warning(
                    self, "No title",
                    "Combined Text mode needs a title for the new text.",
                )
                self.combined_panel.title_edit.setFocus()
                return

            decision = self._confirm_audio_loss()
            if decision is None:
                self.statusBar().showMessage("Cancelled.")
                return
            if decision == "corpus":
                self.statusBar().showMessage(
                    "Switched to Corpus mode — press Combine Files again.")
                return
            if decision == "shift":
                self.statusBar().showMessage(
                    "Switched to shifting offsets — check the gap and combined "
                    "audio, then press Combine Files again.")
                return

            options = {
                "title": title,
                "title_lang": self.combined_panel.lang_combo.currentText().strip() or "en",
                "add_title_notes": self.combined_panel.title_notes_cb.isChecked(),
                "segnum_mode": self.combined_panel.segnum_mode,
                "strip_audio_notes": self.combined_panel.strip_notes_cb.isChecked(),
                "audio_mode": self.combined_panel.audio_mode,
                "gap_ms": self.combined_panel.gap_spin.value(),
                "media_location": self.combined_panel.media_edit.text().strip(),
                "distribute_untimed": self.combined_panel.distribute_cb.isChecked(),
            }
            if self.combined_panel.audio_mode == fx.AUDIO_SHIFT:
                options["audio_paths"] = dict(self.table.pairs())
                options["join_audio"] = self.combined_panel.join_audio_cb.isChecked()
                if options["join_audio"] and not options["media_location"]:
                    QMessageBox.warning(
                        self, "No audio output path",
                        "Choose where to write the joined audio, or turn off "
                        "'Join the matched recordings into one audio file'.",
                    )
                    return
                if not self._confirm_unmatched():
                    self.statusBar().showMessage("Cancelled.")
                    return
        else:
            options = {"strip_guids": self.corpus_panel.strip_guids_cb.isChecked()}

        paths = self._current_paths()
        self._worker = CombineWorker(
            output_file=output, file_paths=paths, mode=mode, options=options)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.success.connect(self._on_combine_success)
        self._worker.error.connect(self._on_combine_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_done)

        self._set_running(True, len(paths))
        self.statusBar().showMessage("Combining…")
        self._thread.start()

    def _set_running(self, running: bool, total: int = 0):
        self.combine_btn.setEnabled(not running)
        self.load_btn.setEnabled(not running)
        self.add_btn.setEnabled(not running)
        self.progress.setVisible(running)
        self.cancel_btn.setVisible(running)
        self.cancel_btn.setEnabled(running)
        if running:
            self.progress.setRange(0, max(total, 1))
            self.progress.setValue(0)

    def _on_progress(self, done: int, message: str):
        self.progress.setValue(done)
        self.statusBar().showMessage(message)

    def _on_cancel(self):
        if self._worker:
            self._worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.statusBar().showMessage("Cancelling…")

    def _on_combine_success(self, result):
        lines = [
            f"Saved:\n{result.output_file}",
            "",
            f"{result.n_texts} text(s), {result.n_paragraphs} paragraph(s), "
            f"{result.n_phrases} phrase(s).",
        ]
        if self._output_mode() == "combined":
            if self.combined_panel.audio_mode == fx.AUDIO_SHIFT:
                if getattr(result, "audio_file", ""):
                    lines.append(
                        f"\nJoined audio written to:\n{result.audio_file}\n"
                        f"({result.audio_ms / 1000:.1f}s of recordings). The "
                        f"offsets were measured from these files, so they are "
                        f"exact."
                    )
                else:
                    lines.append(
                        "\nTime offsets were shifted onto one concatenated "
                        "timeline. Join the source audio in the same order for "
                        "them to line up."
                    )
            else:
                lines.append(
                    "\nAudio segmentation and media information were discarded "
                    "(Combined Text mode). Your source files are unchanged."
                )
        if result.failures:
            lines.append(
                f"\n{result.n_skipped} text(s) could not be read and are not "
                f"in the output:")
            lines += [f"  • {p.name}: {m}" for p, m in result.failures[:10]]
        audio_failures = getattr(result, "audio_failures", [])
        if audio_failures:
            lines.append(
                f"\n{len(audio_failures)} recording(s) could not be read. The "
                f"texts are still in the output, but without timing:")
            lines += [f"  • {p.name}: {m}" for p, m in audio_failures[:10]]
        for warning in getattr(result, "audio_warnings", []):
            lines.append(f"\n⚠ {warning}")
        if result.warnings:
            lines.append("\nNotes:")
            lines += [f"  • {w}" for w in result.warnings[:10]]

        problems = (result.failures or result.warnings or audio_failures
                    or getattr(result, "audio_warnings", []))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning if problems
                    else QMessageBox.Icon.Information)
        box.setWindowTitle("Done")
        box.setText("\n".join(lines))
        box.exec()
        self.statusBar().showMessage(f"Saved: {result.output_file}")

    def _on_combine_error(self, message: str):
        QMessageBox.critical(self, "Error", message)
        self.statusBar().showMessage("Error — nothing was written.")

    def _on_thread_done(self):
        self._set_running(False)
        self._worker = None
        self._thread = None

    def closeEvent(self, event):
        if self._thread is not None and self._thread.isRunning():
            answer = QMessageBox.question(
                self, "Still working",
                "A merge is still running. Quit anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self._worker:
                self._worker.cancel()
            self._thread.quit()
            self._thread.wait(3000)
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()

    # Hidden flag used by CI and the release process to prove a built bundle
    # actually starts. PyInstaller excludes are tuned aggressively (see
    # build.yml), and an over-excluded bundle builds cleanly then dies on
    # launch — this is the check that catches it.
    if "--smoke-test" in sys.argv:
        window.show()
        print(f"SMOKE-OK {window.windowTitle()} "
              f"rows={window.table.rowCount()}")
        return

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
