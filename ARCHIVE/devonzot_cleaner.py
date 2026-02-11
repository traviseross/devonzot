#!/usr/bin/env python3
"""
DEVONzot Continuous Cleaner v3.0
Process B: Continuously finds items with BOTH file + UUID attachments and cleans up safely
Only acts when there's confirmed redundancy
"""

import asyncio
import json
import logging
import time
import requests
import os
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime

# Configuration
ZOTERO_API_KEY = "Iy9J3VIgfoXUHrHIGkRgzTEJ"
ZOTERO_USER_ID = "617019"
ZOTERO_API_BASE = "https://api.zotero.org"
API_VERSION = "3"
RATE_LIMIT_DELAY = 2.0
BATCH_SIZE = 3  # Conservative for deletion operations
CYCLE_DELAY = 120  # Longer wait for cleaner (2 minutes)
CONFIRMATION_DELAY = 10  # Wait N seconds before confirming deletion

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - CLEANER - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/Users/travisross/DEVONzot/cleaner.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ZoteroCleanerAPI:
    """Zotero API client focused on safe cleanup operations"""
    
    def __init__(self, api_key: str, user_id: str):
        self.api_key = api_key
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers.update({
            'Zotero-API-Version': API_VERSION,
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'DEVONzot-Cleaner/3.0'
        })
    
    def _safe_request(self, method: str, url: str, **kwargs):
        """API request with rate limiting"""
        time.sleep(RATE_LIMIT_DELAY)
        try:
            return self.session.request(method, url, timeout=30, **kwargs)
        except Exception as e:
            logger.error(f"API request failed: {e}")
            return None
    
    def find_redundant_attachments(self, limit: int = 100) -> List[Dict]:
        """Find parent items that have BOTH file and UUID attachments"""
        params = {
            'itemType': 'attachment',
            'limit': limit,
            'format': 'json'
        }
        
        response = self._safe_request('GET', f'{ZOTERO_API_BASE}/users/{self.user_id}/items', params=params)
        
        if not response or response.status_code != 200:
            return []
        
        items = response.json()
        
        # Group by parent item
        parent_groups = {}
        for item in items:
            data = item['data']
            parent_key = data.get('parentItem')
            if not parent_key:
                continue
            
            if parent_key not in parent_groups:
                parent_groups[parent_key] = {'file': [], 'uuid': [], 'parent_title': ''}
            
            if data.get('linkMode') == 'linked_file':
                parent_groups[parent_key]['file'].append({
                    'key': data['key'],
                    'title': data['title'],
                    'version': data['version'],
                    'path': data.get('path')
                })
            elif data.get('linkMode') == 'linked_url' and 'x-devonthink-item://' in data.get('url', ''):
                parent_groups[parent_key]['uuid'].append({
                    'key': data['key'],
                    'title': data['title'],
                    'url': data['url']
                })
        
        # Find parents with BOTH file and UUID attachments
        redundant_items = []
        for parent_key, attachments in parent_groups.items():
            if attachments['file'] and attachments['uuid']:
                # Get parent title
                parent_title = self.get_parent_title(parent_key)
                
                redundant_items.append({
                    'parent_key': parent_key,
                    'parent_title': parent_title,
                    'file_attachments': attachments['file'],
                    'uuid_attachments': attachments['uuid']
                })
        
        return redundant_items
    
    def get_parent_title(self, parent_key: str) -> str:
        """Get parent item title"""
        response = self._safe_request('GET', f'{ZOTERO_API_BASE}/users/{self.user_id}/items/{parent_key}')
        if response and response.status_code == 200:
            return response.json()['data'].get('title', 'Unknown')
        return 'Unknown'
    
    def verify_uuid_link_works(self, uuid_url: str) -> bool:
        """Verify the UUID link actually works by testing with DEVONthink"""
        try:
            # Extract UUID from URL
            if 'x-devonthink-item://' not in uuid_url:
                return False
            
            uuid = uuid_url.replace('x-devonthink-item://', '')
            
            # Test if UUID exists in DEVONthink
            script = f'''
            tell application "DEVONthink 3"
                try
                    set testRecord to get record with uuid "{uuid}"
                    return name of testRecord
                on error
                    return ""
                end try
            end tell
            '''
            
            import subprocess
            result = subprocess.run(['osascript', '-e', script], 
                                  capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0 and result.stdout.strip():
                return True
                
        except Exception as e:
            logger.debug(f"UUID verification failed: {e}")
        
        return False
    
    def delete_file_attachment(self, attachment_key: str, version: int) -> bool:
        """Safely delete file attachment"""
        headers = {'If-Unmodified-Since-Version': str(version)}
        response = self._safe_request(
            'DELETE', 
            f'{ZOTERO_API_BASE}/users/{self.user_id}/items/{attachment_key}',
            headers=headers
        )
        
        return response and response.status_code == 204
    
    def delete_symlink_file(self, file_path: str) -> bool:
        """Delete the actual symlink file from filesystem"""
        try:
            if file_path and os.path.exists(file_path):
                if os.path.islink(file_path):
                    os.unlink(file_path)
                    logger.info(f"🗑️ Deleted symlink: {file_path}")
                    return True
                else:
                    logger.warning(f"⚠️ Not a symlink, skipping: {file_path}")
            else:
                logger.debug(f"File doesn't exist: {file_path}")
            return True  # Consider success if file doesn't exist
        except Exception as e:
            logger.error(f"Error deleting symlink {file_path}: {e}")
            return False

class ContinuousCleaner:
    """Continuously cleans up redundant file attachments"""
    
    def __init__(self):
        self.zotero = ZoteroCleanerAPI(ZOTERO_API_KEY, ZOTERO_USER_ID)
        self.stats = {'cleaned': 0, 'verified': 0, 'skipped': 0, 'errors': 0, 'cycles': 0}
        self.running = False
    
    async def process_batch(self) -> Dict[str, int]:
        """Process one batch of redundant attachments"""
        results = {'cleaned': 0, 'verified': 0, 'skipped': 0, 'errors': 0}
        
        try:
            # Find items with both file and UUID attachments
            redundant_items = self.zotero.find_redundant_attachments()
            
            if not redundant_items:
                logger.debug("No redundant attachments found")
                return results
            
            logger.info(f"Found {len(redundant_items)} items with redundant attachments")
            
            # Process up to BATCH_SIZE items
            for item in redundant_items[:BATCH_SIZE]:
                if not self.running:
                    break
                
                try:
                    parent_title = item['parent_title'][:40] + "..."
                    logger.info(f"🔍 Checking redundancy for: {parent_title}")
                    
                    # Verify at least one UUID attachment works
                    working_uuid = False
                    for uuid_att in item['uuid_attachments']:
                        if self.zotero.verify_uuid_link_works(uuid_att['url']):
                            working_uuid = True
                            logger.info(f"✅ Verified working UUID: {uuid_att['title'][:30]}...")
                            results['verified'] += 1
                            break
                    
                    if not working_uuid:
                        logger.warning(f"❌ No working UUID links found, skipping cleanup for: {parent_title}")
                        results['skipped'] += 1
                        continue
                    
                    # Wait confirmation period (safety delay)
                    logger.info(f"⏳ Waiting {CONFIRMATION_DELAY}s before cleanup...")
                    await asyncio.sleep(CONFIRMATION_DELAY)
                    
                    if not self.running:
                        break
                    
                    # Clean up file attachments
                    for file_att in item['file_attachments']:
                        try:
                            logger.info(f"🗑️ Removing file attachment: {file_att['title'][:30]}...")
                            
                            # Delete from Zotero
                            if self.zotero.delete_file_attachment(file_att['key'], file_att['version']):
                                # Delete symlink file
                                if file_att.get('path'):
                                    self.zotero.delete_symlink_file(file_att['path'])
                                
                                results['cleaned'] += 1
                                logger.info(f"✅ Cleaned up: {file_att['title'][:30]}...")
                            else:
                                logger.error(f"❌ Failed to delete attachment: {file_att['key']}")
                                results['errors'] += 1
                                
                        except Exception as e:
                            logger.error(f"Error cleaning file attachment {file_att['key']}: {e}")
                            results['errors'] += 1
                    
                    # Small delay between items
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    logger.error(f"Error processing redundant item: {e}")
                    results['errors'] += 1
                    
        except Exception as e:
            logger.error(f"Error in batch processing: {e}")
            results['errors'] += 1
        
        return results
    
    async def run_continuous(self):
        """Run continuously in background"""
        self.running = True
        logger.info("🧹 Starting continuous attachment cleanup...")
        
        try:
            while self.running:
                cycle_start = datetime.now()
                
                # Process one batch
                batch_results = await self.process_batch()
                
                # Update stats
                self.stats['cleaned'] += batch_results['cleaned']
                self.stats['verified'] += batch_results['verified']
                self.stats['skipped'] += batch_results['skipped']
                self.stats['errors'] += batch_results['errors']
                self.stats['cycles'] += 1
                
                # Log cycle summary
                cycle_time = (datetime.now() - cycle_start).total_seconds()
                if batch_results['cleaned'] > 0 or self.stats['cycles'] % 5 == 0:
                    logger.info(
                        f"📊 Cycle {self.stats['cycles']}: "
                        f"Cleaned={batch_results['cleaned']}, "
                        f"Verified={batch_results['verified']}, "
                        f"Skipped={batch_results['skipped']}, "
                        f"Errors={batch_results['errors']}, "
                        f"Time={cycle_time:.1f}s"
                    )
                    logger.info(
                        f"📈 Total: Cleaned={self.stats['cleaned']}, "
                        f"Verified={self.stats['verified']}, "
                        f"Skipped={self.stats['skipped']}, "
                        f"Errors={self.stats['errors']}"
                    )
                
                # Wait before next cycle
                logger.debug(f"💤 Waiting {CYCLE_DELAY}s before next cycle...")
                await asyncio.sleep(CYCLE_DELAY)
                    
        except KeyboardInterrupt:
            logger.info("🛑 Stopped by user")
        except Exception as e:
            logger.error(f"💥 Unexpected error: {e}")
        finally:
            self.running = False
            logger.info(f"📊 Final stats: Cleaned={self.stats['cleaned']}, Verified={self.stats['verified']}, Errors={self.stats['errors']}")

async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="DEVONzot Continuous Cleaner")
    parser.add_argument('--test', action='store_true', help='Run one test batch')
    parser.add_argument('--once', action='store_true', help='Run one cycle then exit')
    parser.add_argument('--daemon', action='store_true', help='Run continuously')
    
    args = parser.parse_args()
    
    cleaner = ContinuousCleaner()
    
    if args.test or args.once:
        logger.info("🧪 Running single batch...")
        results = await cleaner.process_batch()
        print(f"Results: {results}")
    elif args.daemon:
        logger.info("🚀 Starting continuous daemon mode...")
        await cleaner.run_continuous()
    else:
        print("Usage:")
        print("  --test    Run one test batch")
        print("  --once    Run one cycle then exit")  
        print("  --daemon  Run continuously in background")

if __name__ == "__main__":
    asyncio.run(main())