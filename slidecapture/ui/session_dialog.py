"""Small dialogs for creating a new session and choosing a previous
one to reopen.
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class NewSessionDialog(QDialog):
    def __init__(self, default_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Lecture Session")
        self._chosen_dir = default_dir

        self.name_edit = QLineEdit("Lecture")
        self.dir_label = QLabel(str(default_dir))
        browse_button = QPushButton("Choose Folder...")
        browse_button.clicked.connect(self._browse)

        ok_button = QPushButton("Create")
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Session name:"))
        layout.addWidget(self.name_edit)
        layout.addWidget(QLabel("Save location:"))
        layout.addWidget(self.dir_label)
        layout.addWidget(browse_button)

        button_row = QHBoxLayout()
        button_row.addWidget(ok_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose Save Location", str(self._chosen_dir))
        if chosen:
            self._chosen_dir = Path(chosen)
            self.dir_label.setText(chosen)

    def session_name(self) -> str:
        return self.name_edit.text().strip() or "Lecture"

    def save_dir(self) -> Path:
        return self._chosen_dir


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
