"""Writes a saved screenshot to disk and records it in the session
database. Kept as one small function so the capture worker has a
single call to make when the detection pipeline says SAVE, and so
"autosave immediately" is trivially true -- there is no in-memory
buffering stage between a SAVE decision and bytes on disk.
"""

from pathlib import Path

import cv2
import numpy as np

from sniply.storage.session import SessionStore


def save_screenshot(
    store: SessionStore, session_id: int, save_dir: Path, frame: np.ndarray
) -> Path:
    """Write `frame` (BGR, full resolution) as a PNG into `save_dir`
    and record it against `session_id`. Returns the written path.

    Filenames are zero-padded by the DB-assigned sequence number so
    files sort correctly in a plain file browser too, not just in the
    app's own gallery.
    """
    save_dir.mkdir(parents=True, exist_ok=True)

    # Reserve the sequence number first so the filename and the DB
    # row agree, even though the file write happens before we know
    # the screenshot's own row id.
    next_seq = store.next_sequence(session_id)
    file_path = save_dir / f"slide_{next_seq:04d}.png"

    ok = cv2.imwrite(str(file_path), frame)
    if not ok:
        raise OSError(f"Failed to write screenshot to {file_path}")

    store.add_screenshot(session_id, file_path, sequence=next_seq)
    return file_path
