#!/usr/bin/env python3
"""
ULTIMATE SET-AND-FORGET ZOTERO SYNC
This script runs invisibly and automatically syncs any new Zotero items to DEVONthink
"""

import sys
import os
import time
import json
import hashlib
import logging
import sqlite3
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

# Setup logging for invisible operation
LOG_FILE = Path.home() / "zotero_devonthink_sync.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        # logging.StreamHandler()  # Commented out for silent operation
    ]
)

logger = logging.getLogger(__name__)

class InvisibleZoteroSync:
    def __init__(self):
        self.zotero_db_path = Path.home() / "Zotero" / "zotero.sqlite"
        self.state_file = Path.home() / ".zotero_devonthink_state.json"
        self.config_file = Path.home() / ".zotero_devonthink_config.json"
        
        # Create default config if it doesn't exist
        if not self.config_file.exists():
            self.create_default_config()
    
    def create_default_config(self):
        """Create default configuration"""
        default_config = {
            "sync_interval_minutes": 15,
            "max_items_per_sync": 50,
            "enabled_databases": ["Professional", "Articles", "Research"],
            "tag_categories": {
                "temporal": True,
                "geographic": True, 
                "thematic": True,
                "publication": True,
                "item_type": True
            }
        }
        
        try:
            with open(self.config_file, 'w') as f:
                json.dump(default_config, f, indent=2)
            logger.info("Created default configuration file")
        except Exception as e:
            logger.error(f"Could not create config file: {e}")
    
    def load_state(self) -> Dict:
        """Load sync state"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load state: {e}")
        
        return {
            'last_sync': 0,
            'last_db_hash': '',
            'processed_items': {},
            'total_synced': 0
        }
    
    def save_state(self, state: Dict):
        """Save sync state"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save state: {e}")
    
    def get_db_hash(self) -> str:
        """Get database change hash"""
        try:
            stat = self.zotero_db_path.stat()
            return hashlib.md5(f"{stat.st_mtime}_{stat.st_size}".encode()).hexdigest()
        except:
            return ""
    
    def is_zotero_running(self) -> bool:
        """Check if Zotero is running"""
        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                if proc.info['name'] and 'zotero' in proc.info['name'].lower():
                    return True
        except:
            pass
        return False
    
    def get_recent_zotero_items(self, since_timestamp: int = 0) -> List[Dict]:
        """Get recent Zotero items that might need syncing"""
        try:
            conn = sqlite3.connect(f"file:{self.zotero_db_path}?mode=ro", uri=True, timeout=10.0)
            conn.row_factory = sqlite3.Row
            
            # Simplified query to avoid complex JOINs that might cause issues
            query = """
            SELECT DISTINCT i.key, 
                   COALESCE(title.value, 'Untitled') as title,
                   i.dateAdded, 
                   i.dateModified
            FROM items i
            LEFT JOIN itemData title_data ON i.itemID = title_data.itemID AND title_data.fieldID = 110
            LEFT JOIN itemDataValues title ON title_data.valueID = title.valueID
            WHERE (i.dateAdded > datetime(?, 'unixepoch') OR i.dateModified > datetime(?, 'unixepoch'))
              AND i.key IS NOT NULL
              AND i.key != ''
            ORDER BY i.dateModified DESC
            LIMIT 20
            """
            
            results = conn.execute(query, (since_timestamp, since_timestamp)).fetchall()
            conn.close()
            
            items = []
            for result in results:
                if result and result['key'] and result['title']:
                    items.append({
                        'key': str(result['key']),
                        'title': str(result['title']),
                        'publication': None,  # Simplified - will add back if needed
                        'date': None,         # Simplified - will add back if needed  
                        'date_added': result['dateAdded'],
                        'date_modified': result['dateModified']
                    })
            
            return items
            
        except Exception as e:
            logger.error(f"Error getting recent items: {e}")
            return []
    
    def find_devonthink_record_for_item(self, zotero_item: Dict) -> Optional[str]:
        """Try to find corresponding DEVONthink record UUID using multiple strategies"""
        title = zotero_item['title'].strip()
        
        if not title or title == 'Untitled':
            return None
        
        # Strategy 1: Direct title search (first 50 chars)
        search_title = title[:50].replace('"', '\\"')
        
        script1 = f'''
        tell application "DEVONthink 3"
            try
                set searchResults to search "title:{search_title}" in current database
                if (count of searchResults) > 0 then
                    set theRecord to first item of searchResults
                    return uuid of theRecord
                end if
            on error
            end try
            
            try
                -- Strategy 2: Content search with fewer characters
                set searchResults to search "{search_title[:30]}" in current database
                if (count of searchResults) > 0 then
                    set theRecord to first item of searchResults
                    return uuid of theRecord
                end if
            on error
            end try
            
            try
                -- Strategy 3: Look for Zotero links specifically
                set allRecords to every record of current database
                repeat with aRecord in allRecords
                    set recordURL to URL of aRecord
                    if recordURL contains "zotero://select" then
                        set recordName to name of aRecord
                        if recordName contains "{search_title[:20]}" then
                            return uuid of aRecord
                        end if
                    end if
                end repeat
            on error
            end try
            
            return ""
        end tell
        '''
        
        try:
            result = subprocess.run(['osascript', '-e', script1], 
                                  capture_output=True, text=True, timeout=15)
            uuid = result.stdout.strip()
            if uuid and uuid != "":
                logger.info(f"Found DEVONthink record for: {title[:30]}")
                return uuid
        except Exception as e:
            logger.debug(f"DEVONthink search failed: {e}")
        
        return None
    
    def apply_smart_tags(self, devonthink_uuid: str, zotero_item: Dict) -> bool:
        """Apply smart tags to DEVONthink record"""
        
        # Generate tags based on metadata
        tags = []
        
        title = zotero_item.get('title', '').lower()
        publication = zotero_item.get('publication', '') or ''
        date = zotero_item.get('date', '') or ''
        
        # Add publication
        if publication:
            tags.append(publication)
        
        # Add decade
        if date and len(date) >= 4 and date[:4].isdigit():
            year = int(date[:4])
            decade = (year // 10) * 10
            tags.append(f"{decade}s")
        
        # Add thematic tags based on title
        thematic_keywords = {
            'economics': ['economic', 'market', 'trade', 'finance', 'money'],
            'history': ['historical', 'century', 'war', 'revolution'],
            'politics': ['political', 'government', 'policy', 'election'],
            'science': ['scientific', 'research', 'study', 'analysis'],
            'technology': ['technological', 'digital', 'computer', 'internet']
        }
        
        for theme, keywords in thematic_keywords.items():
            if any(keyword in title for keyword in keywords):
                tags.append(theme.title())
        
        # Apply tags via AppleScript
        if tags:
            tags_str = '", "'.join(tags)
            script = f'''
            tell application "DEVONthink 3"
                try
                    set theRecord to get record with uuid "{devonthink_uuid}"
                    set the tags of theRecord to {{"{tags_str}"}}
                    return "SUCCESS"
                on error errMsg
                    return "ERROR: " & errMsg
                end try
            end tell
            '''
            
            try:
                result = subprocess.run(['osascript', '-e', script], 
                                      capture_output=True, text=True, timeout=10)
                success = "SUCCESS" in result.stdout
                if success:
                    logger.info(f"Applied tags to {devonthink_uuid}: {', '.join(tags)}")
                return success
            except Exception as e:
                logger.error(f"Error applying tags: {e}")
                return False
        
        return True  # No tags to apply, but that's okay
    
    def sync_recent_items(self) -> Dict[str, int]:
        """Sync recent Zotero items to DEVONthink"""
        
        state = self.load_state()
        current_time = int(time.time())
        
        # Check if database changed
        current_hash = self.get_db_hash()
        if current_hash == state.get('last_db_hash', ''):
            logger.info("No database changes detected, skipping sync")
            return {'success': 0, 'errors': 0, 'skipped': 0}
        
        # Get recent items
        since_time = max(state.get('last_sync', 0) - 3600, current_time - 86400)  # Last sync minus 1h, or last 24h
        recent_items = self.get_recent_zotero_items(since_time)
        
        if not recent_items:
            logger.info("No recent items to sync")
            state['last_sync'] = current_time
            state['last_db_hash'] = current_hash
            self.save_state(state)
            return {'success': 0, 'errors': 0, 'skipped': 0}
        
        logger.info(f"Found {len(recent_items)} recent items to check")
        
        success_count = 0
        error_count = 0
        
        for item in recent_items:
            if not item or not item.get('key') or not item.get('title'):
                continue  # Skip malformed items
                
            try:
                # Check if already processed recently
                item_key = item['key']
                last_processed = state['processed_items'].get(item_key, 0)
                
                if (current_time - last_processed) < 1800:  # 30 minutes
                    continue
                
                # Try to find corresponding DEVONthink record
                devonthink_uuid = self.find_devonthink_record_for_item(item)
                
                if devonthink_uuid:
                    # Apply smart tags
                    success = self.apply_smart_tags(devonthink_uuid, item)
                    
                    if success:
                        success_count += 1
                        state['processed_items'][item_key] = current_time
                        logger.info(f"Synced: {item['title'][:50]}")
                    else:
                        error_count += 1
                else:
                    # No DEVONthink record found, but don't count as error
                    logger.debug(f"No DEVONthink record found for: {item['title'][:50]}")
                
                time.sleep(0.1)  # Small delay
                
            except Exception as e:
                error_count += 1
                logger.error(f"Error processing item {item.get('key', 'unknown')}: {e}")
        
        # Update state
        state['last_sync'] = current_time
        state['last_db_hash'] = current_hash
        state['total_synced'] = state.get('total_synced', 0) + success_count
        self.save_state(state)
        
        logger.info(f"Sync complete: {success_count} successful, {error_count} errors")
        return {'success': success_count, 'errors': error_count, 'skipped': 0}
    
    def run_daemon_sync(self):
        """Run a single sync cycle - designed for cron"""
        logger.info("Starting invisible sync cycle")
        
        try:
            results = self.sync_recent_items()
            
            if results['success'] > 0:
                print(f"✅ Synced {results['success']} items invisibly")
            
            return results
            
        except Exception as e:
            logger.error(f"Sync cycle failed: {e}")
            return {'success': 0, 'errors': 1, 'skipped': 0}

def main():
    """Main entry point for cron execution"""
    syncer = InvisibleZoteroSync()
    
    # Check if we should run (don't run too frequently)
    try:
        state = syncer.load_state()
        last_sync = state.get('last_sync', 0)
        current_time = int(time.time())
        
        # Don't sync more than once every 10 minutes
        if (current_time - last_sync) < 600:
            return
        
    except Exception:
        pass  # First run, continue
    
    # Run the sync
    results = syncer.run_daemon_sync()
    
    # Silent operation - only log to file
    # Output is suppressed for true invisibility

if __name__ == "__main__":
    main()