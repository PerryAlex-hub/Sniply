"""Small dialogs for creating a new session and choosing a previous
one to reopen.
"""

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class NewSessionDialog(QDialog):
    """Just asks for a name. Where the session's raw screenshots and
    database actually live is the app's own business (see
    main_window.APP_DATA_ROOT) -- the user shouldn't need to think
    about a save location until they're ready to export a finished
    PDF/images, which is a separate, deliberate choice (see the
    export dialogs, which default to Downloads).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Lecture Session")

        self.name_edit = QLineEdit("Lecture")

        ok_button = QPushButton("Create")
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Session name:"))
        layout.addWidget(self.name_edit)

        button_row = QHBoxLayout()
        button_row.addWidget(ok_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

    def session_name(self) -> str:
        return self.name_edit.text().strip() or "Lecture"


class OpenSessionDialog(QDialog):
    def __init__(self, sessions: list[tuple[int, str, str]], parent=None):
        """`sessions` is a list of (id, name, save_dir), as returned by
        SessionStore.list_sessions()."""
        super().__init__(parent)
        self.setWindowTitle("Reopen Session")
        self._sessions = sessions
        self._selected_id: int | None = None

        self._list = QListWidget()
        for session_id, name, save_dir in sessions:
            item = QListWidgetItem(f"{name}  ({save_dir})")
            item.setData(256, session_id)  # Qt.UserRole
            self._list.addItem(item)

        open_button = QPushButton("Open")
        open_button.clicked.connect(self._on_open)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Previous sessions:"))
        layout.addWidget(self._list)
        button_row = QHBoxLayout()
        button_row.addWidget(open_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

    def _on_open(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._selected_id = item.data(256)
            self.accept()

    def selected_session_id(self) -> int | None:
        return self._selected_id
