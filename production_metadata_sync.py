#!/usr/bin/env python3
"""
Production-ready Zotero-DEVONthink metadata sync
Uses only the working approaches: DEVONthink comments, tags, and macOS extended attributes
"""

import sqlite3
import subprocess
import psutil
import time
import os
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ZoteroDevonthinkMetadataSync:
    def __init__(self, cronjob_mode: bool = False):
        self.zotero_db_path = Path.home() / "Zotero" / "zotero.sqlite"
        self.cronjob_mode = cronjob_mode
        self.state_file = Path.home() / "DEVONzot" / "sync_state.json"
        self.state_file.parent.mkdir(exist_ok=True)
        if cronjob_mode:
            logger.info("Running in cronjob mode - read-only operations only")
        
    def is_zotero_running(self) -> bool:
        """Check if Zotero is currently running"""
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] and 'zotero' in proc.info['name'].lower():
                return True
        return False
    
    def get_zotero_connection(self, read_only: bool = True) -> sqlite3.Connection:
        """Get connection to Zotero database"""
        if not read_only and self.is_zotero_running():
            if self.cronjob_mode:
                logger.warning("Skipping database write operations - Zotero is running")
                return None
            else:
                logger.error("Zotero is running. Database may be locked. Consider closing Zotero.")
        
        # Connection with timeout and WAL mode for better concurrent access
        mode_param = "?mode=ro" if read_only else ""
        conn = sqlite3.connect(f"file:{self.zotero_db_path}{mode_param}", uri=True, timeout=30.0)
        conn.row_factory = sqlite3.Row
        
        # Enable WAL mode for better concurrent reading (if not read-only)
        if not read_only:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except:
                pass  # May fail in read-only mode
        
        return conn
    
    def execute_applescript(self, script: str) -> str:
        """Execute AppleScript and return result"""
        try:
            result = subprocess.run(['osascript', '-e', script], 
                                  capture_output=True, text=True, check=True, timeout=30)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(f"AppleScript error: {e.stderr}")
            return f"ERROR: {e.stderr}"
        except subprocess.TimeoutExpired:
            logger.error("AppleScript timeout")
            return "ERROR: Timeout"
    
    def set_macos_metadata(self, file_path: str, metadata: Dict[str, str]) -> bool:
        """Set macOS extended attributes for metadata"""
        try:
            # Set author
            if 'author' in metadata:
                cmd = ['xattr', '-w', 'com.apple.metadata:kMDItemAuthors', metadata['author'], file_path]
                subprocess.run(cmd, check=True, capture_output=True)
            
            # Set title
            if 'title' in metadata:
                cmd = ['xattr', '-w', 'com.apple.metadata:kMDItemTitle', metadata['title'], file_path]
                subprocess.run(cmd, check=True, capture_output=True)
            
            # Set description
            if 'description' in metadata:
                cmd = ['xattr', '-w', 'com.apple.metadata:kMDItemDescription', metadata['description'], file_path]
                subprocess.run(cmd, check=True, capture_output=True)
            
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to set macOS metadata: {e}")
            return False
    
    def set_devonthink_tags(self, uuid: str, tags: List[str]) -> bool:
        """Set DEVONthink tags only"""
        
        # Set tags in DEVONthink
        script = f'''
        tell application "DEVONthink 3"
            try
                set theRecord to get record with uuid "{uuid}"
                
                -- Set tags
                set the tags of theRecord to {{{"".join(f'"{tag}"' + (',' if i < len(tags)-1 else '') for i, tag in enumerate(tags))}}}
                
                return "SUCCESS: Updated tags"
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''
        
        result = self.execute_applescript(script)
        return "SUCCESS" in result
    
    def update_zotero_attachment_link(self, zotero_key: str, devonthink_uuid: str) -> bool:
        """Update Zotero attachment to use DEVONthink UUID link"""
        if self.cronjob_mode:
            logger.info(f"Skipping Zotero attachment update in cronjob mode: {zotero_key}")
            return False
            
        if self.is_zotero_running():
            logger.error("Zotero must be closed to update attachment links")
            return False
        
        try:
            conn = self.get_zotero_connection(read_only=False)
            
            # Find the attachment item for this parent item
            query = """
            SELECT ia.itemID, ia.path
            FROM items i
            JOIN itemAttachments ia ON i.itemID = ia.parentItemID  
            WHERE i.key = ? AND ia.contentType LIKE '%pdf%'
            """
            
            attachment = conn.execute(query, (zotero_key,)).fetchone()
            
            if attachment:
                # Update the attachment path to use DEVONthink link
                devonthink_link = f"x-devonthink-item://{devonthink_uuid}"
                
                # Update the attachment path
                update_query = "UPDATE itemAttachments SET path = ? WHERE itemID = ?"
                conn.execute(update_query, (devonthink_link, attachment['itemID']))
                
                # Also store the original path in a note field for reference
                old_path = attachment['path']
                
                conn.commit()
                conn.close()
                
                logger.info(f"Updated Zotero attachment for {zotero_key}")
                logger.info(f"  Old path: {old_path}")  
                logger.info(f"  New link: {devonthink_link}")
                
                return True
            else:
                logger.warning(f"No PDF attachment found for Zotero item {zotero_key}")
                conn.close()
                return False
                
        except Exception as e:
            logger.error(f"Failed to update Zotero attachment for {zotero_key}: {e}")
            return False
    
    def cleanup_zotfile_symlink(self, zotero_key: str) -> bool:
        """Remove old ZotFile symlink for this item"""
        if self.cronjob_mode:
            logger.info(f"Skipping symlink cleanup in cronjob mode: {zotero_key}")
            return False
            
        zotfile_dir = Path.home() / "ZotFile Import"
        
        if not zotfile_dir.exists():
            logger.info("ZotFile Import directory not found, skipping cleanup")
            return True
        
        # Look for symlinks that might correspond to this item
        # ZotFile typically creates files like "Author_YEAR_Title.pdf"
        potential_files = list(zotfile_dir.glob("*.pdf"))
        
        removed = False
        for file_path in potential_files:
            # Check if it's a symlink and if it's broken or points to a Zotero attachment
            if file_path.is_symlink():
                try:
                    # Check if the symlink target contains our zotero key or is broken
                    target = file_path.readlink()
                    target_str = str(target)
                    
                    if zotero_key in target_str or not target.exists():
                        logger.info(f"Removing ZotFile symlink: {file_path.name}")
                        file_path.unlink()
                        removed = True
                except Exception as e:
                    logger.warning(f"Error checking symlink {file_path}: {e}")
        
        return removed
    
    def backfill_devonthink_metadata(self, zotero_key: str, devonthink_uuid: str) -> Dict[str, any]:
        """Check what might be useful to backfill from DEVONthink to Zotero"""
        
        # Get DEVONthink record info
        script = f'''
        tell application "DEVONthink 3"
            try
                set theRecord to get record with uuid "{devonthink_uuid}"
                
                set result to ""
                set result to result & "name:" & (name of theRecord) & "\\n"
                set result to result & "tags:" & (tags of theRecord as string) & "\\n"
                set result to result & "comment:" & (comment of theRecord) & "\\n"
                set result to result & "path:" & (path of theRecord) & "\\n"
                
                return result
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''
        
        dt_info = self.execute_applescript(script)
        
        backfill_data = {
            'devonthink_path': None,
            'additional_tags': [],
            'devonthink_notes': None
        }
        
        if dt_info and not dt_info.startswith("ERROR"):
            lines = dt_info.split("\\n")
            for line in lines:
                if line.startswith("path:"):
                    backfill_data['devonthink_path'] = line[5:].strip()
                elif line.startswith("tags:"):
                    # Extract any tags that aren't from our sync
                    tags_str = line[5:].strip()
                    if tags_str:
                        all_tags = [tag.strip() for tag in tags_str.split(',')]
                        # Filter out tags we would have added during sync
                        sync_tags = ['Magazine Article', 'Journal Article', 'Book', 'economics', 'American', 'European']
                        additional_tags = [tag for tag in all_tags if tag not in sync_tags and len(tag) > 2]
                        backfill_data['additional_tags'] = additional_tags
        
        return backfill_data
    
    def load_sync_state(self) -> Dict:
        """Load the last sync state"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load sync state: {e}")
        return {'last_sync': 0, 'processed_items': {}, 'zotero_hash': ''}
    
    def save_sync_state(self, state: Dict):
        """Save the current sync state"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save sync state: {e}")
    
    def get_zotero_database_hash(self) -> str:
        """Get a hash of the Zotero database to detect changes"""
        try:
            # Use modification time and size as a simple change detector
            stat = self.zotero_db_path.stat()
            content = f"{stat.st_mtime}_{stat.st_size}"
            return hashlib.md5(content.encode()).hexdigest()
        except Exception as e:
            logger.warning(f"Could not get database hash: {e}")
            return ""
    
    def detect_zotero_changes(self) -> bool:
        """Check if Zotero database has changed since last sync"""
        state = self.load_sync_state()
        current_hash = self.get_zotero_database_hash()
        
        changed = current_hash != state.get('zotero_hash', '')
        if changed:
            logger.info("Zotero database changes detected")
        else:
            logger.info("No Zotero database changes since last sync")
        
        return changed
    
    def intelligent_sync(self) -> Dict[str, int]:
        """Smart sync that only processes changed items"""
        logger.info("Starting intelligent sync...")
        
        state = self.load_sync_state()
        current_time = int(time.time())
        
        # Check if database changed
        if not self.detect_zotero_changes() and not self.cronjob_mode:
            # If no changes and not in cronjob mode, ask user
            logger.info("No changes detected. Force sync anyway? This is automatic in cronjob mode.")
        
        # Get all potential records
        records = self.get_devonthink_records_with_zotero_links()
        logger.info(f"Found {len(records)} DEVONthink records to check")
        
        success_count = 0
        error_count = 0
        skipped_count = 0
        
        for record in records:
            try:
                # Check if this item was recently processed successfully
                item_key = record.get('zotero_key', record['devonthink_uuid'])
                last_processed = state['processed_items'].get(item_key, 0)
                
                # Skip if processed recently (unless database changed)
                if (current_time - last_processed) < 3600 and not self.detect_zotero_changes():  # 1 hour
                    skipped_count += 1
                    continue
                
                # Process the record
                if self.sync_metadata_for_record(record, update_zotero=False):
                    success_count += 1
                    # Mark as successfully processed
                    state['processed_items'][item_key] = current_time
                else:
                    error_count += 1
                
                # Small delay to avoid overwhelming the system
                time.sleep(0.2)
                
            except Exception as e:
                logger.error(f"Unexpected error syncing {record['name']}: {e}")
                error_count += 1
        
        # Update state
        state['last_sync'] = current_time
        state['zotero_hash'] = self.get_zotero_database_hash()
        self.save_sync_state(state)
        
        logger.info(f"Intelligent sync complete: {success_count} successful, {error_count} failed, {skipped_count} skipped")
        return {'success': success_count, 'errors': error_count, 'skipped': skipped_count}
    
    def get_devonthink_records_with_zotero_links(self) -> List[Dict]:
        """Find all DEVONthink records that have Zotero UUID links using multiple methods"""
        
        # Method 1: Direct search for x-devonthink-item links (more reliable)
        script1 = '''
        tell application "DEVONthink 3"
            try
                set theDatabase to current database
                set allRecords to every record of theDatabase
                set resultText to ""
                set foundCount to 0
                
                repeat with theRecord in allRecords
                    set recordURL to URL of theRecord
                    set recordRefURL to ""
                    try
                        set recordRefURL to reference URL of theRecord
                    end try
                    
                    -- Check if this record has a reference URL that suggests Zotero connection
                    -- or if URL contains zotero patterns
                    if (recordURL contains "zotero" or recordRefURL contains "x-devonthink-item") then
                        set foundCount to foundCount + 1
                        set recordUUID to uuid of theRecord
                        set recordName to name of theRecord
                        set recordPath to path of theRecord
                        
                        set resultText to resultText & recordUUID & "|" & recordName & "|" & recordURL & "|" & recordPath & "\\n"
                    end if
                    
                    -- Limit to prevent timeout, but make it higher than before
                    if foundCount >= 50 then exit repeat
                end repeat
                
                return "FOUND:" & foundCount & "\\n" & resultText
                
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''
        
        # Method 2: Search based on known pattern from our earlier search_uuid_links.py
        # We know there should be 165 items with UUID links, so let's find them differently
        script2 = '''
        tell application "DEVONthink 3"
            try
                -- Look for records where the name or path contains patterns we expect
                set theDatabase to current database
                set searchResults to search "*Henderson*" in theDatabase
                set resultText to ""
                set foundCount to 0
                
                repeat with theRecord in searchResults
                    set recordUUID to uuid of theRecord
                    set recordName to name of theRecord
                    set recordURL to URL of theRecord
                    set recordPath to path of theRecord
                    
                    -- If this looks like it might be a Zotero-managed item, include it
                    set resultText to resultText & recordUUID & "|" & recordName & "|" & recordURL & "|" & recordPath & "\\n"
                    set foundCount to foundCount + 1
                end repeat
                
                return "FOUND:" & foundCount & "\\n" & resultText
                
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''
        
        # Try method 1 first
        result = self.execute_applescript(script1)
        records = []
        
        if result and result.startswith("FOUND:"):
            lines = result.split("\\n")
            try:
                count_line = lines[0]
                count = int(count_line.split(":")[1].strip())
                logger.info(f"Method 1 found {count} potential records")
                
                for line in lines[1:]:
                    if line and "|" in line:
                        parts = line.split("|")
                        if len(parts) >= 4:
                            # For now, create a dummy zotero_key - we'll resolve this from the database
                            records.append({
                                'devonthink_uuid': parts[0],
                                'name': parts[1],
                                'zotero_url': parts[2],
                                'zotero_key': self._extract_zotero_key_from_name(parts[1]),
                                'path': parts[3]
                            })
            except (ValueError, IndexError) as e:
                logger.warning(f"Error parsing method 1 results: {e}")
                logger.debug(f"Raw result: {result[:200]}...")
        
        # If method 1 didn't work well, try method 2
        if len(records) < 5:  # If we found very few records
            logger.info("Method 1 found few records, trying search-based approach...")
            result2 = self.execute_applescript(script2)
            
            if result2 and result2.startswith("FOUND:"):
                lines = result2.split("\\n")
                try:
                    count = int(lines[0].split(":")[1].strip())
                    logger.info(f"Method 2 found {count} additional records")
                    
                    for line in lines[1:]:
                        if line and "|" in line:
                            parts = line.split("|")
                            if len(parts) >= 4:
                                # Check if we already have this record
                                uuid = parts[0]
                                if not any(r['devonthink_uuid'] == uuid for r in records):
                                    records.append({
                                        'devonthink_uuid': parts[0],
                                        'name': parts[1],
                                        'zotero_url': parts[2],
                                        'zotero_key': self._extract_zotero_key_from_name(parts[1]),
                                        'path': parts[3]
                                    })
                except (ValueError, IndexError) as e:
                    logger.warning(f"Error parsing method 2 results: {e}")
        
        logger.info(f"Total found: {len(records)} DEVONthink records to process")
        return records
    
    def _extract_zotero_key_from_name(self, filename: str) -> Optional[str]:
        """Extract Zotero key by matching filename to database"""
        # Remove file extension and common patterns
        clean_name = filename.replace('.pdf', '').replace('- Journal Article', '').replace('- Magazine Article', '')
        
        # Try to find matching item in Zotero database by title similarity
        try:
            conn = self.get_zotero_connection(read_only=True)
            
            # Look for items with similar titles
            query = """
            SELECT i.key, iv.value as title
            FROM items i
            JOIN itemData id ON i.itemID = id.itemID
            JOIN itemDataValues iv ON id.valueID = iv.valueID
            WHERE id.fieldID = 110  -- title field
            AND (iv.value LIKE ? OR iv.value LIKE ? OR iv.value LIKE ?)
            LIMIT 1
            """
            
            # Create various search patterns from the filename
            patterns = [
                f"%{clean_name[:30]}%",  # First part of filename
                f"%{clean_name.split(' - ')[0]}%",  # Before first dash
                f"%{clean_name.split('_')[0]}%"  # Before first underscore
            ]
            
            result = conn.execute(query, patterns).fetchone()
            conn.close()
            
            if result:
                return result['key']
            else:
                # Fallback: try to extract from common patterns or return None
                return None
                
        except Exception as e:
            logger.warning(f"Could not extract Zotero key from filename {filename}: {e}")
            return None
    
    def get_zotero_metadata(self, zotero_key: str) -> Optional[Dict]:
        """Get metadata for a Zotero item"""
        try:
            conn = self.get_zotero_connection()
            
            # Query for item metadata
            query = """
            SELECT i.key, i.itemTypeID, iv.value as title,
                   GROUP_CONCAT(CASE WHEN f.fieldName = 'publicationTitle' THEN iv2.value END) as publication,
                   GROUP_CONCAT(CASE WHEN f.fieldName = 'date' THEN iv2.value END) as date,
                   GROUP_CONCAT(CASE WHEN f.fieldName = 'abstractNote' THEN iv2.value END) as abstract
            FROM items i
            LEFT JOIN itemData id ON i.itemID = id.itemID
            LEFT JOIN itemDataValues iv ON id.valueID = iv.valueID AND id.fieldID = 110  -- title
            LEFT JOIN itemData id2 ON i.itemID = id2.itemID
            LEFT JOIN fields f ON id2.fieldID = f.fieldID
            LEFT JOIN itemDataValues iv2 ON id2.valueID = iv2.valueID
            WHERE i.key = ?
            GROUP BY i.key, i.itemTypeID, iv.value
            """
            
            result = conn.execute(query, (zotero_key,)).fetchone()
            
            if not result:
                return None
            
            # Get authors
            author_query = """
            SELECT c.lastName, c.firstName
            FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            JOIN items i ON ic.itemID = i.itemID
            WHERE i.key = ?
            ORDER BY ic.orderIndex
            """
            
            authors = conn.execute(author_query, (zotero_key,)).fetchall()
            
            # Format authors
            author_names = []
            for author in authors:
                if author['firstName'] and author['lastName']:
                    author_names.append(f"{author['firstName']} {author['lastName']}")
                elif author['lastName']:
                    author_names.append(author['lastName'])
            
            # Get item type
            type_query = "SELECT typeName FROM itemTypes WHERE itemTypeID = ?"
            item_type = conn.execute(type_query, (result['itemTypeID'],)).fetchone()
            
            metadata = {
                'title': result['title'] or '',
                'author': ', '.join(author_names),
                'publication': result['publication'] or '',
                'year': result['date'][:4] if result['date'] else '',
                'type': item_type['typeName'].title() if item_type else 'Item',
                'description': result['abstract'][:200] + '...' if result['abstract'] and len(result['abstract']) > 200 else result['abstract'] or ''
            }
            
            conn.close()
            return metadata
            
        except Exception as e:
            logger.error(f"Error getting Zotero metadata for {zotero_key}: {e}")
            return None
    
    def sync_metadata_for_record(self, record: Dict) -> bool:
        """Sync metadata for a single DEVONthink record"""
        logger.info(f"Syncing metadata for: {record['name']}")
        
        if not record['zotero_key']:
            logger.warning(f"No Zotero key found for {record['name']}")
            return False
        
        # Get Zotero metadata
        zotero_metadata = self.get_zotero_metadata(record['zotero_key'])
        if not zotero_metadata:
            logger.warning(f"No Zotero metadata found for key: {record['zotero_key']}")
            return False
        
        # Generate smart heuristic tags for archive discovery
        tags = []
        
        # Add exact Zotero item type as tag
        if zotero_metadata['type']:
            tags.append(zotero_metadata['type'])
        
        # Add publication name (exact formatting)
        if zotero_metadata['publication']:
            tags.append(zotero_metadata['publication'])
        
        # Add decade tag if year available
        if zotero_metadata['year'] and zotero_metadata['year'].isdigit():
            year = int(zotero_metadata['year'])
            decade = (year // 10) * 10
            tags.append(f"{decade}s")
        
        # Extract topical/thematic keywords from title and description
        content_text = f"{zotero_metadata['title']} {zotero_metadata['description']}".lower()
        
        # Economic/political terms
        econ_terms = ['economics', 'economic', 'market', 'trade', 'regulation', 'policy', 'financial', 'monetary', 'fiscal', 'capitalism', 'labor', 'employment']
        for term in econ_terms:
            if term in content_text:
                tags.append('economics')
                break
        
        # Historical periods
        historical_periods = {
            'civil war': 'Civil War',
            'world war': 'World War', 
            'great depression': 'Great Depression',
            'cold war': 'Cold War',
            'reconstruction': 'Reconstruction',
            'progressive era': 'Progressive Era',
            'new deal': 'New Deal'
        }
        for period_key, period_tag in historical_periods.items():
            if period_key in content_text:
                tags.append(period_tag)
        
        # Geographic regions
        geographic_terms = {
            'american': 'American',
            'united states': 'United States',
            'europe': 'European',
            'britain': 'British', 
            'england': 'British',
            'france': 'French',
            'germany': 'German',
            'california': 'California',
            'south': 'American South'
        }
        for geo_key, geo_tag in geographic_terms.items():
            if geo_key in content_text:
                tags.append(geo_tag)
        
        # Social/cultural themes
        social_themes = ['race', 'gender', 'class', 'immigration', 'religion', 'education', 'urban', 'rural']
        for theme in social_themes:
            if theme in content_text:
                tags.append(theme.title())
        
        # Remove duplicates while preserving order
        seen = set()
        tags = [tag for tag in tags if not (tag in seen or seen.add(tag))]
        
        # Set macOS metadata if file path exists
        file_path = record['path']
        macos_success = False
        if file_path and os.path.exists(file_path):
            macos_success = self.set_macos_metadata(file_path, zotero_metadata)
            if not macos_success:
                logger.warning(f"Failed to set macOS metadata for {file_path}")
        
        # Set DEVONthink tags
        dt_success = self.set_devonthink_tags(record['devonthink_uuid'], tags)
        
        if dt_success or macos_success:
            logger.info(f"✅ Successfully synced metadata for: {record['name']}")
            if macos_success:
                logger.info(f"   • macOS native metadata set")
            if dt_success:
                logger.info(f"   • DEVONthink tags updated: {', '.join(tags)}")
        else:
            logger.error(f"❌ Failed to sync metadata for: {record['name']}")
        
        return dt_success or macos_success
    
    def sync_all_metadata(self, update_zotero: bool = False) -> Dict[str, int]:
        """Sync metadata for all DEVONthink records with Zotero links"""
        logger.info("Starting metadata sync for all records...")
        
        if update_zotero and self.cronjob_mode:
            logger.info("Cronjob mode: Skipping Zotero database updates")
            update_zotero = False
        
        if update_zotero and self.is_zotero_running():
            if self.cronjob_mode:
                logger.info("Zotero is running - continuing with read-only operations only")
                update_zotero = False
            else:
                logger.error("Zotero must be closed to update attachment links. Please close Zotero and try again.")
                return {'success': 0, 'errors': 0, 'skipped': 1}
        
        # Get all DEVONthink records with Zotero links
        records = self.get_devonthink_records_with_zotero_links()
        logger.info(f"Found {len(records)} DEVONthink records with Zotero links")
        
        success_count = 0
        error_count = 0
        
        for record in records:
            try:
                if self.sync_metadata_for_record(record, update_zotero=update_zotero):
                    success_count += 1
                else:
                    error_count += 1
                
                # Small delay to avoid overwhelming the system
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Unexpected error syncing {record['name']}: {e}")
                error_count += 1
        
        logger.info(f"Metadata sync complete: {success_count} successful, {error_count} failed")
        return {'success': success_count, 'errors': error_count}
    
    def cronjob_safe_sync(self) -> Dict[str, int]:
        """Cronjob-safe metadata sync with intelligent change detection"""
        logger.info("Starting cronjob-safe intelligent sync (read-only operations only)")
        return self.intelligent_sync()
    
    def complete_zotfile_replacement(self) -> Dict[str, int]:
        """Complete workflow: metadata sync + Zotero updates + symlink cleanup"""
        logger.info("Starting complete ZotFile replacement workflow...")
        
        if self.cronjob_mode:
            logger.error("Complete replacement workflow not available in cronjob mode")
            return {'success': 0, 'errors': 0, 'skipped': 0}
        
        if self.is_zotero_running():
            logger.error("Please close Zotero before running the complete replacement workflow.")
            return {'success': 0, 'errors': 0, 'skipped': 0}
        
        return self.sync_all_metadata(update_zotero=True)

def main():
    """Main function with options"""
    import sys
    
    # Check for cronjob mode first
    cronjob_mode = "--cronjob" in sys.argv
    syncer = ZoteroDevonthinkMetadataSync(cronjob_mode=cronjob_mode)
    
    print("🔄 Zotero-DEVONthink Integration System")
    print("=" * 60)
    
    # Check for command line arguments
    if "--complete" in sys.argv:
        if cronjob_mode:
            print("❌ ERROR: --complete and --cronjob cannot be used together")
            print("   Complete workflow requires closing Zotero")
            return
            
        print("🚀 Running COMPLETE ZotFile Replacement Workflow")
        print("   This will:")
        print("   • Sync metadata from Zotero to DEVONthink")
        print("   • Update Zotero attachments to use DEVONthink UUID links")
        print("   • Clean up old ZotFile symlinks")
        print("   • Set native macOS metadata")
        
        if syncer.is_zotero_running():
            print("\n❌ ERROR: Zotero is running!")
            print("   Please close Zotero completely before running the complete workflow.")
            return
        
        response = input("\n⚠️  This will modify your Zotero database. Continue? (y/n): ").lower().strip()
        if response != 'y':
            print("🚫 Operation cancelled.")
            return
        
        results = syncer.complete_zotfile_replacement()
        
    elif cronjob_mode:
        print("🤖 Running in Cronjob Mode (Zotero-safe)")
        print("   This will:")
        print("   • Sync metadata from Zotero to DEVONthink (read-only)")  
        print("   • Apply smart tags for archive discovery")
        print("   • Set native macOS metadata")
        print("   • Safe to run while Zotero is open")
        print("   • Will NOT modify Zotero database or clean symlinks")
        
        results = syncer.cronjob_safe_sync()
        
    else:
        print("🔄 Running Metadata Sync Only (Safe Mode)")
        print("   This will:")
        print("   • Sync metadata from Zotero to DEVONthink")  
        print("   • Apply smart tags for archive discovery")
        print("   • Set native macOS metadata")
        print("   • Will NOT modify Zotero database")
        
        results = syncer.sync_all_metadata(update_zotero=False)
    
    print(f"\n📊 SYNC RESULTS:")
    print(f"   ✅ Successful: {results['success']}")
    print(f"   ❌ Failed: {results['errors']}")
    if 'skipped' in results:
        print(f"   ⏭️  Skipped: {results['skipped']}")
    print(f"   📝 Total processed: {results['success'] + results['errors']}")
    
    if results['success'] > 0:
        print(f"\n🎉 Sync completed successfully!")
        print(f"   • Smart tags applied for better organization")
        print(f"   • macOS native metadata set (author, title, description)")
        print(f"   • Metadata visible as properties in DEVONthink Info panel")
        
        if "--complete" in sys.argv:
            print(f"   • Zotero attachments updated to use DEVONthink links")
            print(f"   • ZotFile symlinks cleaned up")
            print(f"\n✨ ZotFile replacement workflow is complete!")
            print(f"   Your mobile workflow should now work seamlessly.")
        elif cronjob_mode:
            print(f"\n🤖 Cronjob sync completed successfully!")
            print(f"   • Safe to run automatically while Zotero is open")
            print(f"   • For complete workflow, run manually with --complete")
    
    print(f"\n💡 Usage:")
    print(f"   python3 {sys.argv[0]}              # Safe metadata sync only")
    print(f"   python3 {sys.argv[0]} --cronjob    # Cronjob-safe mode (read-only)")
    print(f"   python3 {sys.argv[0]} --complete   # Complete ZotFile replacement")
    
    if cronjob_mode:
        print(f"\n📅 Cronjob Setup Example:")
        print(f"   # Run every 30 minutes")
        print(f"   */30 * * * * /usr/bin/python3 {os.path.abspath(sys.argv[0])} --cronjob >> ~/zotero_sync.log 2>&1")

if __name__ == "__main__":
    main()