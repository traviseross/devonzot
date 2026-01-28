# DEVONzot

Zotero-DEVONthink Integration Tool

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

### Explore Zotero Database
```bash
python explore_zotero_db.py
```

## Development

This project uses a virtual environment to manage dependencies. The main integration will use:
- Direct SQLite access to Zotero's database
- AppleScript integration with DEVONthink
- File monitoring for real-time sync

## Future Features
- Automatic file import from Zotero to DEVONthink
- Metadata preservation and mapping
- Bidirectional link maintenance
- Docker containerization for deployment