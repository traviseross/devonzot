#!/usr/bin/env python3
"""
Search for DEVONthink UUID links in URL fields
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

def search_devonthink_uuid_links():
    """Search for DEVONthink UUID links in URL fields"""
    print("🔍 Searching for DEVONthink UUID links in item URL fields...")
    
    with safe_zotero_connection(ZOTERO_DB_PATH) as conn:
        # Search for x-devonthink-item:// links in URL fields
        cursor = conn.execute("""
            SELECT 
                i.itemID,
                i.dateAdded,
                i.dateModified,
                i.key,
                GROUP_CONCAT(
                    CASE WHEN f.fieldName = 'title' THEN idv.value END
                ) as title,
                GROUP_CONCAT(
                    CASE WHEN f.fieldName = 'url' THEN idv.value END
                ) as url,
                GROUP_CONCAT(
                    CASE WHEN f.fieldName = 'publicationTitle' THEN idv.value END
                ) as publication,
                GROUP_CONCAT(
                    CASE WHEN f.fieldName = 'date' THEN idv.value END
                ) as date
            FROM items i
            JOIN itemData id ON i.itemID = id.itemID
            JOIN fields f ON id.fieldID = f.fieldID  
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE f.fieldName = 'url' 
            AND idv.value LIKE 'x-devonthink-item://%'
            GROUP BY i.itemID
            ORDER BY i.dateModified DESC
        """)
        
        uuid_links = cursor.fetchall()
        
        print(f"\n📊 Found {len(uuid_links)} items with DEVONthink UUID links")
        
        if uuid_links:
            print("\n" + "="*80)
            print("DEVONTHINK UUID LINKS IN ZOTERO")
            print("="*80)
            
            for item in uuid_links:
                item_id = item['itemID']
                title = item['title'] or "No Title"
                url = item['url']
                publication = item['publication'] or ""
                date = item['date'] or ""
                key = item['key']
                date_added = item['dateAdded'][:19] if item['dateAdded'] else "No Date"
                date_modified = item['dateModified'][:19] if item['dateModified'] else "No Date"
                
                # Extract UUID from URL
                uuid = url.replace('x-devonthink-item://', '') if url else "No UUID"
                
                print(f"\n📄 Item {item_id} (Key: {key}):")
                print(f"    Title: {title[:70]}{'...' if len(title) > 70 else ''}")
                if publication:
                    print(f"    Publication: {publication}")
                if date:
                    print(f"    Date: {date}")
                print(f"    UUID: {uuid}")
                print(f"    Added: {date_added}")
                print(f"    Modified: {date_modified}")
                
                # Check if this item has attachments
                att_cursor = conn.execute("""
                    SELECT COUNT(*) as att_count
                    FROM itemAttachments
                    WHERE parentItemID = ?
                """, (item_id,))
                
                att_count = att_cursor.fetchone()['att_count']
                if att_count > 0:
                    print(f"    Attachments: {att_count}")

        # Also search for any items that might have both web URLs and were later converted
        print(f"\n" + "="*80)
        print("WORKFLOW ANALYSIS")
        print("="*80)
        
        # Find items with similar titles but different URLs (web vs DEVONthink)
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as total_items_with_urls
            FROM items i
            JOIN itemData id ON i.itemID = id.itemID
            JOIN fields f ON id.fieldID = f.fieldID  
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE f.fieldName = 'url'
        """)
        
        total_with_urls = cursor.fetchone()['total_items_with_urls']
        
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as web_urls
            FROM items i
            JOIN itemData id ON i.itemID = id.itemID
            JOIN fields f ON id.fieldID = f.fieldID  
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE f.fieldName = 'url' 
            AND idv.value LIKE 'http%'
        """)
        
        web_urls = cursor.fetchone()['web_urls']
        
        devonthink_urls = len(uuid_links)
        
        print(f"Total items with URLs: {total_with_urls}")
        print(f"Web URLs (http/https): {web_urls}")  
        print(f"DEVONthink UUIDs: {devonthink_urls}")
        print(f"Other URL schemes: {total_with_urls - web_urls - devonthink_urls}")
        
        # Show recent conversion pattern
        if len(uuid_links) >= 5:
            print(f"\nRecent DEVONthink conversions (last 5):")
            for item in uuid_links[:5]:
                title = item['title'] or "No Title"
                title = title[:50] + "..." if len(title) > 50 else title
                date = item['dateModified'][:10] if item['dateModified'] else "No Date"
                print(f"  {date}: {title}")

def main():
    """Main function"""
    if not os.path.exists(ZOTERO_DB_PATH):
        print(f"❌ Database not found: {ZOTERO_DB_PATH}")
        return
    
    try:
        search_devonthink_uuid_links()
        print(f"\n✅ Search complete!")
        
    except Exception as e:
        print(f"❌ Failed to search database: {e}")

if __name__ == "__main__":
    main()