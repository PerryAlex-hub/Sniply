from pathlib import Path

import numpy as np
import pytest

from slidecapture.storage.screenshot_store import save_screenshot
from slidecapture.storage.session import SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "session.db")
    yield s
    s.close()


def test_create_session_and_add_screenshots(store, tmp_path):
    session_id = store.create_session("Lecture 1", tmp_path / "Lecture 1")
    frame = np.full((10, 10, 3), 255, dtype=np.uint8)

    save_screenshot(store, session_id, tmp_path / "Lecture 1", frame)
    save_screenshot(store, session_id, tmp_path / "Lecture 1", frame)

    shots = store.list_screenshots(session_id)
    assert len(shots) == 2
    assert shots[0].sequence == 1
    assert shots[1].sequence == 2
    assert Path(shots[0].file_path).exists()


def test_delete_screenshot(store, tmp_path):
    session_id = store.create_session("Lecture 1", tmp_path / "Lecture 1")
    frame = np.full((10, 10, 3), 255, dtype=np.uint8)
    save_screenshot(store, session_id, tmp_path / "Lecture 1", frame)
    shots = store.list_screenshots(session_id)

    store.delete_screenshot(shots[0].id)

    assert store.list_screenshots(session_id) == []


def test_reorder_screenshots(store, tmp_path):
    session_id = store.create_session("Lecture 1", tmp_path / "Lecture 1")
    frame = np.full((10, 10, 3), 255, dtype=np.uint8)
    for _ in range(3):
        save_screenshot(store, session_id, tmp_path / "Lecture 1", frame)

    shots = store.list_screenshots(session_id)
    ids = [s.id for s in shots]
    reversed_ids = list(reversed(ids))

    store.reorder(session_id, reversed_ids)

    reordered = store.list_screenshots(session_id)
    assert [s.id for s in reordered] == reversed_ids


def test_rename_and_list_sessions(store, tmp_path):
    session_id = store.create_session("Original Name", tmp_path / "Original Name")
    store.rename_session(session_id, "Renamed")

    sessions = store.list_sessions()
    assert sessions[0][1] == "Renamed"
