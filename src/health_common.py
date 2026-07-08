"""Shared helpers for the DEVONzot health emitters (mac + server).

Kept deliberately tiny: the two emitters (src/health_emitter.py for the iMac launchd
job, src/health_emitter_server.py for the Linux systemd job) share almost nothing —
different paths, probes, and run models — but they DO share the fault-isolation probe
wrapper and the atomic snapshot write. Extracting just those two here prevents the
emitters from silently diverging on the robustness contract.
"""

import os
import json
from pathlib import Path
from typing import Any, Callable


def probe(fn: Callable[[], Any], default: Any) -> Any:
    """Run a probe, swallowing any failure into `default` so one bad signal never
    sinks the snapshot (esp. `generated_at_epoch`, the monitor's staleness key)."""
    try:
        return fn()
    except Exception:
        return default


def atomic_write_json(path: Path, obj: dict, mode: int = 0o644) -> None:
    """Atomically write `obj` as JSON to `path` (tmp + os.replace), world-readable by
    default — matches the service's own state-save pattern
    (devonzot_service.py:_save_state). Creates the parent dir if needed."""
    path = Path(path)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w') as f:
        json.dump(obj, f, indent=2)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
