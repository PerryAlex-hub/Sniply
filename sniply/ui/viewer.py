"""Full-size screenshot viewer, opened by double-clicking a gallery
thumbnail -- a 160px thumbnail isn't enough to actually read slide
content, so this shows the saved PNG at (up to) its native resolution,
scaled down only if it's bigger than the screen.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout


class ScreenshotViewerDialog(QDialog):
    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(Path(file_path).name)

        pixmap = QPixmap(file_path)
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)

        screen = self.screen() or QGuiApplication.primaryScreen()
        max_size = screen.availableGeometry().size() * 0.85
        if pixmap.width() > max_size.width() or pixmap.height() > max_size.height():
            pixmap = pixmap.scaled(max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        label.setPixmap(pixmap)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)
        self.resize(pixmap.size())
