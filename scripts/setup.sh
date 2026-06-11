#!/usr/bin/env bash
# DEVONzot setup script — run on any machine to rebuild the venv and sync deps.
#
# Usage:
#   ./scripts/setup.sh           # venv only (MBP / dev machine)
#   ./scripts/setup.sh --deploy  # venv + install and start launchd service (iMac ONLY)
#
# NOTE: --deploy installs a persistent background service that modifies your
# Zotero library and DEVONthink database. Only run it on the designated
# deployment machine (iMac). Running it on two machines simultaneously
# will cause duplicate documents and data corruption.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY=false

for arg in "$@"; do
    case "$arg" in
        --deploy) DEPLOY=true ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

# ── 1. Find best available Python ────────────────────────────────────────────

PYTHON=""
# Check both by name (interactive shells) and by full path (SSH/non-login shells)
PYTHON_CANDIDATES=(
    python3.13 python3.12 python3.14 python3
    /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.14
    /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.14
)
for candidate in "${PYTHON_CANDIDATES[@]}"; do
    if [[ -x "$candidate" ]] || command -v "$candidate" &>/dev/null; then
        full=$(command -v "$candidate" 2>/dev/null || echo "$candidate")
        if "$full" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            PYTHON="$full"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "ERROR: Python 3.10+ not found. Install via: brew install python@3.12"
    exit 1
fi

echo "Using Python: $PYTHON ($($PYTHON --version))"

# ── 2. Rebuild venv ──────────────────────────────────────────────────────────

echo "Rebuilding venv..."
"$PYTHON" -m venv "$REPO/venv" --clear
"$REPO/venv/bin/pip" install --quiet --upgrade pip
"$REPO/venv/bin/pip" install --quiet -r "$REPO/src/requirements.txt"
echo "Venv ready: $REPO/venv"

# ── 3. Remove retired com.devonzot.addnew plist if present ──────────────────

OLD_PLIST="$HOME/Library/LaunchAgents/com.devonzot.addnew.plist"
if [[ -f "$OLD_PLIST" ]]; then
    echo "Removing retired com.devonzot.addnew job..."
    launchctl bootout "gui/$(id -u)/com.devonzot.addnew" 2>/dev/null || true
    launchctl unload "$OLD_PLIST" 2>/dev/null || true
    rm "$OLD_PLIST"
    echo "Removed $OLD_PLIST"
fi

# ── 4. Deploy (iMac only) ────────────────────────────────────────────────────

if [[ "$DEPLOY" == true ]]; then
    PLIST_SRC="$REPO/com.devonzot.service.plist"
    PLIST_DEST="$HOME/Library/LaunchAgents/com.devonzot.service.plist"

    echo "Installing service plist..."
    sed "s|__HOME__|$HOME|g" "$PLIST_SRC" > "$PLIST_DEST"
    plutil -lint "$PLIST_DEST"

    # Unload any previous version first
    launchctl bootout "gui/$(id -u)/com.devonzot.service" 2>/dev/null || true

    launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
    echo "Service started: com.devonzot.service"
    echo ""
    launchctl list | grep devonzot || true
else
    echo ""
    echo "Venv rebuilt. Service NOT installed (dev machine mode)."
    echo "To deploy on the iMac, run: ./scripts/setup.sh --deploy"
fi
