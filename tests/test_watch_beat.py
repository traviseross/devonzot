"""Tests for the watcher liveness heartbeat (ZotdavWatcher on_beat).

The heartbeat proves the inotify loop is turning. The two properties that matter:
  1. it fires even in a QUIET library (no blob events) — it must NOT be gated behind
     inotify events, or a stalled-but-quiet watcher would look dead;
  2. it is THROTTLED to ~beat_sec — not once per read_timeout loop iteration.
Uses the real inotify loop (Linux runtime), like test_zotdav_watcher's live test.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from zotdav_watcher import ZotdavWatcher


def test_heartbeat_fires_in_quiet_library_and_is_throttled(tmp_path):
    beats = []
    lock = threading.Lock()

    def on_beat():
        with lock:
            beats.append(time.monotonic())

    # beat every 0.3s; loop wakes every 0.05s. NO pairs are ever dropped (quiet library).
    w = ZotdavWatcher(tmp_path, on_key=lambda k: None, read_timeout_ms=50,
                      on_beat=on_beat, beat_sec=0)
    # beat_sec=0 would fire every loop; use a real throttle instead:
    w.beat_sec = 0.3

    t = threading.Thread(target=w.run, kwargs={"sweep_first": False}, daemon=True)
    t.start()
    time.sleep(0.95)  # ~3 beat windows
    w.stop(); t.join(timeout=5)

    with lock:
        n = len(beats)
        gaps = [b - a for a, b in zip(beats, beats[1:])]

    # fired despite zero inotify events (the quiet-library guarantee)
    assert n >= 1
    # throttled: ~0.95s / 0.3s ≈ 3 beats, NOT ~19 (one per 50ms loop)
    assert n <= 5
    # consecutive beats are spaced by roughly beat_sec, never the loop interval
    assert all(g >= 0.25 for g in gaps)
