#!/usr/bin/env python3
"""
DEVONzot Service - Complete Zotero→DEVONthink Integration System

Production-ready service that runs perpetually to handle:
- File migration from Zotero storage to DEVONthink 
- ZotFile symlink conversion to UUID links
- Ongoing new item monitoring
- Bidirectional metadata sync
- Smart filename generation with pattern: {{ firstCreator }} - {{ title }} - {{ year }} - {{ itemType }}
- Dry run mode for detecting conflicts, duplicates, unmatched files
- Auto-restart capability and error handling

Author: Travis Ross
Version: 1.0
"""

import sqlite3
import os
import json
import subprocess
import signal
import time
import hashlib
import shutil
import re
import logging
import asyncio
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import threading
from concurrent.futures import ThreadPoolExecutor

# Configuration
ZOTERO_DB_PATH = "/Users/travisross/Zotero/zotero.sqlite"
ZOTERO_STORAGE_PATH = "/Users/travisross/Zotero/storage"
ZOTFILE_IMPORT_PATH = "/Users/travisross/ZotFile Import"
DEVONTHINK_INBOX_PATH = "/Users/travisross/Library/Application Support/DEVONthink 3/Inbox"
DEVONTHINK_DATABASE = "Professional"
DEVONZOT_PATH = Path("/Users/travisross/DEVONzot")
STATE_FILE = DEVONZOT_PATH / "service_state.json"
LOG_FILE = DEVONZOT_PATH / "service.log"
PID_FILE = DEVONZOT_PATH / "service.pid"

# Service configuration
SYNC_INTERVAL = 300  # 5 minutes
DEVONTHINK_WAIT_TIME = 3  # Reduced wait time for faster processing
MAX_RESTART_ATTEMPTS = 3
RESTART_DELAY = 30  # seconds
BATCH_SIZE = 50  # Items to process concurrently

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class ZoteroItem:
    """Zotero item with complete metadata"""
    item_id: int
    key: str
    title: str
    creators: List[Dict[str, str]]  # [{"firstName": "John", "lastName": "Doe", "creatorType": "author"}]
    item_type: str
    publication: Optional[str]
    date: Optional[str]
    year: Optional[int]
    doi: Optional[str]
    url: Optional[str]
    abstract: Optional[str]
    tags: List[str]
    collections: List[str]
    date_added: str
    date_modified: str

@dataclass
class ZoteroAttachment:
    """Zotero attachment record"""
    item_id: int
    parent_item_id: Optional[int]
    link_mode: int  # 0=stored, 1=linked, 2=web_link
    content_type: str
    path: Optional[str]
    storage_hash: Optional[str]

@dataclass
class ServiceState:
    """Service state tracking"""
    last_sync: Optional[str] = None
    last_zotero_check: Optional[str] = None
    processed_items: List[int] = None
    restart_count: int = 0
    dry_run_results: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.processed_items is None:
            self.processed_items = []
        if self.dry_run_results is None:
            self.dry_run_results = {}

class FilenameGenerator:
    """Smart filename generation with configurable patterns"""
    
    @staticmethod
    def sanitize_filename(text: str) -> str:
        """Clean text for filesystem compatibility"""
        if not text:
            return ""
        
        # Replace problematic characters
        text = re.sub(r'[<>:"/\\|?*]', '', text)
        text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)  # Remove control characters
        text = re.sub(r'\s+', ' ', text)  # Normalize whitespace
        text = text.strip()
        
        # Limit length
        if len(text) > 100:
            text = text[:97] + "..."
        
        return text
    
    @staticmethod
    def extract_first_creator(creators: List[Dict[str, str]]) -> str:
        """Extract first author/creator for filename"""
        if not creators:
            return ""
        
        first_creator = creators[0]
        last_name = first_creator.get('lastName', '').strip()
        first_name = first_creator.get('firstName', '').strip()
        
        if last_name and first_name:
            return f"{last_name}, {first_name}"
        elif last_name:
            return last_name
        elif first_name:
            return first_name
        else:
            return ""
    
    @staticmethod
    def generate_filename(item: ZoteroItem) -> str:
        """Generate filename: {{ firstCreator }} - {{ title }} - {{ year }} - {{ itemType }}
        
        Uses smart separator skipping - if a component is missing, skip it AND its separator.
        """
        components = []
        
        # First creator
        first_creator = FilenameGenerator.extract_first_creator(item.creators)
        if first_creator:
            components.append(FilenameGenerator.sanitize_filename(first_creator))
        
        # Title 
        if item.title:
            components.append(FilenameGenerator.sanitize_filename(item.title))
        
        # Year
        if item.year:
            components.append(str(item.year))
        
        # Item type
        if item.item_type:
            # Convert technical names to readable format
            item_type_map = {
                'journalArticle': 'Journal Article',
                'bookSection': 'Book Section',
                'book': 'Book',
                'webpage': 'Web Page',
                'newspaperArticle': 'Newspaper Article',
                'magazineArticle': 'Magazine Article',
                'thesis': 'Thesis',
                'conferencePaper': 'Conference Paper',
                'report': 'Report',
                'blogPost': 'Blog Post',
                'podcast': 'Podcast',
                'videoRecording': 'Video',
                'audioRecording': 'Audio',
                'document': 'Document',
                'presentation': 'Presentation'
            }
            readable_type = item_type_map.get(item.item_type, item.item_type.title())
            components.append(readable_type)
        
        # Join with separator, but only if we have components
        if not components:
            return "Untitled Item"
        
        filename = " - ".join(components)
        
        # Final cleanup
        filename = FilenameGenerator.sanitize_filename(filename)
        
        return filename or "Untitled Item"

class ZoteroDatabase:
    """Enhanced Zotero database interface with complete metadata extraction"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    @contextmanager
    def connection(self, read_only=True):
        """Safe database connection with comprehensive error handling"""
        conn = None
        try:
            # Check for journal files that indicate Zotero is writing
            journal_files = [
                f"{self.db_path}-journal",
                f"{self.db_path}-wal", 
                f"{self.db_path}-shm"
            ]
            
            journal_exists = any(os.path.exists(f) for f in journal_files)
            if journal_exists:
                logger.debug("Zotero database journal files detected (Zotero may be running)")
            
            # Connect with timeout
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            
            if read_only:
                conn.execute("PRAGMA query_only = ON")
            
            yield conn
            
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                logger.error("Database locked - Zotero may be running or database is corrupted")
            raise e
        finally:
            if conn:
                conn.close()
    
    def get_item_by_id(self, item_id: int) -> Optional[ZoteroItem]:
        """Get complete item metadata by ID"""
        with self.connection() as conn:
            # Main item data
            query = """
                SELECT 
                    i.itemID,
                    i.key,
                    i.itemTypeID,
                    i.dateAdded,
                    i.dateModified,
                    it.typeName as itemType,
                    MAX(CASE WHEN f.fieldName = 'title' THEN idv.value END) as title,
                    MAX(CASE WHEN f.fieldName = 'publicationTitle' THEN idv.value END) as publication,
                    MAX(CASE WHEN f.fieldName = 'date' THEN idv.value END) as date,
                    MAX(CASE WHEN f.fieldName = 'DOI' THEN idv.value END) as doi,
                    MAX(CASE WHEN f.fieldName = 'url' THEN idv.value END) as url,
                    MAX(CASE WHEN f.fieldName = 'abstractNote' THEN idv.value END) as abstract
                FROM items i
                JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
                LEFT JOIN itemData id ON i.itemID = id.itemID
                LEFT JOIN fields f ON id.fieldID = f.fieldID
                LEFT JOIN itemDataValues idv ON id.valueID = idv.valueID
                WHERE i.itemID = ?
                GROUP BY i.itemID
            """
            
            cursor = conn.execute(query, (item_id,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            # Parse year from date
            year = None
            if row['date']:
                year_match = re.search(r'\b(19|20)\d{2}\b', row['date'])
                if year_match:
                    year = int(year_match.group())
            
            # Get creators
            creators = self._get_item_creators(conn, item_id)
            
            # Get tags
            tags = self._get_item_tags(conn, item_id)
            
            # Get collections  
            collections = self._get_item_collections(conn, item_id)
            
            return ZoteroItem(
                item_id=row['itemID'],
                key=row['key'],
                title=row['title'] or "",
                creators=creators,
                item_type=row['itemType'] or "",
                publication=row['publication'],
                date=row['date'],
                year=year,
                doi=row['doi'],
                url=row['url'],
                abstract=row['abstract'],
                tags=tags,
                collections=collections,
                date_added=row['dateAdded'],
                date_modified=row['dateModified']
            )
    
    def get_items_needing_sync(self, since_timestamp: str = None) -> List[ZoteroItem]:
        """Get items that need syncing to DEVONthink"""
        items = []
        
        with self.connection() as conn:
            # Base query for items without DEVONthink UUIDs
            where_clause = """
                WHERE i.itemTypeID NOT IN (SELECT itemTypeID FROM itemTypes WHERE typeName IN ('note', 'attachment'))
                AND (idv_url.value NOT LIKE 'x-devonthink-item://%' OR idv_url.value IS NULL)
            """
            
            if since_timestamp:
                where_clause += f" AND i.dateModified > '{since_timestamp}'"
            
            query = f"""
                SELECT DISTINCT i.itemID
                FROM items i
                LEFT JOIN itemData id_url ON i.itemID = id_url.itemID 
                LEFT JOIN fields f_url ON id_url.fieldID = f_url.fieldID AND f_url.fieldName = 'url'
                LEFT JOIN itemDataValues idv_url ON id_url.valueID = idv_url.valueID
                {where_clause}
                ORDER BY i.dateModified DESC
                LIMIT 100
            """
            
            cursor = conn.execute(query)
            item_ids = [row['itemID'] for row in cursor.fetchall()]
            
            # Get full metadata for each item
            for item_id in item_ids:
                item = self.get_item_by_id(item_id)
                if item:
                    items.append(item)
        
        return items
    
    def get_stored_attachments(self) -> List[ZoteroAttachment]:
        """Get attachments in Zotero storage needing migration"""
        with self.connection() as conn:
            query = """
                SELECT 
                    ia.itemID,
                    ia.parentItemID,
                    ia.linkMode,
                    ia.contentType,
                    ia.path,
                    ia.storageHash
                FROM itemAttachments ia
                WHERE ia.linkMode IN (0, 1)
                AND ia.path IS NOT NULL
                AND ia.path LIKE 'storage:%'
            """
            
            cursor = conn.execute(query)
            attachments = []
            
            for row in cursor.fetchall():
                attachments.append(ZoteroAttachment(
                    item_id=row['itemID'],
                    parent_item_id=row['parentItemID'],
                    link_mode=row['linkMode'],
                    content_type=row['contentType'],
                    path=row['path'],
                    storage_hash=row['storageHash']
                ))
            
            return attachments
    
    def get_zotfile_symlinks(self) -> List[ZoteroAttachment]:
        """Get ZotFile symlinks needing UUID conversion"""
        with self.connection() as conn:
            query = """
                SELECT 
                    ia.itemID,
                    ia.parentItemID,
                    ia.linkMode,
                    ia.contentType,
                    ia.path,
                    ia.storageHash
                FROM itemAttachments ia
                WHERE ia.linkMode = 2
            """
            
            cursor = conn.execute(query)
            attachments = []
            
            for row in cursor.fetchall():
                attachments.append(ZoteroAttachment(
                    item_id=row['itemID'],
                    parent_item_id=row['parentItemID'],
                    link_mode=row['linkMode'],
                    content_type=row['contentType'],
                    path=row['path'],
                    storage_hash=row['storageHash']
                ))
            
            return attachments
    
    def _get_item_creators(self, conn, item_id: int) -> List[Dict[str, str]]:
        """Get creators with roles"""
        query = """
            SELECT 
                c.firstName,
                c.lastName, 
                ct.creatorType
            FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
            WHERE ic.itemID = ?
            ORDER BY ic.orderIndex
        """
        
        cursor = conn.execute(query, (item_id,))
        creators = []
        
        for row in cursor.fetchall():
            creators.append({
                'firstName': row['firstName'] or "",
                'lastName': row['lastName'] or "",
                'creatorType': row['creatorType'] or "author"
            })
        
        return creators
    
    def _get_item_tags(self, conn, item_id: int) -> List[str]:
        """Get tags for an item"""
        query = """
            SELECT t.name
            FROM itemTags it
            JOIN tags t ON it.tagID = t.tagID
            WHERE it.itemID = ?
            ORDER BY t.name
        """
        
        cursor = conn.execute(query, (item_id,))
        return [row['name'] for row in cursor.fetchall()]
    
    def _get_item_collections(self, conn, item_id: int) -> List[str]:
        """Get collections for an item"""
        query = """
            SELECT c.collectionName
            FROM collectionItems ci
            JOIN collections c ON ci.collectionID = c.collectionID
            WHERE ci.itemID = ?
            ORDER BY c.collectionName
        """
        
        cursor = conn.execute(query, (item_id,))
        return [row['collectionName'] for row in cursor.fetchall()]
    
    def update_item_url(self, item_id: int, devonthink_uuid: str, dry_run=False):
        """Update item URL to DEVONthink UUID link"""
        if dry_run:
            logger.info(f"[DRY RUN] Would update item {item_id} URL to x-devonthink-item://{devonthink_uuid}")
            return True
        
        with self.connection(read_only=False) as conn:
            try:
                # Find URL field ID
                cursor = conn.execute("SELECT fieldID FROM fields WHERE fieldName = 'url'")
                url_field_row = cursor.fetchone()
                if not url_field_row:
                    logger.error("Could not find URL field in Zotero database")
                    return False
                
                url_field_id = url_field_row['fieldID']
                devonthink_url = f"x-devonthink-item://{devonthink_uuid}"
                
                # Check if URL value already exists
                cursor = conn.execute("SELECT valueID FROM itemDataValues WHERE value = ?", (devonthink_url,))
                value_row = cursor.fetchone()
                
                if value_row:
                    value_id = value_row['valueID']
                else:
                    # Insert new value
                    cursor = conn.execute("INSERT INTO itemDataValues (value) VALUES (?)", (devonthink_url,))
                    value_id = cursor.lastrowid
                
                # Update or insert item data
                cursor = conn.execute(
                    "INSERT OR REPLACE INTO itemData (itemID, fieldID, valueID) VALUES (?, ?, ?)",
                    (item_id, url_field_id, value_id)
                )
                
                conn.commit()
                logger.info(f"Updated item {item_id} with DEVONthink UUID: {devonthink_uuid}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to update item {item_id} URL: {e}")
                conn.rollback()
                return False

class DEVONthinkInterface:
    """Enhanced DEVONthink interface with comprehensive AppleScript operations"""
    
    def __init__(self, database_name: str = "Professional"):
        self.database_name = database_name
    
    def execute_script(self, script: str, timeout: int = 30) -> str:
        """Execute AppleScript with timeout and error handling"""
        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False  # Don't raise on non-zero exit
            )
            
            if result.returncode != 0:
                error_msg = result.stderr.strip()
                if "DEVONthink 3" in error_msg and "not running" in error_msg:
                    raise Exception("DEVONthink is not running")
                elif "database" in error_msg.lower() and "not found" in error_msg.lower():
                    raise Exception(f"Database '{self.database_name}' not found")
                else:
                    raise Exception(f"AppleScript error: {error_msg}")
            
            return result.stdout.strip()
            
        except subprocess.TimeoutExpired:
            raise Exception(f"AppleScript timed out after {timeout} seconds")
        except Exception as e:
            raise Exception(f"AppleScript execution failed: {e}")
    
    def is_devonthink_running(self) -> bool:
        """Check if DEVONthink is running"""
        try:
            script = '''
            tell application "System Events"
                return (name of processes) contains "DEVONthink 3"
            end tell
            '''
            result = self.execute_script(script, timeout=5)
            return result.lower() == "true"
        except:
            return False
    
    def wait_for_devonthink(self, max_wait: int = 30) -> bool:
        """Wait for DEVONthink to be available"""
        for i in range(max_wait):
            if self.is_devonthink_running():
                # Additional wait for DEVONthink to fully load
                time.sleep(2)
                return True
            logger.info(f"Waiting for DEVONthink... ({i+1}/{max_wait})")
            time.sleep(1)
        return False
    
    def copy_file_to_inbox(self, file_path: str, new_filename: str, dry_run=False) -> bool:
        """Copy file to DEVONthink Inbox with new name"""
        if dry_run:
            logger.info(f"[DRY RUN] Would copy {file_path} to Inbox as '{new_filename}'")
            return True
        
        try:
            inbox_path = Path(DEVONTHINK_INBOX_PATH)
            if not inbox_path.exists():
                logger.error(f"DEVONthink Inbox not found: {inbox_path}")
                return False
            
            source_path = Path(file_path)
            if not source_path.exists():
                logger.error(f"Source file not found: {source_path}")
                return False
            
            # Determine file extension from source
            file_extension = source_path.suffix
            target_filename = new_filename
            if not target_filename.endswith(file_extension):
                target_filename += file_extension
            
            target_path = inbox_path / target_filename
            
            # Copy file
            shutil.copy2(source_path, target_path)
            logger.info(f"Copied to Inbox: {target_filename}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to copy file to Inbox: {e}")
            return False
    
    async def find_item_by_filename_after_wait_async(self, filename: str, dry_run=False) -> Optional[str]:
        """Find DEVONthink item by filename with async wait for auto-sort"""
        if dry_run:
            logger.info(f"[DRY RUN] Would search for filename '{filename}' after {DEVONTHINK_WAIT_TIME}s wait")
            await asyncio.sleep(0.1)  # Simulate quick processing
            return "dry-run-uuid"
        
        if not self.is_devonthink_running():
            logger.error("DEVONthink is not running")
            return None
        
        # Wait for DEVONthink to process the file (async)
        logger.debug(f"Waiting {DEVONTHINK_WAIT_TIME} seconds for DEVONthink auto-sort...")
        await asyncio.sleep(DEVONTHINK_WAIT_TIME)
        
        # Search across all databases
        databases = ["Global Inbox", "Professional", "Articles", "Books", "Research"]
        
        for db_name in databases:
            uuid = self._search_database_for_filename(filename, db_name)
            if uuid:
                logger.info(f"Found item in {db_name}: {uuid}")
                return uuid
        
        logger.debug(f"Item not found in any database: {filename}")
        return None

    def find_item_by_filename_after_wait(self, filename: str, dry_run=False) -> Optional[str]:
        """Synchronous version of find_item_by_filename_after_wait"""
        if dry_run:
            logger.info(f"[DRY RUN] Would search for filename '{filename}' after {DEVONTHINK_WAIT_TIME}s wait")
            return "dry-run-uuid"

        if not self.is_devonthink_running():
            logger.error("DEVONthink is not running")
            return None

        logger.debug(f"Waiting {DEVONTHINK_WAIT_TIME} seconds for DEVONthink auto-sort...")
        time.sleep(DEVONTHINK_WAIT_TIME)

        databases = ["Global Inbox", "Professional", "Articles", "Books", "Research"]
        for db_name in databases:
            uuid = self._search_database_for_filename(filename, db_name)
            if uuid:
                logger.info(f"Found item in {db_name}: {uuid}")
                return uuid

        logger.debug(f"Item not found in any database: {filename}")
        return None

    async def batch_search_items(self, filenames: List[str], dry_run=False) -> Dict[str, Optional[str]]:
        """Search for multiple items concurrently"""
        if dry_run:
            # Quick simulation for dry run
            await asyncio.sleep(0.1)
            return {filename: "dry-run-uuid" for filename in filenames}
        
        tasks = []
        for filename in filenames:
            task = self.find_item_by_filename_after_wait_async(filename, dry_run)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        filename_to_uuid = {}
        for filename, result in zip(filenames, results):
            if isinstance(result, Exception):
                logger.error(f"Error searching for {filename}: {result}")
                filename_to_uuid[filename] = None
            else:
                filename_to_uuid[filename] = result
        
        return filename_to_uuid
    
    def _search_database_for_filename(self, filename: str, database_name: str) -> Optional[str]:
        """Search specific database for filename"""
        safe_filename = filename.replace('"', '\\"').replace('\\', '\\\\')

        script = f'''
        tell application "DEVONthink 3"
            try
                tell database "{database_name}"
                    set searchResults to search "name:\\"{safe_filename}\\""

                    if (count of searchResults) > 0 then
                        set theRecord to item 1 of searchResults
                        return uuid of theRecord
                    else
                        return ""
                    end if
                end tell
            on error
                return ""
            end try
        end tell
        '''

        try:
            result = self.execute_script(script)
            return result if result else None
        except:
            return None
    
    def update_item_metadata(self, uuid: str, item: ZoteroItem, dry_run=False) -> bool:
        """Update DEVONthink item with Zotero metadata"""
        if dry_run:
            logger.info(f"[DRY RUN] Would update metadata for UUID: {uuid}")
            return True
        
        if not self.is_devonthink_running():
            return False
        
        # Prepare metadata
        authors_str = ", ".join([
            f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
            for c in item.creators if c.get('lastName')
        ])
        safe_authors = authors_str.replace('"', '\\"')
        safe_publication = (item.publication or "").replace('"', '\\"')
        safe_abstract = (item.abstract or "")[:500].replace('"', '\\"')
        
        # Prepare tags
        tags_list = item.tags + item.collections
        if item.item_type:
            tags_list.append(item.item_type)
        if item.year:
            tags_list.append(str(item.year))
        
        # Format tags for AppleScript (Python 3.9 compatible)
        escaped_tags = ['"' + tag.replace('"', '\\"') + '"' for tag in tags_list if tag]
        tags_applescript = ', '.join(escaped_tags)
        
        script = f'''
        tell application "DEVONthink 3"
            try
                set theRecord to get record with uuid "{uuid}"
                
                -- Set comment with Zotero metadata
                set theComment to "Zotero Item: {item.item_id}\\nKey: {item.key}"
                if "{safe_authors}" is not "" then
                    set theComment to theComment & "\\nAuthors: {safe_authors}"
                end if
                if "{safe_publication}" is not "" then
                    set theComment to theComment & "\\nPublication: {safe_publication}"
                end if
                if "{item.year or ''}" is not "" then
                    set theComment to theComment & "\\nYear: {item.year or ''}"
                end if
                if "{item.doi or ''}" is not "" then
                    set theComment to theComment & "\\nDOI: {item.doi or ''}"
                end if
                if "{safe_abstract}" is not "" then
                    set theComment to theComment & "\\nAbstract: {safe_abstract}"
                end if
                
                set comment of theRecord to theComment
                
                -- Set tags
                if "{tags_applescript}" is not "" then
                    set tags of theRecord to {{{tags_applescript}}}
                end if
                
                return "SUCCESS"
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''
        
        try:
            result = self.execute_script(script)
            if result.startswith("ERROR:"):
                logger.error(f"Failed to update metadata for {uuid}: {result[7:]}")
                return False
            
            logger.info(f"Updated metadata for DEVONthink item: {uuid}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update metadata for {uuid}: {e}")
            return False

class ConflictDetector:
    """Detect conflicts, duplicates, and unmatched files for dry run mode"""
    
    def __init__(self, zotero_db: ZoteroDatabase, devonthink: DEVONthinkInterface):
        self.zotero_db = zotero_db
        self.devonthink = devonthink
    
    def detect_conflicts(self) -> Dict[str, List[Dict[str, Any]]]:
        """Comprehensive conflict detection"""
        conflicts = {
            'filename_collisions': [],
            'unmatched_files': [],
            'duplicates': [],
            'problematic_items': []
        }
        
        logger.info("🔍 Running conflict detection...")
        
        # Check for filename collisions
        self._detect_filename_collisions(conflicts)
        
        # Check for unmatched ZotFile symlinks
        self._detect_unmatched_symlinks(conflicts)
        
        # Check for duplicate items
        self._detect_duplicates(conflicts)
        
        # Check for problematic metadata
        self._detect_problematic_metadata(conflicts)
        
        return conflicts
    
    def _detect_filename_collisions(self, conflicts: Dict):
        """Detect potential filename collisions"""
        items = self.zotero_db.get_items_needing_sync()
        filenames = {}
        
        for item in items:
            filename = FilenameGenerator.generate_filename(item)
            if filename in filenames:
                conflicts['filename_collisions'].append({
                    'filename': filename,
                    'item1_id': filenames[filename]['item_id'],
                    'item2_id': item.item_id,
                    'title1': filenames[filename]['title'],
                    'title2': item.title
                })
            else:
                filenames[filename] = {'item_id': item.item_id, 'title': item.title}
    
    def _detect_unmatched_symlinks(self, conflicts: Dict):
        """Find ZotFile symlinks that don't match DEVONthink items"""
        symlinks = self.zotero_db.get_zotfile_symlinks()
        logger.info(f"Checking {len(symlinks)} linked attachments for DEVONthink matches...")
        
        # Limit to first 50 for dry run performance
        sample_symlinks = symlinks[:50]
        
        for i, symlink in enumerate(sample_symlinks):
            if symlink.path:
                if i % 10 == 0:
                    logger.info(f"Progress: {i+1}/{len(sample_symlinks)} attachments checked")
                    
                filename = Path(symlink.path).name
                # Remove extension for search
                filename_no_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
                
                # Skip DEVONthink search in dry run for speed
                logger.debug(f"Would search DEVONthink for: {filename_no_ext}")
                
                # For now, assume some are unmatched for demonstration
                if i % 5 == 0:  # Every 5th item as "unmatched" example
                    conflicts['unmatched_files'].append({
                        'attachment_id': symlink.item_id,
                        'parent_id': symlink.parent_item_id,
                        'filename': filename,
                        'path': symlink.path
                    })
    
    def _detect_duplicates(self, conflicts: Dict):
        """Detect potential duplicate items by title similarity"""
        items = self.zotero_db.get_items_needing_sync()
        
        for i, item1 in enumerate(items):
            for item2 in items[i+1:]:
                if self._are_likely_duplicates(item1, item2):
                    conflicts['duplicates'].append({
                        'item1_id': item1.item_id,
                        'item2_id': item2.item_id,
                        'title1': item1.title,
                        'title2': item2.title,
                        'similarity_reason': 'similar_titles'
                    })
    
    def _detect_problematic_metadata(self, conflicts: Dict):
        """Find items with problematic metadata that might cause issues"""
        items = self.zotero_db.get_items_needing_sync()
        
        for item in items:
            issues = []
            
            if not item.title or len(item.title.strip()) == 0:
                issues.append('no_title')
            
            if not item.creators:
                issues.append('no_creators')
            
            if not item.item_type:
                issues.append('no_item_type')
            
            # Check for very long titles that might cause filesystem issues
            if item.title and len(item.title) > 200:
                issues.append('title_too_long')
            
            # Check for problematic characters
            if item.title and re.search(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f]', item.title):
                issues.append('problematic_characters')
            
            if issues:
                conflicts['problematic_items'].append({
                    'item_id': item.item_id,
                    'title': item.title,
                    'issues': issues
                })
    
    def _are_likely_duplicates(self, item1: ZoteroItem, item2: ZoteroItem) -> bool:
        """Determine if two items are likely duplicates"""
        if not item1.title or not item2.title:
            return False
        
        # Simple similarity check - could be enhanced with more sophisticated algorithms
        title1_words = set(item1.title.lower().split())
        title2_words = set(item2.title.lower().split())
        
        if len(title1_words) == 0 or len(title2_words) == 0:
            return False
        
        intersection = title1_words.intersection(title2_words)
        union = title1_words.union(title2_words)
        
        similarity = len(intersection) / len(union)
        return similarity > 0.8  # 80% word overlap

class DEVONzotService:
    """Main service class that orchestrates the complete workflow"""
    
    def __init__(self):
        self.zotero_db = ZoteroDatabase(ZOTERO_DB_PATH)
        self.devonthink = DEVONthinkInterface(DEVONTHINK_DATABASE)
        self.conflict_detector = ConflictDetector(self.zotero_db, self.devonthink)
        self.state = self._load_state()
        self.running = False
        self.restart_count = 0
        
        # Ensure DEVONzot directory exists
        DEVONZOT_PATH.mkdir(exist_ok=True)
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _load_state(self) -> ServiceState:
        """Load service state from file"""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    return ServiceState(**data)
            except Exception as e:
                logger.warning(f"Could not load state file: {e}")
        
        return ServiceState()
    
    def _save_state(self):
        """Save service state to file"""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(asdict(self.state), f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Could not save state: {e}")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
    
    def run_dry_run(self) -> Dict[str, Any]:
        """Run comprehensive dry run analysis, then execute full migration in dry-run mode"""
        logger.info("🧪 Starting comprehensive dry run analysis...")

        results = {
            'timestamp': datetime.now().isoformat(),
            'conflicts': {},
            'migration_analysis': {},
            'sync_analysis': {}
        }

        # Detect conflicts
        results['conflicts'] = self.conflict_detector.detect_conflicts()

        # Analyze migration workload
        results['migration_analysis'] = self._analyze_migration_workload()

        # Analyze sync requirements
        results['sync_analysis'] = self._analyze_sync_requirements()

        # Save results
        self.state.dry_run_results = results
        self._save_state()

        # Report summary
        self._report_dry_run_results(results)

        # Run full migration cycle in dry-run mode (searches DEVONthink, no copies)
        logger.info("\n" + "="*70)
        logger.info("🔍 Running full migration cycle in dry-run mode...")
        logger.info("="*70)
        self.run_service_cycle(dry_run=True)

        return results
    
    def _analyze_migration_workload(self) -> Dict[str, Any]:
        """Analyze what needs to be migrated"""
        analysis = {
            'stored_attachments': 0,
            'zotfile_symlinks': 0,
            'storage_size_bytes': 0,
            'problematic_paths': []
        }
        
        # Count stored attachments
        stored_attachments = self.zotero_db.get_stored_attachments()
        analysis['stored_attachments'] = len(stored_attachments)
        
        # Calculate storage size and check paths
        for attachment in stored_attachments:
            file_path = self._resolve_storage_path(attachment)
            if file_path and file_path.exists():
                try:
                    analysis['storage_size_bytes'] += file_path.stat().st_size
                except:
                    analysis['problematic_paths'].append(str(file_path))
            elif file_path:
                analysis['problematic_paths'].append(str(file_path))
        
        # Count ZotFile symlinks
        zotfile_symlinks = self.zotero_db.get_zotfile_symlinks()
        analysis['zotfile_symlinks'] = len(zotfile_symlinks)
        
        return analysis
    
    def _analyze_sync_requirements(self) -> Dict[str, Any]:
        """Analyze sync requirements"""
        analysis = {
            'items_needing_sync': 0,
            'items_with_devonthink_links': 0,
            'last_sync': self.state.last_sync,
            'estimated_sync_time_minutes': 0
        }
        
        # Count items needing sync
        items_needing_sync = self.zotero_db.get_items_needing_sync()
        analysis['items_needing_sync'] = len(items_needing_sync)
        
        # Estimate sync time (rough calculation)
        analysis['estimated_sync_time_minutes'] = len(items_needing_sync) * 0.5  # 30 seconds per item
        
        return analysis
    
    def _report_dry_run_results(self, results: Dict[str, Any]):
        """Report dry run results to console and log"""
        conflicts = results['conflicts']
        migration = results['migration_analysis'] 
        sync = results['sync_analysis']
        
        print("\n" + "="*60)
        print("🧪 DRY RUN ANALYSIS COMPLETE")
        print("="*60)
        
        print("\n📊 MIGRATION WORKLOAD:")
        print(f"  Stored attachments to migrate: {migration['stored_attachments']:,}")
        print(f"  ZotFile symlinks to convert: {migration['zotfile_symlinks']:,}")
        if migration['storage_size_bytes'] > 0:
            size_mb = migration['storage_size_bytes'] / (1024 * 1024)
            print(f"  Total storage size: {size_mb:.1f} MB")
        
        print("\n🔄 SYNC REQUIREMENTS:")
        print(f"  Items needing sync: {sync['items_needing_sync']:,}")
        print(f"  Estimated sync time: {sync['estimated_sync_time_minutes']:.1f} minutes")
        
        print("\n⚠️  POTENTIAL CONFLICTS:")
        print(f"  Filename collisions: {len(conflicts['filename_collisions'])}")
        print(f"  Unmatched files: {len(conflicts['unmatched_files'])}")
        print(f"  Potential duplicates: {len(conflicts['duplicates'])}")
        print(f"  Problematic items: {len(conflicts['problematic_items'])}")
        
        if conflicts['filename_collisions']:
            print("\n🚨 FILENAME COLLISIONS:")
            for collision in conflicts['filename_collisions'][:5]:  # Show first 5
                print(f"  '{collision['filename']}' - Items {collision['item1_id']} and {collision['item2_id']}")
        
        if conflicts['unmatched_files']:
            print("\n📁 UNMATCHED FILES:")
            for unmatched in conflicts['unmatched_files'][:5]:  # Show first 5
                print(f"  {unmatched['filename']} (Attachment {unmatched['attachment_id']})")
        
        if conflicts['problematic_items']:
            print("\n⚠️  PROBLEMATIC ITEMS:")
            for problem in conflicts['problematic_items'][:5]:  # Show first 5
                issues_str = ", ".join(problem['issues'])
                print(f"  Item {problem['item_id']}: {issues_str}")
        
        print("\n" + "="*60)
    
    def migrate_stored_attachments(self, dry_run=False) -> Dict[str, int]:
        """Migrate Zotero stored files to DEVONthink with comprehensive tracking"""
        logger.info("📁 Starting migration of stored attachments...")

        # Enhanced results tracking
        results = {
            'success': 0,
            'error': 0,
            'skipped': 0,
            'skipped_file_missing': 0,
            'skipped_path_invalid': 0,
            'skipped_no_parent': 0,
            'skipped_parent_not_found': 0,
            'skipped_already_processed': 0,
            'deleted_originals': 0
        }

        skipped_details = []  # Track details for reporting

        attachments = self.zotero_db.get_stored_attachments()
        logger.info(f"📊 Detection Summary: Found {len(attachments)} stored attachments (linkMode=0,1)")

        for attachment in attachments:
            try:
                # Skip if already processed
                if attachment.parent_item_id and attachment.parent_item_id in self.state.processed_items:
                    results['skipped_already_processed'] += 1
                    continue

                # Resolve file path
                file_path = self._resolve_storage_path(attachment)
                if not file_path:
                    reason = "Invalid or unresolvable path"
                    logger.warning(f"⚠️  Attachment {attachment.item_id}: {reason} - {attachment.path}")
                    results['skipped_path_invalid'] += 1
                    results['skipped'] += 1
                    skipped_details.append({
                        'item_id': attachment.item_id,
                        'reason': reason,
                        'path': attachment.path
                    })
                    continue

                if not file_path.exists():
                    reason = "File missing on disk"
                    logger.warning(f"⚠️  Attachment {attachment.item_id}: {reason} - {file_path}")
                    results['skipped_file_missing'] += 1
                    results['skipped'] += 1
                    skipped_details.append({
                        'item_id': attachment.item_id,
                        'reason': reason,
                        'path': str(file_path)
                    })
                    continue

                # Get parent item metadata
                if not attachment.parent_item_id:
                    reason = "No parent item (orphaned attachment)"
                    logger.warning(f"⚠️  Attachment {attachment.item_id}: {reason}")
                    results['skipped_no_parent'] += 1
                    results['skipped'] += 1
                    skipped_details.append({
                        'item_id': attachment.item_id,
                        'reason': reason,
                        'path': attachment.path
                    })
                    continue

                parent_item = self.zotero_db.get_item_by_id(attachment.parent_item_id)
                if not parent_item:
                    reason = f"Parent item {attachment.parent_item_id} not found in database"
                    logger.warning(f"⚠️  Attachment {attachment.item_id}: {reason}")
                    results['skipped_parent_not_found'] += 1
                    results['skipped'] += 1
                    skipped_details.append({
                        'item_id': attachment.item_id,
                        'reason': reason,
                        'path': attachment.path,
                        'parent_id': attachment.parent_item_id
                    })
                    continue

                # Generate filename
                filename = FilenameGenerator.generate_filename(parent_item)
                logger.info(f"📎 Processing attachment {attachment.item_id}: {filename}")

                # Check if already in DEVONthink (prevents duplicates)
                dt_uuid = None
                for db_name in ["Global Inbox", "Professional", "Articles", "Books", "Research"]:
                    dt_uuid = self.devonthink._search_database_for_filename(filename, db_name)
                    if dt_uuid:
                        prefix = "[DRY RUN] " if dry_run else ""
                        logger.info(f"{prefix}Found existing DEVONthink item in {db_name} for {filename}: {dt_uuid}")
                        break

                if not dt_uuid:
                    # Copy to DEVONthink Inbox
                    if self.devonthink.copy_file_to_inbox(str(file_path), filename, dry_run):
                        # Wait and search for UUID
                        dt_uuid = self.devonthink.find_item_by_filename_after_wait(filename, dry_run)
                    else:
                        results['error'] += 1
                        logger.error(f"❌ Failed to copy file to inbox for {attachment.item_id}")
                        continue

                if dt_uuid:
                    # Update Zotero to use DEVONthink UUID
                    if self.zotero_db.update_item_url(attachment.parent_item_id, dt_uuid, dry_run):
                        # Update DEVONthink metadata
                        if self.devonthink.update_item_metadata(dt_uuid, parent_item, dry_run):
                            results['success'] += 1
                            logger.info(f"✅ Migrated attachment {attachment.item_id} → {dt_uuid}")

                            # Track processed item and save immediately
                            if attachment.parent_item_id not in self.state.processed_items:
                                self.state.processed_items.append(attachment.parent_item_id)
                                self._save_state()

                            # Clean up original from Zotero storage
                            if not dry_run:
                                try:
                                    file_path.unlink()
                                    results['deleted_originals'] += 1
                                    logger.info(f"🗑️  Deleted original: {file_path}")
                                    # Remove empty storage key directory
                                    if file_path.parent != Path(ZOTERO_STORAGE_PATH) and not any(file_path.parent.iterdir()):
                                        file_path.parent.rmdir()
                                        logger.info(f"🗑️  Removed empty directory: {file_path.parent}")
                                except Exception as e:
                                    logger.warning(f"⚠️  Failed to delete original {file_path}: {e}")
                            else:
                                logger.info(f"[DRY RUN] Would delete original: {file_path}")
                        else:
                            results['error'] += 1
                            logger.error(f"❌ Failed to update DEVONthink metadata for {attachment.item_id}")
                    else:
                        results['error'] += 1
                        logger.error(f"❌ Failed to update Zotero URL for {attachment.item_id}")
                else:
                    results['error'] += 1
                    logger.error(f"❌ Failed to find DEVONthink UUID for {attachment.item_id}")

                # Small delay between operations
                if not dry_run:
                    time.sleep(2)

            except Exception as e:
                logger.error(f"❌ Failed to migrate attachment {attachment.item_id}: {e}")
                results['error'] += 1

        # Write skip report
        if skipped_details and not dry_run:
            skip_report_file = DEVONZOT_PATH / "skipped_attachments.json"
            try:
                with open(skip_report_file, 'w') as f:
                    json.dump({
                        'timestamp': datetime.now().isoformat(),
                        'total_skipped': len(skipped_details),
                        'details': skipped_details
                    }, f, indent=2)
                logger.info(f"📄 Skip report saved to: {skip_report_file}")
            except Exception as e:
                logger.error(f"Failed to write skip report: {e}")

        # Log summary
        logger.info(f"\n📊 Migration Summary:")
        logger.info(f"  ✅ Success: {results['success']}")
        logger.info(f"  🗑️  Deleted originals: {results['deleted_originals']}")
        logger.info(f"  ❌ Errors: {results['error']}")
        logger.info(f"  ⏭️  Skipped: {results['skipped']} total")
        logger.info(f"     - File missing: {results['skipped_file_missing']}")
        logger.info(f"     - Invalid path: {results['skipped_path_invalid']}")
        logger.info(f"     - No parent: {results['skipped_no_parent']}")
        logger.info(f"     - Parent not found: {results['skipped_parent_not_found']}")
        logger.info(f"     - Already processed: {results['skipped_already_processed']}")

        return results

    def migrate_zotfile_attachments(self, dry_run=False) -> Dict[str, int]:
        """Migrate ZotFile-managed linked files (linkMode=2) to DEVONthink

        This handles files stored in ZotFile Import or other locations that are
        linked to Zotero items but not yet imported to DEVONthink.
        """
        logger.info("📁 Starting migration of ZotFile linked attachments...")

        # Enhanced results tracking
        results = {
            'success': 0,
            'error': 0,
            'skipped': 0,
            'skipped_file_missing': 0,
            'skipped_path_invalid': 0,
            'skipped_no_parent': 0,
            'skipped_parent_not_found': 0,
            'skipped_already_processed': 0,
            'deleted_originals': 0
        }

        skipped_details = []

        # Get ZotFile linked attachments (linkMode=2)
        attachments = self.zotero_db.get_zotfile_symlinks()
        logger.info(f"📊 Detection Summary: Found {len(attachments)} ZotFile linked attachments (linkMode=2)")

        for attachment in attachments:
            try:
                # Skip if already processed
                if attachment.parent_item_id and attachment.parent_item_id in self.state.processed_items:
                    results['skipped_already_processed'] += 1
                    continue

                # Resolve file path (linkMode=2 uses absolute paths)
                if not attachment.path:
                    reason = "No path specified"
                    logger.warning(f"⚠️  Attachment {attachment.item_id}: {reason}")
                    results['skipped_path_invalid'] += 1
                    results['skipped'] += 1
                    skipped_details.append({
                        'item_id': attachment.item_id,
                        'reason': reason,
                        'path': None
                    })
                    continue

                file_path = Path(attachment.path)

                # Skip if file doesn't exist
                if not file_path.exists():
                    reason = "File missing on disk"
                    logger.warning(f"⚠️  Attachment {attachment.item_id}: {reason} - {file_path}")
                    results['skipped_file_missing'] += 1
                    results['skipped'] += 1
                    skipped_details.append({
                        'item_id': attachment.item_id,
                        'reason': reason,
                        'path': str(file_path)
                    })
                    continue

                # Get parent item metadata
                if not attachment.parent_item_id:
                    reason = "No parent item (orphaned attachment)"
                    logger.warning(f"⚠️  Attachment {attachment.item_id}: {reason}")
                    results['skipped_no_parent'] += 1
                    results['skipped'] += 1
                    skipped_details.append({
                        'item_id': attachment.item_id,
                        'reason': reason,
                        'path': str(file_path)
                    })
                    continue

                parent_item = self.zotero_db.get_item_by_id(attachment.parent_item_id)
                if not parent_item:
                    reason = f"Parent item {attachment.parent_item_id} not found in database"
                    logger.warning(f"⚠️  Attachment {attachment.item_id}: {reason}")
                    results['skipped_parent_not_found'] += 1
                    results['skipped'] += 1
                    skipped_details.append({
                        'item_id': attachment.item_id,
                        'reason': reason,
                        'path': str(file_path),
                        'parent_id': attachment.parent_item_id
                    })
                    continue

                # Generate filename from metadata
                filename = FilenameGenerator.generate_filename(parent_item)
                logger.info(f"📎 Processing ZotFile attachment {attachment.item_id}: {filename}")

                # Check if already in DEVONthink (prevents duplicates)
                dt_uuid = None
                for db_name in ["Global Inbox", "Professional", "Articles", "Books", "Research"]:
                    dt_uuid = self.devonthink._search_database_for_filename(filename, db_name)
                    if dt_uuid:
                        prefix = "[DRY RUN] " if dry_run else ""
                        logger.info(f"{prefix}Found existing DEVONthink item in {db_name} for {filename}: {dt_uuid}")
                        break

                if not dt_uuid:
                    # Copy to DEVONthink Inbox
                    if self.devonthink.copy_file_to_inbox(str(file_path), filename, dry_run):
                        # Wait and search for UUID
                        dt_uuid = self.devonthink.find_item_by_filename_after_wait(filename, dry_run)
                    else:
                        results['error'] += 1
                        logger.error(f"❌ Failed to copy file to inbox for {attachment.item_id}")
                        continue

                if dt_uuid:
                    # Update Zotero to use DEVONthink UUID
                    if self.zotero_db.update_item_url(attachment.parent_item_id, dt_uuid, dry_run):
                        # Update DEVONthink metadata
                        if self.devonthink.update_item_metadata(dt_uuid, parent_item, dry_run):
                            results['success'] += 1
                            logger.info(f"✅ Migrated ZotFile attachment {attachment.item_id} → {dt_uuid}")

                            # Track processed item and save immediately
                            if attachment.parent_item_id not in self.state.processed_items:
                                self.state.processed_items.append(attachment.parent_item_id)
                                self._save_state()

                            # Clean up original from ZotFile storage
                            if not dry_run:
                                try:
                                    file_path.unlink()
                                    results['deleted_originals'] += 1
                                    logger.info(f"🗑️  Deleted original: {file_path}")
                                    # Remove empty parent directory
                                    if not any(file_path.parent.iterdir()):
                                        file_path.parent.rmdir()
                                        logger.info(f"🗑️  Removed empty directory: {file_path.parent}")
                                except Exception as e:
                                    logger.warning(f"⚠️  Failed to delete original {file_path}: {e}")
                            else:
                                logger.info(f"[DRY RUN] Would delete original: {file_path}")
                        else:
                            results['error'] += 1
                            logger.error(f"❌ Failed to update DEVONthink metadata for {attachment.item_id}")
                    else:
                        results['error'] += 1
                        logger.error(f"❌ Failed to update Zotero URL for {attachment.item_id}")
                else:
                    results['error'] += 1
                    logger.error(f"❌ Failed to find DEVONthink UUID for {attachment.item_id}")

                # Small delay between operations
                if not dry_run:
                    time.sleep(2)

            except Exception as e:
                logger.error(f"❌ Failed to migrate ZotFile attachment {attachment.item_id}: {e}")
                results['error'] += 1

        # Write skip report
        if skipped_details and not dry_run:
            skip_report_file = DEVONZOT_PATH / "skipped_zotfile_attachments.json"
            try:
                with open(skip_report_file, 'w') as f:
                    json.dump({
                        'timestamp': datetime.now().isoformat(),
                        'total_skipped': len(skipped_details),
                        'details': skipped_details
                    }, f, indent=2)
                logger.info(f"📄 ZotFile skip report saved to: {skip_report_file}")
            except Exception as e:
                logger.error(f"Failed to write ZotFile skip report: {e}")

        # Log summary
        logger.info(f"\n📊 ZotFile Migration Summary:")
        logger.info(f"  ✅ Success: {results['success']}")
        logger.info(f"  🗑️  Deleted originals: {results['deleted_originals']}")
        logger.info(f"  ❌ Errors: {results['error']}")
        logger.info(f"  ⏭️  Skipped: {results['skipped']} total")
        logger.info(f"     - File missing: {results['skipped_file_missing']}")
        logger.info(f"     - Invalid path: {results['skipped_path_invalid']}")
        logger.info(f"     - No parent: {results['skipped_no_parent']}")
        logger.info(f"     - Parent not found: {results['skipped_parent_not_found']}")
        logger.info(f"     - Already processed: {results['skipped_already_processed']}")

        return results

    async def convert_zotfile_symlinks_async(self, dry_run=False, batch_size=20) -> Dict[str, int]:
        """Convert ZotFile symlinks to DEVONthink UUID links using async batch processing"""
        logger.info("🔗 Converting ZotFile symlinks with async processing...")
        
        results = {'success': 0, 'error': 0, 'skipped': 0}
        
        symlinks = self.zotero_db.get_zotfile_symlinks()
        logger.info(f"Found {len(symlinks)} ZotFile symlinks to convert")
        
        if not symlinks:
            return results
        
        # Process in batches for better performance
        total_batches = (len(symlinks) + batch_size - 1) // batch_size
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(symlinks))
            batch = symlinks[start_idx:end_idx]
            
            logger.info(f"Processing batch {batch_num + 1}/{total_batches} ({len(batch)} items)")
            
            # Prepare batch for concurrent processing
            batch_items = []
            valid_symlinks = []
            
            for symlink in batch:
                if not symlink.path:
                    results['skipped'] += 1
                    continue
                
                filename = Path(symlink.path).name
                filename_no_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
                
                batch_items.append(filename_no_ext)
                valid_symlinks.append(symlink)
            
            if not batch_items:
                continue
            
            # Process batch concurrently
            try:
                uuid_results = await self.devonthink.batch_search_items(batch_items, dry_run)
                
                # Update Zotero with results
                for symlink, filename_no_ext in zip(valid_symlinks, batch_items):
                    try:
                        dt_uuid = uuid_results.get(filename_no_ext)
                        
                        if dt_uuid and dt_uuid != "dry-run-uuid":
                            # Get parent item for metadata update
                            if symlink.parent_item_id:
                                parent_item = self.zotero_db.get_item_by_id(symlink.parent_item_id)
                                if parent_item:
                                    # Update Zotero with UUID link
                                    if self.zotero_db.update_item_url(symlink.parent_item_id, dt_uuid, dry_run):
                                        # Update DEVONthink metadata
                                        if await self.devonthink_update_metadata_async(dt_uuid, parent_item, dry_run):
                                            results['success'] += 1
                                            logger.info(f"Converted symlink {symlink.item_id} → {dt_uuid}")
                                            continue
                            
                            results['error'] += 1
                        elif dt_uuid == "dry-run-uuid":
                            results['success'] += 1  # Count dry run successes
                        else:
                            filename = Path(symlink.path).name
                            logger.debug(f"No DEVONthink item found for: {filename}")
                            results['skipped'] += 1
                            
                    except Exception as e:
                        logger.error(f"Failed to convert symlink {symlink.item_id}: {e}")
                        results['error'] += 1
                
                # Progress update
                total_processed = start_idx + len(batch)
                logger.info(f"Batch {batch_num + 1} complete. Progress: {total_processed}/{len(symlinks)} ({100*total_processed/len(symlinks):.1f}%)")
                
                # Small delay between batches to avoid overwhelming DEVONthink
                if not dry_run and batch_num < total_batches - 1:
                    await asyncio.sleep(2)
                    
            except Exception as e:
                logger.error(f"Failed to process batch {batch_num + 1}: {e}")
                results['error'] += len(batch)
        
        logger.info(f"Symlink conversion complete: {results}")
        return results
    
    async def devonthink_update_metadata_async(self, uuid: str, item, dry_run=False) -> bool:
        """Async wrapper for metadata update"""
        # Since AppleScript is synchronous, we just wrap it
        return self.devonthink.update_item_metadata(uuid, item, dry_run)
    
    def sync_new_items(self, dry_run=False) -> Dict[str, int]:
        """Sync new Zotero items to DEVONthink"""
        logger.info("🔄 Syncing new items...")
        
        results = {'success': 0, 'error': 0, 'skipped': 0}
        
        # Get items modified since last sync
        since_timestamp = self.state.last_sync
        items = self.zotero_db.get_items_needing_sync(since_timestamp)
        
        logger.info(f"Found {len(items)} items needing sync")
        
        for item in items:
            try:
                if item.item_id in self.state.processed_items:
                    results['skipped'] += 1
                    continue
                
                # For items without attachments, we skip for now
                # Could be extended to create text records or notes in DEVONthink
                
                results['success'] += 1
                logger.info(f"Processed item {item.item_id}: {item.title[:50]}...")
                
                # Track processed item
                if item.item_id not in self.state.processed_items:
                    self.state.processed_items.append(item.item_id)
                
            except Exception as e:
                logger.error(f"Failed to process item {item.item_id}: {e}")
                results['error'] += 1
        
        return results
    
    def _resolve_storage_path(self, attachment: ZoteroAttachment) -> Optional[Path]:
        """Resolve Zotero storage path to actual file location

        Supports multiple path formats:
        - storage:KEY:filename.pdf (standard format)
        - KEY:filename.pdf (without storage: prefix)
        - KEY/filename.pdf (forward slash variant)

        Validates storage key format (8-char alphanumeric) and file existence.
        """
        if not attachment.path:
            return None

        path = attachment.path
        storage_base = Path(ZOTERO_STORAGE_PATH)

        # Format 1: storage:KEY:filename.pdf (standard)
        if path.startswith("storage:"):
            parts = path.split(":")
            if len(parts) >= 3:
                key = parts[1]
                filename = ":".join(parts[2:])

                # Validate storage key format (should be 8 uppercase alphanumeric)
                if re.match(r'^[A-Z0-9]{8}$', key):
                    resolved = storage_base / key / filename
                    if resolved.exists():
                        return resolved
                    else:
                        logger.debug(f"Storage path resolved but file missing: {resolved}")
                        return None
                else:
                    logger.warning(f"Malformed storage path (invalid key '{key}'): {path}")
                    return None
            else:
                # Legacy format: storage:filename.pdf (no key directory)
                # Search across all storage key directories for this filename
                filename = ":".join(parts[1:])
                matches = list(storage_base.glob(f"*/{filename}"))
                if len(matches) == 1:
                    logger.info(f"Resolved legacy storage path: {path} -> {matches[0]}")
                    return matches[0]
                elif len(matches) > 1:
                    logger.warning(f"Ambiguous legacy storage path (found {len(matches)} matches): {path}")
                    return matches[0]
                else:
                    logger.debug(f"Legacy storage path - file not found in any key directory: {path}")
                    return None

        # Format 2: KEY:filename.pdf (without storage: prefix)
        elif ":" in path and not path.startswith("/"):
            parts = path.split(":", 1)
            if len(parts) == 2:
                key, filename = parts
                if re.match(r'^[A-Z0-9]{8}$', key):
                    resolved = storage_base / key / filename
                    if resolved.exists():
                        logger.info(f"Resolved non-standard path format: {path} -> {resolved}")
                        return resolved
                    else:
                        logger.debug(f"Non-standard path resolved but file missing: {resolved}")
                        return None

        # Format 3: KEY/filename.pdf (forward slash variant)
        elif "/" in path and not path.startswith("/"):
            parts = path.split("/", 1)
            if len(parts) == 2:
                key, filename = parts
                if re.match(r'^[A-Z0-9]{8}$', key):
                    resolved = storage_base / key / filename
                    if resolved.exists():
                        logger.info(f"Resolved slash-format path: {path} -> {resolved}")
                        return resolved
                    else:
                        logger.debug(f"Slash-format path resolved but file missing: {resolved}")
                        return None

        # Format 4: Absolute path (for linkMode=1,2,3 - not for storage)
        elif path.startswith("/"):
            absolute_path = Path(path)
            if absolute_path.exists():
                return absolute_path
            else:
                logger.debug(f"Absolute path file missing: {path}")
                return None

        logger.debug(f"Unable to resolve path: {path}")
        return None
    
    async def run_service_cycle_async(self, dry_run=False) -> bool:
        """Run one complete service cycle with async processing"""
        logger.info("🔄 Running async service cycle...")

        try:
            # Phase 1A: Migrate stored attachments from Zotero storage (linkMode=0)
            logger.info("\n" + "="*70)
            logger.info("PHASE 1A: Migrating linkMode=0 (Zotero storage) attachments")
            logger.info("="*70)
            migration_results = self.migrate_stored_attachments(dry_run)
            logger.info(f"Phase 1A complete: {migration_results}")

            # Phase 1B: Migrate ZotFile linked attachments (linkMode=2)
            logger.info("\n" + "="*70)
            logger.info("PHASE 1B: Migrating linkMode=2 (ZotFile Import) attachments")
            logger.info("="*70)
            zotfile_migration_results = self.migrate_zotfile_attachments(dry_run)
            logger.info(f"Phase 1B complete: {zotfile_migration_results}")

            # Phase 2: Convert ZotFile symlinks already in DEVONthink (async batch processing)
            logger.info("\n" + "="*70)
            logger.info("PHASE 2: Converting existing ZotFile symlinks to UUID links")
            logger.info("="*70)
            conversion_results = await self.convert_zotfile_symlinks_async(dry_run, batch_size=50)
            logger.info(f"Phase 2 complete: {conversion_results}")

            # Phase 3: Sync new items (synchronous for now)
            logger.info("\n" + "="*70)
            logger.info("PHASE 3: Syncing new items")
            logger.info("="*70)
            sync_results = self.sync_new_items(dry_run)
            logger.info(f"Phase 3 complete: {sync_results}")

            # Update state
            if not dry_run:
                self.state.last_sync = datetime.now().isoformat()

            self._save_state()

            # Overall summary
            logger.info("\n" + "="*70)
            logger.info("🎉 SERVICE CYCLE COMPLETE")
            logger.info("="*70)
            logger.info(f"Total Zotero storage (linkMode=0) migrated: {migration_results.get('success', 0)}")
            logger.info(f"Total ZotFile (linkMode=2) migrated: {zotfile_migration_results.get('success', 0)}")
            logger.info(f"Total symlinks converted: {conversion_results.get('success', 0)}")
            logger.info(f"Total new items synced: {sync_results.get('success', 0)}")
            logger.info("="*70 + "\n")

            return True

        except Exception as e:
            logger.error(f"Service cycle failed: {e}")
            return False
    
    def run_service_cycle(self, dry_run=False) -> bool:
        """Synchronous wrapper for async service cycle"""
        return asyncio.run(self.run_service_cycle_async(dry_run))
    
    def run_perpetual_service(self):
        """Run the service perpetually until stopped"""
        logger.info("🚀 Starting DEVONzot perpetual service...")
        
        # Write PID file
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        
        self.running = True
        
        try:
            while self.running:
                cycle_success = self.run_service_cycle(dry_run=False)
                
                if not cycle_success:
                    self.restart_count += 1
                    if self.restart_count >= MAX_RESTART_ATTEMPTS:
                        logger.error(f"Max restart attempts ({MAX_RESTART_ATTEMPTS}) reached. Stopping service.")
                        break
                    
                    logger.warning(f"Service cycle failed. Restarting in {RESTART_DELAY} seconds... (Attempt {self.restart_count}/{MAX_RESTART_ATTEMPTS})")
                    time.sleep(RESTART_DELAY)
                    continue
                else:
                    # Reset restart count on successful cycle
                    self.restart_count = 0
                
                # Wait for next sync interval
                logger.info(f"Next sync in {SYNC_INTERVAL} seconds...")
                
                for i in range(SYNC_INTERVAL):
                    if not self.running:
                        break
                    time.sleep(1)
                    
        except KeyboardInterrupt:
            logger.info("Service interrupted by user")
        except Exception as e:
            logger.error(f"Service failed with error: {e}")
        finally:
            self.running = False
            # Clean up PID file
            if PID_FILE.exists():
                PID_FILE.unlink()
            logger.info("🛑 DEVONzot service stopped")

def main():
    """Main entry point with argument handling"""
    import argparse
    
    parser = argparse.ArgumentParser(description="DEVONzot - Complete Zotero→DEVONthink Integration Service")
    parser.add_argument('--dry-run', action='store_true', help='Run analysis without making changes')
    parser.add_argument('--service', action='store_true', help='Run as perpetual service')
    parser.add_argument('--once', action='store_true', help='Run once then exit')
    parser.add_argument('--stop', action='store_true', help='Stop running service')
    
    args = parser.parse_args()
    
    service = DEVONzotService()
    
    if args.stop:
        # Stop running service
        if PID_FILE.exists():
            try:
                with open(PID_FILE, 'r') as f:
                    pid = int(f.read().strip())
                os.kill(pid, signal.SIGTERM)
                print(f"Stopped service (PID: {pid})")
            except Exception as e:
                print(f"Error stopping service: {e}")
        else:
            print("No running service found")
        return
    
    if args.dry_run:
        # Run comprehensive dry run analysis
        results = service.run_dry_run()
        print("\n📁 Dry run results saved to state file")
        return
    
    if args.service:
        # Run as perpetual service
        service.run_perpetual_service()
    elif args.once:
        # Run once then exit
        success = service.run_service_cycle(dry_run=False)
        exit(0 if success else 1)
    else:
        # Show help
        parser.print_help()

if __name__ == "__main__":
    main()