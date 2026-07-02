"""Tests for the off-host health emitter's metric derivation.

Focus: seconds_since_sync must track the poll HEARTBEAT (last_zotero_check), which
advances on every completed poll incl. no-ops — so a quiet library (no Zotero
traffic) does not read as "stalled". Falls back to last_sync for pre-heartbeat
state files. Pure/offline.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import health_emitter


class TestSecondsSinceSync:
    def test_prefers_heartbeat_over_stale_last_sync(self):
        """The quiet-library case: last_sync is 13h old (no applied change), but a
        fresh heartbeat means the loop is polling — metric must read fresh."""
        heartbeat = (datetime.now() - timedelta(seconds=30)).isoformat()
        stale_sync = (datetime.now() - timedelta(hours=13)).isoformat()
        secs = health_emitter._seconds_since_sync(
            {'last_zotero_check': heartbeat, 'last_sync': stale_sync}
        )
        assert secs is not None and secs < 120

    def test_falls_back_to_last_sync_when_no_heartbeat(self):
        """Pre-upgrade state files have no last_zotero_check yet."""
        last_sync = (datetime.now() - timedelta(hours=2)).isoformat()
        secs = health_emitter._seconds_since_sync({'last_sync': last_sync})
        assert 7000 < secs < 7400

    def test_none_when_both_absent(self):
        assert health_emitter._seconds_since_sync({}) is None
