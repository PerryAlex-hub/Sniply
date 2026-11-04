"""Sniply entry point."""

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from sniply.ui.main_window import MainWindow
from sniply.ui.theme import STYLESHEET

LOG_PATH = Path(__file__).parent / "sniply_debug.log"


def _resource_path(relative: str) -> Path:
    """Resolves a bundled asset both when running from source and
    when running as a PyInstaller-frozen executable -- a --onefile
    build unpacks its bundled data (see the --add-data build flag in
    README.md) into a temp dir at sys._MEIPASS at startup, which
    doesn't exist when just running `python main.py` directly.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative


def _configure_logging() -> None:
    """Every tick's stability diff, dedup decision, and save/error
    outcome gets logged here. When something behaves differently on
    real screen content than in testing (a real Zoom/PowerPoint
    capture vs. synthetic test frames), this file is what tells us
    what actually happened without needing to reproduce it live.
    """
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    _configure_logging()
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    icon_path = _resource_path("assets/icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
