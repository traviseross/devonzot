# DEVONzot

DEVONzot converts ZotFile symlinks and Zotero stored attachments into robust UUID-based links using the `x-devonthink-item://` scheme. It migrates files from Zotero storage into DEVONthink and rewrites Zotero attachment records to point at those DEVONthink items permanently, eliminating fragile file-path dependencies.

## Quick Start

```bash
# 1. Clone and set up virtual environment
git clone <repository-url> DEVONzot
cd DEVONzot
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r src/requirements.txt

# 3. Configure credentials
cp config/.env.example .env
# Edit .env: set ZOTERO_API_KEY and ZOTERO_USER_ID

# 4. Preview without making changes
python3 src/devonzot_service.py --dry-run
```

## Service Modes

| Command | Behavior |
|---------|----------|
| `python3 src/devonzot_service.py` | Default: WebSocket streaming with polling fallback |
| `python3 src/devonzot_service.py --dry-run` | Preview all actions without making changes |
| `python3 src/devonzot_service.py --once` | Run one complete migration cycle then exit |
| `python3 src/devonzot_service.py --service` | Perpetual polling loop (no streaming) |
| `python3 src/devonzot_service.py --no-stream` | Disable WebSocket streaming, use polling only |
| `python3 src/devonzot_service.py --stop` | Signal a running service instance to stop |
| `python3 src/devonzot_add_new.py --add N` | Batch-add N items via alternative workflow |

### Service Cycle Phases

Each migration cycle runs the following phases in order:

- **Phase 0**: Delete `linkMode=1` (imported_url) attachments from disk and via Zotero Web API
- **Phase 1A**: Migrate `linkMode=0` (stored file) attachments to DEVONthink with UUID links
- **Phase 1B**: Migrate `linkMode=2` (ZotFile symlinks) to UUID links
- **Phase 2**: Batch async conversion of existing symlinks already present in DEVONthink (batch size: 50)
- **Phase 3**: Sync metadata for new items
- **Retry**: Process any `pending_deletes` queue entries from previous failed cycles

## URL Pipeline

```bash
python3 src/pipeline_add_url.py <url> [--dry-run]
```

Creates a Zotero item from a URL, extracts the article content, imports it to DEVONthink, and creates a UUID-based attachment link back in Zotero. Extraction uses a 4-tier cascade:

- **Tier 0** (standard): newspaper3k + readability-lxml + trafilatura
- **Tier 1** (RSS): RSS/Atom feed extraction for Substack, Medium, WordPress
- **Tier 2** (Playwright, optional): Headless browser for JavaScript-heavy pages
- **Tier 3** (Wayback): Internet Archive fallback for paywalled or deleted content

## Configuration

All settings are loaded from `.env` via python-dotenv. Only `ZOTERO_API_KEY` and `ZOTERO_USER_ID` are required; all others have defaults.

### Zotero API

| Variable | Default | Description |
|----------|---------|-------------|
| `ZOTERO_API_KEY` | (required) | Zotero API key |
| `ZOTERO_USER_ID` | (required) | Zotero user ID |
| `ZOTERO_API_BASE` | `https://api.zotero.org` | Zotero API base URL |
| `API_VERSION` | `3` | Zotero API version |

### Rate Limiting and Batching

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_DELAY` | `2.0` | Seconds between API calls |
| `BATCH_SIZE` | `5` | Items per API batch |
| `CYCLE_DELAY` | `60` | Seconds between polling cycles |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `CREATOR_LOG_PATH` | `creator.log` | Creator workflow log |
| `ADDNEW_LOG_PATH` | `api_v2_service.log` | Add-new workflow log |
| `INSPECTOR_LOG_PATH` | `api_service.log` | Inspector log |
| `ATTACHMENT_PAIRS_PATH` | `attachment_pairs.json` | Legacy-to-UUID mapping file |

### WebSocket Streaming

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBSOCKET_ENABLED` | `true` | Use Zotero Streaming API |
| `FALLBACK_POLL_INTERVAL` | `600` | Fallback poll interval in seconds |
| `FALLBACK_POLL_ENABLED` | `true` | Enable polling fallback |

### Translation Server

| Variable | Default | Description |
|----------|---------|-------------|
| `TRANSLATION_SERVER_URL` | AWS endpoint | Zotero Translation Server URL |
| `TRANSLATION_TIMEOUT` | `30` | Request timeout in seconds |

### Pipeline and Extraction

| Variable | Default | Description |
|----------|---------|-------------|
| `TMP_DIR` | `tmp_extractions/` | Temporary extraction directory |
| `EXTRACTION_TIMEOUT` | `120` | Article extraction timeout in seconds |
| `DEBUG_MODE` | `false` | Enable debug output |
| `ENABLE_RSS_FALLBACK` | `true` | Tier 1: RSS/Atom feed extraction |
| `ENABLE_PLAYWRIGHT` | `false` | Tier 2: Playwright headless browser |
| `ENABLE_WAYBACK` | `true` | Tier 3: Internet Archive fallback |
| `PLAYWRIGHT_TIMEOUT` | `30000` | Playwright page load timeout in milliseconds |
| `WAYBACK_TIMEOUT` | `15` | Wayback Machine API timeout in seconds |

## Source Files

All source files are in `src/`.

| File | Purpose |
|------|---------|
| `devonzot_service.py` | Main async service, production entry point |
| `devonzot_add_new.py` | Alternative batch workflow for adding UUID attachments |
| `zotero_api_client.py` | Zotero Web API v3 client (primary Zotero integration) |
| `pipeline_add_url.py` | 5-step URL-to-Zotero+DEVONthink pipeline |
| `article_extraction.py` | Multi-engine extraction (newspaper3k, readability, trafilatura) |
| `rss_extractor.py` | Tier 1 RSS/Atom feed extraction |
| `playwright_extractor.py` | Tier 2 headless browser extraction (optional) |
| `wayback_extractor.py` | Tier 3 Internet Archive fallback |
| `combine_article_extracts.py` | Extraction orchestrator with YAML frontmatter output |
| `zotero_stream.py` | WebSocket client for Zotero Streaming API |
| `cleanup_service.py` | TempFileManager for pipeline temporary files |
| `exceptions.py` | DEVONzotError exception hierarchy |
| `diagnose_attachments.py` | Attachment landscape diagnostic tool |
| `create_zotero_item_from_url.py` | Standalone URL-to-Zotero CLI utility |
| `requirements.txt` | Python dependencies |

## Tests

```bash
source venv/bin/activate
pytest
```

Test files live in `tests/`. See `pytest.ini` at the project root for configuration. Coverage reports are generated in `htmlcov/`.

## Troubleshooting

- **DEVONthink must be running.** All DEVONthink integration uses AppleScript, which requires the application to be open.
- **Zotero can be running.** The service uses the Zotero Web API for all writes. SQLite is accessed read-only for change detection only.
- **Check `service.log`** for real-time operation logs and error traces.
- **Check `service_state.json`** for migration progress, the `pending_deletes` queue, and database hashes.
- **Run `--dry-run` first** to preview all planned actions before committing changes.
- **Full Disk Access** must be granted to Terminal and/or Python in System Settings > Privacy & Security.
- **Automation permission** for Terminal to control DEVONthink must be granted in System Settings > Privacy & Security > Automation.
