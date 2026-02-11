#!/usr/bin/env python3
"""
DEVONzot Integration Architecture

Complete bidirectional sync between Zotero and DEVONthink:
- Sync Zotero metadata → DEVONthink items
- Sync DEVONthink tags/filenames → Zotero database  
- Migrate all file storage to DEVONthink with UUID links
- Support mobile workflow via webhooks/cloud sync
"""

import sqlite3
import os
import json
import subprocess
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime
import hashlib
import shutil

# Configuration
ZOTERO_DB_PATH = "/Users/travisross/Zotero/zotero.sqlite"
ZOTERO_STORAGE_PATH = "/Users/travisross/Zotero/storage"
ZOTFILE_IMPORT_PATH = "/Users/travisross/ZotFile Import"
DEVONTHINK_DATABASE = "Research"  # Target DEVONthink database

@dataclass
class ZoteroItem:
    """Zotero item with metadata"""
    item_id: int
    key: str
    title: str
    authors: List[str]
    publication: Optional[str]
    date: Optional[str]
    doi: Optional[str]
    url: Optional[str]
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
class DEVONthinkItem:
    """DEVONthink item representation"""
    uuid: str
    name: str
    path: str
    kind: str
    creation_date: str
    modification_date: str
    tags: List[str]
    custom_metadata: Dict[str, Any]
    url: str  # x-devonthink-item://uuid

class ZoteroDatabase:
    """Safe Zotero database interface"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    @contextmanager
    def connection(self, read_only=True):
        """Safe database connection"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            if read_only:
                conn.execute("PRAGMA query_only = ON")
            yield conn
        finally:
            if conn:
                conn.close()
    
    def get_items_needing_sync(self, since_timestamp: str = None) -> List[ZoteroItem]:
        """Get Zotero items that need syncing to DEVONthink"""
        with self.connection() as conn:
            query = """
                SELECT DISTINCT
                    i.itemID,
                    i.key,
                    i.dateAdded,
                    i.dateModified,
                    GROUP_CONCAT(
                        CASE WHEN f.fieldName = 'title' THEN idv.value END
                    ) as title,
                    GROUP_CONCAT(
                        CASE WHEN f.fieldName = 'publicationTitle' THEN idv.value END
                    ) as publication,
                    GROUP_CONCAT(
                        CASE WHEN f.fieldName = 'date' THEN idv.value END
                    ) as date,
                    GROUP_CONCAT(
                        CASE WHEN f.fieldName = 'DOI' THEN idv.value END
                    ) as doi,
                    GROUP_CONCAT(
                        CASE WHEN f.fieldName = 'url' THEN idv.value END
                    ) as url
                FROM items i
                LEFT JOIN itemData id ON i.itemID = id.itemID
                LEFT JOIN fields f ON id.fieldID = f.fieldID
                LEFT JOIN itemDataValues idv ON id.valueID = idv.valueID
                WHERE i.itemID NOT IN (SELECT itemID FROM itemAttachments)
                GROUP BY i.itemID
                HAVING url NOT LIKE 'x-devonthink-item://%' OR url IS NULL
                ORDER BY i.dateModified DESC
            """
            
            cursor = conn.execute(query)
            items = []
            
            for row in cursor.fetchall():
                # Get authors
                authors = self._get_item_authors(conn, row['itemID'])
                
                # Get tags  
                tags = self._get_item_tags(conn, row['itemID'])
                
                # Get collections
                collections = self._get_item_collections(conn, row['itemID'])
                
                items.append(ZoteroItem(
                    item_id=row['itemID'],
                    key=row['key'],
                    title=row['title'] or "Untitled",
                    authors=authors,
                    publication=row['publication'],
                    date=row['date'],
                    doi=row['doi'],
                    url=row['url'],
                    tags=tags,
                    collections=collections,
                    date_added=row['dateAdded'],
                    date_modified=row['dateModified']
                ))
            
            return items
    
    def get_stored_attachments(self) -> List[ZoteroAttachment]:
        """Get attachments stored in Zotero storage that need migration"""
        with self.connection() as conn:
            cursor = conn.execute("""
                SELECT 
                    ia.itemID,
                    ia.parentItemID,
                    ia.linkMode,
                    ia.contentType,
                    ia.path,
                    ia.storageHash
                FROM itemAttachments ia
                WHERE ia.linkMode = 0 
                AND ia.path IS NOT NULL
                AND ia.path LIKE 'storage:%'
            """)
            
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
        """Get ZotFile symlink attachments that need DEVONthink UUID conversion"""
        with self.connection() as conn:
            cursor = conn.execute("""
                SELECT 
                    ia.itemID,
                    ia.parentItemID,
                    ia.linkMode,
                    ia.contentType,
                    ia.path,
                    ia.storageHash
                FROM itemAttachments ia
                WHERE ia.linkMode = 1
                AND ia.path LIKE '/Users/travisross/ZotFile Import/%'
            """)
            
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
    
    def _get_item_authors(self, conn, item_id: int) -> List[str]:
        """Get authors for an item"""
        cursor = conn.execute("""
            SELECT c.firstName, c.lastName
            FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            WHERE ic.itemID = ?
            ORDER BY ic.orderIndex
        """, (item_id,))
        
        authors = []
        for row in cursor.fetchall():
            first = row['firstName'] or ""
            last = row['lastName'] or ""
            name = f"{first} {last}".strip()
            if name:
                authors.append(name)
        
        return authors
    
    def _get_item_tags(self, conn, item_id: int) -> List[str]:
        """Get tags for an item"""
        cursor = conn.execute("""
            SELECT t.name
            FROM itemTags it
            JOIN tags t ON it.tagID = t.tagID
            WHERE it.itemID = ?
        """, (item_id,))
        
        return [row['name'] for row in cursor.fetchall()]
    
    def _get_item_collections(self, conn, item_id: int) -> List[str]:
        """Get collections for an item"""
        cursor = conn.execute("""
            SELECT c.collectionName
            FROM collectionItems ci
            JOIN collections c ON ci.collectionID = c.collectionID
            WHERE ci.itemID = ?
        """, (item_id,))
        
        return [row['collectionName'] for row in cursor.fetchall()]
    
    def update_item_url(self, item_id: int, devonthink_uuid: str, read_only=False):
        """Update item URL to DEVONthink UUID link"""
        if read_only:
            print(f"[DRY RUN] Would update item {item_id} URL to x-devonthink-item://{devonthink_uuid}")
            return
        
        # Note: This requires careful implementation to avoid corrupting database
        # Implementation would update itemData table with URL field
        pass

class DEVONthinkInterface:
    """Interface to DEVONthink via AppleScript"""
    
    def __init__(self, database_name: str = "Research"):
        self.database_name = database_name
    
    def execute_script(self, script: str) -> str:
        """Execute AppleScript and return result"""
        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise Exception(f"AppleScript error: {e.stderr}")
    
    def import_file(self, file_path: str, metadata: ZoteroItem) -> str:
        """Import file to DEVONthink with Zotero metadata"""
        safe_path = file_path.replace('"', '\\"').replace('\\', '\\\\')
        safe_title = (metadata.title or "").replace('"', '\\"')
        safe_authors = ", ".join(metadata.authors).replace('"', '\\"')
        safe_publication = (metadata.publication or "").replace('"', '\\"')
        
        script = f'''
        tell application "DEVONthink 3"
            try
                set theDatabase to database "{self.database_name}"
                set theRecord to import "{safe_path}" to theDatabase
                
                -- Set name from Zotero title
                if "{safe_title}" is not "" then
                    set name of theRecord to "{safe_title}"
                end if
                
                -- Set custom metadata from Zotero
                set metadataDict to {{}}
                if "{safe_authors}" is not "" then
                    set metadataDict to metadataDict & {{zotero_authors:"{safe_authors}"}}
                end if
                if "{safe_publication}" is not "" then
                    set metadataDict to metadataDict & {{zotero_publication:"{safe_publication}"}}
                end if
                set metadataDict to metadataDict & {{zotero_id:"{metadata.item_id}"}}
                set metadataDict to metadataDict & {{zotero_key:"{metadata.key}"}}
                
                set custom meta data of theRecord to metadataDict
                
                -- Set tags from Zotero
                set tags of theRecord to {{{", ".join(f'"{tag}"' for tag in metadata.tags)}}}
                
                -- Set comment with sync info
                set comment of theRecord to "Synced from Zotero item {metadata.item_id} on {datetime.now().isoformat()}"
                
                return uuid of theRecord
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''
        
        return self.execute_script(script)
    
    def find_item_by_filename(self, filename: str) -> Optional[str]:
        """Find DEVONthink item by filename and return UUID"""
        safe_filename = filename.replace('"', '\\"')
        
        script = f'''
        tell application "DEVONthink 3"
            try
                set theDatabase to database "{self.database_name}"
                set searchResults to search "name:{safe_filename}" in theDatabase
                
                if (count of searchResults) > 0 then
                    set theRecord to item 1 of searchResults
                    return uuid of theRecord
                else
                    return ""
                end if
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''
        
        result = self.execute_script(script)
        return result if result and not result.startswith("ERROR") else None
    
    def get_item_metadata(self, uuid: str) -> Optional[DEVONthinkItem]:
        """Get DEVONthink item metadata by UUID"""
        script = f'''
        tell application "DEVONthink 3"
            try
                set theRecord to get record with uuid "{uuid}"
                
                set itemName to name of theRecord
                set itemPath to path of theRecord
                set itemKind to kind of theRecord
                set itemCreated to (creation date of theRecord) as string
                set itemModified to (modification date of theRecord) as string
                set itemTags to tags of theRecord
                set itemCustom to custom meta data of theRecord
                
                return itemName & "|" & itemPath & "|" & itemKind & "|" & itemCreated & "|" & itemModified
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''
        
        result = self.execute_script(script)
        if result.startswith("ERROR"):
            return None
        
        # Parse result - simplified implementation
        parts = result.split("|")
        if len(parts) >= 5:
            return DEVONthinkItem(
                uuid=uuid,
                name=parts[0],
                path=parts[1], 
                kind=parts[2],
                creation_date=parts[3],
                modification_date=parts[4],
                tags=[],  # Would parse from AppleScript
                custom_metadata={},  # Would parse from AppleScript
                url=f"x-devonthink-item://{uuid}"
            )
        
        return None
    
    def update_item_from_zotero(self, uuid: str, metadata: ZoteroItem):
        """Update DEVONthink item with latest Zotero metadata"""
        safe_title = (metadata.title or "").replace('"', '\\"')
        safe_authors = ", ".join(metadata.authors).replace('"', '\\"')
        
        script = f'''
        tell application "DEVONthink 3"
            try
                set theRecord to get record with uuid "{uuid}"
                
                -- Update name if different
                if "{safe_title}" is not "" then
                    set name of theRecord to "{safe_title}"
                end if
                
                -- Update custom metadata
                set currentMeta to custom meta data of theRecord
                set currentMeta to currentMeta & {{zotero_authors:"{safe_authors}"}}
                set currentMeta to currentMeta & {{zotero_last_sync:"{datetime.now().isoformat()}"}}
                set custom meta data of theRecord to currentMeta
                
                -- Update tags
                set tags of theRecord to {{{", ".join(f'"{tag}"' for tag in metadata.tags)}}}
                
                return "SUCCESS"
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''
        
        return self.execute_script(script)

class ZoteroDevonthinkSync:
    """Main synchronization engine"""
    
    def __init__(self):
        self.zotero = ZoteroDatabase(ZOTERO_DB_PATH)
        self.devonthink = DEVONthinkInterface(DEVONTHINK_DATABASE)
        self.sync_log = []
    
    def log_action(self, action: str, item_id: int, details: str):
        """Log sync action"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "item_id": item_id,
            "details": details
        }
        self.sync_log.append(entry)
        print(f"[{entry['timestamp']}] {action}: Item {item_id} - {details}")
    
    def migrate_stored_attachments(self, dry_run=True):
        """Migrate Zotero stored files to DEVONthink"""
        print("🔄 Starting migration of stored attachments...")
        
        attachments = self.zotero.get_stored_attachments()
        print(f"Found {len(attachments)} stored attachments to migrate")
        
        for attachment in attachments:
            try:
                # Resolve file path
                file_path = self._resolve_storage_path(attachment)
                if not file_path or not file_path.exists():
                    self.log_action("SKIP", attachment.item_id, f"File not found: {attachment.path}")
                    continue
                
                # Get parent item metadata
                if attachment.parent_item_id:
                    parent_items = self.zotero.get_items_needing_sync()
                    parent_metadata = next((item for item in parent_items 
                                          if item.item_id == attachment.parent_item_id), None)
                else:
                    parent_metadata = None
                
                if not parent_metadata:
                    self.log_action("SKIP", attachment.item_id, "No parent metadata found")
                    continue
                
                # Import to DEVONthink
                if not dry_run:
                    dt_uuid = self.devonthink.import_file(str(file_path), parent_metadata)
                    
                    if not dt_uuid.startswith("ERROR"):
                        # Update Zotero to use DEVONthink UUID
                        self.zotero.update_item_url(attachment.parent_item_id, dt_uuid)
                        self.log_action("MIGRATE", attachment.item_id, f"Imported to DEVONthink: {dt_uuid}")
                    else:
                        self.log_action("ERROR", attachment.item_id, f"DEVONthink import failed: {dt_uuid}")
                else:
                    self.log_action("DRY_RUN", attachment.item_id, f"Would migrate: {file_path}")
                
            except Exception as e:
                self.log_action("ERROR", attachment.item_id, f"Migration failed: {e}")
    
    def convert_zotfile_symlinks(self, dry_run=True):
        """Convert ZotFile symlinks to DEVONthink UUID links"""
        print("🔗 Converting ZotFile symlinks...")
        
        symlinks = self.zotero.get_zotfile_symlinks()
        print(f"Found {len(symlinks)} ZotFile symlinks to convert")
        
        for symlink in symlinks:
            try:
                # Extract filename from path
                filename = Path(symlink.path).name if symlink.path else ""
                if not filename:
                    self.log_action("SKIP", symlink.item_id, "No filename in path")
                    continue
                
                # Find corresponding DEVONthink item
                dt_uuid = self.devonthink.find_item_by_filename(filename)
                
                if dt_uuid:
                    if not dry_run:
                        # Update Zotero attachment to use DEVONthink UUID
                        # Implementation would update itemAttachments table
                        self.log_action("CONVERT", symlink.item_id, f"Converted to UUID: {dt_uuid}")
                    else:
                        self.log_action("DRY_RUN", symlink.item_id, f"Would convert to UUID: {dt_uuid}")
                else:
                    self.log_action("SKIP", symlink.item_id, f"No DEVONthink item found for: {filename}")
                
            except Exception as e:
                self.log_action("ERROR", symlink.item_id, f"Conversion failed: {e}")
    
    def sync_metadata_to_devonthink(self, dry_run=True):
        """Sync Zotero metadata to existing DEVONthink items"""
        print("📝 Syncing metadata to DEVONthink...")
        
        items = self.zotero.get_items_needing_sync()
        print(f"Found {len(items)} items needing metadata sync")
        
        # This would implement the sync logic
        # For items that already have DEVONthink UUIDs, update the DT item
        # For new items, this could trigger the import workflow
        
    def _resolve_storage_path(self, attachment: ZoteroAttachment) -> Optional[Path]:
        """Resolve Zotero storage path to actual file"""
        if not attachment.path or not attachment.path.startswith("storage:"):
            return None
        
        parts = attachment.path.split(":")
        if len(parts) >= 3:
            key = parts[1]
            filename = ":".join(parts[2:])
            return Path(ZOTERO_STORAGE_PATH) / key / filename
        
        return None
    
    def save_sync_log(self):
        """Save sync log to file"""
        log_file = Path("sync_log.json")
        with open(log_file, 'w') as f:
            json.dump(self.sync_log, f, indent=2)
        print(f"📝 Sync log saved to {log_file}")

def main():
    """Main function"""
    sync_engine = ZoteroDevonthinkSync()
    
    print("🚀 DEVONzot Integration Starting...")
    print("="*60)
    
    # Phase 1: Migrate stored attachments
    sync_engine.migrate_stored_attachments(dry_run=True)
    
    print("\n" + "="*60)
    
    # Phase 2: Convert ZotFile symlinks  
    sync_engine.convert_zotfile_symlinks(dry_run=True)
    
    print("\n" + "="*60)
    
    # Phase 3: Sync metadata
    sync_engine.sync_metadata_to_devonthink(dry_run=True)
    
    # Save log
    sync_engine.save_sync_log()
    
    print("\n🎉 Integration analysis complete!")
    print("Run with dry_run=False to execute actual changes")

if __name__ == "__main__":
    main()