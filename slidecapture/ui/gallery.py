"""The screenshot gallery: a thumbnail grid backed by the session's
screenshot list, supporting delete, reorder (drag and drop), export
selection (checkable), and opening a screenshot at full size.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from slidecapture.storage.session import Screenshot

THUMB_SIZE = 160


class GalleryWidget(QWidget):
    deletion_requested = Signal(int)          # screenshot id
    reorder_requested = Signal(list)           # list[int] of screenshot ids, new order
    selection_changed = Signal(int, bool)      # screenshot id, selected_for_export
    open_requested = Signal(str)               # file path, to open at full size

    def __init__(self, parent=None):
        super().__init__(parent)
        self._list = QListWidget()
        self._list.setViewMode(QListWidget.IconMode)
        self._list.setIconSize(self._list.iconSize())
        self._list.setResizeMode(QListWidget.Adjust)
        self._list.setMovement(QListWidget.Snap)
        self._list.setDragDropMode(QAbstractItemView.InternalMove)
        self._list.setSpacing(8)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._list.model().rowsMoved.connect(self._on_rows_moved)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Captured Screenshots"))
        layout.addWidget(self._list)

        self._by_item: dict[int, Screenshot] = {}

    def set_screenshots(self, screenshots: list[Screenshot]) -> None:
        self._list.clear()
        self._by_item.clear()

        for shot in screenshots:
            pixmap = QPixmap(shot.file_path)
            thumb = pixmap.scaled(
                THUMB_SIZE, THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            item = QListWidgetItem(f"#{shot.sequence}")
            item.setData(Qt.UserRole, shot.id)
            item.setIcon(thumb if not thumb.isNull() else QPixmap())
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if shot.selected_for_export else Qt.Unchecked)
            self._list.addItem(item)
            self._by_item[shot.id] = shot

        self._list.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        shot_id = item.data(Qt.UserRole)
        selected = item.checkState() == Qt.Checked
        self.selection_changed.emit(shot_id, selected)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        shot_id = item.data(Qt.UserRole)
        shot = self._by_item.get(shot_id)
        if shot is not None:
            self.open_requested.emit(shot.file_path)

    def _on_rows_moved(self, *_args) -> None:
        ordered_ids = []
        for i in range(self._list.count()):
            ordered_ids.append(self._list.item(i).data(Qt.UserRole))
        self.reorder_requested.emit(ordered_ids)

    def selected_ids(self) -> list[int]:
        """Screenshot ids currently checked for export, in gallery (display) order."""
        ids = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.Checked:
                ids.append(item.data(Qt.UserRole))
        return ids
