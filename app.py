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
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import flextext as fx
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
    ("Remove — let FLEx number the text", fx.SEGNUM_STRIP),
    ("Renumber continuously (1…N)",       fx.SEGNUM_RENUMBER),
    ("Keep each text's original numbers", fx.SEGNUM_KEEP),
]

# Discarding is the default because it needs nothing from the user; shifting
# assumes the source audio really will be concatenated in the same order.
AUDIO_CHOICES = [
    ("Discard (offsets point at per-text audio)", fx.AUDIO_DISCARD),
    ("Shift onto one concatenated recording",     fx.AUDIO_SHIFT),
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


def _apply_suffix_order(paths: list[str], patterns: list[str]) -> list[str]:
    """
    Group files by base name, sort groups by natural key, sort within each
    group by the pattern's rank in the user's list.

    Longest patterns are tried first when matching so that a specific pattern
    is never shadowed by a shorter one that is a substring of it, regardless
    of their position in the user's list.
    """
    if not patterns:
        return paths

    # Compile; auto-escape anything that isn't valid regex.
    compiled: list[tuple[int, int, re.Pattern]] = []
    for rank, pat in enumerate(patterns):
        try:
            rx = re.compile(pat)
        except re.error:
            rx = re.compile(re.escape(pat))
        compiled.append((rank, len(pat), rx))

    # For matching: try longest pattern string first.
    by_length = sorted(compiled, key=lambda x: x[1], reverse=True)

    def classify(stem: str) -> tuple[str, int]:
        """Return (base_name, suffix_rank) for a filename stem."""
        for rank, _, rx in by_length:
            m = rx.search(stem)
            if m:
                pre  = stem[:m.start()].rstrip("-_")
                post = stem[m.end():].lstrip("-_")
                base = pre + ("-" if pre and post else "") + post
                return base, rank
        # No pattern matched — use the full stem as the base key,
        # rank beyond all named patterns so it sorts last in its group.
        return stem, len(patterns)

    groups: dict[str, list[tuple[int, str]]] = {}
    for path in paths:
        base, rank = classify(Path(path).stem)
        if base not in groups:
            groups[base] = []
        groups[base].append((rank, path))

    sorted_bases = sorted(groups.keys(), key=_natural_key)

    # Within each group sort by suffix rank; ties keep original list order
    # because Python's sort is stable.
    result: list[str] = []
    for base in sorted_bases:
        result.extend(path for _, path in sorted(groups[base], key=lambda x: x[0]))
    return result


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
# Simple (GUI) sort panel
# ---------------------------------------------------------------------------

class SuffixOrderWidget(QGroupBox):
    """Sub-sort by ordered suffix patterns, for systematically named exports."""

    _HELP = (
        "Add substrings or regex patterns that appear in your filenames.\n\n"
        "Files that share the same base name (the filename with the matched\n"
        "pattern removed) are grouped together and ordered by this list.\n\n"
        "MATCHING RULE — longer patterns are always tried first, regardless\n"
        "of their position in the list. This prevents a short pattern from\n"
        "accidentally matching inside a longer one.\n\n"
        "EXAMPLE — several dated exports of the same text:\n"
        "  Tosokai 2026-07-20-1803.flextext\n"
        "  Tosokai 2026-07-23-1218.flextext\n"
        "Add a pattern like  \\d{4}-\\d{2}-\\d{2}-\\d{4}  to group them.\n\n"
        "Drag entries to reorder their priority within matched groups."
    )

    def __init__(self, parent=None):
        super().__init__("Suffix Order", parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self.pattern_list = DraggableListWidget()
        self.pattern_list.setMaximumHeight(110)
        layout.addWidget(self.pattern_list)

        row = QHBoxLayout()
        self.add_input = QLineEdit()
        self.add_input.setPlaceholderText("Substring or regex pattern…")
        self.add_input.returnPressed.connect(self._add)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_selected)
        help_btn = QPushButton("?")
        help_btn.setFixedWidth(28)
        help_btn.setToolTip(self._HELP)
        help_btn.clicked.connect(
            lambda: QMessageBox.information(self, "Suffix Order Help", self._HELP)
        )
        row.addWidget(self.add_input, 1)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addWidget(help_btn)
        layout.addLayout(row)

        self.apply_btn = QPushButton("Apply Suffix Order")
        layout.addWidget(self.apply_btn)

    def patterns(self) -> list[str]:
        return [self.pattern_list.item(i).text()
                for i in range(self.pattern_list.count())]

    def set_patterns(self, patterns: list[str]):
        self.pattern_list.clear()
        for p in patterns:
            self.pattern_list.addItem(p)

    def _add(self):
        text = self.add_input.text().strip()
        if not text:
            return
        try:
            re.compile(text)
            display = text
        except re.error:
            escaped = re.escape(text)
            QMessageBox.information(
                self, "Auto-escaped",
                f"'{text}' is not a valid regex pattern.\n\n"
                f"It has been added as a literal string (auto-escaped to '{escaped}').",
            )
            display = escaped
        self.pattern_list.addItem(display)
        self.add_input.clear()

    def _remove_selected(self):
        for item in reversed(self.pattern_list.selectedItems()):
            self.pattern_list.takeItem(self.pattern_list.row(item))


class GuiSortPanel(QWidget):
    """The Simple sort panel: standard sort fields + optional suffix order."""

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

        self.suffix_widget = SuffixOrderWidget()
        layout.addWidget(self.suffix_widget)

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

        self.strip_guids_cb = QCheckBox("Strip text GUIDs (import as new texts)")
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

        # ── Audio segmentation ───────────────────────────────────────────────
        audio_row = QHBoxLayout()
        audio_row.addWidget(QLabel("Audio segmentation:"))
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
        layout.addLayout(audio_row)

        self.shift_box = QWidget()
        shift_layout = QVBoxLayout(self.shift_box)
        shift_layout.setContentsMargins(18, 0, 0, 0)
        shift_layout.setSpacing(4)

        gap_row = QHBoxLayout()
        gap_row.addWidget(QLabel("Gap between texts:"))
        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(0, 600000)
        self.gap_spin.setValue(fx.DEFAULT_GAP_MS)
        self.gap_spin.setSuffix(" ms")
        self.gap_spin.setFixedWidth(110)
        self.gap_spin.setToolTip(
            "Silence inserted between texts in the concatenated audio.\n\n"
            f"{fx.DEFAULT_GAP_MS} ms is what the Audio Concatenator app adds "
            "between files\n(500 ms silence + 5 ms click + 500 ms silence), so "
            "leaving it\nat that value keeps the two apps' outputs aligned.\n"
            "Use 0 for plain gapless concatenation."
        )
        gap_row.addWidget(self.gap_spin)
        reset_btn = QPushButton("Match Audio Concatenator")
        reset_btn.setToolTip(f"Set the gap back to {fx.DEFAULT_GAP_MS} ms.")
        reset_btn.clicked.connect(
            lambda: self.gap_spin.setValue(fx.DEFAULT_GAP_MS))
        gap_row.addWidget(reset_btn)
        gap_row.addStretch()
        shift_layout.addLayout(gap_row)

        media_row = QHBoxLayout()
        media_row.addWidget(QLabel("Combined audio:"))
        self.media_edit = QLineEdit()
        self.media_edit.setPlaceholderText(
            "Path or URL of the joined recording (optional)…")
        self.media_edit.setToolTip(
            "The single audio file the shifted offsets point into.\n"
            "Written as one <media> entry that every phrase references, which\n"
            "is what lets ELAN open the text against the audio.\n\n"
            "Leave empty to shift the offsets without linking any media."
        )
        media_browse = QPushButton("Browse…")
        media_browse.clicked.connect(self._browse_media)
        media_row.addWidget(self.media_edit, 1)
        media_row.addWidget(media_browse)
        shift_layout.addLayout(media_row)

        layout.addWidget(self.shift_box)

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
        layout.addLayout(title_row)

        self.title_notes_cb = QCheckBox(
            "Add each source text's title as a note on its first line")
        self.title_notes_cb.setChecked(True)
        self.title_notes_cb.setToolTip(
            "Records where each original text begins. FLEx shows this on the\n"
            "Note line. A paragraph cannot carry a title of its own, so the\n"
            "note is attached to the paragraph's first phrase."
        )
        layout.addWidget(self.title_notes_cb)

        segnum_row = QHBoxLayout()
        segnum_row.addWidget(QLabel("Line numbers (segnum):"))
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
        layout.addLayout(segnum_row)

        self.strip_notes_cb = QCheckBox(
            "Strip audio timestamp notes (e.g. “audio ~0:00.000–0:02.421”)")
        self.strip_notes_cb.setChecked(True)
        self.strip_notes_cb.setToolTip(
            "Removes note items whose entire content is an audio timestamp.\n"
            "Notes that mix real commentary with a timestamp are left alone."
        )
        layout.addWidget(self.strip_notes_cb)
        layout.addStretch()

        self._on_audio_mode_changed()

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

    def _browse_media(self):
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
        "suffix_order:  # substrings or regex patterns that identify file suffixes;",
        "               # one pattern per line — delete all entries (or write []) to disable.",
        "               # Longer patterns are always tried first, so a specific pattern",
        "               # is never shadowed by a shorter one it contains.",
    ]

    suffix = settings.get("suffix_order") or []
    if suffix:
        for p in suffix:
            lines.append(f"  - {_ys(p)}")
    else:
        lines.append("  []")

    lines += [
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
        self.setMinimumSize(820, 760)
        self._worker: Optional[CombineWorker] = None
        self._thread: Optional[QThread] = None
        # Parsed metadata per path, for list labels, title sorting and the
        # audio-loss warning.  Populated whenever files are added.
        self._info: dict[str, fx.FlextextFile] = {}
        self._warn_audio_loss = True
        self._build_ui()

    # ── Construction ─────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

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

        # ── File list ────────────────────────────────────────────────────────
        self.file_list = DraggableListWidget(accept_external=True)
        self.file_list.files_dropped.connect(self._on_files_dropped)
        self.file_list.setToolTip(
            "Drag files here from your file manager, or drag rows to reorder.")
        root.addWidget(self.file_list, 1)

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

        # ── Sort mode toggle ─────────────────────────────────────────────────
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
        load_settings_btn = QPushButton("Load Settings…")
        load_settings_btn.setToolTip("Load settings from a YAML file")
        load_settings_btn.clicked.connect(self._load_settings)
        save_settings_btn = QPushButton("Save Settings…")
        save_settings_btn.setToolTip("Save current settings to a YAML file")
        save_settings_btn.clicked.connect(self._save_settings)
        mode_row.addWidget(load_settings_btn)
        mode_row.addWidget(save_settings_btn)
        root.addLayout(mode_row)

        # ── Stacked sort panels ──────────────────────────────────────────────
        self._sort_stack = QStackedWidget()

        self.gui_panel = GuiSortPanel()
        self.gui_panel.apply_sort_btn.clicked.connect(self._on_apply_sort)
        self.gui_panel.suffix_widget.apply_btn.clicked.connect(
            self._on_apply_suffix_order)
        self._sort_stack.addWidget(self.gui_panel)    # index 0

        self.regex_panel = RegexSortPanel()
        self.regex_panel.apply_btn.clicked.connect(self._on_apply_regex_sort)
        self._sort_stack.addWidget(self.regex_panel)  # index 1

        root.addWidget(self._sort_stack)

        # ── Output mode ──────────────────────────────────────────────────────
        out_mode_box = QGroupBox("Combine as")
        out_mode_layout = QVBoxLayout(out_mode_box)
        out_mode_layout.setSpacing(6)

        out_row = QHBoxLayout()
        self._out_group = QButtonGroup(self)
        self._radio_corpus   = QRadioButton("Corpus — individual texts, one file")
        self._radio_combined = QRadioButton("Combined Text — one text in FLEx")
        self._radio_corpus.setChecked(True)
        self._out_group.addButton(self._radio_corpus,   0)
        self._out_group.addButton(self._radio_combined, 1)
        self._out_group.idToggled.connect(self._on_output_mode_toggle)
        out_row.addWidget(self._radio_corpus)
        out_row.addWidget(self._radio_combined)
        out_row.addStretch()
        out_mode_layout.addLayout(out_row)

        self._out_stack = QStackedWidget()
        self.corpus_panel = CorpusOptionsPanel()
        self.combined_panel = CombinedOptionsPanel()
        self._out_stack.addWidget(self.corpus_panel)    # index 0
        self._out_stack.addWidget(self.combined_panel)  # index 1
        out_mode_layout.addWidget(self._out_stack)
        root.addWidget(out_mode_box)

        # ── Output row ───────────────────────────────────────────────────────
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output:"))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Choose output file…")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse_output)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse_btn)
        root.addLayout(output_row)

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
        root.addLayout(run_row)

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
            "suffix_order":        self.gui_panel.suffix_widget.patterns(),
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

        # ── suffix_order ─────────────────────────────────────────────────────
        suffix = data.get("suffix_order")
        if suffix is not None:
            if isinstance(suffix, list):
                self.gui_panel.suffix_widget.set_patterns([str(p) for p in suffix])
            else:
                warnings.append(
                    f"suffix_order: expected a list of strings, "
                    f"got {type(suffix).__name__}. Skipped."
                )

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
        return [
            self.file_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.file_list.count())
        ]

    def _titles(self) -> dict[str, str]:
        return {p: info.title for p, info in self._info.items() if info.title}

    def _set_paths(self, paths: list[str]):
        self.file_list.clear()
        for path in paths:
            info = self._info.get(path)
            item = QListWidgetItem(info.label if info else Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.file_list.addItem(item)
        self._refresh_counts()

    def _refresh_counts(self):
        n = self.file_list.count()
        infos = [self._info[p] for p in self._current_paths() if p in self._info]
        phrases = sum(i.n_phrases for i in infos)
        texts = sum(len(i.texts) for i in infos)
        self.count_label.setText(
            f"{n} file(s) · {texts} text(s) · {phrases} phrase(s)" if n else ""
        )
        self.combined_panel.set_languages(
            fx.collect_languages(infos), fx.default_title_lang(infos)
        )

    def _add_paths(self, new_paths: list[str], *, replace: bool = False):
        """
        Parse and add files, keeping the existing order and skipping duplicates.

        Files that cannot be parsed are reported and left out rather than
        silently added, so the list only ever contains usable texts.
        """
        existing = [] if replace else self._current_paths()
        if replace:
            self._info.clear()

        seen = set(existing)
        to_parse = [p for p in new_paths if p not in seen]
        parsed, failures = fx.parse_files(to_parse)
        for info in parsed:
            self._info[str(info.path)] = info

        added = [str(i.path) for i in parsed]
        self._set_paths(existing + added)

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
            f"Files loaded: {self.file_list.count()}",
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
            "FLExText files (*.flextext *.xml);;All files (*)",
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

    def _on_remove_selected(self):
        for item in reversed(self.file_list.selectedItems()):
            self._info.pop(item.data(Qt.ItemDataRole.UserRole), None)
            self.file_list.takeItem(self.file_list.row(item))
        self._refresh_counts()

    def _on_clear(self):
        self.file_list.clear()
        self._info.clear()
        self.folder_label.setText("No files loaded")
        self._refresh_counts()
        self.statusBar().showMessage("Cleared.")

    def _on_apply_sort(self):
        if self.file_list.count() == 0:
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

    def _on_apply_suffix_order(self):
        patterns = self.gui_panel.suffix_widget.patterns()
        if not patterns:
            QMessageBox.information(
                self, "No patterns", "Add at least one pattern first.")
            return
        if self.file_list.count() == 0:
            return
        self._set_paths(_apply_suffix_order(self._current_paths(), patterns))
        self.statusBar().showMessage("Suffix order applied.")

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
        if self.file_list.count() == 0:
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

    def _on_combine(self):
        if self.file_list.count() == 0:
            QMessageBox.warning(
                self, "No files", "Load or add some .flextext files first.")
            return
        output = self.output_edit.text().strip()
        if not output:
            QMessageBox.warning(
                self, "No output path", "Choose an output file path first.")
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
            }
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
            lines.append(f"\n{result.n_skipped} file(s) were skipped:")
            lines += [f"  • {p.name}: {m}" for p, m in result.failures[:10]]
        if result.warnings:
            lines.append("\nNotes:")
            lines += [f"  • {w}" for w in result.warnings[:10]]

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning if (result.failures or result.warnings)
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
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
