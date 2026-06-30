#!/usr/bin/env python3
"""DEVONzot health emitter — writes a single off-host health snapshot.

Run once per invocation (the LaunchDaemon's StartInterval re-runs it ~every 60s)
and exit. It writes /Users/Shared/devonzot/health.json, which the ross-server
fleet monitor pulls over SSH (`ssh iMac cat <path>`) on its 15s poll. See
config/com.devonzot.health.plist and docs/INSTALL_LAUNCHD.md.

Why a single-shot root daemon rather than a gui/501 LaunchAgent: it must keep
reporting after uid 501 logs out so the snapshot names the cause
(`gui_session_active:false`) instead of merely going stale. Run as root so it can
read the 0600 .env, `ps` any pid, and inspect `gui/501` regardless of who is
frontmost.

Robustness contract: every probe is isolated in its own try/except and degrades
to a safe default, so the snapshot — and especially `generated_at_epoch`, the
server's staleness key — is *always* written. A partial snapshot is far better
than a missing one.
"""

import os
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / '.env')

# DevonthinkMCP lives alongside this module in src/.
from devonthink_mcp import DevonthinkMCP

STATE_FILE = REPO / 'service_state.json'
PID_FILE = REPO / 'service.pid'
OUTPUT_DIR = Path('/Users/Shared/devonzot')
OUTPUT_FILE = OUTPUT_DIR / 'health.json'
GUI_UID = 501  # travisross — the GUI session DEVONzot's launchd job runs in.


def _gui_session_active() -> bool:
    """True if uid 501 has a bootstrapped GUI (Aqua) domain.

    This — not the frontmost console user — is the right signal: fast user
    switching can put another user in front while uid 501 stays logged in and
    DEVONzot keeps running. `launchctl print gui/501` succeeds iff that domain
    exists, which is exactly the precondition DEVONzot's gui/501 job needs
    (its absence is the `gui/501 error 125` failure).
    """
    r = subprocess.run(
        ['launchctl', 'print', f'gui/{GUI_UID}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
    )
    return r.returncode == 0


def _service_process_alive() -> bool:
    """True if service.pid points at a live devonzot_service.py process.

    The command-line check guards against PID reuse. Root sees all processes,
    so this works regardless of session.
    """
    pid = PID_FILE.read_text().strip()
    if not pid:
        return False
    r = subprocess.run(
        ['ps', '-p', pid, '-o', 'command='],
        capture_output=True, text=True, timeout=10,
    )
    return r.returncode == 0 and 'devonzot_service.py' in r.stdout


def _mcp_reachable() -> bool:
    """True if DEVONthink's MCP server answers. Short timeout so a hung/absent
    server doesn't blow the ~60s tick."""
    return DevonthinkMCP(timeout=5).is_running()


def _load_state() -> dict:
    return json.loads(STATE_FILE.read_text())


def _seconds_since_sync(state: dict):
    """now - last_sync. `last_sync` is naive *local* time (the service writes
    datetime.now().isoformat()), so compare against a local naive now."""
    last_sync = state.get('last_sync')
    if not last_sync:
        return None
    dt = datetime.fromisoformat(last_sync)
    return int((datetime.now() - dt).total_seconds())


def _probe(fn, default):
    """Run a probe, swallowing any failure into `default` so one bad signal
    never sinks the snapshot."""
    try:
        return fn()
    except Exception:
        return default


def build_snapshot() -> dict:
    state = _probe(_load_state, {})

    restart_count = state.get('restart_count', 0) if isinstance(state, dict) else 0
    status = 'degraded' if restart_count else 'ok'

    now = time.time()
    return {
        'service': 'devonzot',
        'generated_at_epoch': int(now),
        'generated_at': datetime.fromtimestamp(now, timezone.utc)
                                .strftime('%Y-%m-%dT%H:%M:%SZ'),
        'status': status,
        'gui_session_active': _probe(_gui_session_active, False),
        'service_process_alive': _probe(_service_process_alive, False),
        'mcp_reachable': _probe(_mcp_reachable, False),
        'seconds_since_sync': _probe(lambda: _seconds_since_sync(state), None),
        'restart_count': restart_count,
        'pending_deletes': len(state.get('pending_deletes', [])) if isinstance(state, dict) else 0,
        'pending_downloads': len(state.get('pending_downloads', [])) if isinstance(state, dict) else 0,
        'last_library_version': state.get('last_library_version') if isinstance(state, dict) else None,
    }


def write_snapshot(snapshot: dict) -> None:
    """Atomically write the snapshot world-readable (tmp + os.replace), matching
    the service's own state-save pattern (devonzot_service.py:_save_state)."""
    OUTPUT_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    tmp = OUTPUT_FILE.with_suffix('.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(snapshot, f, indent=2)
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUTPUT_FILE)


def main() -> None:
    write_snapshot(build_snapshot())


if __name__ == '__main__':
    main()
