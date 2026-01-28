#!/usr/bin/env python3
"""
Search for specific item in Zotero database
"""

import sqlite3
import os
from pathlib import Path
from contextlib import contextmanager

ZOTERO_DB_PATH = "/Users/travisross/Zotero/zotero.sqlite"

@contextmanager
def safe_zotero_connection(db_path: str, timeout: int = 30):
    """Safely connect to Zotero database"""
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        yield conn
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            print(f"❌ Database locked - Zotero may be running")
        raise e
    finally:
        if conn:
            conn.close()

def search_specific_item(search_title="How Black is our Market", search_author="Henderson"):
    """Search for specific item by title and author"""
    print(f"🔍 Searching for: '{search_title}' by {search_author}")
    
    with safe_zotero_connection(ZOTERO_DB_PATH) as conn:
        # Search for items with matching title
        cursor = conn.execute("""
            SELECT DISTINCT
                i.itemID,
                i.dateAdded,
                i.dateModified,
                i.key,
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
                    CASE WHEN f.fieldName = 'url' THEN idv.value END
                ) as url
            FROM items i
            LEFT JOIN itemData id ON i.itemID = id.itemID
            LEFT JOIN fields f ON id.fieldID = f.fieldID
            LEFT JOIN itemDataValues idv ON id.valueID = idv.valueID
            GROUP BY i.itemID
            HAVING title LIKE ?
            ORDER BY i.dateModified DESC
        """, (f"%{search_title}%",))
        
        matching_items = cursor.fetchall()
        
        if not matching_items:
            print(f"❌ No items found matching '{search_title}'")
            return
        
        print(f"\n📊 Found {len(matching_items)} matching items")
        
        for item in matching_items:
            item_id = item['itemID']
            title = item['title'] or "No Title"
            publication = item['publication'] or ""
            date = item['date'] or ""
            url = item['url'] or ""
            key = item['key']
            date_added = item['dateAdded'][:19] if item['dateAdded'] else "No Date"
            date_modified = item['dateModified'][:19] if item['dateModified'] else "No Date"
            
            print(f"\n" + "="*80)
            print(f"📄 ITEM {item_id} (Key: {key})")
            print("="*80)
            print(f"Title: {title}")
            if publication:
                print(f"Publication: {publication}")
            if date:
                print(f"Date: {date}")
            if url:
                print(f"URL: {url}")
            print(f"Added: {date_added}")
            print(f"Modified: {date_modified}")
            
            # Get all creators (authors)
            creators_cursor = conn.execute("""
                SELECT 
                    c.firstName,
                    c.lastName,
                    ct.creatorType
                FROM itemCreators ic
                JOIN creators c ON ic.creatorID = c.creatorID
                JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
                WHERE ic.itemID = ?
                ORDER BY ic.orderIndex
            """, (item_id,))
            
            creators = creators_cursor.fetchall()
            if creators:
                print(f"\nAuthors:")
                for creator in creators:
                    first_name = creator['firstName'] or ""
                    last_name = creator['lastName'] or ""
                    creator_type = creator['creatorType']
                    full_name = f"{first_name} {last_name}".strip()
                    print(f"  • {full_name} ({creator_type})")
            
            # Get all field data for this item
            fields_cursor = conn.execute("""
                SELECT 
                    f.fieldName,
                    idv.value
                FROM itemData id
                JOIN fields f ON id.fieldID = f.fieldID
                JOIN itemDataValues idv ON id.valueID = idv.valueID
                WHERE id.itemID = ?
                ORDER BY f.fieldName
            """, (item_id,))
            
            fields = fields_cursor.fetchall()
            if fields:
                print(f"\nAll Fields:")
                for field in fields:
                    field_name = field['fieldName']
                    value = field['value']
                    # Truncate very long values
                    if len(value) > 100:
                        value = value[:97] + "..."
                    print(f"  {field_name}: {value}")
            
            # Get all attachments for this item
            attachments_cursor = conn.execute("""
                SELECT 
                    ia.itemID as attachmentID,
                    ia.linkMode,
                    ia.contentType,
                    ia.path,
                    ia.storageHash,
                    i.dateAdded as att_dateAdded,
                    GROUP_CONCAT(
                        CASE WHEN f.fieldName = 'title' THEN idv.value END
                    ) as att_title
                FROM itemAttachments ia
                JOIN items i ON ia.itemID = i.itemID
                LEFT JOIN itemData id ON ia.itemID = id.itemID
                LEFT JOIN fields f ON id.fieldID = f.fieldID
                LEFT JOIN itemDataValues idv ON id.valueID = idv.valueID
                WHERE ia.parentItemID = ?
                GROUP BY ia.itemID
                ORDER BY i.dateAdded DESC
            """, (item_id,))
            
            attachments = attachments_cursor.fetchall()
            
            if attachments:
                print(f"\n📎 ATTACHMENTS ({len(attachments)}):")
                link_modes = {0: "Stored", 1: "Linked", 2: "Web Link", 3: "Linked (relative)"}
                
                for att in attachments:
                    att_id = att['attachmentID']
                    link_mode = link_modes.get(att['linkMode'], f"Unknown({att['linkMode']})")
                    content_type = att['contentType'] or "Unknown"
                    path = att['path'] or "No path"
                    att_title = att['att_title'] or "No title"
                    att_date = att['att_dateAdded'][:19] if att['att_dateAdded'] else "No Date"
                    storage_hash = att['storageHash'] or "No hash"
                    
                    print(f"\n  📎 Attachment {att_id}:")
                    print(f"     Title: {att_title}")
                    print(f"     Type: {link_mode}")
                    print(f"     Content: {content_type}")
                    print(f"     Added: {att_date}")
                    print(f"     Storage Hash: {storage_hash}")
                    print(f"     Path: {path}")
                    
                    # Check if file exists for linked files
                    if att['linkMode'] == 1 and path and not path.startswith('x-devonthink'):
                        exists = "✅" if os.path.exists(path) else "❌"
                        print(f"     File Exists: {exists}")
                    elif att['linkMode'] == 0 and path and path.startswith("storage:"):
                        # Check stored file
                        parts = path.split(":")
                        if len(parts) >= 3:
                            key = parts[1]
                            filename = ":".join(parts[2:])
                            storage_path = Path("/Users/travisross/Zotero/storage") / key / filename
                            exists = "✅" if storage_path.exists() else "❌"
                            print(f"     Storage Exists: {exists} - {storage_path}")

def main():
    """Main function"""
    if not os.path.exists(ZOTERO_DB_PATH):
        print(f"❌ Database not found: {ZOTERO_DB_PATH}")
        return
    
    try:
        search_specific_item("How Black is our Market", "Henderson")
        print(f"\n✅ Search complete!")
        
    except Exception as e:
        print(f"❌ Failed to search database: {e}")

if __name__ == "__main__":
    main()