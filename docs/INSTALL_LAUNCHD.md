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

## Health emitter (`com.devonzot.health`)

A second launchd job reports DEVONzot's health to the ross-server **Server
dashboard**. Because DEVONzot is a macOS service (not a server container), the
fleet monitor can't poll it — instead it **pulls** a snapshot over SSH
(`ssh iMac cat /Users/Shared/devonzot/health.json`) on its 15s cycle. Our only job
is to keep that file fresh.

- **What:** `src/health_emitter.py` runs once per fire, reads the live signals
  (`service_state.json`, `service.pid`, `DevonthinkMCP.is_running()`, and
  `launchctl print gui/501`), and atomically writes
  `/Users/Shared/devonzot/health.json` (world-readable). `StartInterval` re-runs
  it every 60s.
- **Why a root LaunchDaemon** (not a `gui/501` agent): it must keep reporting after
  uid 501 logs out, so the dashboard can name the cause
  (`gui_session_active:false` → "log back in on the iMac") instead of just showing
  a generic "stale". Root also lets it read the `0600` `.env`, `ps` the service pid,
  and inspect the `gui/501` domain regardless of who is frontmost (fast user
  switching does **not** take DEVONzot down — only a full uid-501 logout does).
- **Install:** handled by `scripts/setup.sh --deploy` (needs `sudo` —
  installs to `/Library/LaunchDaemons/com.devonzot.health.plist` and bootstraps
  the `system` domain).
- **Operate:**
  ```bash
  sudo launchctl print system/com.devonzot.health      # status
  sudo launchctl kickstart -k system/com.devonzot.health   # run now
  cat /Users/Shared/devonzot/health.json                # latest snapshot
  ```
- **Go live:** once the file is being written, ping the HA team to re-enable the
  `devonzot:` block in `fleet.yaml` and restart `fleet-monitor`. DEVONzot then
  appears on the Server dashboard within ~30s.
- **Caveat:** if the iMac system-sleeps, the snapshot goes stale → "down". That's
  accurate (DEVONzot isn't syncing while asleep) as long as the iMac is always-on.

---

For full installation and usage, see `docs/README.md`.
