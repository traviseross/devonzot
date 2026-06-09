# scan-import — Design Spec
**Date:** 2026-06-08
**Status:** Awaiting implementation plan

## Overview

A new standalone Mac service that watches the network scan output folder and imports OCR'd PDFs into DEVONthink 4 with human-readable names and automatic filing. This is the missing final hop in the scanservjs pipeline on `zvra.traviseross.com`.

**Not in DEVONzot.** Different concern: scan ingestion vs. Zotero/DEVONthink sync. Separate service directory.

## Context

- **Scan pipeline on server:** `scanservjs` → ocrmypdf → `/media/external/scanning/pdf_out/`
- **SMB share on iMac:** `/Volumes/Media/scanning/pdf_out/` (always-on ethernet, same network)
- **Processing machine:** iMac (always on; MBP excluded — may be away for days)
- **Urgency:** Low. Files can sit unprocessed for days without issue.
- **AI budget:** Free only. Ollama (local LLM) for rename; DEVONthink's built-in AI for filing.

## Service Location

```
~/scan-import/
```

New standalone service, not a submodule of DEVONzot. `devonthink_mcp.py` copied from DEVONzot at creation time; divergence accepted unless it becomes a maintenance burden.

## File Layout

```
~/scan-import/
  scan_import_service.py               # FSEvents watcher + pipeline
  devonthink_mcp.py                    # copied from DEVONzot/src/
  config/
    .env.example
    .env                               # gitignored
  com.traviseross.scan-import.plist    # launchd agent definition
  requirements.txt
  .gitignore
```

## Architecture

```
/media/external/scanning/pdf_out/     (server, written by ocrmypdf)
  ↕  SMB over ethernet
/Volumes/Media/scanning/pdf_out/      (iMac mount point)
  ↓  FSEvents watcher (watchdog library)
scan_import_service.py
  ↓  devonthink_mcp.py → localhost:8420
DEVONthink 4 (iMac)
```

The service is fully iMac-local after file transfer. No tunnel, no reverse bridge. The SMB share is the only cross-machine dependency.

## Processing Pipeline

For each new `.pdf` file detected in the watch folder:

**1. Stabilization wait**
Wait until file size is stable (poll 0.5 s × 3) before processing. Prevents reading a partially-written scp transfer.

**2. Import**
```python
rec = mcp.import_file(path)
uuid = rec["uuid"]
```
Imported to DT4 Global Inbox. Original name is the scanner-generated filename (`scan_2026-06-08_155200.pdf`).

**3. Read OCR text**
```python
result = mcp.get_record_text(uuid)
text = result.get("text", "")[:500]
```
DT4 already has the OCR text from the imported PDF. No pdfminer needed.

**4. Generate label via Ollama**
Send the first 400 characters of OCR text to Ollama with a minimal prompt:

```
Given this scanned document text, output ONLY a short human-readable label
(2–5 words) suitable for a filename. No extension, no path, no punctuation.
Examples: "T-Mobile Bill", "Providence Invoice", "Driver License".

Text:
{text[:400]}
```

Model: `llama3.2:3b` (default). Called with `options.keep_alive=0` so the model unloads from RAM immediately after the response — respects iMac memory pressure.

**Fallback:** If Ollama is unreachable or returns empty, use the first non-empty line of OCR text, truncated to 60 characters and stripped of special characters.

**5. Build filename**
```python
date_prefix = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
new_name = f"{date_prefix} {label}"   # e.g. "2026-06-08 T-Mobile Bill"
```
Date is taken from the file's mtime (the scan timestamp, not today's date).

**6. Rename in DT4**
```python
mcp.update_record(uuid, name=new_name)
```

**7. Classify and file**
```python
suggestions = mcp.classify_record(uuid)
```
DT4's built-in AI suggests destination groups based on library similarity.

- If suggestions returned: `move_record` to the top suggestion.
- If no suggestions (new document type, sparse library): leave in Global Inbox. No forced moves.

The classify threshold improves naturally over time as the library grows with examples of each document type.

**8. Archive source file**
On success, move the source PDF into a sibling `archive/` directory on the SMB share, organised by month:
```
/Volumes/Media/scanning/pdf_out/archive/2026-06/scan_2026-06-08_155200.pdf
```
The service creates the monthly subdirectory if it does not exist. Provides a safety copy without growing the watched folder indefinitely. Manual cleanup at discretion.

**9. On failure**
Move the source file to `pdf_out/failed/` with a `.log` sidecar containing the error and timestamp. Does not block processing of subsequent files.

## Error Handling

| Condition | Behavior |
|---|---|
| SMB share not mounted | Log warning on startup; skip processing until mount appears |
| DT4 not running | `is_running()` check before each file; skip + log, retry on next FSEvents event |
| Ollama unavailable | Fall back to first-line-of-OCR rename; continue pipeline |
| `classify_record` returns no suggestions | Leave in Global Inbox; success (not a failure) |
| MCP error on import | Move file to `failed/`, write sidecar log |
| MCP error after import (rename/classify) | Record is in DT inbox with original name; log error but do not re-import |

## Configuration (.env)

```bash
# Required
DEVONTHINK_MCP_TOKEN=<from DEVONthink > Settings > AI > MCP>

# Optional overrides
DEVONTHINK_MCP_URL=http://localhost:8420
WATCH_FOLDER=/Volumes/Media/scanning/pdf_out
ARCHIVE_FOLDER=/Volumes/Media/scanning/pdf_out/archive
FAILED_FOLDER=/Volumes/Media/scanning/pdf_out/failed
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
LOG_LEVEL=INFO
```

## launchd Agent

`com.traviseross.scan-import.plist` installed to `~/Library/LaunchAgents/`.

Key settings:
- `KeepAlive: true` — restarts on crash
- `RunAtLoad: true` — starts on login
- `StandardOutPath` / `StandardErrorPath` → `~/Library/Logs/scan-import.log`

The service does not use `StartInterval`; it is purely event-driven via FSEvents.

## Dependencies

```
watchdog       # FSEvents watcher
requests       # HTTP to Ollama and MCP
python-dotenv  # .env loading
```

Plus Ollama installed on iMac: `brew install ollama && ollama pull llama3.2:3b`.

No test framework dependency for v1 (the service is thin enough to verify manually against real scans, and the MCP client is already tested in DEVONzot).

## What This Does Not Do

- No OCR — the PDFs arriving in `pdf_out/` are already OCR'd by ocrmypdf on the server.
- No Zotero interaction — this is a scan-to-DT pipeline, orthogonal to DEVONzot.
- No tagging — `set_record_tags` is not called in v1. DT's classify action handles filing; tags can be added via Smart Rules once filing patterns are established.
- No deduplication — if the same file is scp'd twice, it will be imported twice. The server pipeline is responsible for not doing that.
- No auto-mount of the SMB share — the volume is expected to be mounted; the service logs and skips if it is not.
