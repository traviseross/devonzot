#!/usr/bin/env python3
"""
Search for DEVONthink links in Zotero database
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

def search_devonthink_links():
    """Search for DEVONthink links in attachments"""
    print("🔍 Searching for DEVONthink links in Zotero attachments...")
    
    with safe_zotero_connection(ZOTERO_DB_PATH) as conn:
        # Search for x-devonthink-item:// links
        cursor = conn.execute("""
            SELECT 
                ia.itemID,
                ia.parentItemID,
                ia.linkMode,
                ia.contentType,
                ia.path,
                i.dateAdded,
                i.dateModified
            FROM itemAttachments ia
            JOIN items i ON ia.itemID = i.itemID
            WHERE ia.path LIKE 'x-devonthink-item://%'
            ORDER BY i.dateModified DESC
        """)
        
        devonthink_links = cursor.fetchall()
        
        print(f"\n📊 Found {len(devonthink_links)} DEVONthink links")
        
        if devonthink_links:
            print("\n" + "="*80)
            print("DEVONTHINK LINKS IN ZOTERO")
            print("="*80)
            
            # Get parent item titles for context
            for link in devonthink_links[:20]:  # Show first 20
                att_id = link['itemID']
                parent_id = link['parentItemID']
                path = link['path']
                date_added = link['dateAdded'][:19] if link['dateAdded'] else "No Date"
                date_modified = link['dateModified'][:19] if link['dateModified'] else "No Date"
                
                # Extract UUID from path
                uuid = path.replace('x-devonthink-item://', '') if path else "No UUID"
                
                # Get parent item title if exists
                title = "No Title"
                if parent_id:
                    title_cursor = conn.execute("""
                        SELECT 
                            GROUP_CONCAT(
                                CASE WHEN f.fieldName = 'title' THEN idv.value END
                            ) as title
                        FROM itemData id
                        JOIN fields f ON id.fieldID = f.fieldID
                        JOIN itemDataValues idv ON id.valueID = idv.valueID
                        WHERE id.itemID = ?
                    """, (parent_id,))
                    
                    title_result = title_cursor.fetchone()
                    if title_result and title_result['title']:
                        title = title_result['title']
                
                print(f"\n📎 Attachment {att_id}:")
                if parent_id:
                    print(f"    Parent: {parent_id} - {title[:60]}{'...' if len(title) > 60 else ''}")
                else:
                    print(f"    Standalone attachment")
                print(f"    UUID: {uuid}")
                print(f"    Added: {date_added}")
                print(f"    Modified: {date_modified}")
            
            if len(devonthink_links) > 20:
                print(f"\n... and {len(devonthink_links) - 20} more")
        
        # Also search for any other patterns that might be DEVONthink links
        cursor = conn.execute("""
            SELECT COUNT(*) as count
            FROM itemAttachments
            WHERE path LIKE '%devonthink%'
            AND path NOT LIKE 'x-devonthink-item://%'
        """)
        
        other_dt_count = cursor.fetchone()['count']
        if other_dt_count > 0:
            print(f"\n📋 Found {other_dt_count} other potential DEVONthink references")
            
            cursor = conn.execute("""
                SELECT path
                FROM itemAttachments
                WHERE path LIKE '%devonthink%'
                AND path NOT LIKE 'x-devonthink-item://%'
                LIMIT 10
            """)
            
            other_paths = cursor.fetchall()
            for path_row in other_paths:
                print(f"    {path_row['path']}")

        # Check link mode distribution for DEVONthink links
        cursor = conn.execute("""
            SELECT linkMode, COUNT(*) as count
            FROM itemAttachments
            WHERE path LIKE 'x-devonthink-item://%'
            GROUP BY linkMode
        """)
        
        link_modes = cursor.fetchall()
        if link_modes:
            print(f"\n📊 Link Mode Distribution for DEVONthink links:")
            mode_names = {0: "Stored", 1: "Linked", 2: "Web Link", 3: "Linked (relative)"}
            for mode in link_modes:
                mode_name = mode_names.get(mode['linkMode'], f"Unknown({mode['linkMode']})")
                print(f"    {mode_name}: {mode['count']}")

def main():
    """Main function"""
    if not os.path.exists(ZOTERO_DB_PATH):
        print(f"❌ Database not found: {ZOTERO_DB_PATH}")
        return
    
    try:
        search_devonthink_links()
        print(f"\n✅ Search complete!")
        
    except Exception as e:
        print(f"❌ Failed to search database: {e}")

if __name__ == "__main__":
    main()