"""Sniply entry point."""

import logging
import logging.handlers
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from sniply.paths import APP_DATA_DIR, LOG_PATH
from sniply.ui.main_window import MainWindow
from sniply.ui.theme import STYLESHEET
from sniply.__version__ import __version__

# Caps the log at ~6 MB total. It now lives in a permanent location
# rather than a temp directory that was wiped on exit, so for the
# first time it can actually accumulate -- and a DEBUG line per
# capture tick across a term of lectures would otherwise grow without
# any bound at all.
_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUPS = 2


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
    """Every tick's stability diff, dedup decision, capture-area
    tracking decision and save/error outcome gets logged here. When
    something behaves differently on real screen content than in
    testing (a real Zoom/PowerPoint capture vs. synthetic test
    frames), this file is what tells us what actually happened
    without needing to reproduce it live.

    Rotating rather than truncating on each launch: the previous
    behaviour threw the log away at startup, so reproducing a problem
    destroyed the record of the run that showed it -- the one run
    worth having. Runs stay distinguishable via the banner written
    below.
    """
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS, encoding="utf-8"
        )
    ]
    # A windowed PyInstaller build has no console, and sys.stdout is
    # None there. Handing that to StreamHandler produces a handler
    # that raises on every single log call, which logging then
    # swallows -- so the packaged app would quietly do nothing but
    # error-handle its own logging, all session long.
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))

    # Root at INFO, Sniply's own loggers at DEBUG. basicConfig's level
    # applies to the ROOT logger, so setting DEBUG there turns on
    # debug logging for every third-party package in the bundle too.
    # That was harmless while the log went nowhere, but now that it
    # goes to a real file it means a per-frame torrent from libraries
    # whose internals nobody reading this file cares about -- and it
    # would blow through the rotation limit fast enough to discard
    # the capture decisions this log exists for.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    logging.getLogger("sniply").setLevel(logging.DEBUG)

    # Marks where each run begins, since the file is no longer one
    # run per file. This is the first line anyone reading a bug
    # report needs: which build produced what follows.
    logging.getLogger(__name__).info("--- Sniply %s starting ---", __version__)


def main() -> int:
    _configure_logging()
    log = logging.getLogger("sniply.startup")

    # Startup milestones, logged at INFO. In a windowed build there is
    # no console and no traceback dialog, so a failure or a hang
    # between here and the first painted frame is otherwise completely
    # silent -- the log is the only instrument that exists.
    log.info("creating QApplication")
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    icon_path = _resource_path("assets/icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    log.info("constructing main window")
    window = MainWindow()
    log.info("showing main window")
    window.show()
    log.info("entering event loop")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
