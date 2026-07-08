#!/usr/bin/env python3
"""DEVONzot health emitter — SERVER (Linux/systemd) profile.

The server equivalent of src/health_emitter.py (which targets the iMac's launchd job).
Run once per invocation (devonzot-health.timer re-runs it ~every 60s) and exit. It
writes REPO/var/health.json, which the ross-server fleet monitor reads locally
(via: ssh host: localhost — see ops/fleet-rules.yaml) on its poll.

The server's health model is entirely different from the iMac's — there is no GUI
session, no launchd PID file, and no LOCAL DEVONthink. So the probes here are:
  - service_active         : `systemctl --user is-active devonzot.service` (the watcher)
  - mcp_reachable          : the iMac's DEVONthink MCP, reached over TLS from the server
  - seconds_since_reconcile: age of state.last_reconcile (the 2h backstop's heartbeat)
  - seconds_since_watch_beat: age of REPO/var/watch.beat (the inotify loop's heartbeat,
                              written by the watcher itself — proves the loop is turning)

Robustness contract (shared with the mac emitter): every probe is isolated in its own
try/except and degrades to a safe default, so `generated_at_epoch` — the server's
staleness key — is *always* written. A partial snapshot beats a missing one.
"""

import os
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / '.env')

from devonthink_mcp import DevonthinkMCP
from health_common import probe, atomic_write_json

STATE_FILE = REPO / 'service_state.json'
OUTPUT_FILE = REPO / 'var' / 'health.json'
BEAT_FILE = REPO / 'var' / 'watch.beat'
WATCHER_UNIT = 'devonzot.service'


def _service_active() -> bool:
    """True iff the systemd USER watcher unit reports 'active'. This — not a PID file —
    is the authoritative liveness signal on the server."""
    r = subprocess.run(
        ['systemctl', '--user', 'is-active', WATCHER_UNIT],
        capture_output=True, text=True, timeout=10,
    )
    return r.returncode == 0 and r.stdout.strip() == 'active'


def _mcp_reachable() -> bool:
    """True if the (remote, iMac) DEVONthink MCP answers over TLS.

    Build the client from the first configured endpoint's env — a bare DevonthinkMCP()
    would default to localhost:8420 and report MCP permanently down on this server.
    Mirrors the endpoint parsing in devonzot_service.py:_parse_mcp_endpoints without
    importing that (heavy, side-effectful) module.
    """
    labels = [l.strip() for l in os.environ.get("DEVONTHINK_MCP_ENDPOINTS", "").split(",") if l.strip()]
    if labels:
        u = labels[0].upper()
        url = os.environ.get(f"DEVONTHINK_MCP_{u}_URL")
        token = os.environ.get(f"DEVONTHINK_MCP_{u}_TOKEN", "")
        cacert = os.environ.get(f"DEVONTHINK_MCP_{u}_CACERT")
    else:  # legacy single-endpoint fallback
        url = os.environ.get("DEVONTHINK_MCP_URL")
        token = os.environ.get("DEVONTHINK_MCP_TOKEN", "")
        cacert = os.environ.get("DEVONTHINK_MCP_CACERT")
    return DevonthinkMCP(url=url, token=token, cacert=cacert, timeout=5).is_running()


def _load_state() -> dict:
    import json
    return json.loads(STATE_FILE.read_text())


def _seconds_since_reconcile(state: dict):
    """Seconds since the reconcile backstop last completed. last_reconcile is written
    naive *local* (datetime.now().isoformat()), so compare against a local now — NOT
    epoch. (Same discipline as the mac emitter's _seconds_since_sync.)"""
    ts = state.get('last_reconcile')
    if not ts:
        return None
    return int((datetime.now() - datetime.fromisoformat(ts)).total_seconds())


def _seconds_since_watch_beat():
    """Seconds since the watcher last stamped var/watch.beat (its inotify-loop heartbeat).
    The beat carries an epoch int in its body, so this is a plain epoch delta."""
    import json
    data = json.loads(BEAT_FILE.read_text())
    return int(time.time() - float(data['beat_epoch']))


def build_snapshot() -> dict:
    state = probe(_load_state, {})
    if not isinstance(state, dict):
        state = {}

    restart_count = state.get('restart_count', 0)
    status = 'degraded' if restart_count else 'ok'

    now = time.time()
    return {
        'service': 'devonzot',
        'generated_at_epoch': int(now),
        'generated_at': datetime.fromtimestamp(now, timezone.utc)
                                .strftime('%Y-%m-%dT%H:%M:%SZ'),
        'status': status,
        'service_active': probe(_service_active, False),
        'mcp_reachable': probe(_mcp_reachable, False),
        'seconds_since_reconcile': probe(lambda: _seconds_since_reconcile(state), None),
        'seconds_since_watch_beat': probe(_seconds_since_watch_beat, None),
        'restart_count': restart_count,
        'pending_deletes': len(state.get('pending_deletes', [])),
        'last_library_version': state.get('last_library_version'),
    }


def main() -> None:
    atomic_write_json(OUTPUT_FILE, build_snapshot())


if __name__ == '__main__':
    main()
