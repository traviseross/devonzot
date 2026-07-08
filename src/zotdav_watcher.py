"""inotify watcher over the zotdav store — the migration fast path.

Zotero writes {KEY}.zip then {KEY}.prop; the `.prop` is the write-completion
sentinel, so a CLOSE_WRITE/MOVED_TO on `*.prop` (with the paired `.zip` present)
means "a new attachment blob is ready" — emit its KEY. On start we also sweep the
directory once to drain any pairs that predate the watcher (the standing backlog).

The inotify dependency is imported lazily inside run() so this module imports on any
OS (mac tests, CI); only run() requires Linux. Dedup of already-migrated keys is the
consumer's job (ServiceState.processed_attachment_keys), not the watcher's.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from zotdav_blob import list_pending_keys, key_for

logger = logging.getLogger(__name__)


class ZotdavWatcher:
    def __init__(self, zotdav_dir, on_key: Callable[[str], None], read_timeout_ms: int = 1000,
                 on_interval: Optional[Callable[[], None]] = None, interval_sec: int = 1800,
                 on_beat: Optional[Callable[[], None]] = None, beat_sec: int = 60):
        self.zotdav_dir = Path(zotdav_dir)
        self.on_key = on_key
        self.read_timeout_ms = read_timeout_ms
        # Liveness heartbeat: the inotify loop wakes every read_timeout_ms even with zero
        # events, so calling on_beat on a throttle proves the loop is turning regardless
        # of Zotero traffic — the health emitter reads it to detect a wedged watcher. It
        # writes a SEPARATE file (not service_state.json) to avoid the two-writer race.
        self.on_beat = on_beat
        self.beat_sec = beat_sec
        # Periodic re-sweep hook: inotify only sees NEW completions, so a blob the
        # consumer skipped (e.g. endpoint blip -> skipped_no_endpoint) or that landed
        # while the process was down never re-fires. Calling on_interval every
        # interval_sec between reads lets the watcher self-heal those misses without a
        # second writer — the consumer's own idempotency guard makes re-sweeps a no-op
        # for already-migrated keys.
        self.on_interval = on_interval
        self.interval_sec = interval_sec
        self._stop = threading.Event()

    # --- pure logic (unit-testable without inotify) ---

    def key_if_ready(self, name: str) -> Optional[str]:
        """Given an inotify event filename, return the KEY iff it's a `.prop` whose
        paired `.zip` exists; else None. (The zip is written before the prop, so its
        presence confirms a complete pair.)"""
        if not name or not name.endswith(".prop"):
            return None
        key = key_for(name)
        if (self.zotdav_dir / f"{key}.zip").exists():
            return key
        logger.debug(f"zotdav: {name} has no paired .zip yet; ignoring")
        return None

    def sweep(self) -> int:
        """Emit every complete pair already present (oldest first). Returns the count —
        this is what drains the standing backlog on startup."""
        keys = list_pending_keys(self.zotdav_dir)
        for key in keys:
            self.on_key(key)
        if keys:
            logger.info(f"zotdav startup sweep: emitted {len(keys)} pending key(s)")
        return len(keys)

    # --- inotify loop (Linux-only; lazy import) ---

    def run(self, sweep_first: bool = True) -> None:
        """Blocking watch loop until stop(). Sweeps first (default), then watches for
        new `.prop` completions. Safe to run in a background thread."""
        from inotify_simple import INotify, flags  # lazy: Linux-only

        if not self.zotdav_dir.is_dir():
            raise FileNotFoundError(f"zotdav dir not found: {self.zotdav_dir}")

        if sweep_first:
            self.sweep()

        inotify = INotify()
        last_resweep = time.monotonic()
        last_beat = time.monotonic()
        try:
            inotify.add_watch(str(self.zotdav_dir), flags.CLOSE_WRITE | flags.MOVED_TO)
            logger.info(f"zotdav watcher armed on {self.zotdav_dir}")
            if self.on_interval:
                logger.info(f"zotdav periodic re-sweep every {self.interval_sec}s (self-heal misses)")
            if self.on_beat:
                logger.info(f"zotdav liveness heartbeat every {self.beat_sec}s")
            while not self._stop.is_set():
                for event in inotify.read(timeout=self.read_timeout_ms):
                    key = self.key_if_ready(event.name)
                    if key:
                        logger.info(f"zotdav: new blob ready -> {key}")
                        try:
                            self.on_key(key)
                        except Exception as e:  # never let a consumer error kill the loop
                            logger.error(f"zotdav on_key({key}) failed: {e}")
                # Liveness heartbeat — OUTSIDE the event loop so a quiet library still beats.
                if self.on_beat and (time.monotonic() - last_beat) >= self.beat_sec:
                    last_beat = time.monotonic()
                    try:
                        self.on_beat()
                    except Exception as e:  # a heartbeat failure must not kill the loop
                        logger.error(f"zotdav heartbeat failed: {e}")
                # Periodic re-sweep between reads (drains any skipped/missed blobs).
                if self.on_interval and (time.monotonic() - last_resweep) >= self.interval_sec:
                    last_resweep = time.monotonic()
                    logger.info("zotdav periodic re-sweep: retrying any skipped/missed blobs")
                    try:
                        self.on_interval()
                    except Exception as e:  # a re-sweep failure must not kill the loop
                        logger.error(f"zotdav periodic re-sweep failed: {e}")
        finally:
            inotify.close()
            logger.info("zotdav watcher stopped")

    def stop(self) -> None:
        self._stop.set()
