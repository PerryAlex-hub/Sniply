"""The screenshot gallery: a thumbnail grid backed by the session's
screenshot list, supporting delete (single or multi-select batch, via
right-click or the Delete key), reorder (drag and drop), export
selection (checkable), and opening a screenshot at full size
(double-click).

Two separate notions of "selected" exist here and are deliberately
kept apart: the per-item CHECKBOX marks a screenshot for export, while
the list's highlighted/selected state (click, Ctrl+click, Shift+click)
is what batch delete acts on. Conflating them would mean checking a
box for export risked also deleting it, or vice versa.
"""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from sniply.storage.session import Screenshot

THUMB_SIZE = 160


class GalleryWidget(QWidget):
    deletion_requested = Signal(list)          # list[int] of screenshot ids to delete
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
        # Lets the user Ctrl+click / Shift+click / rubber-band-drag to
        # highlight several thumbnails at once for batch delete --
        # distinct from the per-item export checkbox.
        self._list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._list.setSpacing(8)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)

        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.itemSelectionChanged.connect(self._update_delete_button)
        # Connected once here, not inside set_screenshots(): that method
        # is called on every refresh (new capture, delete, reorder...),
        # and re-connecting there on each call previously stacked up a
        # new duplicate connection per refresh, making checkbox toggles
        # fire selection_changed multiple times after a session had been
        # running a while.
        self._list.itemChanged.connect(self._on_item_changed)

        delete_shortcut = QShortcut(QKeySequence.Delete, self._list)
        delete_shortcut.activated.connect(self._delete_highlighted)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Captured Screenshots"))
        hint = QLabel(
            "Double-click to view full size  •  Drag to reorder  •  "
            "Check to include in export  •  Click to highlight (Ctrl/Shift "
            "for multiple), then Delete key or right-click to remove"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)
        layout.addWidget(self._list)

        delete_row = QHBoxLayout()
        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.delete_button.setIconSize(QSize(16, 16))
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._delete_highlighted)
        delete_row.addWidget(self.delete_button)
        delete_row.addStretch()
        layout.addLayout(delete_row)

        self._by_item: dict[int, Screenshot] = {}

    def set_screenshots(self, screenshots: list[Screenshot]) -> None:
        # Block itemChanged while repopulating: setCheckState() on each
        # newly-added item would otherwise fire selection_changed for
        # every screenshot on every refresh, redundantly re-writing
        # values that are already correct in the database.
        self._list.blockSignals(True)
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

        self._list.blockSignals(False)
        self._update_delete_button()

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

    def _on_context_menu(self, pos) -> None:
        clicked_item = self._list.itemAt(pos)
        if clicked_item is None:
            return

        highlighted = self._list.selectedItems()
        # Right-clicking an item that's already part of a multi-item
        # highlight acts on the whole highlight (standard file-manager
        # behavior); right-clicking something outside it acts on just
        # that one item instead.
        targets = highlighted if clicked_item in highlighted and len(highlighted) > 1 else [clicked_item]

        menu = QMenu(self)
        if len(targets) == 1:
            view_action = menu.addAction("View Full Size")
            delete_action = menu.addAction("Delete")
        else:
            view_action = None
            delete_action = menu.addAction(f"Delete Selected ({len(targets)})")

        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen is view_action:
            self._on_item_double_clicked(clicked_item)
        elif chosen is delete_action:
            self._delete_items(targets)

    def _update_delete_button(self) -> None:
        count = len(self._list.selectedItems())
        self.delete_button.setEnabled(count > 0)
        self.delete_button.setText(f"Delete Selected ({count})" if count else "Delete Selected")

    def _delete_highlighted(self) -> None:
        self._delete_items(self._list.selectedItems())

    def _delete_items(self, items: list[QListWidgetItem]) -> None:
        if not items:
            return
        ids = [item.data(Qt.UserRole) for item in items]
        self.deletion_requested.emit(ids)

    def selected_ids(self) -> list[int]:
        """Screenshot ids currently checked for export, in gallery (display) order."""
        ids = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.Checked:
                ids.append(item.data(Qt.UserRole))
        return ids
