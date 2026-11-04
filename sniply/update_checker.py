"""Checks GitHub for a newer Sniply release than the one currently
running, so users find out without having to remember to check the
releases page themselves.

The actual network call runs on a background QThread (see
start_update_check) since it's a blocking HTTP request and must never
stall the UI on startup -- a slow or failed check (no internet,
GitHub unreachable, API rate limit) should never delay or break
opening the app normally. Uses urllib (standard library) rather than
requests, so this adds no new dependency.
"""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal

from sniply.__version__ import __version__

logger = logging.getLogger(__name__)

GITHUB_REPO = "PerryAlex-hub/Sniply"
_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class UpdateInfo:
    version: str                # e.g. "0.2.0"
    release_url: str            # the GitHub release page, for "see what's new"
    download_url: str | None    # direct .exe asset URL, if the release has one attached


def _parse_version(tag: str) -> tuple:
    """'v0.2.1' / '0.2.1' -> (0, 2, 1). Non-numeric parts are dropped
    rather than raising, so an unusually-named tag can't crash the
    check -- worst case it just fails to compare meaningfully, which
    means no update gets offered rather than the app breaking.
    """
    cleaned = tag.lstrip("vV")
    parts = []
    for piece in cleaned.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def is_newer(remote_version: str, local_version: str = __version__) -> bool:
    return _parse_version(remote_version) > _parse_version(local_version)


def fetch_latest_release() -> UpdateInfo | None:
    """Blocking network call -- always run this off the UI thread
    (see UpdateCheckWorker below). Returns None on any failure rather
    than raising: a failed update check is never an application
    error, just a missed opportunity to notify.
    """
    request = urllib.request.Request(
        _API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Sniply-update-check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.info("update check failed (non-fatal): %s", exc)
        return None

    tag = data.get("tag_name", "")
    if not tag:
        return None

    download_url = None
    for asset in data.get("assets", []):
        if asset.get("name", "").lower().endswith(".exe"):
            download_url = asset.get("browser_download_url")
            break

    return UpdateInfo(
        version=tag.lstrip("vV"),
        release_url=data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases",
        download_url=download_url,
    )


class UpdateCheckWorker(QObject):
    """Runs fetch_latest_release() on a background thread and reports
    back only if a genuinely newer version was found (never fires
    update_available for "up to date" or "check failed")."""

    update_available = Signal(object)  # UpdateInfo
    finished = Signal()

    def run(self) -> None:
        info = fetch_latest_release()
        if info is not None and is_newer(info.version):
            self.update_available.emit(info)
        self.finished.emit()


def start_update_check(on_update_available) -> QThread:
    """Starts a background check; `on_update_available` is called
    with an UpdateInfo if (and only if) a newer release is found.
    Returns the QThread -- the CALLER must keep a reference to it
    (e.g. as self._update_thread) for as long as the app runs, or
    Python may garbage-collect the thread object mid-check.
    """
    thread = QThread()
    worker = UpdateCheckWorker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.update_available.connect(on_update_available)
    worker.finished.connect(thread.quit)
    thread.finished.connect(thread.deleteLater)
    thread._worker_ref = worker  # keep the worker alive alongside the thread
    thread.start()
    return thread
