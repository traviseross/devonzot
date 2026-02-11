# DEVONzot: Automated Zotero–DEVONthink Attachment Sync

## Overview
DEVONzot automates the process of linking Zotero attachments with DEVONthink, ensuring every Zotero file attachment has a unique UUID and is properly tracked. It supports batching, rate limit handling, logging, and can run continuously as a background service (launchd on macOS).

---

## Features
- Batch creation of UUID-linked attachments in Zotero
- Robust Zotero API rate limit handling
- Logging of all changes for audit and troubleshooting
- Immediate deletion of old attachments after migration
- Continuous operation via loop mode or macOS launchd
- Secure configuration via `.env` file

---

## Installation

### 1. Clone the Repository
```
git clone https://github.com/yourusername/DEVONzot.git
cd DEVONzot
```

### 2. Set Up Python Environment
It is recommended to use a virtual environment:
```
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your Zotero API credentials and other settings:
```
cp .env.example .env
# Edit .env with your editor
```

### 5. (Optional) Add to .gitignore
`.env` is already in `.gitignore` for security.

---

## Usage

### One-Time Batch Run
Run the script to process a batch of attachments:
```
python devonzot_add_new.py
```

### Continuous 15-Minute Watch Mode
Run in loop mode to process new attachments every 15 minutes:
```
python devonzot_add_new.py --loop15
```

### Logging
- All changes are logged in `changed_files_log.json`.
- Operational logs are written to `api_v2_service.log`.

---

## Running as a Background Service (macOS launchd)

1. Edit the provided `com.devonzot.addnew.plist` if needed (update paths for your system).
2. Copy it to your LaunchAgents folder:
    ```
    cp com.devonzot.addnew.plist ~/Library/LaunchAgents/
    ```
3. Load the service:
    ```
    launchctl load ~/Library/LaunchAgents/com.devonzot.addnew.plist
    ```
4. Logs will be written to `launchd_stdout.log` and `launchd_stderr.log` in your DEVONzot directory.

---

## Troubleshooting
- Check `.env` for correct API keys and settings.
- Review `api_v2_service.log` and `changed_files_log.json` for errors or progress.
- If the script stalls, check for API rate limits or increase log verbosity.

---

## Contributing
Pull requests and issues are welcome!

---

## License
See LICENSE for details.
# DEVONzot v1.0.0

🔗 **Invisible, Bulletproof Zotero-DEVONthink Integration**

DEVONzot transforms fragile ZotFile symlinks into robust UUID-based integration, enabling seamless mobile workflows between Zotero and DEVONthink with intelligent metadata synchronization.

## ✨ Key Features

### 🚀 **Set-and-Forget Automation**
- **Invisible Operation**: Runs automatically via cron with zero user intervention
- **Async Performance**: Optimized batch processing (50x faster than sequential)
- **Bulletproof Error Handling**: Graceful recovery from any DEVONthink/Zotero issues
- **Comprehensive Logging**: Detailed service logs with progress tracking

### 🔄 **Symlink Migration**
- **ZotFile Replacement**: Converts fragile symlinks to permanent UUID links
- **Filename Intelligence**: Smart conflict resolution and duplicate detection  
- **Batch Processing**: Handles thousands of attachments efficiently
- **State Management**: Tracks progress across interruptions

### 📝 **Intelligent Metadata Sync**
- **Bidirectional Sync**: Zotero ↔ DEVONthink metadata preservation
- **Native macOS Integration**: Spotlight-searchable metadata via extended attributes
- **Smart Archive Discovery**: Auto-tags items based on collection analysis
- **Mobile Workflow Support**: Full metadata access on iOS/iPadOS

### 🛡️ **Production-Ready Architecture**
- **Change Detection**: Only syncs when databases actually change
- **Conflict Resolution**: Handles filename collisions and duplicate items
- **Service Architecture**: Daemon mode with signal handling
- **Dry Run Capability**: Test migrations before committing changes

## 🏗️ System Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│     Zotero      │◄──►│   DEVONzot       │◄──►│   DEVONthink   │
│   (SQLite DB)   │    │   Service        │    │  (AppleScript)  │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                        │                       │
         ▼                        ▼                       ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  File Monitor   │    │  State Manager   │    │ macOS Metadata  │
│   (Changes)     │    │ (JSON Tracking)  │    │ (Extended Attr) │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## 🔌 API + Controller Toolkit (v3)

While the flagship service still talks directly to the Zotero SQLite database, this branch also ships a full API-driven toolchain that runs safely while Zotero stays open. Use it when you need a "set and forget" background sync without touching the database file.

### Core API Services
- **devonzot_api_service.py** – Experimental all-API converter with smart DEVONthink search, inspection reports, and batch conversion helpers.
- **devonzot_safe_api.py** – Adds atomic state saves, rolling backups, and resume hooks for long-running API jobs.
- **devonzot_inspector.py / demo_inspection.py / show_api_results.py** – Quick inspection utilities for reviewing API reachability and recent conversions.

### Two-Process Automation
- **devonzot_creator.py** – Continuously scans for linked-file attachments, finds DEVONthink UUID matches, and creates safe linked-URL copies.
- **devonzot_cleaner.py** – Verifies redundant items (file + UUID) and removes only the obsolete ZotFile symlinks once the UUID link is confirmed.
- **devonzot_sync_controller.py** – Async supervisor that launches creator/cleaner daemons, restarts them if they crash, and centralizes logging.
- **devonzot_controller.py** – Simpler controller with manual start/status/test modes if you prefer classic subprocess supervision.
- **start_sync.sh / stop_sync.sh** – Friendly wrappers to launch or halt the controller plus any lingering child processes.

### Maintenance + Safety Utilities
- **check_attachments.py / attachment_pairs.json** – Track every legacy->UUID mapping for audits and reruns.
- **cleanup_and_confirm.py / cleanup_attachments.py** – Remove junk DEVONthink-tagged attachments, confirm deletions, and reset test data.
- **debug_api.py / debug_create_attachment.py** – Narrow-scope repro scripts for the Zotero API linkMode limitations.
- **results_summary.py / safety_analysis.py** – Narrative reports comparing the SQLite vs API approaches and documenting interruption-handling plans.
- **test_api_conversion.py / test_real_conversion.py / devonzot_add_new.py / devonzot_safe_api.py** – Focused test harnesses for proving new UUID conversions before running the full daemon.

> 💡 Tip: Run ./start_sync.sh once, tail creator.log or cleaner.log for live progress, and ./stop_sync.sh to halt everything cleanly.

## 🚀 Quick Start

### Prerequisites
- **macOS** with DEVONthink 3
- **Zotero** with existing attachment library
- **Python 3.8+** with asyncio support

### Installation

```bash
# Clone and setup
cd /Users/$(whoami)
git clone <repository-url> DEVONzot
cd DEVONzot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure Credentials

```bash
# Copy the template and fill in your private Zotero values
cp .env.example .env
open .env
```

- `ZOTERO_API_KEY` and `ZOTERO_USER_ID` stay in `.env`, which remains ignored by git.
- All CLI tools now call `python-dotenv` automatically, so credentials load without extra flags once the file exists.
- Rotate keys by editing `.env`; no Python changes required.

### First Run (Dry Run Recommended)

```bash
# Test migration without making changes
python3 devonzot_service.py --dry-run

# Review the analysis output, then run for real
python3 devonzot_service.py --once

# Set up automatic service (cron)
python3 devonzot_service.py  # Runs continuously
```

### Automation Setup

```bash
# Add to crontab for invisible automation
crontab -e

# Add this line for hourly sync:
0 * * * * cd /Users/$(whoami)/DEVONzot && python3 devonzot_service.py --once >/dev/null 2>&1
```

## 📋 Usage Modes

### `--dry-run` - Analysis Mode
- **Purpose**: Analyze migration workload without making changes
- **Output**: Comprehensive conflict detection and migration estimates
- **Use Case**: First-time setup, troubleshooting, capacity planning

### `--once` - Single Run Mode  
- **Purpose**: Run one complete sync cycle then exit
- **Use Case**: Manual sync, cron job execution, testing
- **Logging**: Full operation logs with progress tracking

### Service Mode (Default)
- **Purpose**: Continuous monitoring with periodic sync cycles
- **Features**: Change detection, signal handling, automatic restart
- **Use Case**: Long-running service for real-time integration

## 🔧 Core Components

### `devonzot_service.py` - Main Service
The production-ready async service handling all integration tasks:

- **AsyncZoteroDevonthinkSync**: Main orchestration class
- **Batch Processing**: Concurrent DEVONthink operations
- **State Management**: JSON-based progress tracking
- **Error Recovery**: Graceful handling of all failure modes

### Key Features:
- **6,162 symlink conversion** capability (tested workload)
- **~3-4 hour migration time** (down from 50+ hours)
- **50-item batch processing** with async concurrency
- **Comprehensive conflict detection** before migration

## 📊 Performance Metrics

### Migration Performance
- **Sequential Processing**: ~30 seconds per item (50+ hours total)
- **Async Batch Processing**: ~3 seconds per item (~4 hours total)  
- **Speed Improvement**: **12-15x faster**
- **Batch Size**: 50 items processed concurrently

### Real-World Testing
- **6,162 ZotFile symlinks** successfully analyzed
- **267 stored attachments** ready for migration
- **1 filename collision** detected and flagged
- **14 problematic items** identified for manual review

## 🛠️ Technical Details

### Database Integration
- **Zotero**: Read-only SQLite access with change detection
- **Attachment Types**: Handles linkMode=2 (linked files) conversion
- **Metadata Extraction**: Title, authors, collections, tags, notes

### DEVONthink Integration
- **AppleScript Automation**: Robust search and linking operations
- **UUID-Based Links**: `x-devonthink-item://` permanent references
- **Metadata Preservation**: Native DEVONthink properties + macOS attributes

### macOS Integration
- **Extended Attributes**: `kMDItemAuthors`, `kMDItemTitle`, `kMDItemDescription`
- **Spotlight Integration**: Full-text search across synced metadata
- **File Monitoring**: Change detection via database hash comparison

## 📝 Logging and Monitoring

### Service Logs (`service.log`)
- **Progress Tracking**: Real-time processing status
- **Error Reporting**: Detailed failure analysis with stack traces
- **Performance Metrics**: Timing data for optimization

### State Tracking (`service_state.json`)
- **Migration Progress**: Completed/pending item tracking
- **Conflict Records**: Filename collisions and resolutions
- **Database Hashes**: Change detection across service runs

## 🎯 Mobile Workflow

### iOS/iPadOS Integration
1. **DEVONthink To Go**: Access all synced documents with full metadata
2. **Spotlight Search**: Find documents by author, title, or content
3. **Cross-App Links**: Tap UUID links to jump between apps
4. **Offline Access**: Full document library available without internet

### Zotero Mobile Sync
- **Metadata Changes**: Sync back to Zotero on next desktop session
- **New Attachments**: Auto-discover and integrate new files
- **Collection Management**: Maintain folder structure across platforms

## ⚙️ Configuration Options

### Environment Variables
```bash
export DEVONZOT_WAIT_TIME=3        # Seconds between operations (default: 3)
export DEVONZOT_BATCH_SIZE=50      # Items per batch (default: 50)  
export DEVONZOT_DRY_RUN=true       # Force dry-run mode
export DEVONZOT_LOG_LEVEL=INFO     # Logging verbosity
```

### Service Customization
- **Sync Frequency**: Adjust cron schedule for your workflow
- **Batch Size**: Tune for your system's AppleScript performance  
- **Wait Times**: Balance speed vs system responsiveness
- **Conflict Handling**: Manual vs automatic resolution policies

## 🐛 Troubleshooting

### Common Issues

**AppleScript Timeouts**
```bash
# Increase wait times for slower systems
export DEVONZOT_WAIT_TIME=5
python3 devonzot_service.py --dry-run
```

**Database Lock Conflicts**  
```bash
# Ensure Zotero is closed during migration
ps aux | grep zotero
# Kill if running, then retry
```

**Filename Collisions**
```bash
# Review collision report in dry-run output
python3 devonzot_service.py --dry-run | grep "COLLISION"
# Manually resolve duplicates in Zotero
```

### Debug Mode
```bash
# Enable verbose logging
export DEVONZOT_LOG_LEVEL=DEBUG
python3 devonzot_service.py --once
```

## 🔄 Development History

### Evolution Path
1. **symlink_replacement.py** - Initial proof of concept
2. **production_metadata_sync.py** - Comprehensive sync system  
3. **invisible_sync.py** - Set-and-forget automation
4. **devonzot_service.py** - Production async optimization

### Performance Journey
- **v0.1**: Sequential processing (50+ hours for 6K items)
- **v0.5**: Basic automation with cron integration
- **v1.0**: Async batch processing (3-4 hours for 6K items)

## 📦 Dependencies

### Core Requirements
- **Python 3.8+**: Async/await support required
- **macOS 10.15+**: Extended attributes and AppleScript
- **DEVONthink 3**: AppleScript automation support
- **Zotero 6+**: SQLite database structure

### Python Packages
- psutil - process supervision utilities used by the controllers
- requests - Zotero Web API calls for the API-first toolchain
- python-dotenv - loads .env credentials for every standalone script
- pytest (dev only) - keeps the regression harness runnable locally

> ⚠️ The old "no external dependencies" note is outdated. Always run pip install -r requirements.txt after pulling.

## 🔐 Security & Privacy

### Data Handling
- **Read-Only Database Access**: Never modifies Zotero database
- **Local Operation Only**: No cloud services or external APIs
- **Metadata Privacy**: All operations stay on your Mac
- **Backup Friendly**: State files are human-readable JSON

### Permissions Required
- **Full Disk Access**: Required for Zotero database read access
- **Automation**: AppleScript control of DEVONthink
- **File System**: Read/write access to attachment directories

## 🚧 Roadmap

### Future Enhancements
- [ ] **Bi-directional Sync**: DEVONthink changes → Zotero  
- [ ] **Attachment Import**: Direct file import to DEVONthink
- [ ] **Collection Mapping**: Smart folder creation based on collections
- [ ] **Web Clipper Integration**: Direct capture to both apps
- [ ] **Multi-Library Support**: Handle multiple Zotero profiles

### Performance Optimizations
- [ ] **Parallel AppleScript**: Multiple DEVONthink connections
- [ ] **Incremental Sync**: Track individual item changes
- [ ] **Memory Optimization**: Stream processing for large libraries  
- [ ] **Cache Layer**: Persistent DEVONthink item cache

## 📄 License

MIT License - See LICENSE file for details

## 🤝 Contributing

This project represents a complete solution for Zotero-DEVONthink integration. Contributions welcome for:

- **Performance optimizations**  
- **Additional metadata fields**
- **Error handling improvements**
- **Mobile workflow enhancements**

## 📞 Support

### Documentation
- **Service Logs**: Check `service.log` for detailed operation history
- **State Files**: Review `service_state.json` for progress tracking  
- **Dry Run Analysis**: Always test with `--dry-run` before major operations

### Community
- **Issue Reports**: Include full log output and system configuration
- **Feature Requests**: Describe your specific workflow needs
- **Performance Issues**: Include timing data and system specifications

---

**DEVONzot v1.0.0** - Invisible, Bulletproof Zotero-DEVONthink Integration  
*"Set it and forget it" - Your research workflow deserves better than fragile symlinks.*
