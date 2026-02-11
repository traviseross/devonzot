#!/usr/bin/env python3
"""
DEVONzot Continuous Sync Controller v3.0
"Set and forget" controller for continuous Zotero-DEVONthink sync
Runs both Creator and Cleaner processes safely in background
"""

import asyncio
import subprocess
import signal
import sys
import time
from pathlib import Path
from datetime import datetime
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - CONTROLLER - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/Users/travisross/DEVONzot/controller.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DEVONzotController:
    """Controls both Creator and Cleaner processes"""
    
    def __init__(self):
        self.creator_process = None
        self.cleaner_process = None
        self.running = False
        self.base_dir = Path('/Users/travisross/DEVONzot')
    
    async def start_processes(self):
        """Start both Creator and Cleaner processes"""
        logger.info("🚀 Starting DEVONzot Continuous Sync...")
        
        try:
            # Start Creator process
            logger.info("▶️ Starting Creator (UUID attachment creation)...")
            self.creator_process = await asyncio.create_subprocess_exec(
                sys.executable, 'devonzot_creator.py', '--daemon',
                cwd=self.base_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Wait a moment for Creator to start
            await asyncio.sleep(3)
            
            # Start Cleaner process  
            logger.info("▶️ Starting Cleaner (redundant attachment cleanup)...")
            self.cleaner_process = await asyncio.create_subprocess_exec(
                sys.executable, 'devonzot_cleaner.py', '--daemon',
                cwd=self.base_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            logger.info("✅ Both processes started successfully")
            logger.info("📊 Monitor logs:")
            logger.info(f"  Creator: {self.base_dir}/creator.log")
            logger.info(f"  Cleaner: {self.base_dir}/cleaner.log")
            logger.info(f"  Controller: {self.base_dir}/controller.log")
            
            self.running = True
            
        except Exception as e:
            logger.error(f"💥 Error starting processes: {e}")
            await self.stop_processes()
            raise
    
    async def monitor_processes(self):
        """Monitor and restart processes if they crash"""
        logger.info("👁️ Monitoring processes...")
        
        while self.running:
            try:
                # Check Creator process
                if self.creator_process and self.creator_process.returncode is not None:
                    logger.warning("⚠️ Creator process died, restarting...")
                    self.creator_process = await asyncio.create_subprocess_exec(
                        sys.executable, 'devonzot_creator.py', '--daemon',
                        cwd=self.base_dir,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                
                # Check Cleaner process
                if self.cleaner_process and self.cleaner_process.returncode is not None:
                    logger.warning("⚠️ Cleaner process died, restarting...")
                    self.cleaner_process = await asyncio.create_subprocess_exec(
                        sys.executable, 'devonzot_cleaner.py', '--daemon',
                        cwd=self.base_dir,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                
                # Wait before next check
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"Error monitoring processes: {e}")
                await asyncio.sleep(30)
    
    async def stop_processes(self):
        """Gracefully stop both processes"""
        logger.info("🛑 Stopping DEVONzot processes...")
        self.running = False
        
        # Stop Creator
        if self.creator_process and self.creator_process.returncode is None:
            try:
                self.creator_process.terminate()
                await asyncio.wait_for(self.creator_process.wait(), timeout=10)
                logger.info("✅ Creator stopped gracefully")
            except asyncio.TimeoutError:
                logger.warning("⏰ Creator didn't stop gracefully, killing...")
                self.creator_process.kill()
            except Exception as e:
                logger.error(f"Error stopping Creator: {e}")
        
        # Stop Cleaner
        if self.cleaner_process and self.cleaner_process.returncode is None:
            try:
                self.cleaner_process.terminate()
                await asyncio.wait_for(self.cleaner_process.wait(), timeout=10)
                logger.info("✅ Cleaner stopped gracefully")
            except asyncio.TimeoutError:
                logger.warning("⏰ Cleaner didn't stop gracefully, killing...")
                self.cleaner_process.kill()
            except Exception as e:
                logger.error(f"Error stopping Cleaner: {e}")
        
        logger.info("🔒 All processes stopped")
    
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            logger.info(f"📡 Received signal {signum}, shutting down...")
            asyncio.create_task(self.stop_processes())
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def run_continuous(self):
        """Run continuous sync"""
        self.setup_signal_handlers()
        
        try:
            await self.start_processes()
            await self.monitor_processes()
        except KeyboardInterrupt:
            logger.info("🛑 Stopped by user")
        except Exception as e:
            logger.error(f"💥 Controller error: {e}")
        finally:
            await self.stop_processes()
    
    def show_status(self):
        """Show current process status"""
        print("\n🔍 DEVONzot Continuous Sync Status")
        print("=" * 45)
        
        # Check if processes are running using simpler method
        import subprocess
        
        try:
            # Check for running processes using ps
            result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
            processes = result.stdout
            
            creator_running = 'devonzot_creator.py --daemon' in processes
            cleaner_running = 'devonzot_cleaner.py --daemon' in processes
            
            if creator_running:
                print("✅ Creator: Running")
            else:
                print("❌ Creator: Not running")
            
            if cleaner_running:
                print("✅ Cleaner: Running")
            else:
                print("❌ Cleaner: Not running")
            
        except Exception as e:
            print(f"⚠️ Could not check process status: {e}")
        
        # Show log files
        print(f"\n📄 Log files:")
        for log_file in ['creator.log', 'cleaner.log', 'controller.log']:
            log_path = self.base_dir / log_file
            if log_path.exists():
                size = log_path.stat().st_size
                mtime = datetime.fromtimestamp(log_path.stat().st_mtime)
                print(f"  {log_file}: {size:,} bytes, modified {mtime.strftime('%Y-%m-%d %H:%M')}")
            else:
                print(f"  {log_file}: Not found")
        
        print("\n🎯 Commands:")
        print("  python3 devonzot_controller.py --start    # Start continuous sync")
        print("  python3 devonzot_controller.py --status   # Show this status")
        print("  python3 devonzot_controller.py --stop     # Stop all processes")
        print("=" * 45)
    
    def stop_all(self):
        """Stop all running processes"""
        import psutil
        
        stopped = 0
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline'])
                if ('devonzot_creator.py --daemon' in cmdline or 
                    'devonzot_cleaner.py --daemon' in cmdline):
                    print(f"🛑 Stopping PID {proc.info['pid']}...")
                    proc.terminate()
                    stopped += 1
            except:
                continue
        
        if stopped > 0:
            print(f"✅ Stopped {stopped} processes")
        else:
            print("ℹ️ No DEVONzot processes found running")

async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="DEVONzot Continuous Sync Controller")
    parser.add_argument('--start', action='store_true', help='Start continuous sync')
    parser.add_argument('--status', action='store_true', help='Show process status')
    parser.add_argument('--stop', action='store_true', help='Stop all processes')
    
    args = parser.parse_args()
    
    controller = DEVONzotController()
    
    if args.start:
        print("🚀 Starting DEVONzot Continuous Sync...")
        print("📝 This will run continuously until interrupted")
        print("🔄 Press Ctrl+C to stop")
        await controller.run_continuous()
    elif args.status:
        controller.show_status()
    elif args.stop:
        controller.stop_all()
    else:
        print("🔄 DEVONzot Continuous Sync Controller")
        print("=" * 40)
        print("Safe two-process architecture:")
        print("• Creator: Finds file attachments → creates UUID links")
        print("• Cleaner: Finds redundant pairs → removes file attachments")
        print("=" * 40)
        print("Usage:")
        print("  --start   Start continuous sync daemon")
        print("  --status  Show current process status")
        print("  --stop    Stop all running processes")

if __name__ == "__main__":
    asyncio.run(main())