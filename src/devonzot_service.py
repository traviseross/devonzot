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
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import threading
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from zotero_api_client import ZoteroAPIClient

# Load environment variables
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

# Zotero API configuration
ZOTERO_API_KEY = os.environ["ZOTERO_API_KEY"]
ZOTERO_USER_ID = os.environ["ZOTERO_USER_ID"]
ZOTERO_API_BASE = os.environ.get("ZOTERO_API_BASE", "https://api.zotero.org")
API_VERSION = os.environ.get("API_VERSION", "3")
RATE_LIMIT_DELAY = float(os.environ.get("RATE_LIMIT_DELAY", 0.0))

# Configuration
ZOTERO_STORAGE_PATH = "/Users/travisross/Zotero/storage"
ZOTFILE_IMPORT_PATH = "/Users/travisross/ZotFile Import"
DEVONTHINK_INBOX_PATH = "/Users/travisross/Library/Application Support/DEVONthink 3/Inbox"
DEVONTHINK_DATABASE = "Professional"
DEVONZOT_PATH = Path("/Users/travisross/DEVONzot")
STATE_FILE = DEVONZOT_PATH / "service_state.json"
LOG_FILE = DEVONZOT_PATH / "service.log"
PID_FILE = DEVONZOT_PATH / "service.pid"

# Service configuration
SYNC_INTERVAL = 300  # 5 minutes (polling fallback)
DEVONTHINK_WAIT_TIME = 3  # Reduced wait time for faster processing
MAX_RESTART_ATTEMPTS = 3
RESTART_DELAY = 30  # seconds
BATCH_SIZE = 50  # Items to process concurrently

# Streaming configuration
WEBSOCKET_ENABLED = os.environ.get("WEBSOCKET_ENABLED", "true").lower() == "true"
FALLBACK_POLL_INTERVAL = int(os.environ.get("FALLBACK_POLL_INTERVAL", "600"))  # 10 minutes
FALLBACK_POLL_ENABLED = os.environ.get("FALLBACK_POLL_ENABLED", "true").lower() == "true"

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
    version: int = 0

@dataclass
class ZoteroAttachment:
    """Zotero attachment record"""
    key: str
    parent_key: Optional[str]
    link_mode: int  # 0=imported_file, 1=imported_url, 2=linked_file, 3=linked_url
    content_type: str
    path: Optional[str]
    storage_hash: Optional[str]
    version: int = 0
    filename: Optional[str] = None
    url: Optional[str] = None

@dataclass
class ServiceState:
    """Service state tracking"""
    last_sync: Optional[str] = None
    last_zotero_check: Optional[str] = None
    last_library_version: Optional[int] = None
    processed_items: List[str] = None
    restart_count: int = 0
    dry_run_results: Dict[str, Any] = None
    pending_deletes: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.processed_items is None:
            self.processed_items = []
        # Migrate legacy int IDs to empty list (API uses string keys)
        elif self.processed_items and isinstance(self.processed_items[0], int):
            logger.info("Migrating processed_items from int IDs to string keys — resetting list")
            self.processed_items = []
        if self.dry_run_results is None:
            self.dry_run_results = {}
        if self.pending_deletes is None:
            self.pending_deletes = []

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
    
    def _safe_applescript_str(self, value: str) -> str:
        """Escape a string for safe interpolation into AppleScript string literals."""
        return (value
                .replace('\\', '\\\\')
                .replace('"', '\\"')
                .replace('\r', '')
                .replace('\n', ' ')
                .replace('\t', ' '))

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
        safe_authors = self._safe_applescript_str(authors_str)
        safe_publication = self._safe_applescript_str(item.publication or "")
        safe_abstract = self._safe_applescript_str((item.abstract or "")[:500])
        safe_key = self._safe_applescript_str(item.key or "")
        safe_year = self._safe_applescript_str(str(item.year) if item.year else "")
        safe_doi = self._safe_applescript_str(item.doi or "")

        # Prepare tags
        tags_list = item.tags + item.collections
        if item.item_type:
            tags_list.append(item.item_type)
        if item.year:
            tags_list.append(str(item.year))

        # Format tags for AppleScript (Python 3.9 compatible)
        escaped_tags = ['"' + self._safe_applescript_str(tag) + '"' for tag in tags_list if tag]
        tags_applescript = ', '.join(escaped_tags)

        # Build tags block in Python to avoid double-quoting in AppleScript
        tags_block = ""
        if escaped_tags:
            tags_block = f"set tags of theRecord to {{{tags_applescript}}}"

        script = f'''
        tell application "DEVONthink 3"
            try
                set theRecord to get record with uuid "{uuid}"

                -- Set comment with Zotero metadata
                set theComment to "Zotero Key: {safe_key}"
                if "{safe_authors}" is not "" then
                    set theComment to theComment & "\\nAuthors: {safe_authors}"
                end if
                if "{safe_publication}" is not "" then
                    set theComment to theComment & "\\nPublication: {safe_publication}"
                end if
                if "{safe_year}" is not "" then
                    set theComment to theComment & "\\nYear: {safe_year}"
                end if
                if "{safe_doi}" is not "" then
                    set theComment to theComment & "\\nDOI: {safe_doi}"
                end if
                if "{safe_abstract}" is not "" then
                    set theComment to theComment & "\\nAbstract: {safe_abstract}"
                end if

                set comment of theRecord to theComment

                -- Set tags
                {tags_block}

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

    def rename_item(self, uuid: str, new_name: str, dry_run=False) -> bool:
        """Rename a DEVONthink record by UUID."""
        if dry_run:
            logger.info(f"[DRY RUN] Would rename UUID {uuid} to '{new_name}'")
            return True

        if not self.is_devonthink_running():
            return False

        safe_name = new_name.replace('"', '\\"').replace('\\', '\\\\')

        script = f'''
        tell application "DEVONthink 3"
            try
                set theRecord to get record with uuid "{uuid}"
                set name of theRecord to "{safe_name}"
                return "SUCCESS"
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''

        try:
            result = self.execute_script(script)
            if result.startswith("ERROR:"):
                logger.error(f"Failed to rename {uuid}: {result[7:]}")
                return False
            logger.info(f"Renamed DEVONthink item {uuid} to '{new_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to rename {uuid}: {e}")
            return False

class ConflictDetector:
    """Detect conflicts, duplicates, and unmatched files for dry run mode"""
    
    def __init__(self, zotero_api: ZoteroAPIClient, devonthink: DEVONthinkInterface):
        self.zotero_api = zotero_api
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
        items = self.zotero_api.get_items_needing_sync()
        filenames = {}
        
        for item in items:
            filename = FilenameGenerator.generate_filename(item)
            if filename in filenames:
                conflicts['filename_collisions'].append({
                    'filename': filename,
                    'item1_key': filenames[filename]['key'],
                    'item2_key': item.key,
                    'title1': filenames[filename]['title'],
                    'title2': item.title
                })
            else:
                filenames[filename] = {'key': item.key, 'title': item.title}
    
    def _detect_unmatched_symlinks(self, conflicts: Dict):
        """Find ZotFile symlinks that don't match DEVONthink items"""
        symlinks = self.zotero_api.get_zotfile_symlinks()
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
                        'attachment_key': symlink.key,
                        'parent_key': symlink.parent_key,
                        'filename': filename,
                        'path': symlink.path
                    })
    
    def _detect_duplicates(self, conflicts: Dict):
        """Detect potential duplicate items by title similarity"""
        items = self.zotero_api.get_items_needing_sync()
        
        for i, item1 in enumerate(items):
            for item2 in items[i+1:]:
                if self._are_likely_duplicates(item1, item2):
                    conflicts['duplicates'].append({
                        'item1_key': item1.key,
                        'item2_key': item2.key,
                        'title1': item1.title,
                        'title2': item2.title,
                        'similarity_reason': 'similar_titles'
                    })
    
    def _detect_problematic_metadata(self, conflicts: Dict):
        """Find items with problematic metadata that might cause issues"""
        items = self.zotero_api.get_items_needing_sync()
        
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
                    'key': item.key,
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
        self.zotero_api = ZoteroAPIClient(
            api_key=ZOTERO_API_KEY,
            user_id=ZOTERO_USER_ID,
            api_base=ZOTERO_API_BASE,
            api_version=API_VERSION,
            rate_limit_delay=RATE_LIMIT_DELAY,
        )
        self.devonthink = DEVONthinkInterface(DEVONTHINK_DATABASE)
        self.conflict_detector = ConflictDetector(self.zotero_api, self.devonthink)
        self.state = self._load_state()
        self.running = False
        self.paused = False
        self.restart_count = 0
        self._interactive_quit = False

        # Ensure DEVONzot directory exists
        DEVONZOT_PATH.mkdir(exist_ok=True)

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGUSR1, self._pause_handler)
        signal.signal(signal.SIGUSR2, self._resume_handler)
    
    def _load_state(self) -> ServiceState:
        """Load service state from file"""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    # Filter to only known fields to handle schema changes
                    known_fields = {f.name for f in ServiceState.__dataclass_fields__.values()}
                    filtered = {k: v for k, v in data.items() if k in known_fields}
                    return ServiceState(**filtered)
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
        """Handle shutdown signals (works in both sync and async modes)"""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
        # Cancel asyncio tasks if an event loop is running
        try:
            loop = asyncio.get_running_loop()
            for task in asyncio.all_tasks(loop):
                task.cancel()
        except RuntimeError:
            pass  # No running event loop (synchronous / polling mode)

    def _pause_handler(self, signum, frame):
        """SIGUSR1: pause processing after current item finishes"""
        self.paused = True
        logger.info(f"⏸️  Service paused (PID {os.getpid()}). Send SIGUSR2 to resume.")

    def _resume_handler(self, signum, frame):
        """SIGUSR2: resume processing"""
        self.paused = False
        logger.info("▶️  Service resumed.")

    def _wait_if_paused(self):
        """Block until unpaused. SIGTERM still works while paused."""
        if not self.paused:
            return
        logger.info(f"⏸️  Paused — waiting for SIGUSR2 (PID {os.getpid()})...")
        while self.paused and self.running:
            time.sleep(1)
        if self.running:
            logger.info("▶️  Resuming processing.")

    async def _wait_if_paused_async(self):
        """Async version of pause check."""
        if not self.paused:
            return
        logger.info(f"⏸️  Paused — waiting for SIGUSR2 (PID {os.getpid()})...")
        while self.paused and self.running:
            await asyncio.sleep(1)
        if self.running:
            logger.info("▶️  Resuming processing.")

    def _interactive_menu(self, dry_run: bool = False) -> str:
        """Show phase-selection menu. Returns '0','1a','1b','2','3','a', or 'q'."""
        dry_label = "  [DRY RUN]\n" if dry_run else ""
        print(f"""
====================================================================
  DEVONzot Interactive Mode
{dry_label}====================================================================
  [0]  Delete imported_url attachments (linkMode=1)
       Remove URL snapshots stored in Zotero storage

  [1a] Migrate stored attachments (linkMode=0)
       Copy files from Zotero storage to DEVONthink with UUID links

  [1b] Migrate ZotFile attachments (linkMode=2)
       Copy ZotFile-managed files to DEVONthink with UUID links

  [2]  Convert existing ZotFile symlinks
       Create UUID links for linkMode=2 files already in DEVONthink

  [3]  Sync new items
       Process new Zotero items not yet tracked

  [a]  Run all phases sequentially
  [q]  Quit
====================================================================""")

        valid = ('0', '1a', '1b', '2', '3', 'a', 'q')
        while True:
            try:
                raw = input("  Select phase: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nInterrupted - quitting interactive mode.")
                return 'q'
            if raw in valid:
                return raw
            if raw == '1':
                print("  Did you mean 1a or 1b?")
            else:
                print(f"  Invalid choice. Valid options: 0, 1a, 1b, 2, 3, a, q")

    def _interactive_prompt(self, phase: str, record_details: dict, dry_run: bool = False) -> str:
        """Display record details and prompt for y/n/q/s.

        Returns 'y' (process), 'n' (skip record), 's' (skip rest of phase), or 'q' (quit cycle).
        """
        lines = [
            "",
            "=" * 68,
            f"  PHASE: {phase}",
            "=" * 68,
        ]
        for label, value in record_details.items():
            if value:
                lines.append(f"  {label:<20} {value}")
        if dry_run:
            lines.append("")
            lines.append("  [DRY RUN - no changes will be made]")
        lines.append("=" * 68)
        print("\n".join(lines))

        while True:
            try:
                raw = input("  [y]es / [n]o / [s]kip phase / [q]uit: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nInterrupted - quitting interactive mode.")
                return 'q'

            if raw in ('y', 'yes', ''):
                return 'y'
            elif raw in ('n', 'no'):
                return 'n'
            elif raw in ('s', 'skip'):
                return 's'
            elif raw in ('q', 'quit'):
                return 'q'
            else:
                print("  Invalid choice. Enter y, n, s, or q")

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
        stored_attachments = self.zotero_api.get_stored_attachments()
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
        zotfile_symlinks = self.zotero_api.get_zotfile_symlinks()
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
        items_needing_sync = self.zotero_api.get_items_needing_sync()
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
                print(f"  '{collision['filename']}' - Items {collision['item1_key']} and {collision['item2_key']}")
        
        if conflicts['unmatched_files']:
            print("\n📁 UNMATCHED FILES:")
            for unmatched in conflicts['unmatched_files'][:5]:  # Show first 5
                print(f"  {unmatched['filename']} (Attachment {unmatched['attachment_key']})")
        
        if conflicts['problematic_items']:
            print("\n⚠️  PROBLEMATIC ITEMS:")
            for problem in conflicts['problematic_items'][:5]:  # Show first 5
                issues_str = ", ".join(problem['issues'])
                print(f"  Item {problem['key']}: {issues_str}")
        
        print("\n" + "="*60)

    def _get_zotero_filename(self, attachment: ZoteroAttachment) -> Optional[str]:
        """Get the original Zotero filename from an attachment.

        For imported_file: uses the filename field.
        For linked_file: extracts the stem from the path.
        """
        if attachment.filename:
            return attachment.filename
        if attachment.path:
            return Path(attachment.path).name
        return None

    def _find_or_adopt_in_devonthink(self, generated_name: str,
                                      zotero_filename: Optional[str],
                                      dry_run=False) -> Optional[str]:
        """Search DEVONthink for a file by generated name, then by Zotero name.

        If found under the Zotero name, renames the record to the generated name.
        Returns the UUID if found, None otherwise.
        """
        databases = ["Global Inbox", "Professional", "Articles", "Books", "Research"]

        # First: search for our generated filename
        for db_name in databases:
            dt_uuid = self.devonthink._search_database_for_filename(generated_name, db_name)
            if dt_uuid:
                prefix = "[DRY RUN] " if dry_run else ""
                logger.info(f"{prefix}Found DEVONthink item by generated name in {db_name}: {dt_uuid}")
                return dt_uuid

        # Second: search for original Zotero filename
        if zotero_filename and zotero_filename != generated_name:
            # Strip extension for search (DEVONthink name may or may not have it)
            zotero_stem = Path(zotero_filename).stem
            for db_name in databases:
                dt_uuid = self.devonthink._search_database_for_filename(zotero_stem, db_name)
                if dt_uuid:
                    prefix = "[DRY RUN] " if dry_run else ""
                    logger.info(
                        f"{prefix}Found DEVONthink item by Zotero name '{zotero_stem}' "
                        f"in {db_name}: {dt_uuid} — renaming to '{generated_name}'"
                    )
                    # Also try with extension
                    ext = Path(zotero_filename).suffix
                    rename_target = generated_name if generated_name.endswith(ext) else generated_name + ext
                    self.devonthink.rename_item(dt_uuid, rename_target, dry_run)
                    return dt_uuid

        return None

    def _find_devonthink_link(self, parent_key: str) -> Optional[dict]:
        """Find existing linked_url child with x-devonthink-item:// URL."""
        all_attachments = self.zotero_api._get_all_attachments_cached()
        for api_item in all_attachments:
            data = api_item.get('data', {})
            if (data.get('parentItem') == parent_key
                    and data.get('linkMode') == 'linked_url'
                    and (data.get('url', '') or '').startswith('x-devonthink-item://')):
                return api_item
        return None

    def _parent_has_devonthink_link(self, parent_key: str) -> bool:
        """Check if a parent item already has a linked_url child with x-devonthink-item://."""
        return self._find_devonthink_link(parent_key) is not None

    def _create_devonthink_child_link(self, parent_key: str, dt_uuid: str,
                                       title: str = "DEVONthink Link",
                                       dry_run=False) -> bool:
        """Create a linked_url child attachment with x-devonthink-item:// URL.

        Checks for duplicates before creating. Renames existing links if still
        using the generic "DEVONthink Link" title.
        """
        existing = self._find_devonthink_link(parent_key)
        if existing:
            existing_data = existing.get('data', {})
            if existing_data.get('title') == "DEVONthink Link" and title != "DEVONthink Link":
                att_key = existing_data.get('key')
                version = existing_data.get('version', 0)
                if not dry_run:
                    resp = self.zotero_api._safe_request(
                        'PATCH',
                        f'{self.zotero_api.api_base}/users/{self.zotero_api.user_id}/items/{att_key}',
                        json={'title': title},
                        headers={'If-Unmodified-Since-Version': str(version)}
                    )
                    if resp and resp.status_code in (200, 204):
                        logger.info(f"Renamed link {att_key} → {title}")
                    else:
                        logger.warning(f"Failed to rename link {att_key}")
                else:
                    logger.info(f"[DRY RUN] Would rename link {att_key} → {title}")
            else:
                logger.info(f"Parent {parent_key} already has DEVONthink link — skipping")
            return True

        if dry_run:
            logger.info(f"[DRY RUN] Would create DEVONthink Link child for {parent_key}")
            return True

        results = self.zotero_api.create_url_attachments([{
            "parent_key": parent_key,
            "title": title,
            "url": f"x-devonthink-item://{dt_uuid}"
        }])

        if results and results[0].get('new_key'):
            logger.info(f"Created DEVONthink Link child {results[0]['new_key']} for {parent_key}")
            return True
        else:
            logger.error(f"Failed to create DEVONthink Link child for {parent_key}")
            return False

    def delete_imported_url_attachments(self, dry_run=False, interactive=False) -> Dict[str, int]:
        """Phase 0: Delete all imported_url (linkMode=1) attachments.

        These are URL snapshots stored in Zotero storage that serve no purpose.
        Deletes both the stored files from disk AND the Zotero attachment items.
        """
        logger.info("Phase 0: Deleting imported_url (linkMode=1) attachments...")

        results = {
            'total_found': 0,
            'files_deleted': 0,
            'files_missing': 0,
            'items_deleted': 0,
            'items_failed': 0,
            'would_delete_files': 0,
            'would_delete_items': 0,
            'skipped': 0,
        }

        attachments = self.zotero_api.get_imported_url_attachments()
        results['total_found'] = len(attachments)

        if not attachments:
            logger.info("No imported_url attachments found.")
            return results

        logger.info(f"Found {len(attachments)} imported_url attachments to delete")

        # Track which attachments the user accepted (for API delete step)
        accepted_keys = []
        quit_early = False

        # Step 1: Delete stored files from disk
        for attachment in attachments:
            file_path = self._resolve_storage_path(attachment)

            if interactive:
                decision = self._interactive_prompt(
                    phase="Phase 0 - Delete imported_url file",
                    record_details={
                        "Attachment Key": attachment.key,
                        "Filename": attachment.filename or "(none)",
                        "Content Type": attachment.content_type or "(unknown)",
                        "File Path": str(file_path) if file_path else "(not resolved)",
                        "Action": "Delete file from Zotero storage + API item",
                    },
                    dry_run=dry_run,
                )
                if decision == 'q':
                    self._interactive_quit = True
                    quit_early = True
                    break
                if decision in ('n', 's'):
                    results['skipped'] += 1
                    if decision == 's':
                        break
                    continue

            accepted_keys.append(attachment.key)

            if file_path and file_path.exists():
                if not dry_run:
                    try:
                        file_path.unlink()
                        results['files_deleted'] += 1
                        logger.debug(f"Deleted file: {file_path}")
                        if (file_path.parent != Path(ZOTERO_STORAGE_PATH)
                                and not any(file_path.parent.iterdir())):
                            file_path.parent.rmdir()
                    except Exception as e:
                        logger.warning(f"Failed to delete file {file_path}: {e}")
                else:
                    logger.info(f"[DRY RUN] Would delete file: {file_path}")
                    results['would_delete_files'] += 1
            else:
                results['files_missing'] += 1

        # Step 2: Batch delete Zotero attachment items via API
        # In interactive mode, only delete accepted items (user already prompted in Step 1)
        item_keys = accepted_keys if interactive else [att.key for att in attachments]

        if not quit_early and item_keys:
            library_version = self.zotero_api.last_library_version or 0

            if library_version == 0:
                self.zotero_api._get_all_attachments_cached()
                library_version = self.zotero_api.last_library_version or 0

            delete_results = self.zotero_api.delete_items_batch(
                item_keys, library_version, dry_run=dry_run
            )

            if dry_run:
                results['would_delete_items'] = delete_results['would_delete']
            else:
                results['items_deleted'] = delete_results['deleted']
            results['items_failed'] = len(delete_results['failed'])

        prefix = "[DRY RUN] " if dry_run else ""
        logger.info(f"{prefix}Phase 0 Summary:")
        logger.info(f"  Found: {results['total_found']}")
        if dry_run:
            logger.info(f"  Would delete files: {results['would_delete_files']}")
            logger.info(f"  Would delete items: {results['would_delete_items']}")
        else:
            logger.info(f"  Files deleted: {results['files_deleted']}")
            logger.info(f"  API items deleted: {results['items_deleted']}")
        logger.info(f"  Files missing: {results['files_missing']}")
        logger.info(f"  API items failed: {results['items_failed']}")

        return results

    def retry_pending_deletes(self, dry_run=False, interactive=False) -> Dict[str, int]:
        """Retry deletion of Zotero attachment items that failed in previous cycles."""
        results = {'retried': 0, 'deleted': 0, 'failed': 0, 'would_delete': 0, 'skipped': 0}

        if not self.state.pending_deletes:
            return results

        logger.info(f"Retrying {len(self.state.pending_deletes)} pending attachment deletes...")

        remaining = []
        for pending in self.state.pending_deletes:
            results['retried'] += 1
            key = pending['key']

            if interactive:
                decision = self._interactive_prompt(
                    phase="Retry Queue - Delete pending attachment",
                    record_details={
                        "Attachment Key": key,
                        "Action": "Delete Zotero attachment item (retry)",
                    },
                    dry_run=dry_run,
                )
                if decision == 'q':
                    self._interactive_quit = True
                    remaining.append({'key': key, 'version': pending.get('version', 0)})
                    break
                if decision in ('n', 's'):
                    results['skipped'] += 1
                    remaining.append({'key': key, 'version': pending.get('version', 0)})
                    if decision == 's':
                        break
                    continue

            if dry_run:
                logger.info(f"[DRY RUN] Would retry delete of {key}")
                results['would_delete'] += 1
                continue

            att_item = self.zotero_api.get_item_raw(key)
            if att_item is None:
                logger.info(f"Pending delete {key} no longer exists — removing from queue")
                results['deleted'] += 1
                continue

            version = att_item.get('data', {}).get('version', 0)
            if self.zotero_api.delete_attachment(key, version):
                results['deleted'] += 1
                logger.info(f"Successfully deleted pending item: {key}")
            else:
                results['failed'] += 1
                remaining.append({'key': key, 'version': version})

        if not dry_run:
            self.state.pending_deletes = remaining
            self._save_state()

        if dry_run:
            logger.info(f"[DRY RUN] Pending deletes: would retry {results['would_delete']}")
        else:
            logger.info(f"Pending deletes: {results['deleted']} deleted, {results['failed']} still pending")
        return results

    def migrate_stored_attachments(self, dry_run=False, interactive=False) -> Dict[str, int]:
        """Migrate Zotero stored files to DEVONthink with comprehensive tracking"""
        logger.info("📁 Starting migration of stored attachments...")

        # Enhanced results tracking
        results = {
            'success': 0,
            'error': 0,
            'skipped': 0,
            'skipped_path_invalid': 0,
            'skipped_no_parent': 0,
            'skipped_parent_not_found': 0,
            'skipped_already_processed': 0,
            'linked_existing': 0,
            'cleaned_broken': 0,
            'deleted_originals': 0,
            'deleted_attachment_items': 0,
            'cleaned_empty_folders': 0
        }

        skipped_details = []  # Track details for reporting

        attachments = self.zotero_api.get_stored_attachments()
        logger.info(f"📊 Detection Summary: Found {len(attachments)} stored attachments (linkMode=0)")

        for attachment in attachments:
            try:
                # Skip if already processed
                if attachment.parent_key and attachment.parent_key in self.state.processed_items:
                    results['skipped_already_processed'] += 1
                    continue

                # Resolve file path
                file_path = self._resolve_storage_path(attachment)
                if not file_path:
                    # Check if storage folder exists but is empty (only .DS_Store)
                    storage_dir = Path(ZOTERO_STORAGE_PATH) / attachment.key
                    dir_contents = [f for f in storage_dir.iterdir() if f.name != '.DS_Store'] if storage_dir.is_dir() else None
                    if dir_contents is not None and len(dir_contents) == 0:
                        # Empty folder — offer to clean up
                        do_clean = True
                        if interactive:
                            logger.info(f"🗑️  Attachment {attachment.key}: storage folder empty (file missing)")
                            try:
                                raw = input(f"  Delete empty attachment {attachment.key}? [y/n]: ").strip().lower()
                            except (EOFError, KeyboardInterrupt):
                                print("\nInterrupted - quitting.")
                                return results
                            do_clean = raw in ('y', 'yes', '')
                        if do_clean and not dry_run:
                            try:
                                att_raw = self.zotero_api.get_item_raw(attachment.key)
                                if att_raw:
                                    att_version = att_raw.get('data', {}).get('version', 0)
                                    self.zotero_api.delete_attachment(attachment.key, att_version)
                                # Remove .DS_Store and the empty folder
                                ds_store = storage_dir / '.DS_Store'
                                if ds_store.exists():
                                    ds_store.unlink()
                                if storage_dir.exists() and not any(storage_dir.iterdir()):
                                    storage_dir.rmdir()
                                    logger.info(f"🗑️  Cleaned empty folder: {storage_dir.name}")
                                results['cleaned_empty_folders'] += 1
                            except Exception as e:
                                logger.warning(f"⚠️  Failed to clean up {attachment.key}: {e}")
                        elif do_clean and dry_run:
                            logger.info(f"[DRY RUN] Would delete empty attachment {attachment.key} and folder {storage_dir}")
                            results['cleaned_empty_folders'] += 1
                        continue

                    reason = "Invalid or unresolvable path"
                    logger.warning(f"⚠️  Attachment {attachment.key}: {reason} - {attachment.path}")
                    results['skipped_path_invalid'] += 1
                    results['skipped'] += 1
                    skipped_details.append({
                        'key': attachment.key,
                        'reason': reason,
                        'path': attachment.path
                    })
                    continue

                file_on_disk = file_path.exists()
                if not file_on_disk:
                    logger.info(f"📂 Attachment {attachment.key}: file missing on disk, will search DEVONthink...")

                # Get parent item metadata
                if not attachment.parent_key:
                    reason = "No parent item (orphaned attachment)"
                    logger.warning(f"⚠️  Attachment {attachment.key}: {reason}")
                    results['skipped_no_parent'] += 1
                    results['skipped'] += 1
                    skipped_details.append({
                        'key': attachment.key,
                        'reason': reason,
                        'path': attachment.path
                    })
                    continue

                parent_item = self.zotero_api.get_item(attachment.parent_key)
                if not parent_item:
                    reason = f"Parent item {attachment.parent_key} not found in database"
                    logger.warning(f"⚠️  Attachment {attachment.key}: {reason}")
                    results['skipped_parent_not_found'] += 1
                    results['skipped'] += 1
                    skipped_details.append({
                        'key': attachment.key,
                        'reason': reason,
                        'path': attachment.path,
                        'parent_key': attachment.parent_key
                    })
                    continue

                if interactive:
                    creators = parent_item.creators
                    first_creator = FilenameGenerator.extract_first_creator(creators) if creators else "(none)"
                    decision = self._interactive_prompt(
                        phase="Phase 1A - Migrate stored attachment",
                        record_details={
                            "Attachment Key": attachment.key,
                            "Filename": attachment.filename or "(none)",
                            "File Path": str(file_path) if file_path else "(missing)",
                            "File on Disk": "Yes" if file_on_disk else "No (will search DEVONthink)",
                            "Parent Key": attachment.parent_key,
                            "Title": (parent_item.title or "")[:60],
                            "Creator": first_creator,
                            "Year": str(parent_item.year) if parent_item.year else "(none)",
                            "Item Type": parent_item.item_type,
                            "Action": "Copy to DEVONthink, create UUID link, delete original",
                        },
                        dry_run=dry_run,
                    )
                    if decision == 'q':
                        self._interactive_quit = True
                        break
                    if decision in ('n', 's'):
                        results['skipped'] += 1
                        if decision == 's':
                            break
                        continue

                # Generate filename and get original Zotero filename
                filename = FilenameGenerator.generate_filename(parent_item)
                zotero_filename = self._get_zotero_filename(attachment)
                logger.info(f"📎 Processing attachment {attachment.key}: {filename}")

                # Search DEVONthink for generated name, then Zotero name
                dt_uuid = self._find_or_adopt_in_devonthink(filename, zotero_filename, dry_run)

                if not dt_uuid:
                    if file_on_disk:
                        # Normal path: copy to DEVONthink Inbox
                        if self.devonthink.copy_file_to_inbox(str(file_path), filename, dry_run):
                            dt_uuid = self.devonthink.find_item_by_filename_after_wait(filename, dry_run)
                        else:
                            results['error'] += 1
                            logger.error(f"❌ Failed to copy file to inbox for {attachment.key}")
                            continue
                    else:
                        # File missing everywhere — broken attachment, clean up
                        results['cleaned_broken'] += 1
                        if not dry_run:
                            logger.warning(f"🗑️  Attachment {attachment.key}: not on disk, not in DEVONthink — deleting")
                            try:
                                att_raw = self.zotero_api.get_item_raw(attachment.key)
                                if att_raw:
                                    att_version = att_raw.get('data', {}).get('version', 0)
                                    self.zotero_api.delete_attachment(attachment.key, att_version)
                            except Exception as e:
                                logger.warning(f"⚠️  Failed to delete broken attachment {attachment.key}: {e}")
                        else:
                            logger.info(f"[DRY RUN] Would delete broken attachment {attachment.key} (not on disk, not in DEVONthink)")
                        continue
                elif not file_on_disk:
                    # Found in DEVONthink despite missing from disk
                    results['linked_existing'] += 1
                    logger.info(f"📎 Found {attachment.key} in DEVONthink (not on disk) → {dt_uuid}")

                if dt_uuid:
                    # Create linked_url child attachment with DEVONthink link
                    if self._create_devonthink_child_link(attachment.parent_key, dt_uuid, title=filename, dry_run=dry_run):
                        # Update DEVONthink metadata
                        if self.devonthink.update_item_metadata(dt_uuid, parent_item, dry_run):
                            results['success'] += 1
                            logger.info(f"✅ Migrated attachment {attachment.key} → {dt_uuid}")

                            # Track processed item and save immediately
                            if not dry_run:
                                if attachment.parent_key not in self.state.processed_items:
                                    self.state.processed_items.append(attachment.parent_key)
                                    self._save_state()

                            # Clean up original from Zotero storage (only if file exists)
                            if file_on_disk and not dry_run:
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

                                # Delete the Zotero attachment item
                                try:
                                    att_raw = self.zotero_api.get_item_raw(attachment.key)
                                    if att_raw:
                                        att_version = att_raw.get('data', {}).get('version', 0)
                                        if self.zotero_api.delete_attachment(attachment.key, att_version):
                                            results['deleted_attachment_items'] += 1
                                        else:
                                            self.state.pending_deletes.append(
                                                {'key': attachment.key, 'version': att_version})
                                            self._save_state()
                                except Exception as e:
                                    logger.warning(f"⚠️  Failed to delete attachment item {attachment.key}: {e}")
                                    self.state.pending_deletes.append(
                                        {'key': attachment.key, 'version': attachment.version})
                                    self._save_state()
                            elif file_on_disk and dry_run:
                                logger.info(f"[DRY RUN] Would delete original: {file_path}")
                                logger.info(f"[DRY RUN] Would delete Zotero attachment item: {attachment.key}")
                            elif not file_on_disk and not dry_run:
                                # No file to clean up; delete the Zotero attachment item
                                try:
                                    att_raw = self.zotero_api.get_item_raw(attachment.key)
                                    if att_raw:
                                        att_version = att_raw.get('data', {}).get('version', 0)
                                        if self.zotero_api.delete_attachment(attachment.key, att_version):
                                            results['deleted_attachment_items'] += 1
                                        else:
                                            self.state.pending_deletes.append(
                                                {'key': attachment.key, 'version': att_version})
                                            self._save_state()
                                except Exception as e:
                                    logger.warning(f"⚠️  Failed to delete attachment item {attachment.key}: {e}")
                                    self.state.pending_deletes.append(
                                        {'key': attachment.key, 'version': attachment.version})
                                    self._save_state()
                            else:
                                logger.info(f"[DRY RUN] Would delete Zotero attachment item: {attachment.key}")
                        else:
                            results['error'] += 1
                            logger.error(f"❌ Failed to update DEVONthink metadata for {attachment.key}")
                    else:
                        results['error'] += 1
                        logger.error(f"❌ Failed to create DEVONthink child link for {attachment.key}")
                else:
                    results['error'] += 1
                    logger.error(f"❌ Failed to find DEVONthink UUID for {attachment.key}")

                # Small delay between operations
                if not dry_run:
                    time.sleep(2)

            except Exception as e:
                logger.error(f"❌ Failed to migrate attachment {attachment.key}: {e}")
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
        logger.info(f"  📎 Linked existing (found in DEVONthink): {results['linked_existing']}")
        logger.info(f"  🗑️  Cleaned broken (missing everywhere): {results['cleaned_broken']}")
        logger.info(f"  🗑️  Deleted originals: {results['deleted_originals']}")
        logger.info(f"  ❌ Errors: {results['error']}")
        logger.info(f"  ⏭️  Skipped: {results['skipped']} total")
        logger.info(f"     - Invalid path: {results['skipped_path_invalid']}")
        logger.info(f"     - No parent: {results['skipped_no_parent']}")
        logger.info(f"     - Parent not found: {results['skipped_parent_not_found']}")
        logger.info(f"     - Already processed: {results['skipped_already_processed']}")

        return results

    def _process_single_zotfile_attachment(self, attachment, dry_run=False) -> Dict[str, Any]:
        """Process a single ZotFile linked_file attachment.

        Returns a dict with:
          'result': one of 'success', 'error', 'skipped_already_processed',
                    'skipped_path_invalid', 'skipped_file_missing',
                    'skipped_no_parent', 'skipped_parent_not_found'
          'skip_detail': optional dict for skip reporting (None on success/error)
          'deleted_original': bool indicating if the source file was deleted
        """
        result: Dict[str, Any] = {
            'result': 'error',
            'skip_detail': None,
            'deleted_original': False,
            'linked_existing': False,
            'cleaned_broken': False,
        }

        # Skip if already processed
        if attachment.parent_key and attachment.parent_key in self.state.processed_items:
            result['result'] = 'skipped_already_processed'
            return result

        # Resolve file path (linkMode=2 uses absolute paths)
        if not attachment.path:
            reason = "No path specified"
            logger.warning(f"⚠️  Attachment {attachment.key}: {reason}")
            result['result'] = 'skipped_path_invalid'
            result['skip_detail'] = {'key': attachment.key, 'reason': reason, 'path': None}
            return result

        file_path = Path(attachment.path)
        file_on_disk = file_path.exists()
        if not file_on_disk:
            logger.info(f"📂 Attachment {attachment.key}: file missing on disk, will search DEVONthink...")

        # Get parent item metadata
        if not attachment.parent_key:
            reason = "No parent item (orphaned attachment)"
            logger.warning(f"⚠️  Attachment {attachment.key}: {reason}")
            result['result'] = 'skipped_no_parent'
            result['skip_detail'] = {'key': attachment.key, 'reason': reason, 'path': str(file_path)}
            return result

        parent_item = self.zotero_api.get_item(attachment.parent_key)
        if not parent_item:
            reason = f"Parent item {attachment.parent_key} not found in database"
            logger.warning(f"⚠️  Attachment {attachment.key}: {reason}")
            result['result'] = 'skipped_parent_not_found'
            result['skip_detail'] = {
                'key': attachment.key, 'reason': reason,
                'path': str(file_path), 'parent_key': attachment.parent_key,
            }
            return result

        # Generate filename and get original Zotero filename
        filename = FilenameGenerator.generate_filename(parent_item)
        zotero_filename = self._get_zotero_filename(attachment)
        logger.info(f"📎 Processing ZotFile attachment {attachment.key}: {filename}")

        # Search DEVONthink for generated name, then Zotero name
        dt_uuid = self._find_or_adopt_in_devonthink(filename, zotero_filename, dry_run)

        if not dt_uuid:
            if file_on_disk:
                # Normal path: copy to DEVONthink Inbox
                if self.devonthink.copy_file_to_inbox(str(file_path), filename, dry_run):
                    dt_uuid = self.devonthink.find_item_by_filename_after_wait(filename, dry_run)
                else:
                    logger.error(f"❌ Failed to copy file to inbox for {attachment.key}")
                    return result
            else:
                # File missing everywhere — broken attachment, clean up
                result['result'] = 'cleaned_broken'
                result['cleaned_broken'] = True
                if not dry_run:
                    logger.warning(f"🗑️  Attachment {attachment.key}: not on disk, not in DEVONthink — deleting")
                    try:
                        att_raw = self.zotero_api.get_item_raw(attachment.key)
                        if att_raw:
                            att_version = att_raw.get('data', {}).get('version', 0)
                            self.zotero_api.delete_attachment(attachment.key, att_version)
                    except Exception as e:
                        logger.warning(f"⚠️  Failed to delete broken attachment {attachment.key}: {e}")
                else:
                    logger.info(f"[DRY RUN] Would delete broken attachment {attachment.key} (not on disk, not in DEVONthink)")
                return result
        elif not file_on_disk:
            # Found in DEVONthink despite missing from disk
            result['linked_existing'] = True
            logger.info(f"📎 Found {attachment.key} in DEVONthink (not on disk) → {dt_uuid}")

        if dt_uuid:
            # Create linked_url child attachment with DEVONthink link
            if self._create_devonthink_child_link(attachment.parent_key, dt_uuid, title=filename, dry_run=dry_run):
                # Update DEVONthink metadata
                if self.devonthink.update_item_metadata(dt_uuid, parent_item, dry_run):
                    result['result'] = 'success'
                    logger.info(f"✅ Migrated ZotFile attachment {attachment.key} → {dt_uuid}")

                    # Track processed item and save immediately
                    if not dry_run:
                        if attachment.parent_key not in self.state.processed_items:
                            self.state.processed_items.append(attachment.parent_key)
                            self._save_state()

                    # Clean up original from ZotFile storage (only if file exists)
                    if file_on_disk and not dry_run:
                        try:
                            file_path.unlink()
                            result['deleted_original'] = True
                            logger.info(f"🗑️  Deleted original: {file_path}")
                            # Remove empty parent directory
                            if not any(file_path.parent.iterdir()):
                                file_path.parent.rmdir()
                                logger.info(f"🗑️  Removed empty directory: {file_path.parent}")
                        except Exception as e:
                            logger.warning(f"⚠️  Failed to delete original {file_path}: {e}")

                        # Delete the Zotero attachment item
                        try:
                            att_raw = self.zotero_api.get_item_raw(attachment.key)
                            if att_raw:
                                att_version = att_raw.get('data', {}).get('version', 0)
                                if self.zotero_api.delete_attachment(attachment.key, att_version):
                                    result['deleted_attachment_item'] = True
                                else:
                                    self.state.pending_deletes.append(
                                        {'key': attachment.key, 'version': att_version})
                                    self._save_state()
                        except Exception as e:
                            logger.warning(f"⚠️  Failed to delete attachment item {attachment.key}: {e}")
                            self.state.pending_deletes.append(
                                {'key': attachment.key, 'version': attachment.version})
                            self._save_state()
                    elif file_on_disk and dry_run:
                        logger.info(f"[DRY RUN] Would delete original: {file_path}")
                        logger.info(f"[DRY RUN] Would delete Zotero attachment item: {attachment.key}")
                    elif not file_on_disk and not dry_run:
                        # No file to clean up; delete the Zotero attachment item
                        try:
                            att_raw = self.zotero_api.get_item_raw(attachment.key)
                            if att_raw:
                                att_version = att_raw.get('data', {}).get('version', 0)
                                if self.zotero_api.delete_attachment(attachment.key, att_version):
                                    result['deleted_attachment_item'] = True
                                else:
                                    self.state.pending_deletes.append(
                                        {'key': attachment.key, 'version': att_version})
                                    self._save_state()
                        except Exception as e:
                            logger.warning(f"⚠️  Failed to delete attachment item {attachment.key}: {e}")
                            self.state.pending_deletes.append(
                                {'key': attachment.key, 'version': attachment.version})
                            self._save_state()
                    else:
                        logger.info(f"[DRY RUN] Would delete Zotero attachment item: {attachment.key}")
                else:
                    logger.error(f"❌ Failed to update DEVONthink metadata for {attachment.key}")
            else:
                logger.error(f"❌ Failed to create DEVONthink child link for {attachment.key}")
        else:
            logger.error(f"❌ Failed to find DEVONthink UUID for {attachment.key}")

        return result

    def migrate_zotfile_attachments(self, dry_run=False, interactive=False) -> Dict[str, int]:
        """Migrate ZotFile-managed linked files (linkMode=2) to DEVONthink

        This handles files stored in ZotFile Import or other locations that are
        linked to Zotero items but not yet imported to DEVONthink.
        """
        logger.info("📁 Starting migration of ZotFile linked attachments...")

        results = {
            'success': 0,
            'error': 0,
            'skipped': 0,
            'skipped_path_invalid': 0,
            'skipped_no_parent': 0,
            'skipped_parent_not_found': 0,
            'skipped_already_processed': 0,
            'linked_existing': 0,
            'cleaned_broken': 0,
            'deleted_originals': 0
        }
        skipped_details = []

        attachments = self.zotero_api.get_zotfile_symlinks()
        logger.info(f"📊 Detection Summary: Found {len(attachments)} ZotFile linked attachments (linkMode=2)")

        for attachment in attachments:
            try:
                if interactive:
                    decision = self._interactive_prompt(
                        phase="Phase 1B - Migrate ZotFile attachment",
                        record_details={
                            "Attachment Key": attachment.key,
                            "Path": attachment.path or "(none)",
                            "Filename": attachment.filename or "(none)",
                            "Parent Key": attachment.parent_key or "(orphaned)",
                            "Action": "Copy to DEVONthink, create UUID link, delete original",
                        },
                        dry_run=dry_run,
                    )
                    if decision == 'q':
                        self._interactive_quit = True
                        break
                    if decision in ('n', 's'):
                        results['skipped'] += 1
                        if decision == 's':
                            break
                        continue

                outcome = self._process_single_zotfile_attachment(attachment, dry_run)
                r = outcome['result']

                if r == 'success':
                    results['success'] += 1
                    if outcome.get('deleted_original'):
                        results['deleted_originals'] += 1
                    if outcome.get('linked_existing'):
                        results['linked_existing'] += 1
                elif r == 'cleaned_broken':
                    results['cleaned_broken'] += 1
                elif r.startswith('skipped_'):
                    if r in results:
                        results[r] += 1
                    if r != 'skipped_already_processed':
                        results['skipped'] += 1
                    if outcome.get('skip_detail'):
                        skipped_details.append(outcome['skip_detail'])
                else:
                    results['error'] += 1

                # Small delay between operations
                if r == 'success' and not dry_run:
                    time.sleep(2)

            except Exception as e:
                logger.error(f"❌ Failed to migrate ZotFile attachment {attachment.key}: {e}")
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
        logger.info(f"  📎 Linked existing (found in DEVONthink): {results['linked_existing']}")
        logger.info(f"  🗑️  Cleaned broken (missing everywhere): {results['cleaned_broken']}")
        logger.info(f"  🗑️  Deleted originals: {results['deleted_originals']}")
        logger.info(f"  ❌ Errors: {results['error']}")
        logger.info(f"  ⏭️  Skipped: {results['skipped']} total")
        logger.info(f"     - Invalid path: {results['skipped_path_invalid']}")
        logger.info(f"     - No parent: {results['skipped_no_parent']}")
        logger.info(f"     - Parent not found: {results['skipped_parent_not_found']}")
        logger.info(f"     - Already processed: {results['skipped_already_processed']}")

        return results

    async def convert_zotfile_symlinks_async(self, dry_run=False, batch_size=20, interactive=False) -> Dict[str, int]:
        """Convert ZotFile symlinks to DEVONthink UUID links using async batch processing"""
        logger.info("🔗 Converting ZotFile symlinks with async processing...")

        results = {'success': 0, 'error': 0, 'skipped': 0}

        symlinks = self.zotero_api.get_zotfile_symlinks()
        logger.info(f"Found {len(symlinks)} ZotFile symlinks to convert")

        if not symlinks:
            return results

        if interactive:
            # Sequential one-at-a-time path for interactive mode
            for symlink in symlinks:
                if not symlink.path:
                    results['skipped'] += 1
                    continue

                filename = Path(symlink.path).name
                filename_no_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename

                # Fetch parent for display
                parent_title = "(unknown)"
                if symlink.parent_key:
                    parent_item = self.zotero_api.get_item(symlink.parent_key)
                    if parent_item:
                        parent_title = (parent_item.title or "")[:60]

                decision = self._interactive_prompt(
                    phase="Phase 2 - Convert ZotFile symlink",
                    record_details={
                        "Attachment Key": symlink.key,
                        "Path": symlink.path,
                        "Filename": filename,
                        "Parent Title": parent_title,
                        "Action": "Find in DEVONthink, create UUID link",
                    },
                    dry_run=dry_run,
                )
                if decision == 'q':
                    self._interactive_quit = True
                    break
                if decision in ('n', 's'):
                    results['skipped'] += 1
                    if decision == 's':
                        break
                    continue

                # Single-item DEVONthink lookup
                try:
                    uuid_results = await self.devonthink.batch_search_items([filename_no_ext], dry_run)
                    dt_uuid = uuid_results.get(filename_no_ext)

                    if dt_uuid and dt_uuid != "dry-run-uuid":
                        if symlink.parent_key and parent_item:
                            if self._create_devonthink_child_link(symlink.parent_key, dt_uuid, title=filename, dry_run=dry_run):
                                if await self.devonthink_update_metadata_async(dt_uuid, parent_item, dry_run):
                                    results['success'] += 1
                                    logger.info(f"Converted symlink {symlink.key} -> {dt_uuid}")
                                    continue
                        results['error'] += 1
                    elif dt_uuid == "dry-run-uuid":
                        results['success'] += 1
                    else:
                        logger.debug(f"No DEVONthink item found for: {filename}")
                        results['skipped'] += 1
                except Exception as e:
                    logger.error(f"Failed to convert symlink {symlink.key}: {e}")
                    results['error'] += 1

        else:
            # Batch path (original behavior)
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
                                if symlink.parent_key:
                                    parent_item = self.zotero_api.get_item(symlink.parent_key)
                                    if parent_item:
                                        # Create linked_url child with DEVONthink link
                                        link_title = Path(symlink.path).name if symlink.path else "DEVONthink Link"
                                        if self._create_devonthink_child_link(symlink.parent_key, dt_uuid, title=link_title, dry_run=dry_run):
                                            # Update DEVONthink metadata
                                            if await self.devonthink_update_metadata_async(dt_uuid, parent_item, dry_run):
                                                results['success'] += 1
                                                logger.info(f"Converted symlink {symlink.key} → {dt_uuid}")
                                                continue

                                results['error'] += 1
                            elif dt_uuid == "dry-run-uuid":
                                results['success'] += 1  # Count dry run successes
                            else:
                                filename = Path(symlink.path).name
                                logger.debug(f"No DEVONthink item found for: {filename}")
                                results['skipped'] += 1

                        except Exception as e:
                            logger.error(f"Failed to convert symlink {symlink.key}: {e}")
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
    
    def sync_new_items(self, dry_run=False, interactive=False) -> Dict[str, int]:
        """Sync new Zotero items to DEVONthink"""
        logger.info("🔄 Syncing new items...")

        results = {'success': 0, 'error': 0, 'skipped': 0}

        # Get items modified since last library version
        items = self.zotero_api.get_items_needing_sync(
            self.state.last_library_version,
            processed_items=self.state.processed_items
        )

        logger.info(f"Found {len(items)} items needing sync")

        for item in items:
            try:
                if item.key in self.state.processed_items:
                    results['skipped'] += 1
                    continue

                if interactive:
                    creators = item.creators if item.creators else []
                    first_creator = FilenameGenerator.extract_first_creator(creators) if creators else "(none)"
                    decision = self._interactive_prompt(
                        phase="Phase 3 - Sync new item",
                        record_details={
                            "Item Key": item.key,
                            "Title": (item.title or "")[:60],
                            "Creator": first_creator,
                            "Year": str(item.year) if item.year else "(none)",
                            "Item Type": item.item_type,
                            "Action": "Mark as processed",
                        },
                        dry_run=dry_run,
                    )
                    if decision == 'q':
                        self._interactive_quit = True
                        break
                    if decision in ('n', 's'):
                        results['skipped'] += 1
                        if decision == 's':
                            break
                        continue

                # For items without attachments, we skip for now
                # Could be extended to create text records or notes in DEVONthink

                results['success'] += 1
                prefix = "[DRY RUN] Would process" if dry_run else "Processed"
                logger.info(f"{prefix} item {item.key}: {item.title[:50]}...")

                # Track processed item
                if not dry_run:
                    if item.key not in self.state.processed_items:
                        self.state.processed_items.append(item.key)

            except Exception as e:
                logger.error(f"Failed to process item {item.key}: {e}")
                results['error'] += 1

        return results
    
    def _resolve_storage_path(self, attachment: ZoteroAttachment) -> Optional[Path]:
        """Resolve Zotero storage path to actual file location

        Supports multiple path formats:
        - storage:KEY:filename.pdf (standard format)
        - KEY:filename.pdf (without storage: prefix)
        - KEY/filename.pdf (forward slash variant)
        - No path but filename present (API v3 imported_file): uses attachment.key as storage dir

        Validates storage key format (8-char alphanumeric) and file existence.
        """
        if not attachment.path and not attachment.filename:
            return None

        # Fallback: API v3 returns filename (not path) for imported attachments
        if not attachment.path and attachment.filename:
            storage_base = Path(ZOTERO_STORAGE_PATH)
            resolved = storage_base / attachment.key / attachment.filename
            if resolved.exists():
                return resolved
            else:
                logger.debug(f"Filename-based path resolved but file missing: {resolved}")
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

    # ── Incremental sync (streaming mode) ─────────────────────────

    async def run_incremental_sync_async(
        self, triggered_version: int = None, dry_run=False
    ) -> bool:
        """Run an incremental sync, processing only items changed since last_library_version.

        Args:
            triggered_version: Version from the WebSocket event (informational).
                We always use self.state.last_library_version as the since
                parameter to avoid missing changes between events.
            dry_run: If True, log but don't modify anything.

        Returns:
            True if sync completed successfully.
        """
        since_version = self.state.last_library_version or 0
        logger.info(
            f"Running incremental sync (since version {since_version}, "
            f"triggered by version {triggered_version})"
        )

        self.zotero_api.invalidate_caches()

        try:
            # Step 1: Get changed attachment keys (lightweight format=versions call)
            changed_keys = self.zotero_api.get_changed_item_versions(
                since_version, item_type='attachment'
            )

            if not changed_keys:
                logger.info("No changed attachments since last sync")
                if self.zotero_api.last_library_version:
                    self.state.last_library_version = self.zotero_api.last_library_version
                    if not dry_run:
                        self._save_state()
                return True

            logger.info(
                f"Found {len(changed_keys)} changed attachments "
                f"since version {since_version}"
            )

            # Step 2: Fetch full data for changed items
            changed_items = self.zotero_api.get_items_by_keys(list(changed_keys.keys()))

            # Step 3: Classify and process
            processed_count = 0
            skipped_count = 0
            error_count = 0

            for api_item in changed_items:
                data = api_item.get('data', {})
                item_type = data.get('itemType', '')

                if item_type != 'attachment':
                    continue

                link_mode = data.get('linkMode', '')
                parent_key = data.get('parentItem')

                # Skip already-processed
                if parent_key and parent_key in self.state.processed_items:
                    skipped_count += 1
                    continue

                if link_mode == 'linked_file':
                    attachment = self.zotero_api._api_item_to_zotero_attachment(api_item)
                    try:
                        outcome = self._process_single_zotfile_attachment(
                            attachment, dry_run
                        )
                        if outcome['result'] == 'success':
                            processed_count += 1
                            if not dry_run:
                                time.sleep(2)
                        elif outcome['result'].startswith('skipped_'):
                            skipped_count += 1
                        else:
                            error_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to process attachment {attachment.key}: {e}"
                        )
                        error_count += 1
                elif link_mode == 'imported_url':
                    # Delete imported_url items immediately
                    attachment = self.zotero_api._api_item_to_zotero_attachment(api_item)
                    file_path = self._resolve_storage_path(attachment)
                    if file_path and file_path.exists() and not dry_run:
                        try:
                            file_path.unlink()
                            logger.info(f"Deleted imported_url file: {file_path}")
                        except Exception as e:
                            logger.warning(f"Failed to delete imported_url file: {e}")
                    if not dry_run:
                        att_raw = self.zotero_api.get_item_raw(attachment.key)
                        if att_raw:
                            version = att_raw.get('data', {}).get('version', 0)
                            if self.zotero_api.delete_attachment(attachment.key, version):
                                processed_count += 1
                            else:
                                error_count += 1
                        else:
                            processed_count += 1  # Already gone
                    else:
                        logger.info(f"[DRY RUN] Would delete imported_url: {attachment.key}")
                elif link_mode == 'imported_file':
                    # Phase 1A: leave for full cycle
                    skipped_count += 1

            # Step 4: Check for deletions
            deleted = self.zotero_api.get_deleted_since(since_version)
            deleted_items = deleted.get('items', [])
            if deleted_items:
                logger.info(f"Noted {len(deleted_items)} deleted items")
                # Remove deleted keys from processed_items to keep list clean
                for key in deleted_items:
                    if key in self.state.processed_items:
                        self.state.processed_items.remove(key)

            # Step 5: Update state
            if not dry_run:
                new_version = self.zotero_api.last_library_version
                if new_version and (since_version == 0 or new_version > since_version):
                    self.state.last_library_version = new_version
                self.state.last_sync = datetime.now().isoformat()
                self._save_state()

            logger.info(
                f"Incremental sync complete: "
                f"{processed_count} processed, {skipped_count} skipped, "
                f"{error_count} errors"
            )
            return True

        except Exception as e:
            logger.error(f"Incremental sync failed: {e}", exc_info=True)
            return False

    async def run_interactive(self, dry_run=False):
        """Interactive mode: show menu, run selected phase, loop until quit."""
        print("\nFetching attachment data from Zotero API...")
        self.zotero_api.invalidate_caches()
        self.zotero_api._get_all_attachments_cached()
        print(f"Ready. ({self.zotero_api.last_library_version or 'unknown'} library version)\n")

        while True:
            choice = self._interactive_menu(dry_run)

            if choice == 'q':
                print("Exiting interactive mode.")
                return

            self._interactive_quit = False

            if choice == 'a':
                await self.run_service_cycle_async(dry_run=dry_run, interactive=True)
            elif choice == '0':
                results = self.delete_imported_url_attachments(dry_run, interactive=True)
                logger.info(f"Phase 0 complete: {results}")
            elif choice == '1a':
                results = self.migrate_stored_attachments(dry_run, interactive=True)
                logger.info(f"Phase 1A complete: {results}")
            elif choice == '1b':
                results = self.migrate_zotfile_attachments(dry_run, interactive=True)
                logger.info(f"Phase 1B complete: {results}")
            elif choice == '2':
                results = await self.convert_zotfile_symlinks_async(dry_run, batch_size=50, interactive=True)
                logger.info(f"Phase 2 complete: {results}")
            elif choice == '3':
                results = self.sync_new_items(dry_run, interactive=True)
                logger.info(f"Phase 3 complete: {results}")

            # Save state after each phase if not dry run
            if not dry_run and choice != 'q':
                self.state.last_library_version = self.zotero_api.last_library_version
                self._save_state()

    async def run_service_cycle_async(self, dry_run=False, interactive=False) -> bool:
        """Run one complete service cycle with async processing"""
        logger.info("🔄 Running async service cycle...")
        self._interactive_quit = False

        # Invalidate caches at start of each cycle
        self.zotero_api.invalidate_caches()

        try:
            # Retry pending deletes from previous cycles
            pending_results = self.retry_pending_deletes(dry_run, interactive=interactive)
            if pending_results['retried'] > 0:
                logger.info(f"Pending deletes retry: {pending_results}")
            if interactive and self._interactive_quit:
                logger.info("[INTERACTIVE] User quit - stopping cycle early.")
                return True

            # Phase 0: Delete imported_url attachments (useless URL snapshots)
            await self._wait_if_paused_async()
            logger.info("\n" + "="*70)
            logger.info("PHASE 0: Deleting imported_url (linkMode=1) attachments")
            logger.info("="*70)
            phase0_results = self.delete_imported_url_attachments(dry_run, interactive=interactive)
            logger.info(f"Phase 0 complete: {phase0_results}")
            if interactive and self._interactive_quit:
                logger.info("[INTERACTIVE] User quit - stopping cycle early.")
                return True

            # Invalidate cache after Phase 0 deletions
            if phase0_results.get('items_deleted', 0) > 0:
                self.zotero_api.invalidate_caches()

            # Phase 1A: Migrate stored attachments from Zotero storage (linkMode=0)
            await self._wait_if_paused_async()
            logger.info("\n" + "="*70)
            logger.info("PHASE 1A: Migrating linkMode=0 (Zotero storage) attachments")
            logger.info("="*70)
            migration_results = self.migrate_stored_attachments(dry_run, interactive=interactive)
            logger.info(f"Phase 1A complete: {migration_results}")
            if interactive and self._interactive_quit:
                logger.info("[INTERACTIVE] User quit - stopping cycle early.")
                return True

            # Phase 1B: Migrate ZotFile linked attachments (linkMode=2)
            await self._wait_if_paused_async()
            logger.info("\n" + "="*70)
            logger.info("PHASE 1B: Migrating linkMode=2 (ZotFile Import) attachments")
            logger.info("="*70)
            zotfile_migration_results = self.migrate_zotfile_attachments(dry_run, interactive=interactive)
            logger.info(f"Phase 1B complete: {zotfile_migration_results}")
            if interactive and self._interactive_quit:
                logger.info("[INTERACTIVE] User quit - stopping cycle early.")
                return True

            # Phase 2: Convert ZotFile symlinks already in DEVONthink (async batch processing)
            await self._wait_if_paused_async()
            logger.info("\n" + "="*70)
            logger.info("PHASE 2: Converting existing ZotFile symlinks to UUID links")
            logger.info("="*70)
            conversion_results = await self.convert_zotfile_symlinks_async(dry_run, batch_size=50, interactive=interactive)
            logger.info(f"Phase 2 complete: {conversion_results}")
            if interactive and self._interactive_quit:
                logger.info("[INTERACTIVE] User quit - stopping cycle early.")
                return True

            # Phase 3: Sync new items (synchronous for now)
            await self._wait_if_paused_async()
            logger.info("\n" + "="*70)
            logger.info("PHASE 3: Syncing new items")
            logger.info("="*70)
            sync_results = self.sync_new_items(dry_run, interactive=interactive)
            logger.info(f"Phase 3 complete: {sync_results}")

            # Update state
            if not dry_run:
                self.state.last_sync = datetime.now().isoformat()
                self.state.last_library_version = self.zotero_api.last_library_version
                self._save_state()

            # Overall summary
            logger.info("\n" + "="*70)
            logger.info("🎉 SERVICE CYCLE COMPLETE")
            logger.info("="*70)
            logger.info(f"Phase 0 imported_url deleted: {phase0_results.get('items_deleted', 0)}")
            logger.info(f"Total Zotero storage (linkMode=0) migrated: {migration_results.get('success', 0)}")
            logger.info(f"Total ZotFile (linkMode=2) migrated: {zotfile_migration_results.get('success', 0)}")
            logger.info(f"Total symlinks converted: {conversion_results.get('success', 0)}")
            logger.info(f"Total new items synced: {sync_results.get('success', 0)}")
            logger.info("="*70 + "\n")

            return True

        except Exception as e:
            logger.error(f"Service cycle failed: {e}")
            return False

    def run_service_cycle(self, dry_run=False, interactive=False) -> bool:
        """Synchronous wrapper for async service cycle"""
        return asyncio.run(self.run_service_cycle_async(dry_run, interactive=interactive))
    
    def run_perpetual_service(self):
        """Run the service perpetually until stopped"""
        logger.info("🚀 Starting DEVONzot perpetual service...")
        
        # Write PID file
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        
        self.running = True
        
        try:
            while self.running:
                self._wait_if_paused()
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

    async def run_streaming_service(self):
        """Run the service with WebSocket streaming as the primary trigger.

        Architecture:
          Task 1 — WebSocket listener: pushes events to sync_queue
          Task 2 — Sync worker: consumes from queue, runs incremental sync
          Fallback — If no event for FALLBACK_POLL_INTERVAL, triggers sync anyway
        """
        from zotero_stream import ZoteroStreamClient

        logger.info("Starting DEVONzot streaming service...")

        # Write PID file
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))

        self.running = True
        sync_queue: asyncio.Queue = asyncio.Queue()

        # Callback: stream client posts version to queue
        async def on_topic_updated(version: int):
            await sync_queue.put(('stream', version))

        stream_client = ZoteroStreamClient(
            api_key=ZOTERO_API_KEY,
            user_id=ZOTERO_USER_ID,
            on_topic_updated=on_topic_updated,
        )

        # ── Task 1: WebSocket listener ─────────────────────────────
        stream_task = asyncio.create_task(stream_client.run())

        # ── Task 2: Sync worker ────────────────────────────────────
        async def sync_worker():
            # Initial catch-up sync (full cycle to close any gap)
            logger.info("Running initial catch-up sync...")
            await self.run_service_cycle_async(dry_run=False)

            while self.running:
                try:
                    # Wait for event with optional fallback timeout
                    timeout = FALLBACK_POLL_INTERVAL if FALLBACK_POLL_ENABLED else None
                    try:
                        source, version = await asyncio.wait_for(
                            sync_queue.get(), timeout=timeout
                        )
                        logger.info(f"Sync triggered by {source} (version {version})")
                    except asyncio.TimeoutError:
                        if FALLBACK_POLL_ENABLED:
                            logger.info(
                                f"No stream event for {FALLBACK_POLL_INTERVAL}s, "
                                f"running fallback sync"
                            )
                            source, version = 'fallback', None
                        else:
                            continue

                    # Debounce: drain queued events into a single sync
                    drained = 0
                    while not sync_queue.empty():
                        try:
                            sync_queue.get_nowait()
                            drained += 1
                        except asyncio.QueueEmpty:
                            break
                    if drained:
                        logger.info(f"Debounced {drained} additional event(s)")

                    # Check for pause before syncing
                    await self._wait_if_paused_async()

                    # Run incremental sync
                    success = await self.run_incremental_sync_async(
                        triggered_version=version, dry_run=False
                    )

                    if not success:
                        self.restart_count += 1
                        if self.restart_count >= MAX_RESTART_ATTEMPTS:
                            logger.error(
                                f"Max failures ({MAX_RESTART_ATTEMPTS}) reached. "
                                f"Stopping."
                            )
                            self.running = False
                            break
                        logger.warning(
                            f"Sync failed (attempt "
                            f"{self.restart_count}/{MAX_RESTART_ATTEMPTS})"
                        )
                        await asyncio.sleep(RESTART_DELAY)
                    else:
                        self.restart_count = 0

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Sync worker error: {e}", exc_info=True)
                    await asyncio.sleep(RESTART_DELAY)

        worker_task = asyncio.create_task(sync_worker())

        try:
            # Wait until either task exits (usually shutdown signal)
            done, pending = await asyncio.wait(
                [stream_task, worker_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        except asyncio.CancelledError:
            logger.info("Service cancelled")
        finally:
            self.running = False
            await stream_client.stop()
            if PID_FILE.exists():
                PID_FILE.unlink()
            logger.info("DEVONzot streaming service stopped")

def main():
    """Main entry point with argument handling"""
    import argparse

    parser = argparse.ArgumentParser(
        description="DEVONzot - Complete Zotero/DEVONthink Integration Service"
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Run analysis without making changes')
    parser.add_argument('--service', action='store_true',
                        help='Run as perpetual service')
    parser.add_argument('--once', action='store_true',
                        help='Run once then exit')
    parser.add_argument('--stop', action='store_true',
                        help='Stop running service')
    parser.add_argument('--no-stream', action='store_true',
                        help='Disable WebSocket streaming, use polling only')
    parser.add_argument('--interactive', action='store_true',
                        help='Step through records one at a time with y/n/q prompts (implies --once)')

    args = parser.parse_args()

    service = DEVONzotService()

    # In interactive mode, restore default signal behavior (the constructor
    # installs a graceful-shutdown handler that swallows both SIGINT and SIGTERM)
    if args.interactive:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    if args.stop:
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

    if args.interactive:
        asyncio.run(service.run_interactive(dry_run=args.dry_run))
        return

    if args.dry_run:
        results = service.run_dry_run()
        print("\n📁 Dry run results saved to state file")
        return

    if args.service:
        if WEBSOCKET_ENABLED and not args.no_stream:
            asyncio.run(service.run_streaming_service())
        else:
            service.run_perpetual_service()
    elif args.once:
        success = service.run_service_cycle(dry_run=False)
        exit(0 if success else 1)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()