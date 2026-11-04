"""SlideCapture entry point."""

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from slidecapture.ui.main_window import MainWindow
from slidecapture.ui.theme import STYLESHEET

LOG_PATH = Path(__file__).parent / "slidecapture_debug.log"


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
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
