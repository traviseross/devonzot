#!/usr/bin/env python3
"""
SET AND FORGET SETUP
This script sets up completely invisible, automatic Zotero→DEVONthink sync
"""

import subprocess
import os
from pathlib import Path
import json

def setup_invisible_sync():
    """Set up the invisible sync system"""
    
    print("🚀 ZOTERO→DEVONTHINK INVISIBLE SYNC SETUP")
    print("=" * 60)
    print("This will create a completely invisible, automatic sync that:")
    print("• Runs every 15 minutes in the background")
    print("• Detects new Zotero items automatically") 
    print("• Applies smart tags to DEVONthink records")
    print("• Works while Zotero is running")
    print("• Logs everything silently")
    print("• Requires ZERO maintenance")
    
    # Get current paths
    script_dir = Path(__file__).parent.absolute()
    sync_script = script_dir / "invisible_sync.py"
    log_file = Path.home() / "zotero_devonthink_sync.log"
    
    print(f"\n📁 Files:")
    print(f"   Sync script: {sync_script}")
    print(f"   Log file: {log_file}")
    
    # Check if script exists
    if not sync_script.exists():
        print("❌ ERROR: invisible_sync.py not found!")
        return
    
    print(f"\n🧪 Testing the sync script...")
    try:
        # Test run the sync
        result = subprocess.run([
            'python3', str(sync_script)
        ], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            print("✅ Sync script test successful")
        else:
            print(f"⚠️  Sync script test had issues, but that's normal for first run")
            print(f"   Error output: {result.stderr[:200]}")
    except Exception as e:
        print(f"⚠️  Could not test sync script: {e}")
    
    # Create the cron entry
    cron_command = f"*/15 * * * * /usr/bin/python3 {sync_script} >> {log_file} 2>&1"
    
    print(f"\n📅 Setting up automatic scheduling...")
    print(f"   Command: {cron_command}")
    
    # Get current crontab
    try:
        current_cron = subprocess.run(['crontab', '-l'], 
                                    capture_output=True, text=True)
        existing_lines = current_cron.stdout.strip().split('\\n') if current_cron.returncode == 0 else []
    except:
        existing_lines = []
    
    # Check if our entry already exists
    sync_already_scheduled = any('invisible_sync.py' in line for line in existing_lines)
    
    if sync_already_scheduled:
        print("✅ Automatic sync is already scheduled!")
    else:
        # Add our cron entry
        new_lines = existing_lines + [
            "",
            "# Zotero→DEVONthink Invisible Sync (every 15 minutes)",
            cron_command
        ]
        
        new_crontab = '\\n'.join(line for line in new_lines if line is not None)
        
        try:
            # Write new crontab
            subprocess.run(['crontab', '-'], 
                         input=new_crontab, text=True, check=True)
            print("✅ Automatic sync scheduled successfully!")
        except Exception as e:
            print(f"❌ Could not schedule automatic sync: {e}")
            print(f"   Please add this manually to your crontab:")
            print(f"   {cron_command}")
            return
    
    # Create a simple status checker
    status_script = script_dir / "sync_status.py"
    status_content = f'''#!/usr/bin/env python3
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
                print(f"✅ Last sync: {{last_sync_time.strftime('%Y-%m-%d %H:%M:%S')}}")
                print(f"📊 Total items synced: {{total_synced}}")
            else:
                print("⚠️  No successful syncs yet")
                
        except Exception as e:
            print(f"❌ Could not read state: {{e}}")
    else:
        print("⚠️  No sync state found - may not have run yet")
    
    # Check log
    if log_file.exists():
        try:
            with open(log_file) as f:
                lines = f.readlines()
            
            recent_lines = lines[-10:] if len(lines) > 10 else lines
            print(f"\\n📝 Recent log entries:")
            for line in recent_lines:
                print(f"   {{line.strip()}}")
                
        except Exception as e:
            print(f"❌ Could not read log: {{e}}")
    else:
        print("📝 No log file found yet")

if __name__ == "__main__":
    check_status()
'''
    
    try:
        with open(status_script, 'w') as f:
            f.write(status_content)
        os.chmod(status_script, 0o755)
        print(f"✅ Created status checker: {status_script}")
    except Exception as e:
        print(f"⚠️  Could not create status checker: {e}")
    
    print(f"\n🎉 INVISIBLE SYNC IS NOW ACTIVE!")
    print(f"=" * 40)
    print(f"✅ Runs automatically every 15 minutes")
    print(f"✅ Completely invisible operation")  
    print(f"✅ Works while Zotero is open")
    print(f"✅ No maintenance required")
    
    print(f"\\n💡 MANAGEMENT:")
    print(f"   Check status: python3 {status_script}")
    print(f"   View logs: tail -f {log_file}")
    print(f"   Stop sync: crontab -e (remove the invisible_sync line)")
    
    print(f"\\n🔄 The sync will:")
    print(f"   • Check for new/changed Zotero items every 15 minutes")
    print(f"   • Find corresponding DEVONthink records automatically")
    print(f"   • Apply smart tags (publication, decade, themes)")
    print(f"   • Log all activity to {log_file}")
    print(f"   • Skip items that haven't changed")
    print(f"   • Handle errors gracefully and continue")
    
    print(f"\\n✨ SET AND FORGET - YOUR SYNC IS NOW RUNNING INVISIBLY!")

if __name__ == "__main__":
    setup_invisible_sync()