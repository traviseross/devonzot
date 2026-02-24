# Installation Guide

## Requirements

- macOS 10.15 (Catalina) or later
- Python 3.8 or later (3.13 recommended)
- DEVONthink 3 (any edition)
- Zotero 6 or later

## macOS Permissions

### Full Disk Access

DEVONzot reads Zotero's SQLite database for change detection and accesses attachment files on disk.

System Settings > Privacy & Security > Full Disk Access

Add Terminal.app and/or your Python installation.

### Automation

DEVONthink integration uses AppleScript, which requires explicit automation permission.

System Settings > Privacy & Security > Automation

Allow Terminal (or Python) to control DEVONthink 3.

## Installation

```bash
git clone <repository-url> DEVONzot
cd DEVONzot
python3 -m venv venv
source venv/bin/activate
pip install -r src/requirements.txt
```

### Optional: Playwright (Tier 2 Extraction)

Playwright is disabled by default. Enable it only if you need headless browser extraction for JavaScript-heavy pages.

```bash
pip install playwright>=1.40.0
playwright install chromium
```

Set `ENABLE_PLAYWRIGHT=true` in `.env` to activate.

## Configuration

```bash
cp config/.env.example .env
```

Open `.env` and set the two required values:

```
ZOTERO_API_KEY=your-api-key-here
ZOTERO_USER_ID=your-user-id-here
```

All other settings have sensible defaults and do not need to be changed for a standard installation. See `docs/README.md` for a full reference of every environment variable.

To find your Zotero credentials: Zotero > Settings > Feeds/API > Create new private key.

## Verification

**1. Test Python**

```bash
python3 --version
```

Should show 3.8 or later.

**2. Test DEVONthink**

```bash
osascript -e 'tell application "DEVONthink 3" to get version'
```

Should return a version string. If it fails, check that DEVONthink 3 is installed, licensed, and that Automation permission has been granted.

**3. Test the service (dry run)**

```bash
source venv/bin/activate
python3 src/devonzot_service.py --dry-run
```

Should print a summary of planned actions without making any changes. DEVONthink must be open for this to succeed.

## Directory Structure

```
DEVONzot/
├── src/                         # Source code
│   ├── devonzot_service.py      # Main async service
│   ├── zotero_api_client.py     # Zotero Web API client
│   ├── pipeline_add_url.py      # URL pipeline
│   ├── article_extraction.py
│   ├── rss_extractor.py
│   ├── playwright_extractor.py
│   ├── wayback_extractor.py
│   ├── combine_article_extracts.py
│   ├── zotero_stream.py
│   ├── cleanup_service.py
│   ├── exceptions.py
│   ├── diagnose_attachments.py
│   ├── create_zotero_item_from_url.py
│   ├── devonzot_add_new.py
│   └── requirements.txt
├── tests/                       # Test suite
├── config/
│   └── .env.example             # Environment variable template
├── docs/                        # Documentation
├── .env                         # Your credentials (gitignored)
├── pytest.ini                   # Test configuration
├── service_state.json           # Runtime state (created on first run)
├── service.log                  # Runtime logs
└── venv/                        # Python virtual environment
```

## Background Service

For unattended operation via macOS launchd, see `docs/INSTALL_LAUNCHD.md`.

## Uninstallation

```bash
# Stop any running service instance
python3 src/devonzot_service.py --stop

# Remove launchd job if installed (see INSTALL_LAUNCHD.md for details)
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.devonzot.addnew.plist
rm ~/Library/LaunchAgents/com.devonzot.addnew.plist

# Remove the project directory
rm -rf /path/to/DEVONzot
```

UUID links already written into Zotero (`x-devonthink-item://...`) continue to work after DEVONzot is removed, as long as DEVONthink and its databases remain intact.
