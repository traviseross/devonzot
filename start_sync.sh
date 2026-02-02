#!/bin/bash
# DEVONzot "Set and Forget" Startup Script
# Run this once to start continuous background sync

# Change to the DEVONzot directory
cd /Users/travisross/DEVONzot

# Start the controller in background mode
echo "🚀 Starting DEVONzot continuous sync..."
echo "This will run in the background and handle all your sync needs."
echo ""

# Start the sync controller
nohup python3 devonzot_sync_controller.py --start > sync.log 2>&1 &

# Get the PID
PID=$!

echo "✅ Started with PID: $PID"
echo "📊 Logs will be written to:"
echo "   • Main log: sync.log"
echo "   • Creator: creator.log"
echo "   • Cleaner: cleaner.log"
echo ""
echo "To check status: python3 devonzot_sync_controller.py --status"
echo "To stop: kill $PID"
echo ""
echo "🎉 You're all set! The system will now:"
echo "   • Find file attachments and create UUID versions"
echo "   • Safely remove file attachments when UUID versions exist"
echo "   • Run continuously in the background"
echo "   • Restart processes if they fail"
echo ""
echo "It's completely 'set and forget' - just let it run!"