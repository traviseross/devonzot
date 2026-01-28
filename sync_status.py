#!/usr/bin/env python3
"""Quick status check for invisible sync"""
import json
from pathlib import Path
from datetime import datetime

def check_status():
    state_file = Path.home() / ".zotero_devonthink_state.json"
    log_file = Path.home() / "zotero_devonthink_sync.log"
    
    print("🤖 INVISIBLE ZOTERO SYNC STATUS")
    print("=" * 40)
    
    # Check state
    if state_file.exists():
        try:
            with open(state_file) as f:
                state = json.load(f)
            
            last_sync = state.get('last_sync', 0)
            total_synced = state.get('total_synced', 0)
            
            if last_sync > 0:
                last_sync_time = datetime.fromtimestamp(last_sync)
                print(f"✅ Last sync: {last_sync_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"📊 Total items synced: {total_synced}")
            else:
                print("⚠️  No successful syncs yet")
                
        except Exception as e:
            print(f"❌ Could not read state: {e}")
    else:
        print("⚠️  No sync state found - may not have run yet")
    
    # Check log
    if log_file.exists():
        try:
            with open(log_file) as f:
                lines = f.readlines()
            
            recent_lines = lines[-10:] if len(lines) > 10 else lines
            print(f"\n📝 Recent log entries:")
            for line in recent_lines:
                print(f"   {line.strip()}")
                
        except Exception as e:
            print(f"❌ Could not read log: {e}")
    else:
        print("📝 No log file found yet")

if __name__ == "__main__":
    check_status()
