# DEVONzot Server Migration — Implementation Plan

> **For agentic workers:** This is a top-level *migration* plan spanning multiple
> subsystems across two machines (Linux server + macOS). Each **Workstream** below
> is independently shippable and should get its own fine-grained TDD decomposition
> (via superpowers:writing-plans → subagent-driven-development) **at execution time
> on the server**, where MCP tool signatures, TLS specifics, and systemd details are
> live-verifiable. Do not fabricate those details from this plan — verify them first.

**Goal:** Move the DEVONzot orchestrator off the always-logged-in iMac and onto the
always-on Linux server (`~/DEVONzot`, systemd, not docker), driving DEVONthink 4 over
the network via its MCP server and detecting new work by watching the local zotdav
WebDAV store instead of polling the entire Zotero API.

**Architecture:** The server hosts the zotdav WebDAV share, so it gains a *local,
pre-filtered work queue*: each new attachment appears as `{KEY}.zip` + `{KEY}.prop`
under `/media/external/zotdav/data/zotero/`. An inotify watcher on the `.prop`
(Zotero's write-completion sentinel) becomes the fast path for the time-sensitive
job — get the file into DEVONthink and write the `x-devonthink-item://UUID` link back
to Zotero so the record is usable almost immediately. DEVONthink stays macOS-bound;
the server reaches it by selecting a live MCP endpoint in strict priority
(iMac → elif MBP → else wait), `scp`-ing the file to that Mac's `/private/tmp`, and
calling `import_file`. The single shared, synced DEVONthink database means the minted
UUID resolves on every device regardless of which Mac imported. The slow full-library
API scan is retained but demoted to a low-frequency **backstop reconcile** for the
cases the watcher can't see (metadata-only edits, linked-file items).

**Tech Stack:** Python 3.13 + asyncio; Zotero Web API v3; DEVONthink 4 native MCP
(JSON-RPC over HTTP, no LLM); Linux inotify (`watchdog` or `inotify_simple`); `ssh`/`scp`
over the existing passwordless mesh; systemd user service; TLS for MCP LAN exposure.

## Global Constraints

- **No LLM in the daemon.** MCP tools are called as deterministic JSON-RPC/HTTP RPCs.
- **DEVONthink is the single source of truth for the DT database**, synced across
  iMac/MBP/iOS via the server-hosted WebDAV DT sync store. UUIDs are preserved across
  sync — a link minted on any Mac resolves everywhere (after sync settles).
- **Never delete a zotdav `{KEY}.zip`/`.prop` blob directly as a mechanism.** Deleting
  the Zotero *attachment item* via the Web API is what triggers Zotero's own purge of
  the orphaned blob. Direct `rm` is permitted only as post-deletion hygiene, *after*
  the API delete is confirmed — never before, never as the primary path.
- **Idempotency invariant (already true in code, must be preserved):** an attachment is
  added to `state.processed_items` **only after** the full DT chain succeeds
  (`copy_file_to_inbox` → find UUID → `_create_devonthink_child_link` →
  `update_item_metadata`), at `devonzot_service.py:2067`. "No Mac available" must remain
  a clean *skip*, never a "mark processed and move on."
- **Single-writer failover:** exactly one MCP endpoint services a given attachment per
  cycle (strict iMac>MBP priority). Never fan the same import at both Macs — structural,
  not merely recoverable via the dedup gate.
- **Same path convention on all three hosts:** `~/DEVONzot`. The `.code-workspace`
  folder path must resolve identically on server, iMac, and MBP.
- **Per-team git identity on the server** (independent repos rule): commits as
  `DEVONzot Team (Claude) <devonzot@traviseross.com>`, plus the additive
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.

---

## Motivation & Evidence (why now)

- The iMac keeps getting logged out; when it is, the current service does not run at all.
- Concrete backlog proving the gap: `/media/external/zotdav/data/zotero/` currently holds
  ~8 undrained attachment pairs, oldest from **2026-06-05 and 2026-06-22** — up to a month
  of files that should have been migrated and weren't. The server-side orchestrator drains
  this on first run.
- The slowest component is full-library change detection at startup. The work the user
  cares about most (new file → DEVONthink link, ASAP, so annotation happens on the final
  DT file) is currently stuck behind that slow maintenance scan. This plan decouples them.

## Converged Design (net flow)

```
inotify .prop CLOSE_WRITE on server (/media/external/zotdav/data/zotero/{KEY}.prop)
  → unzip {KEY}.zip locally → real filename + bytes (MD5 from .prop feeds dedup gate)
  → Zotero API: get_item(KEY) + parent  (surgical lookup, NOT a library scan)
  → select MCP endpoint: iMac live? → elif MBP live? → else requeue/wait
  → scp file to {mac}:/private/tmp/devonzot/{KEY}/{filename}
  → MCP import_file(mac_path) → UUID → dedup.reconcile_after_import(UUID, sha)
  → create x-devonthink-item child link on the Zotero parent
  → MCP update_record metadata
  → Zotero API: delete the attachment item  → Zotero purges the zotdav blob on next sync
  → ssh {mac} rm -rf /private/tmp/devonzot/{KEY}
  → record KEY in processed set so a mid-purge blob is not reprocessed
+ periodic low-frequency API reconcile = backstop for metadata edits / linked-file items
```

---

## Prerequisites & Locked Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Dev model | **Feature branch → converge to strategy-based `main`** | Reuses ~all code; server-vs-Mac becomes a runtime strategy, not a fork. Cutover = "run with server profile." |
| Repo location | **`~/DEVONzot` on server** (own repo, not in the docker monorepo, not gitignored elsewhere) | Not a docker service; matches iMac/MBP convention so the `.code-workspace` and paths are uniform. |
| Runtime | **systemd user service** (Linux launchd equivalent) | Server standard for always-on automations; no docker. |
| v1 MCP endpoints | **iMac only** | iMac is synced + always on and *not* where the user makes interactive DT edits. MBP as 2nd endpoint is deferred. |
| AppleScript path | **Keep, do not remove** | Stays valid for a Mac-local fallback; server profile just selects the MCP+scp transport. |
| Backstop reconcile | **Retained, low-frequency** | Covers metadata-only + linked-file cases the watcher can't see; also catches watcher downtime. |

### Copy manifest (clone brings code only — everything below is gitignored/local)

Run on the server after `git clone https://github.com/traviseross/devonzot.git ~/DEVONzot`:

| Item | Action |
|---|---|
| `DEVONzot.code-workspace` | `scp` from MBP → `~/DEVONzot/` |
| `.claude/CLAUDE.md` | `scp` from MBP (local-only; then apply DT3→DT4 + server edits) |
| `.claude/agents/*.md` | `scp` from MBP (devonthink-expert, devonthink-mcp-expert, zotero-expert) |
| `.claude/settings.local.json` | **do not copy** — regenerate for the server (paths/permissions differ) |
| `.env` | `scp` then edit (API key/user-id carry; DT paths + MCP URL change) |
| `service_state.json` + runtime state | **at cutover, from the iMac** (authoritative migration progress), not MBP |

> Also fix the misleading `.gitignore` comment "Claude Code (managed by claude-teams repo)"
> — the *project* `.claude` is purely local, not synced by claude-teams.

### Open decisions to resolve before the workstreams they gate

1. **TLS for MCP LAN exposure (gates WS4/WS1-live).** DT4 rejects self-signed certs for
   network binding. Options: (a) Let's Encrypt via DNS-01 against a subdomain you control
   (renewal automation needed); (b) a local CA whose root is trusted in the iMac Keychain
   (untested against DT4's own TLS validation — verify before committing). **Recommendation:
   (a).** Decide before WS4.
2. **Byte retrieval: unzip-local vs Web API download (gates WS2).** The blob is right there
   on the server → **unzip-local** (fastest, plus the `.prop` MD5 for dedup). Web API
   `download_attachment_file` is the format-agnostic fallback. **Recommendation: unzip-local,
   API download as fallback.**
3. **State at cutover:** copy the iMac's `service_state.json` (avoid re-scanning the whole
   library) vs. start fresh (safe, idempotent, slower first run). **Recommendation: copy the
   iMac's state**, verified against a dry-run.

---

## Workstreams

Ordering: WS0 → (WS1 ∥ WS2 ∥ WS3 buildable against mocks in parallel) → WS4 (live infra)
→ WS1-live integration → WS5 cutover. WS4 can start anytime since it's iMac-side config.

### WS0 — Server landing & disabled skeleton

**Deliverable:** `~/DEVONzot` cloned on the server, on a `feat/server-orchestrator` branch,
runnable under `--dry-run`, installed as a **disabled** systemd unit. iMac service still live.

- Clone + apply the copy manifest above; set the DEVONzot Team git identity locally.
- Create `.env` for the server (API creds; leave DT transport pointed at a not-yet-live
  MCP URL — dry-run must not require it).
- Create a systemd **user** unit `devonzot.service` (`WantedBy=default.target`,
  `Restart=on-failure`), installed but **not enabled/started**.
- Verify: `python -m src.devonzot_service --dry-run` (or the existing dry-run entrypoint)
  runs on Linux without touching DEVONthink or deleting anything.

**Acceptance:** dry-run completes on the server; no writes to Zotero or DT; unit exists and
is disabled. The iMac remains the live service.

**Risks to surface here:** any hardcoded macOS paths that break import on Linux
(`devonzot_service.py:51-62`) — catalogue them; WS3 fixes them. Do not hot-patch blind.

### WS1 — Network DT transport (endpoint-aware MCP + scp delivery + failover)

**Deliverable:** `DEVONthinkMCPInterface` can target a *remote* Mac over TLS, select a live
endpoint by strict priority, deliver the file via scp, import, and clean up — all behind the
existing interface seam so the service code (`self.devonthink.*`) is unchanged.

**Files:**
- Modify: `src/devonthink_mcp.py` (base URL → configurable remote host+TLS+bearer token;
  add health probe via the existing `is_running` tool)
- Modify: `src/devonzot_service.py` (the `DEVONthinkMCPInterface.copy_file_to_inbox` at
  ~L657 becomes: pick endpoint → scp → `import_file(mac_path)`; endpoint selection helper)
- Test: extend `tests/conftest.py` `mock_devonthink_interface` + new
  `tests/test_endpoint_failover.py`

**Interfaces:**
- Consumes: existing MCP methods found in `src/devonthink_mcp.py` — `import_file`,
  `search_records`, `update_record`, `trash_record`, `get_record_duplicates`,
  `set_record_custom_metadata`, `is_running`. **Verify exact signatures against the live
  iMac MCP server before writing import steps.**
- Produces: `select_endpoint() -> Endpoint | None` (strict iMac>MBP>None); a
  `copy_file_to_inbox` that scp's to the *selected* endpoint and returns success only if
  that endpoint's import path is reachable.

**Approach (TDD against the mock, then live against WS4):**
- Endpoint selection is pure logic → unit-test it: iMac-up → iMac; iMac-down/MBP-up → MBP;
  both down → None (⇒ caller skips, item stays unprocessed).
- scp + import is an integration seam → test the *sequencing/cleanup contract* with a fake
  transport (assert: file scp'd to `/private/tmp/devonzot/{KEY}/`, import called with that
  path, `rm` issued only after UUID returned). Real scp/import verified in WS1-live.

**Acceptance:** with a fake transport, failover picks the right endpoint and never double-
imports; cleanup happens only on success; `select_endpoint()==None` produces a clean skip
that leaves the item out of `processed_items`.

**Do NOT:** verify DEVONthink can read `/private/tmp` from *this* plan — that's a one-time
manual check in WS4 (build-dependent: the direct DEVONtechnologies `.app` is non-sandboxed
and reads `/private/tmp`; the MAS build may not — you are on the direct build).

### WS2 — zotdav change-detector (fast path)

**Deliverable:** an inotify watcher on `/media/external/zotdav/data/zotero/` that, on a new
`.prop` (write-completion sentinel), unzips the paired `.zip`, extracts filename + bytes +
MD5, and enqueues that KEY into the existing incremental-sync path. Plus a **processed-key
set** so a blob lingering mid-purge is not reprocessed.

**Files:**
- Create: `src/zotdav_watcher.py` (inotify loop; `.prop` CLOSE_WRITE → parse → emit KEY)
- Create: `src/zotdav_blob.py` (locate `{KEY}.zip`, unzip to temp, read filename + MD5 from
  `.prop`)
- Modify: `src/devonzot_service.py` (`ServiceState`: add processed **attachment-key** set
  alongside `processed_items`; wire the watcher as a change source feeding the existing
  incremental sync)
- Test: `tests/test_zotdav_blob.py`, `tests/test_zotdav_watcher.py` (fixture dir with
  sample `{KEY}.zip`/`.prop` pairs — synthesize small ones; do not use real library data)

**Interfaces:**
- Produces: `parse_prop(path) -> {key, mtime, md5}`; `extract_blob(key) -> {filename, path,
  md5}`; watcher emits `key` strings to the sync queue used by `run_streaming_service`.
- Consumes: the existing incremental-sync entrypoint that already handles Phase 0/1A/1B for
  a given change (`run_incremental_sync_async`).

**Approach:** Trigger on `.prop` (written after `.zip`, so the zip is complete). Watch via
`inotify` for `CLOSE_WRITE`. On startup, also **sweep** the directory once to pick up any
blobs present before the watcher started (drains the existing backlog). The MD5 from `.prop`
feeds `content_dedup` without re-hashing.

**Acceptance:** dropping a synthetic `{KEY}.zip`/`.prop` pair into the fixture dir emits the
KEY exactly once; a second identical drop for an already-processed key is skipped; unzip
yields the correct filename and bytes; startup sweep picks up pre-existing pairs.

**Boundary (must stay explicit):** the watcher only sees blob-producing changes (stored +
imported-url attachments). Metadata-only edits and linked-file (ZotFile) items are **not**
covered here — WS3's backstop reconcile owns those.

### WS3 — Config/path generalization + strategy selection + backstop

**Deliverable:** the service runs cleanly on Linux with server-appropriate paths, selects
change-source and DT-transport by profile, and runs the full-library API scan on a
low-frequency schedule as a backstop rather than as the primary detector.

**Files:**
- Modify: `src/devonzot_service.py:51-62` (hardcoded macOS paths → config-driven;
  `DEVONTHINK_INBOX_PATH` becomes irrelevant on the server profile; add zotdav path)
- Modify: `config/.env.example` (new keys: `DEVONZOT_PROFILE=server|mac`,
  `ZOTDAV_PATH`, `DEVONTHINK_MCP_ENDPOINTS` (ordered), TLS/bearer, reconcile interval)
- Modify: service startup to choose {zotdav-watcher | streaming/API} and {MCP-remote |
  AppleScript-local} from the profile
- Test: `tests/test_profile_selection.py`

**Interfaces:**
- Produces: a profile object gating change-source and transport. `DEVONZOT_USE_MCP` folds
  into this (`server` ⇒ MCP-remote; `mac` ⇒ existing behavior).

**Acceptance:** with `DEVONZOT_PROFILE=server`, no macOS-only path is dereferenced; the
backstop reconcile runs on its interval and re-discovers any un-migrated attachment (proven
by leaving one un-migrated and asserting the reconcile finds it). `mac` profile reproduces
current behavior (regression guard for the iMac fallback).

### WS4 — MCP LAN exposure + TLS on the iMac (shared infra)

**Deliverable:** the iMac's DT4 MCP server bound to the LAN with a CA-trusted TLS cert and
bearer token; a one-time manual proof that `import_file` works from the server against a
`/private/tmp` path.

**Steps (procedure, not unit-testable):**
- Resolve Open Decision #1 (TLS). Install the cert into the iMac Keychain; enable
  Settings → AI → MCP → network access (`server.access=local_network`, `auth.required=true`).
- Store bearer token + host in the server `.env` (not in source).
- **Manual proof:** from the server, `scp` a test PDF to `iMac:/private/tmp/devonzot/TEST/`,
  then a raw JSON-RPC `import_file` call against that path; confirm a UUID comes back and the
  record appears in DEVONthink. **This is the gate that de-risks WS1-live.**

**Acceptance:** authenticated network `import_file` from the server returns a UUID for a
`/private/tmp` file; the resulting `x-devonthink-item://UUID` resolves on the MBP after sync.

**Note:** the MCP server only runs while the iMac has an active user session (it's a Login
Item, not a launchd daemon). This is a macOS constraint, not a bug — it's *why* the failover
+ "else wait" design exists. Keeping the iMac reliably logged in (auto-login / no
auto-logout / `caffeinate`) remains the complementary reliability lever.

### WS5 — Cutover

**Deliverable:** the server is the live orchestrator; the iMac service is disabled (not
deleted); the backlog is drained; behavior is verified end-to-end.

**Steps:**
- On the iMac: stop + `disable` the launchd service; leave files in place for instant
  re-enable. Record the exact re-enable command in this plan's Status section.
- Copy the iMac's authoritative `service_state.json` to the server (Open Decision #3).
- Enable + start the server systemd unit. Watch it drain the ~8-blob backlog first.
- Verify: add a new item via the normal path (or zadd), confirm the file lands in zotdav,
  the watcher fires, DEVONthink gets the file, the `x-devonthink-item` link appears on the
  Zotero record, the attachment item is deleted, and the blob purges — end to end, quickly.

**Acceptance:** a freshly added file becomes a resolvable DEVONthink link on the user's
devices within the target latency, with no manual steps; the backlog is empty; the iMac
service is disabled and re-enableable.

---

## Explicitly Out of Scope (deferred, do NOT build here)

- **zadd integration.** zadd and DEVONzot are complementary and non-overlapping by design
  (zadd: identifier/URL → enriched Zotero record + file attachment, DEVONthink-unaware;
  DEVONzot: Zotero file ↔ DEVONthink link). The implicit handoff already works (zadd drops a
  file → the zotdav watcher now picks it up near-instantly). Revisit only after cutover, and
  only if a *direct* link handoff is wanted.
- **MBP as a second MCP endpoint.** Design keeps the priority list open for it, but v1 is
  iMac-only. Enable after cutover proves stable.
- **Removing the AppleScript transport.** Kept as the `mac`-profile fallback.

## Self-Review (against the converged design)

- Fast-path (zotdav watcher) — WS2. Backstop (API reconcile) — WS3. ✅ both present, boundary
  between them stated.
- Network DT transport with strict-priority failover + single-writer invariant — WS1 +
  Global Constraints. ✅
- File locality via scp to `/private/tmp` + cleanup-on-success-only — WS1, proven in WS4. ✅
- Blob lifecycle (API-delete drives purge; no direct `rm` as mechanism; processed-key set) —
  Global Constraints + WS2. ✅
- Idempotency invariant preserved (skip ≠ processed) — Global Constraints, referenced to
  `devonzot_service.py:2067`. ✅
- Deploy (systemd, `~/DEVONzot`, copy manifest, git identity) — WS0 + Prerequisites. ✅
- Cutover with iMac-disable + state migration + backlog drain — WS5. ✅
- **Known deferrals to verify live before their tasks:** MCP tool signatures (WS1, verify on
  iMac), `/private/tmp` readability (WS4), TLS mechanism (Open Decision #1).

---

## Status / Next step

- **WS4 (MCP LAN exposure) — essentially DONE and validated (2026-07-05):** Caddy team
  provisioned LE certs + Unbound records for `imacdevonthink.traviseross.com` (192.168.1.103)
  and `mbpdevonthink.traviseross.com` (192.168.1.125), auto-renewing, fullchain+key delivered
  daily to `~/docker/services/caddy/cert-delivery/<host>/`. Keychain identities installed on
  both Macs (`scripts/install-devonthink-tls-identity.sh`). **Both endpoints return 200 +
  tools from the server** over TLS + bearer. Full setup mechanics + gotchas captured in the
  `reference-devonthink-mcp-network` memory.
- **Per-Mac setup is NOT fully headless.** SSH can set `config.json` (`access`/`tlsIdentity`,
  DEVONthink watches the file) and `kickstart` the server, but each Mac still needs GUI
  (both Macs done at the machine — MBP locally, iMac over VNC): **select the cert in the MCP
  pane** and a keychain **"always allow"** password grant so the MCP *helper* can read the TLS
  private key (plus an Automation/Apple-Events grant so it can control DEVONthink). Root cause
  of the keychain prompt: the install script's `-T` granted key access to `DEVONthink.app`,
  but the serving process is `.../LoginItems/DEVONthink MCP.app` — the script now grants both,
  which *should* remove the "always allow" prompt on future imports/renewals (unverified —
  confirm on the next Mac or next renewal).
- **Client requirement confirmed:** DEVONthink presents **leaf-only** TLS → the DEVONzot MCP
  client must `verify=` the delivered **fullchain.pem**, not the system CA bundle. Calls need
  `Content-Type: application/json` + `Authorization: Bearer <token>`.
- **WS0 (server landing) — STARTED 2026-07-06:** committed plan+script to
  `feat/server-orchestrator` (`a04a2e2`, pushed); cloned to `/home/tradmin/DEVONzot` on the
  server on that branch; copy manifest applied (`.claude/` CLAUDE.md+agents, `.code-workspace`,
  `.env`); DEVONzot Team git identity set on the clone. Deps manifest is at `src/requirements.txt`
  (not top-level). **Linux breakage catalogue (feeds WS3):**
  (a) server has only Python **3.10**; target is 3.13 — provision 3.13 (uv/pyenv/deadsnakes) or
  confirm 3.10 compatibility.
  (b) **module fails to import** — logging `FileHandler` opens the hardcoded macOS path
  `/Users/travisross/DEVONzot/service.log` at load → `FileNotFoundError` on Linux. All hardcoded
  paths (`devonzot_service.py:51-62`) + logging setup must become `DEVONZOT_PATH`/config-driven
  before it imports on the server.
  (c) DT3→DT4 references still pending per the project CLAUDE.md.
- **WS0 + WS3-slice — DONE 2026-07-06 (server imports + dry-run runs on Linux):**
  - **Python decision: stay on 3.10** (target 3.13 dropped). `scripts/setup.sh` already accepts
    3.10+; the whole `src/requirements.txt` (incl. lxml, newspaper3k, extruct, trafilatura)
    installs cleanly into `venv/` on the server's 3.10.12. No 3.11+ syntax in the tree. Provision
    3.13 later only if a dependency demands it.
  - **Config/path generalization (fixes the import crash):** `DEVONZOT_PATH` now defaults to the
    repo root (env-overridable); `LOG_FILE`/`STATE_FILE`/`PID_FILE` derive from it; logging setup
    `mkdir -p`s the log dir before opening the handler. `ZOTERO_STORAGE_PATH`/`ZOTFILE_IMPORT_PATH`/
    `DEVONTHINK_INBOX_PATH` are env-overridable macOS defaults, never dereferenced on `server`.
    Added `DEVONZOT_PROFILE=server|mac` (default `mac` = unchanged historical behavior). Fixed the
    two hardcoded `load_dotenv('/Users/...')` calls (`pipeline_add_url.py`,
    `create_zotero_item_from_url.py`) + `diagnose_attachments.py` storage path.
  - **Tests:** `tests/test_profile_paths.py` (6) — server-profile import touches no `/Users` path,
    missing log dir is created, `mac` profile preserves macOS defaults, and `_resolve_storage_path`
    returns `None` (not a crash) for macOS-style paths on Linux. Full offline core suite green
    (260 pass). *Out of scope, pre-existing:* 8 URL-extraction-pipeline tests
    (`test_pipeline_fallback`/`test_pipeline_integration`) fail on a `create_url_attachments` mock
    mismatch — they were **collection-errors** (0 tests) before the import fix, so this is newly
    *visible* Linux brittleness in the deferred zadd/URL path, not a regression. Triage separately.
  - **Dry-run:** `venv/bin/python src/devonzot_service.py --dry-run` runs on Linux (correct
    invocation — NOT `python -m src.devonzot_service`, which breaks on the flat imports). Reads the
    live Zotero library (16,551 items), no writes, no macOS-path crash.
  - **systemd skeleton:** `ops/devonzot.service` (user unit, `%h`-relative) installed to
    `~/.config/systemd/user/` and left **disabled + inactive** (verified). Enable only at WS5.
  - **Known robustness gap (not an A-blocker):** the service's SIGTERM handler logs "shutting down
    gracefully" but the synchronous library-scan loop doesn't check the shutdown flag mid-scan, so
    a kill during startup scan is ignored until the scan finishes (harness escalated to SIGKILL).
    Worth a cooperative-cancellation check in the scan loop before WS5.
- **WS1 de-risking DONE 2026-07-06 (the WS4 `/private/tmp` gate is closed):**
  - **Endpoint shape:** DEVONthink's MCP server binds **directly on `:8420`** with its own TLS
    identity (NOT fronted by Caddy on 443). Endpoint = `https://imacdevonthink.traviseross.com:8420`
    (iMac 192.168.1.103), `https://mbpdevonthink.traviseross.com:8420` (MBP, deferred). Server
    section of `~/Library/Application Support/DEVONthink/MCP/config.json` confirms
    `access=local-network, port=8420, tlsIdentity=<host>`. (Note: `auth.required=false` there, but
    the bearer token is still accepted/needed — send it.)
  - **Bearer token** lives in that iMac `config.json` (`auth.bearerToken`). Goes in the server
    `.env` (gitignored), NOT source.
  - **TLS trust (the real gotcha):** the server presents **leaf-only**. `curl --cacert fullchain.pem`
    works, but Python `requests` with `verify=fullchain.pem` fails `unable to get issuer certificate`
    (leaf-first bundle + the delivered ISRG roots aren't accepted as anchors). **Working fix:
    `verify = certifi roots + the intermediate cert(s) from fullchain`** (server sends leaf-only, so
    we only need to supply the missing intermediate; ISRG roots come from certifi). Rebuild this
    bundle from the current fullchain at client init → renewal-safe. Cert delivered daily to
    `~/docker/services/caddy/cert-delivery/<host>/fullchain.pem`.
  - **Round-trip PROVEN:** server → scp to `iMac:/private/tmp/devonzot/TEST/` → MCP `import_file`
    returned a real UUID → `get_record_properties` confirmed → `trash_record` cleaned up. This is
    exactly the WS4 acceptance ("authenticated network import_file from the server returns a UUID for
    a /private/tmp file"). DEVONthink (direct/non-MAS build) reads `/private/tmp`. ✅
- **WS1 (network DT transport) — DONE 2026-07-06:**
  - `DevonthinkMCP(cacert=...)` builds the certifi+intermediate verify bundle (renewal-safe,
    content-hashed temp file). `DEVONTHINK_MCP_CACERT` also honored.
  - `MCPEndpoint` model + `_parse_mcp_endpoints()` (per-label env: `DEVONTHINK_MCP_<L>_URL/
    _TOKEN/_CACERT/_SSH`; ordered list = strict priority; no config => single local endpoint =
    mac-profile default). `DEVONthinkMCPInterface.select_endpoint()` returns the first live
    endpoint and binds `self.mcp` to it (downstream search/metadata/dedup hit the same synced Mac);
    None => clean skip. `copy_file_to_inbox` scp's to `/private/tmp/devonzot/<uid>/` for a remote
    endpoint, imports, and cleans the temp in a `finally` (leak-free on success OR failure).
  - Tests: `tests/test_endpoint_transport.py` (10) — parsing, failover (iMac>MBP>None),
    cleanup-on-success-and-on-failure, None=>skip (never a false success), local path direct-import,
    TLS bundle drops leaf/keeps intermediate. Full offline suite 271 pass (8 URL-pipeline failures
    still pre-existing/out of scope).
  - **Live-proven:** server `.env` (`DEVONZOT_USE_MCP=true`, `DEVONTHINK_MCP_ENDPOINTS=imac`) →
    `select_endpoint()` picks iMac → `copy_file_to_inbox` scp+import returned a real UUID → trashed →
    remote temp confirmed cleaned. v1 is iMac-only; the mbp block is present-but-commented in `.env`.
- **Immediate next (WS2 — zotdav fast-path watcher):** inotify on
  `/media/external/zotdav/data/zotero/` `.prop` CLOSE_WRITE → unzip paired `.zip` → enqueue KEY into
  `run_incremental_sync_async`; startup sweep to drain pre-existing blobs; processed-key set.
  NOTE: that dir is currently **empty** (0 pairs) — confirm the Zotero client still syncs to this
  WebDAV (served by the `zotero_webdav` container) before relying on it for the WS5 live test.
- **Verify commands:** per-workstream Acceptance sections above.
- **iMac service DISABLED early (2026-07-05):** `com.devonzot.service` stopped + persistently
  disabled (`launchctl disable` override survives login), plist left in place at
  `~/Library/LaunchAgents/com.devonzot.service.plist`. **No DEVONzot runs anywhere now** — new
  Zotero files accumulate in zotdav (idempotent; drains when the server version comes up).
  **Re-enable (from an iMac session):**
  `launchctl enable gui/$(id -u)/com.devonzot.service && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.devonzot.service.plist`
