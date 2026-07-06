"""Profile & path-resolution tests for the server migration (WS3 slice).

The service historically hardcoded macOS paths at module scope, so importing it on
Linux crashed when the logging FileHandler opened `/Users/travisross/DEVONzot/service.log`.
These tests lock in the fix: paths resolve from env / repo-root, the module imports on
any host, and the historical `mac` behavior is preserved as the default.

Path constants are frozen at import time, so each case runs the import in a fresh
subprocess with a controlled environment.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

# Snippet that imports the service and dumps the resolved module-level path constants.
_DUMP = textwrap.dedent(
    """
    import json, devonzot_service as d
    print("__RESULT__" + json.dumps({
        "profile": d.DEVONZOT_PROFILE,
        "root": str(d.DEVONZOT_PATH),
        "log": str(d.LOG_FILE),
        "state": str(d.STATE_FILE),
        "pid": str(d.PID_FILE),
        "zotero_storage": d.ZOTERO_STORAGE_PATH,
        "inbox": d.DEVONTHINK_INBOX_PATH,
    }))
    """
)


def _import_service(env_overrides):
    """Import devonzot_service in a clean subprocess; return the dumped constants dict."""
    env = {
        # Minimal required creds so the module-level os.environ[...] reads don't KeyError.
        "ZOTERO_API_KEY": "test-key",
        "ZOTERO_USER_ID": "0",
        # Keep the subprocess from finding a real ~/.env with a different profile.
        "PATH": "/usr/bin:/bin",
    }
    env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-c", _DUMP],
        cwd=str(SRC),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"import failed:\n{proc.stderr}"
    line = next(l for l in proc.stdout.splitlines() if l.startswith("__RESULT__"))
    return json.loads(line[len("__RESULT__"):])


def test_server_profile_imports_without_macos_paths(tmp_path):
    """On the server profile with a Linux root, the module imports and every runtime
    path resolves under that root — no /Users path is required or dereferenced."""
    root = tmp_path / "DEVONzot"
    res = _import_service({
        "DEVONZOT_PROFILE": "server",
        "DEVONZOT_PATH": str(root),
    })

    assert res["profile"] == "server"
    assert res["root"] == str(root)
    for key in ("log", "state", "pid"):
        assert res[key].startswith(str(root)), f"{key} not under root: {res[key]}"
        assert "/Users/" not in res[key]


def test_server_profile_creates_missing_log_dir(tmp_path):
    """Import must succeed even when the configured root does not exist yet: the logging
    setup creates it (this is exactly the FileNotFoundError-at-import that broke Linux)."""
    root = tmp_path / "does" / "not" / "exist" / "DEVONzot"
    assert not root.exists()
    res = _import_service({
        "DEVONZOT_PROFILE": "server",
        "DEVONZOT_PATH": str(root),
    })
    assert res["root"] == str(root)
    assert (root / "service.log").exists()


def test_log_file_env_override(tmp_path):
    """DEVONZOT_LOG_FILE overrides the log path independently of the root."""
    root = tmp_path / "DEVONzot"
    logdir = tmp_path / "logs"
    res = _import_service({
        "DEVONZOT_PROFILE": "server",
        "DEVONZOT_PATH": str(root),
        "DEVONZOT_LOG_FILE": str(logdir / "devonzot.log"),
    })
    assert res["log"] == str(logdir / "devonzot.log")
    assert (logdir / "devonzot.log").exists()


def test_mac_profile_preserves_macos_defaults(tmp_path):
    """Regression guard for the iMac fallback: the 'mac' profile keeps the macOS Zotero
    storage + DEVONthink Inbox defaults, so the historical local behavior is unchanged.
    (Set explicitly because a local server .env would otherwise supply the profile.)"""
    res = _import_service({
        "DEVONZOT_PROFILE": "mac",
        "DEVONZOT_PATH": str(tmp_path / "DEVONzot"),
    })
    assert res["profile"] == "mac"
    assert res["zotero_storage"] == "/Users/travisross/Zotero/storage"
    assert res["inbox"].startswith("/Users/travisross/")


def test_storage_path_env_override(tmp_path):
    """Server deployments can point storage at a Linux path via env."""
    res = _import_service({
        "DEVONZOT_PROFILE": "server",
        "DEVONZOT_PATH": str(tmp_path / "DEVONzot"),
        "ZOTERO_STORAGE_PATH": "/media/external/zotdav/data/zotero",
    })
    assert res["zotero_storage"] == "/media/external/zotdav/data/zotero"


# --- In-process: the dry-run analysis path must be Linux-safe on macOS-style paths ---

def test_service_instantiates_and_resolves_macos_paths_safely():
    """The dry-run migration analysis resolves each attachment's storage path. On Linux,
    a macOS-style Zotero storage path simply doesn't exist, so resolution must return
    None (treated as a missing/problematic path) rather than raising — this is what lets
    --dry-run run to completion on the server. Also proves __init__ is network-free and
    Linux-safe (no macOS path dereferenced at construction)."""
    import devonzot_service as d

    service = d.DEVONzotService()

    # storage:KEY:filename form (macOS storage root doesn't exist on Linux)
    stored = d.ZoteroAttachment(
        key="ABCD1234", parent_key="PARENT01", link_mode=0,
        content_type="application/pdf", path="storage:ABCD1234:paper.pdf",
        storage_hash=None, filename="paper.pdf",
    )
    # API v3 filename-only form (no explicit path)
    filename_only = d.ZoteroAttachment(
        key="EFGH5678", parent_key="PARENT02", link_mode=0,
        content_type="application/pdf", path=None, storage_hash=None,
        filename="report.pdf",
    )

    assert service._resolve_storage_path(stored) is None
    assert service._resolve_storage_path(filename_only) is None
