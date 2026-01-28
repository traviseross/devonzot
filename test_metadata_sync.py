#!/usr/bin/env python3
"""
Test metadata sync for existing DEVONthink UUID links
"""

import sqlite3
import subprocess
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional
import json

ZOTERO_DB_PATH = "/Users/travisross/Zotero/zotero.sqlite"
DEVONTHINK_DATABASE = "Research"

@dataclass
class ZoteroItemWithUUID:
    item_id: int
    key: str
    title: str
    authors: List[str]
    publication: Optional[str]
    date: Optional[str]
    doi: Optional[str]
    uuid_url: str
    uuid: str
    tags: List[str]
    date_added: str
    date_modified: str

@contextmanager
def safe_zotero_connection(db_path: str):
    """Safe database connection"""
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        yield conn
    finally:
        if conn:
            conn.close()

def execute_applescript(script: str) -> str:
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
        return f"ERROR: {e.stderr}"

def get_zotero_items_with_uuids(limit=5) -> List[ZoteroItemWithUUID]:
    """Get Zotero items that already have DEVONthink UUID links"""
    with safe_zotero_connection(ZOTERO_DB_PATH) as conn:
        # Get items with x-devonthink-item URLs
        cursor = conn.execute("""
            SELECT 
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
            JOIN itemData id ON i.itemID = id.itemID
            JOIN fields f ON id.fieldID = f.fieldID  
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE f.fieldName = 'url' 
            AND idv.value LIKE 'x-devonthink-item://%'
            GROUP BY i.itemID
            ORDER BY i.dateModified DESC
            LIMIT ?
        """, (limit,))
        
        items = []
        for row in cursor.fetchall():
            item_id = row['itemID']
            url = row['url']
            uuid = url.replace('x-devonthink-item://', '') if url else ""
            
            # Get authors
            authors_cursor = conn.execute("""
                SELECT c.firstName, c.lastName
                FROM itemCreators ic
                JOIN creators c ON ic.creatorID = c.creatorID
                WHERE ic.itemID = ?
                ORDER BY ic.orderIndex
            """, (item_id,))
            
            authors = []
            for author_row in authors_cursor.fetchall():
                first = author_row['firstName'] or ""
                last = author_row['lastName'] or ""
                name = f"{first} {last}".strip()
                if name:
                    authors.append(name)
            
            # Get tags
            tags_cursor = conn.execute("""
                SELECT t.name
                FROM itemTags it
                JOIN tags t ON it.tagID = t.tagID
                WHERE it.itemID = ?
            """, (item_id,))
            
            tags = [tag_row['name'] for tag_row in tags_cursor.fetchall()]
            
            items.append(ZoteroItemWithUUID(
                item_id=item_id,
                key=row['key'],
                title=row['title'] or "No Title",
                authors=authors,
                publication=row['publication'],
                date=row['date'],
                doi=row['doi'],
                uuid_url=url,
                uuid=uuid,
                tags=tags,
                date_added=row['dateAdded'],
                date_modified=row['dateModified']
            ))
        
        return items

def update_devonthink_metadata(item: ZoteroItemWithUUID, dry_run=False) -> str:
    """Update DEVONthink item with Zotero metadata"""
    
    # Escape special characters for AppleScript
    safe_title = (item.title or "").replace('"', '\\"').replace('\\', '\\\\')
    safe_authors = ", ".join(item.authors).replace('"', '\\"')
    safe_publication = (item.publication or "").replace('"', '\\"')
    safe_date = (item.date or "").replace('"', '\\"')
    safe_doi = (item.doi or "").replace('"', '\\"')
    
    if dry_run:
        print(f"[DRY RUN] Would update DEVONthink item {item.uuid}")
        print(f"  Title: {safe_title}")
        print(f"  Authors: {safe_authors}")
        print(f"  Publication: {safe_publication}")
        print(f"  Tags: {', '.join(item.tags)}")
        return "DRY_RUN_SUCCESS"
    
    # Build tag list for AppleScript
    tags_applescript = "{" + ", ".join(f'"{tag.replace('"', '\\"')}"' for tag in item.tags) + "}"
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{item.uuid}"
            
            -- Update name if we have a better title
            if "{safe_title}" is not "" and "{safe_title}" is not "No Title" then
                set name of theRecord to "{safe_title}"
            end if
            
            -- Update custom metadata
            if "{safe_authors}" is not "" then
                set custom meta data of theRecord to (custom meta data of theRecord) & {{zotero_authors:"{safe_authors}"}}
            end if
            if "{safe_publication}" is not "" then
                set custom meta data of theRecord to (custom meta data of theRecord) & {{zotero_publication:"{safe_publication}"}}
            end if
            if "{safe_date}" is not "" then
                set custom meta data of theRecord to (custom meta data of theRecord) & {{zotero_date:"{safe_date}"}}
            end if
            if "{safe_doi}" is not "" then
                set custom meta data of theRecord to (custom meta data of theRecord) & {{zotero_doi:"{safe_doi}"}}
            end if
            set custom meta data of theRecord to (custom meta data of theRecord) & {{zotero_id:"{item.item_id}"}}
            set custom meta data of theRecord to (custom meta data of theRecord) & {{zotero_key:"{item.key}"}}
            set custom meta data of theRecord to (custom meta data of theRecord) & {{zotero_last_sync:"2026-01-27T18:50:00"}}
            
            -- Update tags from Zotero
            set tags of theRecord to {tags_applescript}
            
            -- Get current item name for confirmation
            set itemName to name of theRecord
            
            return "SUCCESS: " & itemName
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def get_devonthink_item_info(uuid: str) -> dict:
    """Get current DEVONthink item information"""
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            set itemName to name of theRecord
            set itemKind to kind of theRecord
            set itemTags to tags of theRecord
            set itemURL to "x-devonthink-item://" & uuid of theRecord
            
            -- Convert tags to string
            set tagString to ""
            repeat with aTag in itemTags
                set tagString to tagString & aTag & ", "
            end repeat
            
            return itemName & "|" & itemKind & "|" & tagString & "|" & itemURL
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    result = execute_applescript(script)
    if result.startswith("ERROR"):
        return {"error": result}
    
    parts = result.split("|")
    if len(parts) >= 4:
        return {
            "name": parts[0],
            "kind": parts[1],
            "tags": parts[2].strip(", ").split(", ") if parts[2].strip(", ") else [],
            "url": parts[3]
        }
    
    return {"error": "Could not parse result"}

def main():
    """Test metadata sync"""
    print("🧪 Testing Metadata Sync for DEVONthink UUID Items")
    print("=" * 60)
    
    # Get items with existing UUID links
    print("📋 Getting Zotero items with DEVONthink UUID links...")
    items = get_zotero_items_with_uuids(limit=5)
    
    if not items:
        print("❌ No items found with DEVONthink UUID links")
        return
    
    print(f"✅ Found {len(items)} items with UUID links\n")
    
    # Show what we found and sync metadata
    for i, item in enumerate(items, 1):
        print(f"📄 Item {i}: {item.title}")
        print(f"   ID: {item.item_id} | Key: {item.key}")
        print(f"   Authors: {', '.join(item.authors) if item.authors else 'None'}")
        print(f"   Publication: {item.publication or 'None'}")
        print(f"   Date: {item.date or 'None'}")
        print(f"   DOI: {item.doi or 'None'}")
        print(f"   Tags: {', '.join(item.tags) if item.tags else 'None'}")
        print(f"   UUID: {item.uuid}")
        print(f"   Link: {item.uuid_url}")
        
        # Get current DEVONthink info
        print(f"\n   🔍 Current DEVONthink item info:")
        dt_info = get_devonthink_item_info(item.uuid)
        if "error" in dt_info:
            print(f"   ❌ Error: {dt_info['error']}")
        else:
            print(f"   Name: {dt_info['name']}")
            print(f"   Kind: {dt_info['kind']}")
            print(f"   Tags: {', '.join(dt_info['tags']) if dt_info['tags'] else 'None'}")
        
        # Ask if user wants to sync this item
        print(f"\n   🔄 Sync metadata to DEVONthink?")
        choice = input(f"   [y/N/q]: ").strip().lower()
        
        if choice == 'q':
            print("   ⏹️  Stopping sync test")
            break
        elif choice == 'y':
            print(f"   🚀 Syncing metadata...")
            result = update_devonthink_metadata(item, dry_run=False)
            print(f"   📝 Result: {result}")
            
            # Show updated info
            if not result.startswith("ERROR"):
                print(f"   ✅ Updated! Getting new DEVONthink info...")
                updated_info = get_devonthink_item_info(item.uuid)
                if "error" not in updated_info:
                    print(f"   New name: {updated_info['name']}")
                    print(f"   New tags: {', '.join(updated_info['tags']) if updated_info['tags'] else 'None'}")
        else:
            print(f"   ⏭️  Skipping")
        
        print("\n" + "-" * 60 + "\n")
    
    print("🎉 Test complete!")
    print(f"\nTo open any of these items in DEVONthink, click their x-devonthink-item:// links above")

if __name__ == "__main__":
    main()