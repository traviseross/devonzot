"""Tests for the zotdav inotify watcher.

key_if_ready() and sweep() are pure and run anywhere; the live inotify loop test is
Linux-only (the server runtime) and drives a real drop into a watched dir.
"""

import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from zotdav_watcher import ZotdavWatcher


def make_pair(directory, key, data=b"pdf"):
    directory = Path(directory)
    with zipfile.ZipFile(directory / f"{key}.zip", "w") as zf:
        zf.writestr("paper.pdf", data)
    (directory / f"{key}.prop").write_text(
        '<properties version="1"><mtime>1</mtime><hash>abc</hash></properties>'
    )


def test_key_if_ready_requires_paired_zip(tmp_path):
    w = ZotdavWatcher(tmp_path, on_key=lambda k: None)
    # prop present but no zip -> not ready
    (tmp_path / "K.prop").write_text("<properties/>")
    assert w.key_if_ready("K.prop") is None
    # add the zip -> ready
    with zipfile.ZipFile(tmp_path / "K.zip", "w") as zf:
        zf.writestr("f.pdf", b"x")
    assert w.key_if_ready("K.prop") == "K"
    # non-prop events ignored
    assert w.key_if_ready("K.zip") is None
    assert w.key_if_ready("") is None


def test_sweep_emits_all_pending_oldest_first(tmp_path):
    make_pair(tmp_path, "A")
    make_pair(tmp_path, "B")
    import os
    os.utime(tmp_path / "A.prop", (1000, 1000))
    os.utime(tmp_path / "B.prop", (2000, 2000))
    seen = []
    w = ZotdavWatcher(tmp_path, on_key=seen.append)
    n = w.sweep()
    assert n == 2 and seen == ["A", "B"]


def test_live_inotify_emits_on_new_pair(tmp_path):
    """Drop a pair into a watched dir and confirm the watcher emits its KEY."""
    seen = []
    lock = threading.Lock()

    def on_key(k):
        with lock:
            seen.append(k)

    w = ZotdavWatcher(tmp_path, on_key=on_key, read_timeout_ms=100)
    t = threading.Thread(target=w.run, kwargs={"sweep_first": False}, daemon=True)
    t.start()
    time.sleep(0.4)  # let the watch arm

    # zip first, then prop (Zotero's order; prop is the sentinel that triggers)
    with zipfile.ZipFile(tmp_path / "LIVE1.zip", "w") as zf:
        zf.writestr("paper.pdf", b"bytes")
    (tmp_path / "LIVE1.prop").write_text("<properties/>")

    deadline = time.time() + 8
    while time.time() < deadline:
        with lock:
            if "LIVE1" in seen:
                break
        time.sleep(0.1)
    w.stop(); t.join(timeout=5)
    assert "LIVE1" in seen


def test_run_missing_dir_raises(tmp_path):
    w = ZotdavWatcher(tmp_path / "nope", on_key=lambda k: None)
    with pytest.raises(FileNotFoundError):
        w.run()
