#!/usr/bin/env python3
"""
DEVONzot Control Script - "Set and forget" two-process architecture
Manages Creator and Cleaner processes for continuous background operation
"""

import subprocess
import time
import json
import signal
import sys
from pathlib import Path
from datetime import datetime

class DEVONzotController:
    """Controller for the two-process architecture"""
    
    def __init__(self):
        self.base_dir = Path('/Users/travisross/DEVONzot')
        self.creator_script = self.base_dir / 'devonzot_creator.py'
        self.cleaner_script = self.base_dir / 'devonzot_cleaner.py'
        self.creator_process = None
        self.cleaner_process = None
        self.running = False
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print(f"\n🛑 Received signal {signum}, shutting down gracefully...")
        self.stop()
        sys.exit(0)
    
    def start_creator(self):
        """Start the Creator process"""
        try:
            self.creator_process = subprocess.Popen([
                'python3', str(self.creator_script), '--daemon'
            ], cwd=str(self.base_dir))
            print(f"🔗 Started Creator (PID: {self.creator_process.pid})")
            return True
        except Exception as e:
            print(f"❌ Failed to start Creator: {e}")
            return False
    
    def start_cleaner(self):
        """Start the Cleaner process"""
        try:
            # Start cleaner 30 seconds after creator to let it build up UUID attachments
            time.sleep(30)
            self.cleaner_process = subprocess.Popen([
                'python3', str(self.cleaner_script), '--daemon'
            ], cwd=str(self.base_dir))
            print(f"🧹 Started Cleaner (PID: {self.cleaner_process.pid})")
            return True
        except Exception as e:
            print(f"❌ Failed to start Cleaner: {e}")
            return False
    
    def start(self):
        """Start both processes"""
        print("🚀 Starting DEVONzot continuous sync...")
        print("Creator: Finds file attachments → creates UUID attachments")
        print("Cleaner: Finds redundant file attachments → removes them safely")
        print("")
        
        self.running = True
        
        # Start Creator first
        if not self.start_creator():
            return False
        
        # Start Cleaner after delay
        if not self.start_cleaner():
            return False
        
        print("✅ Both processes running. Press Ctrl+C to stop.")
        print(f"📊 Logs: creator.log, cleaner.log")
        print("")
        
        # Monitor processes
        self.monitor()
    
    def monitor(self):
        """Monitor both processes"""
        while self.running:
            try:
                time.sleep(60)  # Check every minute
                
                # Check if processes are still running
                if self.creator_process and self.creator_process.poll() is not None:
                    print("⚠️ Creator process died, restarting...")
                    self.start_creator()
                
                if self.cleaner_process and self.cleaner_process.poll() is not None:
                    print("⚠️ Cleaner process died, restarting...")
                    self.start_cleaner()
                    
            except KeyboardInterrupt:
                break
    
    def stop(self):
        """Stop both processes"""
        self.running = False
        
        print("🛑 Stopping processes...")
        
        if self.creator_process:
            try:
                self.creator_process.terminate()
                self.creator_process.wait(timeout=10)
                print("✅ Creator stopped")
            except:
                self.creator_process.kill()
                print("🔨 Creator killed")
        
        if self.cleaner_process:
            try:
                self.cleaner_process.terminate()
                self.cleaner_process.wait(timeout=10)
                print("✅ Cleaner stopped")
            except:
                self.cleaner_process.kill()
                print("🔨 Cleaner killed")
    
    def status(self):
        """Check status of processes"""
        print("📊 DEVONzot Status")
        print("=" * 40)
        
        # Check if process files exist
        creator_log = self.base_dir / 'creator.log'
        cleaner_log = self.base_dir / 'cleaner.log'
        
        if creator_log.exists():
            lines = creator_log.read_text().strip().split('\n')
            if lines:
                print(f"🔗 Creator last log: {lines[-1]}")
        else:
            print("🔗 Creator: No log file")
        
        if cleaner_log.exists():
            lines = cleaner_log.read_text().strip().split('\n')
            if lines:
                print(f"🧹 Cleaner last log: {lines[-1]}")
        else:
            print("🧹 Cleaner: No log file")
        
        print("=" * 40)
    
    def test_both(self):
        """Test both processes once"""
        print("🧪 Testing both processes...")
        
        print("\n🔗 Testing Creator:")
        subprocess.run(['python3', str(self.creator_script), '--once'], cwd=str(self.base_dir))
        
        print("\n🧹 Testing Cleaner:")
        subprocess.run(['python3', str(self.cleaner_script), '--once'], cwd=str(self.base_dir))

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="DEVONzot Controller - Set and forget sync")
    parser.add_argument('--start', action='store_true', help='Start continuous sync')
    parser.add_argument('--status', action='store_true', help='Check status')
    parser.add_argument('--test', action='store_true', help='Test both processes once')
    
    args = parser.parse_args()
    
    controller = DEVONzotController()
    
    if args.start:
        controller.start()
    elif args.status:
        controller.status()
    elif args.test:
        controller.test_both()
    else:
        print("DEVONzot Controller - Two-process 'set and forget' architecture")
        print("")
        print("Safe continuous sync that runs in background:")
        print("• Creator: Finds file attachments → creates UUID attachments")
        print("• Cleaner: Finds items with BOTH → removes file attachment safely")
        print("")
        print("Usage:")
        print("  --test    Test both processes once")
        print("  --start   Start continuous background sync")
        print("  --status  Check running status")
        print("")
        print("Features:")
        print("• Safe interruption handling")
        print("• Never deletes without confirmed redundancy")
        print("• Works while Zotero is running")
        print("• Automatic process restart on failure")
        print("• Detailed logging (creator.log, cleaner.log)")

if __name__ == "__main__":
    main()