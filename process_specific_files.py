#!/usr/bin/env python3
"""
DEVONzot Specific File Processor v1.0.0

Processes a specific list of files through the complete DEVONzot workflow:
1. Find corresponding Zotero items for each file
2. Import files to DEVONthink with proper naming
3. Apply comprehensive metadata sync
4. Convert from stored to linked with UUID references
5. Remove original stored files (optional)

This handles the workflow for files that won't be caught by the main service,
such as stored attachments (linkMode=0) in Zotero storage directories.
"""

import asyncio
import sqlite3
import subprocess
import json
import logging
import os
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass
import sys
import argparse

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/Users/travisross/DEVONzot/specific_files.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class FileProcessingResult:
    """Result of processing a specific file"""
    file_path: str
    zotero_item_id: Optional[int] = None
    devonthink_uuid: Optional[str] = None
    success: bool = False
    error_message: Optional[str] = None
    metadata_applied: bool = False
    converted_to_linked: bool = False

@dataclass
class ZoteroItem:
    """Zotero item with metadata"""
    item_id: int
    parent_item_id: Optional[int]
    title: str
    authors: List[str]
    publication_year: Optional[str]
    item_type: str
    collections: List[str]
    tags: List[str]
    notes: List[str]
    attachment_path: Optional[str] = None
    attachment_id: Optional[int] = None
    link_mode: Optional[int] = None

class SpecificFileProcessor:
    """Processes specific files through the complete DEVONzot workflow"""
    
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.zotero_db_path = '/Users/travisross/Zotero/zotero.sqlite'
        self.wait_time = int(os.environ.get('DEVONZOT_WAIT_TIME', '3'))
        self.processed_files: List[FileProcessingResult] = []
        
    def get_zotero_connection(self, read_only: bool = True) -> sqlite3.Connection:
        """Get connection to Zotero database"""
        if not read_only and self.is_zotero_running():
            raise Exception("Zotero must be closed for write operations")
            
        conn = sqlite3.connect(self.zotero_db_path)
        conn.row_factory = sqlite3.Row
        return conn
        
    def is_zotero_running(self) -> bool:
        """Check if Zotero is currently running"""
        try:
            result = subprocess.run(['pgrep', '-f', 'Zotero'], 
                                  capture_output=True, text=True)
            return result.returncode == 0
        except Exception:
            return False
    
    def extract_storage_key_from_path(self, file_path: str) -> Optional[str]:
        """Extract storage directory key from file path"""
        # Path format: /Users/travisross/Zotero/storage/7RAW3VWE/filename.pdf
        parts = Path(file_path).parts
        try:
            storage_idx = parts.index('storage')
            if storage_idx + 1 < len(parts):
                return parts[storage_idx + 1]
        except ValueError:
            pass
        return None
    
    def find_zotero_item_by_storage_key(self, storage_key: str) -> Optional[ZoteroItem]:
        """Find Zotero item by its storage directory key"""
        with self.get_zotero_connection() as conn:
            # Find attachment with matching storage directory
            query = """
                SELECT 
                    ia.itemID as attachment_id,
                    ia.parentItemID,
                    ia.linkMode,
                    ia.path,
                    i.itemID as parent_id,
                    i.itemTypeID
                FROM itemAttachments ia
                LEFT JOIN items i ON ia.parentItemID = i.itemID
                WHERE ia.path LIKE ?
                OR ia.path LIKE ?
            """
            
            # Try both with and without 'storage:' prefix
            storage_patterns = [
                f'storage:{storage_key}%',
                f'{storage_key}%'
            ]
            
            for pattern in storage_patterns:
                cursor = conn.execute(query, (pattern, pattern))
                row = cursor.fetchone()
                
                if row:
                    parent_id = row['parentItemID'] or row['attachment_id']
                    return self.get_zotero_item_full(parent_id, conn)
                    
        return None
    
    def find_zotero_item_by_filename(self, file_path: str) -> Optional[ZoteroItem]:
        """Find Zotero item by searching for filename patterns"""
        filename = Path(file_path).name
        base_name = Path(file_path).stem
        
        with self.get_zotero_connection() as conn:
            # Search for items with similar titles or attachment names
            search_patterns = [
                filename,
                base_name,
                # Remove common patterns from academic filenames
                base_name.replace(' - ', ' ').replace('_', ' '),
            ]
            
            for pattern in search_patterns:
                # Search in item titles
                query = """
                    SELECT i.itemID
                    FROM items i
                    JOIN itemData id ON i.itemID = id.itemID
                    JOIN itemDataValues idv ON id.valueID = idv.valueID
                    WHERE idv.value LIKE ?
                    AND i.itemTypeID != 14  -- Exclude attachments
                    LIMIT 1
                """
                
                cursor = conn.execute(query, (f'%{pattern}%',))
                row = cursor.fetchone()
                
                if row:
                    return self.get_zotero_item_full(row['itemID'], conn)
                    
        return None
    
    def get_zotero_item_full(self, item_id: int, conn: sqlite3.Connection) -> ZoteroItem:
        """Get complete Zotero item with all metadata"""
        # Get basic item info
        item_query = """
            SELECT i.itemID, i.itemTypeID, it.typeName
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE i.itemID = ?
        """
        
        item_row = conn.execute(item_query, (item_id,)).fetchone()
        if not item_row:
            raise ValueError(f"Item {item_id} not found")
        
        # Get metadata fields
        metadata_query = """
            SELECT f.fieldName, idv.value
            FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE id.itemID = ?
        """
        
        metadata = {}
        for row in conn.execute(metadata_query, (item_id,)):
            metadata[row['fieldName']] = row['value']
        
        # Get creators (authors)
        creators_query = """
            SELECT ct.creatorType, c.firstName, c.lastName
            FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
            WHERE ic.itemID = ?
            ORDER BY ic.orderIndex
        """
        
        authors = []
        for row in conn.execute(creators_query, (item_id,)):
            if row['firstName'] and row['lastName']:
                authors.append(f"{row['firstName']} {row['lastName']}")
            elif row['lastName']:
                authors.append(row['lastName'])
        
        # Get collections
        collections_query = """
            SELECT c.collectionName
            FROM collectionItems ci
            JOIN collections c ON ci.collectionID = c.collectionID
            WHERE ci.itemID = ?
        """
        
        collections = [row['collectionName'] for row in conn.execute(collections_query, (item_id,))]
        
        # Get tags
        tags_query = """
            SELECT t.name
            FROM itemTags it
            JOIN tags t ON it.tagID = t.tagID
            WHERE it.itemID = ?
        """
        
        tags = [row['name'] for row in conn.execute(tags_query, (item_id,))]
        
        # Get notes
        notes_query = """
            SELECT note
            FROM itemNotes
            WHERE parentItemID = ?
        """
        
        notes = [row['note'] for row in conn.execute(notes_query, (item_id,))]
        
        # Get attachment info if this is a parent item
        attachment_query = """
            SELECT ia.itemID, ia.linkMode, ia.path
            FROM itemAttachments ia
            WHERE ia.parentItemID = ?
            AND ia.contentType LIKE '%pdf%'
            LIMIT 1
        """
        
        attachment_row = conn.execute(attachment_query, (item_id,)).fetchone()
        attachment_id = attachment_row['itemID'] if attachment_row else None
        attachment_path = attachment_row['path'] if attachment_row else None
        link_mode = attachment_row['linkMode'] if attachment_row else None
        
        return ZoteroItem(
            item_id=item_id,
            parent_item_id=None,
            title=metadata.get('title', 'Unknown Title'),
            authors=authors,
            publication_year=metadata.get('date', '').split('-')[0] if metadata.get('date') else None,
            item_type=item_row['typeName'],
            collections=collections,
            tags=tags,
            notes=notes,
            attachment_id=attachment_id,
            attachment_path=attachment_path,
            link_mode=link_mode
        )
    
    def generate_target_filename(self, zotero_item: ZoteroItem, original_path: str) -> str:
        """Generate a proper filename for the file in DEVONthink"""
        extension = Path(original_path).suffix
        
        # Build filename components
        author_part = ""
        if zotero_item.authors:
            if len(zotero_item.authors) == 1:
                author_part = zotero_item.authors[0].split()[-1]  # Last name
            elif len(zotero_item.authors) == 2:
                names = [author.split()[-1] for author in zotero_item.authors[:2]]
                author_part = " and ".join(names)
            else:
                author_part = f"{zotero_item.authors[0].split()[-1]} et al"
        
        # Clean title
        title = zotero_item.title
        if len(title) > 50:
            title = title[:50] + "..."
        
        # Build filename
        parts = [p for p in [
            author_part,
            title,
            zotero_item.publication_year,
            zotero_item.item_type.title()
        ] if p]
        
        filename = " - ".join(parts)
        
        # Clean filename
        filename = "".join(c for c in filename if c.isalnum() or c in ' -.,()[]{}')
        filename = " ".join(filename.split())  # Normalize spaces
        
        return f"{filename}{extension}"
    
    async def import_file_to_devonthink(self, file_path: str, target_filename: str) -> Optional[str]:
        """Import file to DEVONthink and return UUID"""
        if self.dry_run:
            logger.info(f"DRY RUN: Would import {file_path} as {target_filename}")
            return "dry-run-uuid"
        
        try:
            # Copy file with target name to a temporary location
            import tempfile
            import shutil
            
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_file = Path(temp_dir) / target_filename
                shutil.copy2(file_path, temp_file)
                
                # Import to DEVONthink
                script = f'''
                tell application "DEVONthink 3"
                    set theDatabase to current database
                    if theDatabase is missing value then
                        set theDatabase to database 1
                    end if
                    
                    set importedRecord to import "{temp_file}" to theDatabase
                    set recordUUID to uuid of importedRecord
                    return recordUUID
                end tell
                '''
                
                process = await asyncio.create_subprocess_exec(
                    'osascript', '-e', script,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                stdout, stderr = await process.communicate()
                
                if process.returncode == 0:
                    uuid = stdout.decode().strip()
                    logger.info(f"Imported {target_filename} to DEVONthink with UUID: {uuid}")
                    return uuid
                else:
                    logger.error(f"Failed to import {file_path}: {stderr.decode()}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error importing {file_path} to DEVONthink: {e}")
            return None
    
    def apply_metadata_to_devonthink(self, uuid: str, zotero_item: ZoteroItem) -> bool:
        """Apply Zotero metadata to DEVONthink item"""
        if self.dry_run:
            logger.info(f"DRY RUN: Would apply metadata to {uuid}")
            return True
        
        try:
            # Prepare metadata
            authors_str = "; ".join(zotero_item.authors) if zotero_item.authors else ""
            collections_str = ", ".join(zotero_item.collections) if zotero_item.collections else ""
            tags_str = ", ".join(zotero_item.tags) if zotero_item.tags else ""
            
            # Apply to DEVONthink
            script = f'''
            tell application "DEVONthink 3"
                set theRecord to get record with uuid "{uuid}"
                if theRecord is not missing value then
                    set comment of theRecord to "{zotero_item.title}\\n\\nAuthors: {authors_str}\\nCollections: {collections_str}\\nTags: {tags_str}"
                end if
            end tell
            '''
            
            result = subprocess.run(['osascript', '-e', script], 
                                  capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"Applied metadata to DEVONthink item {uuid}")
                
                # Apply macOS extended attributes
                self.apply_macos_metadata(uuid, zotero_item)
                return True
            else:
                logger.error(f"Failed to apply DEVONthink metadata: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Error applying metadata to DEVONthink: {e}")
            return False
    
    def apply_macos_metadata(self, uuid: str, zotero_item: ZoteroItem):
        """Apply macOS extended attributes"""
        try:
            # Get file path from DEVONthink
            script = f'''
            tell application "DEVONthink 3"
                set theRecord to get record with uuid "{uuid}"
                if theRecord is not missing value then
                    return path of theRecord
                end if
            end tell
            '''
            
            result = subprocess.run(['osascript', '-e', script], 
                                  capture_output=True, text=True)
            
            if result.returncode == 0:
                file_path = result.stdout.strip()
                
                # Apply extended attributes
                if zotero_item.authors:
                    authors_str = "; ".join(zotero_item.authors)
                    subprocess.run(['xattr', '-w', 'kMDItemAuthors', authors_str, file_path])
                
                subprocess.run(['xattr', '-w', 'kMDItemTitle', zotero_item.title, file_path])
                
                if zotero_item.publication_year:
                    subprocess.run(['xattr', '-w', 'kMDItemContentCreationDate', 
                                  zotero_item.publication_year, file_path])
                
                logger.info(f"Applied macOS metadata to {file_path}")
                
        except Exception as e:
            logger.error(f"Error applying macOS metadata: {e}")
    
    def convert_to_linked_attachment(self, zotero_item: ZoteroItem, devonthink_uuid: str) -> bool:
        """Convert stored attachment to linked with UUID reference"""
        if self.dry_run:
            logger.info(f"DRY RUN: Would convert attachment {zotero_item.attachment_id} to linked")
            return True
            
        if not zotero_item.attachment_id:
            logger.error("No attachment ID to convert")
            return False
        
        try:
            with self.get_zotero_connection(read_only=False) as conn:
                # Update attachment to be linked (linkMode=1) with UUID path
                uuid_path = f"x-devonthink-item://{devonthink_uuid}"
                
                update_query = """
                    UPDATE itemAttachments 
                    SET linkMode = 1, path = ?
                    WHERE itemID = ?
                """
                
                conn.execute(update_query, (uuid_path, zotero_item.attachment_id))
                conn.commit()
                
                logger.info(f"Converted attachment {zotero_item.attachment_id} to linked with UUID")
                return True
                
        except Exception as e:
            logger.error(f"Error converting attachment to linked: {e}")
            return False
    
    def remove_original_file(self, file_path: str) -> bool:
        """Remove original file from Zotero storage"""
        if self.dry_run:
            logger.info(f"DRY RUN: Would remove {file_path}")
            return True
        
        try:
            os.remove(file_path)
            logger.info(f"Removed original file: {file_path}")
            
            # Also remove parent directory if empty
            parent_dir = Path(file_path).parent
            try:
                parent_dir.rmdir()
                logger.info(f"Removed empty directory: {parent_dir}")
            except OSError:
                pass  # Directory not empty
                
            return True
            
        except Exception as e:
            logger.error(f"Error removing file {file_path}: {e}")
            return False
    
    async def process_file(self, file_path: str) -> FileProcessingResult:
        """Process a single file through the complete workflow"""
        result = FileProcessingResult(file_path=file_path)
        
        try:
            logger.info(f"Processing file: {file_path}")
            
            # Verify file exists
            if not Path(file_path).exists():
                result.error_message = "File does not exist"
                return result
            
            # Find corresponding Zotero item
            storage_key = self.extract_storage_key_from_path(file_path)
            zotero_item = None
            
            if storage_key:
                zotero_item = self.find_zotero_item_by_storage_key(storage_key)
            
            if not zotero_item:
                zotero_item = self.find_zotero_item_by_filename(file_path)
            
            if not zotero_item:
                result.error_message = "Could not find corresponding Zotero item"
                return result
            
            result.zotero_item_id = zotero_item.item_id
            logger.info(f"Found Zotero item {zotero_item.item_id}: {zotero_item.title}")
            
            # Generate target filename
            target_filename = self.generate_target_filename(zotero_item, file_path)
            logger.info(f"Target filename: {target_filename}")
            
            # Import to DEVONthink
            devonthink_uuid = await self.import_file_to_devonthink(file_path, target_filename)
            if not devonthink_uuid:
                result.error_message = "Failed to import to DEVONthink"
                return result
            
            result.devonthink_uuid = devonthink_uuid
            
            # Wait for DEVONthink to process
            await asyncio.sleep(self.wait_time)
            
            # Apply metadata
            if self.apply_metadata_to_devonthink(devonthink_uuid, zotero_item):
                result.metadata_applied = True
            
            # Convert to linked attachment
            if self.convert_to_linked_attachment(zotero_item, devonthink_uuid):
                result.converted_to_linked = True
            
            # Remove original file (optional - can be controlled by flag)
            # if self.remove_original_file(file_path):
            #     logger.info(f"Original file removed: {file_path}")
            
            result.success = True
            logger.info(f"Successfully processed: {file_path}")
            
        except Exception as e:
            result.error_message = str(e)
            logger.error(f"Error processing {file_path}: {e}")
        
        return result
    
    async def process_file_list(self, file_list: List[str]) -> List[FileProcessingResult]:
        """Process a list of files"""
        results = []
        
        for file_path in file_list:
            result = await self.process_file(file_path)
            results.append(result)
            self.processed_files.append(result)
            
            # Brief pause between files
            await asyncio.sleep(1)
        
        return results
    
    def print_summary(self):
        """Print processing summary"""
        total = len(self.processed_files)
        successful = sum(1 for r in self.processed_files if r.success)
        
        print(f"\n{'='*60}")
        print(f"PROCESSING SUMMARY")
        print(f"{'='*60}")
        print(f"Total files processed: {total}")
        print(f"Successful: {successful}")
        print(f"Failed: {total - successful}")
        
        if successful > 0:
            metadata_applied = sum(1 for r in self.processed_files if r.metadata_applied)
            converted = sum(1 for r in self.processed_files if r.converted_to_linked)
            
            print(f"Metadata applied: {metadata_applied}")
            print(f"Converted to linked: {converted}")
        
        # Show failures
        failures = [r for r in self.processed_files if not r.success]
        if failures:
            print(f"\nFAILED FILES:")
            for result in failures:
                print(f"  {Path(result.file_path).name}: {result.error_message}")

def parse_file_list(file_list_str: str) -> List[str]:
    """Parse file list from various input formats"""
    files = []
    
    for line in file_list_str.strip().split('\n'):
        line = line.strip()
        if line and not line.startswith('#') and line.endswith('.pdf'):
            files.append(line)
    
    return files

async def main():
    parser = argparse.ArgumentParser(description='Process specific files through DEVONzot workflow')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Test run without making changes')
    parser.add_argument('--file-list', type=str,
                       help='Newline-separated list of file paths')
    parser.add_argument('--input-file', type=str,
                       help='Read file list from file')
    parser.add_argument('files', nargs='*', 
                       help='Individual file paths to process')
    
    args = parser.parse_args()
    
    # Collect file list
    files_to_process = []
    
    if args.files:
        files_to_process.extend(args.files)
    
    if args.file_list:
        files_to_process.extend(parse_file_list(args.file_list))
    
    if args.input_file:
        with open(args.input_file) as f:
            files_to_process.extend(parse_file_list(f.read()))
    
    if not files_to_process:
        print("No files specified. Use --file-list, --input-file, or provide file paths as arguments.")
        sys.exit(1)
    
    # Process files
    processor = SpecificFileProcessor(dry_run=args.dry_run)
    
    if args.dry_run:
        print(f"🧪 DRY RUN MODE - No changes will be made")
    
    print(f"Processing {len(files_to_process)} files...")
    
    results = await processor.process_file_list(files_to_process)
    processor.print_summary()

if __name__ == "__main__":
    asyncio.run(main())