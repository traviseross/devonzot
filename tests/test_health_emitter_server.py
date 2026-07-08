"""Tests for the SERVER health emitter's probe derivation (src/health_emitter_server.py).

Server-specific correctness the plan flagged as easy to get wrong:
  - seconds_since_reconcile is naive-LOCAL math (last_reconcile is written naive-local),
    not epoch — a tz mixup yields a constant multi-hour offset that false-fires.
  - _mcp_reachable must build the client from the configured endpoint env, not a bare
    DevonthinkMCP() (which would hit localhost and report MCP permanently down).
  - build_snapshot must always emit generated_at_epoch even if every probe raises.
Pure/offline: mocks at the subprocess / MCP boundary.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import health_emitter_server as hes


# ── seconds_since_reconcile: naive-local, guards the tz bug ──────────────────

class TestSecondsSinceReconcile:
    def test_fresh_reconcile_reads_recent(self):
        ts = (datetime.now() - timedelta(seconds=30)).isoformat()
        secs = hes._seconds_since_reconcile({'last_reconcile': ts})
        assert secs is not None and secs < 120

    def test_stale_reconcile(self):
        ts = (datetime.now() - timedelta(hours=3)).isoformat()
        secs = hes._seconds_since_reconcile({'last_reconcile': ts})
        assert 10600 < secs < 11000  # ~3h, proving local-vs-local (no tz offset)

    def test_none_when_absent(self):
        assert hes._seconds_since_reconcile({}) is None


# ── service_active: parse `systemctl --user is-active` ───────────────────────

class TestServiceActive:
    def _run(self, monkeypatch, rc, out):
        monkeypatch.setattr(hes.subprocess, 'run',
                            lambda *a, **k: Mock(returncode=rc, stdout=out))

    def test_active(self, monkeypatch):
        self._run(monkeypatch, 0, 'active\n')
        assert hes._service_active() is True

    def test_inactive(self, monkeypatch):
        self._run(monkeypatch, 3, 'inactive\n')
        assert hes._service_active() is False

    def test_failed_nonzero_rc(self, monkeypatch):
        self._run(monkeypatch, 1, 'failed\n')
        assert hes._service_active() is False


# ── mcp_reachable: built from the configured endpoint env ────────────────────

class TestMcpReachable:
    def test_builds_client_from_endpoint_env(self, monkeypatch):
        monkeypatch.setenv('DEVONTHINK_MCP_ENDPOINTS', 'imac')
        monkeypatch.setenv('DEVONTHINK_MCP_IMAC_URL', 'https://imac.example:8420')
        monkeypatch.setenv('DEVONTHINK_MCP_IMAC_TOKEN', 'tok')
        monkeypatch.setenv('DEVONTHINK_MCP_IMAC_CACERT', '/tmp/ca.pem')
        captured = {}

        def fake_client(url=None, token=None, cacert=None, timeout=None):
            captured.update(url=url, token=token, cacert=cacert)
            return Mock(is_running=Mock(return_value=True))
        monkeypatch.setattr(hes, 'DevonthinkMCP', fake_client)

        assert hes._mcp_reachable() is True
        # the crucial assertion: NOT a bare localhost client
        assert captured['url'] == 'https://imac.example:8420'
        assert captured['token'] == 'tok' and captured['cacert'] == '/tmp/ca.pem'


# ── build_snapshot robustness contract ───────────────────────────────────────

def test_build_snapshot_always_has_epoch_even_if_probes_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('probe down')
    monkeypatch.setattr(hes, '_load_state', boom)
    monkeypatch.setattr(hes, '_service_active', boom)
    monkeypatch.setattr(hes, '_mcp_reachable', boom)
    monkeypatch.setattr(hes, '_seconds_since_watch_beat', boom)

    snap = hes.build_snapshot()

    assert isinstance(snap['generated_at_epoch'], int) and snap['generated_at_epoch'] > 0
    assert snap['service'] == 'devonzot'
    # every raising probe degraded to its safe default
    assert snap['service_active'] is False
    assert snap['mcp_reachable'] is False
    assert snap['seconds_since_watch_beat'] is None
    assert snap['status'] == 'ok'  # empty state -> restart_count 0
