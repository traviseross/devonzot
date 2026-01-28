# Installation Guide

Complete setup instructions for DEVONzot v1.0.0

## System Requirements

### Hardware Requirements
- **Mac**: Intel or Apple Silicon (M1/M2)
- **RAM**: 4GB minimum, 8GB recommended for large libraries
- **Storage**: 100MB for DEVONzot + space for your document libraries
- **Network**: Not required (entirely local operation)

### Software Requirements
- **macOS**: 10.15 (Catalina) or later
- **Python**: 3.8 or later (3.13 recommended)
- **DEVONthink**: DEVONthink 3 (any edition)
- **Zotero**: Version 6 or later

### Permissions Setup

#### 1. Full Disk Access (Required)
DEVONzot needs to read Zotero's database file:

```bash
System Preferences → Security & Privacy → Privacy → Full Disk Access
```
Add Terminal.app and/or your Python installation

#### 2. Automation Access (Required)  
For AppleScript control of DEVONthink:

```bash
System Preferences → Security & Privacy → Privacy → Automation
```
Allow Terminal (or Python) to control DEVONthink

## Installation Methods

### Method 1: Git Clone (Recommended)
```bash
# Create project directory
cd /Users/$(whoami)
git clone <repository-url> DEVONzot
cd DEVONzot

# Set up Python environment
python3 -m venv venv
source venv/bin/activate

# Install (no external dependencies needed)
pip install -r requirements.txt
```

### Method 2: Download and Setup
```bash
# Download project files to /Users/$(whoami)/DEVONzot
# Then:
cd /Users/$(whoami)/DEVONzot
python3 -m venv venv
source venv/bin/activate
```

## Verification Steps

### 1. Test Python Environment
```bash
cd /Users/$(whoami)/DEVONzot
source venv/bin/activate
python3 --version  # Should show 3.8+
```

### 2. Test DEVONthink Connection
```bash
# This should succeed without errors:
osascript -e 'tell application "DEVONthink 3" to get version'
```

### 3. Test Zotero Database Access
```bash
# Run database exploration (safe read-only test):
python3 explore_zotero_db.py
```

### 4. Initial Dry Run
```bash
# Full system test without making changes:
python3 devonzot_service.py --dry-run
```

## Troubleshooting Installation

### Python Path Issues
```bash
# If python3 not found, install Xcode Command Line Tools:
xcode-select --install

# Or use Homebrew:
brew install python3
```

### Permission Errors
```bash
# If database access fails:
# 1. Ensure Zotero is closed
# 2. Check Full Disk Access permissions
# 3. Try running with sudo (not recommended for production)
```

### DEVONthink Connection Issues
```bash
# Test AppleScript access:
osascript -e 'tell application "DEVONthink 3" to activate'

# If this fails, check:
# 1. DEVONthink 3 is installed and licensed
# 2. Automation permissions are granted
# 3. DEVONthink is not blocking AppleScript
```

## First Run Configuration

### 1. Backup Your Data
```bash
# Backup Zotero library (File → Export Library)
# Backup DEVONthink databases (File → Export → Files and Folders)
```

### 2. Close Applications
```bash
# Ensure clean database access:
pkill -f Zotero
pkill -f "DEVONthink"
# Then reopen only DEVONthink
```

### 3. Initial Analysis
```bash
# Run comprehensive analysis:
python3 devonzot_service.py --dry-run

# Review output for:
# - Number of attachments to migrate  
# - Potential filename conflicts
# - Problematic items requiring attention
```

### 4. Resolve Conflicts (If Any)
```bash
# Address filename collisions in Zotero:
# 1. Find duplicate items
# 2. Merge or rename as needed
# 3. Re-run dry-run to verify resolution
```

## Production Deployment

### Manual Operation
```bash
# Single migration run:
python3 devonzot_service.py --once

# Continuous service:
python3 devonzot_service.py
```

### Automated Operation (Cron)
```bash
# Edit crontab:
crontab -e

# Add hourly sync (adjust path as needed):
0 * * * * cd /Users/$(whoami)/DEVONzot && source venv/bin/activate && python3 devonzot_service.py --once >/dev/null 2>&1

# Or daily sync at 2 AM:
0 2 * * * cd /Users/$(whoami)/DEVONzot && source venv/bin/activate && python3 devonzot_service.py --once >/dev/null 2>&1
```

### Service Monitoring
```bash
# Check service logs:
tail -f service.log

# Check service state:
python3 sync_status.py

# Monitor system resources:
ps aux | grep devonzot
```

## Directory Structure

After installation, your directory should look like:

```
DEVONzot/
├── devonzot_service.py      # Main service (production ready)
├── README.md                # Complete documentation
├── CHANGELOG.md             # Version history
├── LICENSE                  # MIT license
├── requirements.txt         # Python dependencies (minimal)
├── venv/                    # Python virtual environment
├── service.log             # Runtime logs (created on first run)
├── service_state.json      # State tracking (created on first run)
└── [test scripts]/         # Development and testing files
```

## Environment Variables

Optional customization:

```bash
# Performance tuning:
export DEVONZOT_WAIT_TIME=3        # Seconds between operations
export DEVONZOT_BATCH_SIZE=50      # Items per batch

# Operation modes:
export DEVONZOT_DRY_RUN=true       # Force dry-run mode
export DEVONZOT_LOG_LEVEL=DEBUG    # Verbose logging

# Add to your shell profile (~/.zshrc) for persistence
```

## Uninstallation

If you need to remove DEVONzot:

```bash
# Stop any running services:
pkill -f devonzot_service.py

# Remove cron jobs:
crontab -e  # Delete any DEVONzot entries

# Remove project directory:
rm -rf /Users/$(whoami)/DEVONzot

# Note: Your Zotero and DEVONthink data is never modified
# Any created UUID links will continue to work
```

## Support Resources

- **Documentation**: See README.md for complete usage guide
- **Logs**: Check `service.log` for detailed operation history  
- **State**: Review `service_state.json` for progress tracking
- **Testing**: Always use `--dry-run` before major operations