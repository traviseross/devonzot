#!/bin/bash
# DEVONzot Stop Script
# Stops the continuous sync processes

echo "🛑 Stopping DEVONzot sync processes..."

# Find and kill the controller process
CONTROLLER_PID=$(pgrep -f "devonzot_sync_controller.py --start")
if [ ! -z "$CONTROLLER_PID" ]; then
    echo "Stopping controller (PID: $CONTROLLER_PID)..."
    kill $CONTROLLER_PID
    sleep 2
fi

# Find and kill creator/cleaner processes
CREATOR_PID=$(pgrep -f "devonzot_creator.py --daemon")
if [ ! -z "$CREATOR_PID" ]; then
    echo "Stopping creator (PID: $CREATOR_PID)..."
    kill $CREATOR_PID
fi

CLEANER_PID=$(pgrep -f "devonzot_cleaner.py --daemon")
if [ ! -z "$CLEANER_PID" ]; then
    echo "Stopping cleaner (PID: $CLEANER_PID)..."
    kill $CLEANER_PID
fi

sleep 2

# Check if anything is still running
REMAINING=$(pgrep -f "devonzot")
if [ ! -z "$REMAINING" ]; then
    echo "⚠️ Some processes still running, force killing..."
    pkill -f "devonzot"
fi

echo "✅ All DEVONzot processes stopped."
echo ""
echo "To restart: ./start_sync.sh"