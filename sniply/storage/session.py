"""Session persistence: tracks a lecture session's metadata and the
ordered list of its saved screenshots, backed by SQLite.

SQLite is used instead of hand-rolled JSON because the gallery needs
to reorder, delete, and select subsets of screenshots -- all of which
are just SQL UPDATE/DELETE/SELECT statements here, instead of
re-serializing a whole JSON document on every small change.
"""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    save_dir TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screenshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    file_path TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    created_at REAL NOT NULL,
    selected_for_export INTEGER NOT NULL DEFAULT 1
);
"""


@dataclass
class Screenshot:
    id: int
    file_path: str
    sequence: int
    created_at: float
    selected_for_export: bool


class SessionStore:
    """One SessionStore wraps one SQLite database file. The database
    lives alongside captured screenshots in the session's own folder,
    so a session directory is fully self-contained and portable.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def create_session(self, name: str, save_dir: Path) -> int:
        cur = self._conn.execute(
            "INSERT INTO sessions (name, created_at, save_dir) VALUES (?, ?, ?)",
            (name, time.time(), str(save_dir)),
        )
        self._conn.commit()
        return cur.lastrowid

    def rename_session(self, session_id: int, new_name: str) -> None:
        self._conn.execute("UPDATE sessions SET name = ? WHERE id = ?", (new_name, session_id))
        self._conn.commit()

    def add_screenshot(self, session_id: int, file_path: Path, sequence: int | None = None) -> int:
        """Record a newly saved screenshot. Called immediately after
        writing the file to disk -- the write-then-record order means
        a crash can at worst leave an orphan file, never a dangling
        database row pointing at a file that doesn't exist.

        Pass `sequence` explicitly when the caller already reserved
        it (e.g. to name the file on disk) so the filename and the DB
        row always agree on the same number.
        """
        if sequence is None:
            sequence = self.next_sequence(session_id)
        cur = self._conn.execute(
            "INSERT INTO screenshots (session_id, file_path, sequence, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, str(file_path), sequence, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid

    def next_sequence(self, session_id: int) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM screenshots WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] + 1

    def list_screenshots(self, session_id: int) -> list[Screenshot]:
        rows = self._conn.execute(
            "SELECT id, file_path, sequence, created_at, selected_for_export "
            "FROM screenshots WHERE session_id = ? ORDER BY sequence ASC",
            (session_id,),
        ).fetchall()
        return [
            Screenshot(id=r[0], file_path=r[1], sequence=r[2], created_at=r[3], selected_for_export=bool(r[4]))
            for r in rows
        ]

    def delete_screenshot(self, screenshot_id: int) -> None:
        self._conn.execute("DELETE FROM screenshots WHERE id = ?", (screenshot_id,))
        self._conn.commit()

    def set_selected_for_export(self, screenshot_id: int, selected: bool) -> None:
        self._conn.execute(
            "UPDATE screenshots SET selected_for_export = ? WHERE id = ?",
            (int(selected), screenshot_id),
        )
        self._conn.commit()

    def reorder(self, session_id: int, screenshot_ids_in_order: list[int]) -> None:
        """Rewrite the `sequence` column to match the given order, as
        produced by the gallery's drag-to-reorder UI."""
        for new_seq, screenshot_id in enumerate(screenshot_ids_in_order, start=1):
            self._conn.execute(
                "UPDATE screenshots SET sequence = ? WHERE id = ? AND session_id = ?",
                (new_seq, screenshot_id, session_id),
            )
        self._conn.commit()

    def list_sessions(self) -> list[tuple[int, str, str]]:
        """Returns (id, name, save_dir) for every known session, most
        recent first, for the "reopen previous session" UI."""
        rows = self._conn.execute(
            "SELECT id, name, save_dir FROM sessions ORDER BY created_at DESC"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def close(self) -> None:
        self._conn.close()
