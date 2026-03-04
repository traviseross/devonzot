# Changelog

All notable changes to DEVONzot will be documented in this file.

## [Unreleased]

### Changed
- `retry_pending_deletes` now uses batch API calls (`get_items_by_keys`, `delete_items_batch`) instead of per-item sequential requests, reducing 414 stale pending deletes from ~15 minutes to ~18 seconds

---

## [2.0.0] - 2026-02-24

### Added
- ZoteroAPIClient: centralized Web API v3 client (`src/zotero_api_client.py`)
- WebSocket streaming via Zotero Streaming API (`src/zotero_stream.py`) with configurable polling fallback
- Phase 0: automated deletion of `linkMode=1` (imported_url) attachments from disk and via API
- `pending_deletes` resilience queue in `service_state.json` for retrying failed API delete operations across cycles
- URL-to-DEVONthink pipeline (`src/pipeline_add_url.py`) with Zotero Translation Server integration
- 4-tier article extraction: newspaper3k + readability-lxml + trafilatura (Tier 0), RSS/Atom feeds (Tier 1), Playwright headless browser (Tier 2, optional), Internet Archive Wayback Machine (Tier 3)
- Structured exception hierarchy (`src/exceptions.py`): `DEVONzotError`, `ZoteroAPIError`, `ArticleExtractionError`, `DEVONthinkIntegrationError`, `NetworkError`, `TimeoutError`
- Temp file lifecycle management (`src/cleanup_service.py`)
- Test suite: 11 test files in `tests/` using pytest, pytest-asyncio, and pytest-mock
- `src/` package layout replacing root-level scripts
- `linkMode=0` (imported_file) and `linkMode=1` (imported_url) support alongside existing `linkMode=2`
- DEVONthink callback URL attachments now use the document filename as link text (was generic "DEVONthink Link"); existing links are retroactively renamed on next run

### Changed
- Primary Zotero integration: Web API via `ZoteroAPIClient` (was direct SQLite for writes)
- Zotero can remain running during migration; the Web API does not lock SQLite
- Dependencies expanded: websockets, newspaper3k, readability-lxml, trafilatura, feedparser, beautifulsoup4, lxml, html2text, extruct

### Removed
- Direct Zotero SQLite as primary write path (read-only change detection via hash is retained)
- Root-level script layout (all entry points moved to `src/`)

---

## [1.0.0] - 2026-01-28

### Production Release

#### Added
- Async batch processing with concurrent DEVONthink operations (`convert_zotfile_symlinks_async()`)
- Set-and-forget automation via cron scheduling
- JSON-based state management with progress tracking across service restarts
- Graceful error recovery for AppleScript timeouts and transient failures
- Daemon mode with signal handling
- Pre-migration conflict detection with filename collision reporting
- Native macOS metadata integration via extended attributes (`kMDItemAuthors`, `kMDItemTitle`, `kMDItemDescription`)
- Spotlight-searchable metadata on synced documents

#### Changed
- Migration architecture evolved from sequential scripts to a comprehensive async service
- Batch size: 50 items processed concurrently
- Processing speed: substantially reduced per-item time compared to sequential operation

#### Technical Details
- `AsyncZoteroDevonthinkSync`: main orchestration class
- State persistence via `service_state.json` survives service restarts and reboots
- Change detection via database hash monitoring prevents unnecessary processing
- UUID-based integration: permanent `x-devonthink-item://` links replace fragile symlinks

### Development Evolution

#### v0.1 - Proof of Concept
- Basic symlink replacement functionality
- Sequential processing
- Manual operation only

#### v0.5 - Automation Integration
- Cron job capability
- Basic error handling
- Initial state management

#### v1.0 - Production
- Async batch processing
- Comprehensive error recovery
- Continuous service mode

---

### Known Limitations at v1.0.0

- **AppleScript performance**: DEVONthink search operations are inherently sequential per call
- **Database locking**: In v1.0.0, direct SQLite access required Zotero to be closed during large migrations. This constraint was removed in post-v1.0.0 work by switching to the Web API as the primary write path (see [Unreleased] above).
- **Filename conflicts**: Manual resolution required for duplicate filenames
