# DEVONzot Launchd Setup Guide

DEVONzot runs as a **single** launchd agent — `com.devonzot.service` — which runs
`src/devonzot_service.py --service` (a perpetual streaming service).

> **Note:** the older `com.devonzot.addnew` job (`devonzot_add_new.py --loop15`)
> is **retired** (moved to `ARCHIVE/`). Its job — converting `linked_file`
> ZotFile symlinks into `x-devonthink-item://` links — is fully covered by the
> main service's Phase 1B/Phase 2. Do not reinstall it.

## Prerequisites

- **DEVONthink 4** running, with its **MCP server enabled** (DEVONthink →
  Settings → AI → MCP).
- `.env` present and configured, including:
  - `ZOTERO_API_KEY`, `ZOTERO_USER_ID`
  - `DEVONZOT_USE_MCP=true`
  - `DEVONTHINK_MCP_TOKEN=<bearer token from DEVONthink's MCP settings>`
  - `DEVONTHINK_MCP_URL=http://localhost:8420` (default)
- A working virtualenv at `venv/` with dependencies installed.

## Installation

1. **Copy the plist** `com.devonzot.service.plist` to
   `~/Library/LaunchAgents/com.devonzot.service.plist`.

2. **Edit paths** in the plist for your system:
   - Python interpreter: `/Users/<you>/DEVONzot/venv/bin/python`
   - Script: `/Users/<you>/DEVONzot/src/devonzot_service.py` (note the `src/` prefix), argument `--service`

3. **Load the job** (macOS 12+):
   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.devonzot.service.plist
   ```

## Operating the service

```bash
# status
launchctl list | grep devonzot

# restart after a code change (reloads new code)
launchctl kickstart -k gui/$(id -u)/com.devonzot.service

# stop / unload
launchctl bootout gui/$(id -u)/com.devonzot.service
```

## Notes

- DEVONthink 4 must be running (with its MCP server enabled) when the job fires.
- The service waits `DEVONZOT_STARTUP_DELAY` seconds (default 120) before its first
  cycle, then logs `DEVONthink control backend: MCP`.
- Runtime output goes to `service.log` in the DEVONzot directory.
- Verify plist syntax: `plutil -lint ~/Library/LaunchAgents/com.devonzot.service.plist`

---

For full installation and usage, see `docs/README.md`.
