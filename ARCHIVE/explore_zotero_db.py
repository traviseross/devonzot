#!/usr/bin/env python3
"""
Zotero Database Explorer
Safe read-only exploration of Zotero's SQLite database
"""

import sqlite3
import os
import psutil
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import json

# Configuration
ZOTERO_DB_PATH = "/Users/travisross/Zotero/zotero.sqlite"

@contextmanager
def safe_zotero_connection(db_path: str, timeout: int = 30):
    """Safely connect to Zotero database with checks"""
    conn = None
    try:
        # Check if journal file exists (indicates Zotero is writing)
        journal_path = f"{db_path}-journal"
        if os.path.exists(journal_path):
            print(f"⚠️  Warning: Journal file exists - Zotero may be running")
            print(f"   Journal: {journal_path}")
        
        # Check if Zotero process is running
        zotero_running = any('zotero' in proc.name().lower() 
                           for proc in psutil.process_iter(['name']) 
                           if proc.info['name'])
        
        if zotero_running:
            print("⚠️  Warning: Zotero process detected running")
        
        # Connect with timeout
        conn = sqlite3.connect(db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        
        # Enable read-only mode for safety
        conn.execute("PRAGMA query_only = ON")
        
        print(f"✅ Connected to database: {db_path}")
        yield conn
        
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            print(f"❌ Database locked - Zotero is likely running")
        else:
            print(f"❌ Database error: {e}")
        raise e
    finally:
        if conn:
            conn.close()

def explore_database_schema(conn):
    """Explore the database schema"""
    print("\n" + "="*60)
    print("DATABASE SCHEMA EXPLORATION")
    print("="*60)
    
    # Get all tables
    cursor = conn.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' 
        ORDER BY name
    """)
    
    tables = [row[0] for row in cursor.fetchall()]
    print(f"\nFound {len(tables)} tables:")
    
    for table in tables:
        print(f"  • {table}")
    
    # Show key table structures
    key_tables = ['items', 'itemAttachments', 'itemData', 'collections', 'tags']
    
    for table in key_tables:
        if table in tables:
            print(f"\n📋 Structure of '{table}':")
            cursor = conn.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()
            
            for col in columns:
                pk_indicator = " (PRIMARY KEY)" if col[5] else ""
                not_null = " NOT NULL" if col[3] else ""
                default = f" DEFAULT {col[4]}" if col[4] else ""
                print(f"    {col[1]:<20} {col[2]:<15}{pk_indicator}{not_null}{default}")

def explore_basic_stats(conn):
    """Get basic statistics about the database"""
    print("\n" + "="*60)
    print("DATABASE STATISTICS")
    print("="*60)
    
    stats_queries = {
        "Total Items": "SELECT COUNT(*) FROM items",
        "Items with Attachments": """
            SELECT COUNT(DISTINCT parentItemID) 
            FROM itemAttachments 
            WHERE parentItemID IS NOT NULL
        """,
        "Total Attachments": "SELECT COUNT(*) FROM itemAttachments",
        "Stored Files (linkMode=0)": "SELECT COUNT(*) FROM itemAttachments WHERE linkMode = 0",
        "Linked Files (linkMode=1)": "SELECT COUNT(*) FROM itemAttachments WHERE linkMode = 1",
        "Web Links (linkMode=2)": "SELECT COUNT(*) FROM itemAttachments WHERE linkMode = 2",
        "Total Collections": "SELECT COUNT(*) FROM collections",
        "Total Tags": "SELECT COUNT(*) FROM tags"
    }
    
    for label, query in stats_queries.items():
        try:
            cursor = conn.execute(query)
            count = cursor.fetchone()[0]
            print(f"  {label:<25}: {count:,}")
        except Exception as e:
            print(f"  {label:<25}: Error - {e}")

def explore_recent_items(conn, limit=10):
    """Show recent items with basic info"""
    print(f"\n" + "="*60)
    print(f"RECENT ITEMS (Last {limit})")
    print("="*60)
    
    query = """
        SELECT 
            i.itemID,
            i.dateAdded,
            i.dateModified,
            GROUP_CONCAT(
                CASE 
                    WHEN f.fieldName = 'title' THEN idv.value
                END
            ) as title,
            GROUP_CONCAT(
                CASE 
                    WHEN f.fieldName = 'publicationTitle' THEN idv.value
                END
            ) as publication
        FROM items i
        LEFT JOIN itemData id ON i.itemID = id.itemID
        LEFT JOIN fields f ON id.fieldID = f.fieldID
        LEFT JOIN itemDataValues idv ON id.valueID = idv.valueID
        WHERE i.itemID NOT IN (SELECT itemID FROM itemAttachments)
        GROUP BY i.itemID
        ORDER BY i.dateModified DESC
        LIMIT ?
    """
    
    try:
        cursor = conn.execute(query, (limit,))
        items = cursor.fetchall()
        
        for item in items:
            item_id = item['itemID']
            title = item['title'] or "No Title"
            pub = item['publication'] or ""
            date = item['dateModified'][:19] if item['dateModified'] else "No Date"
            
            print(f"\n📄 Item {item_id}:")
            print(f"    Title: {title[:80]}{'...' if len(title) > 80 else ''}")
            if pub:
                print(f"    Publication: {pub[:60]}{'...' if len(pub) > 60 else ''}")
            print(f"    Modified: {date}")
            
    except Exception as e:
        print(f"❌ Error fetching recent items: {e}")

def explore_attachments(conn, limit=10):
    """Show recent attachments"""
    print(f"\n" + "="*60)
    print(f"RECENT ATTACHMENTS (Last {limit})")
    print("="*60)
    
    query = """
        SELECT 
            ia.itemID,
            ia.parentItemID,
            ia.linkMode,
            ia.contentType,
            ia.path,
            i.dateAdded,
            COALESCE(parent_title.title, standalone_title.title) as title
        FROM itemAttachments ia
        JOIN items i ON ia.itemID = i.itemID
        LEFT JOIN (
            SELECT 
                i.itemID,
                GROUP_CONCAT(
                    CASE WHEN f.fieldName = 'title' THEN idv.value END
                ) as title
            FROM items i
            LEFT JOIN itemData id ON i.itemID = id.itemID
            LEFT JOIN fields f ON id.fieldID = f.fieldID
            LEFT JOIN itemDataValues idv ON id.valueID = idv.valueID
            GROUP BY i.itemID
        ) parent_title ON ia.parentItemID = parent_title.itemID
        LEFT JOIN (
            SELECT 
                i.itemID,
                GROUP_CONCAT(
                    CASE WHEN f.fieldName = 'title' THEN idv.value END
                ) as title
            FROM items i
            LEFT JOIN itemData id ON i.itemID = id.itemID
            LEFT JOIN fields f ON id.fieldID = f.fieldID
            LEFT JOIN itemDataValues idv ON id.valueID = idv.valueID
            GROUP BY i.itemID
        ) standalone_title ON ia.itemID = standalone_title.itemID
        ORDER BY i.dateAdded DESC
        LIMIT ?
    """
    
    try:
        cursor = conn.execute(query, (limit,))
        attachments = cursor.fetchall()
        
        link_modes = {0: "Stored", 1: "Linked", 2: "Web Link", 3: "Linked (rel)"}
        
        for att in attachments:
            att_id = att['itemID']
            parent_id = att['parentItemID']
            link_mode = link_modes.get(att['linkMode'], f"Unknown({att['linkMode']})")
            content_type = att['contentType'] or "Unknown"
            path = att['path']
            title = att['title'] or "No Title"
            date = att['dateAdded'][:19] if att['dateAdded'] else "No Date"
            
            print(f"\n📎 Attachment {att_id}:")
            if parent_id:
                print(f"    Parent: {parent_id} - {title[:60]}{'...' if len(title) > 60 else ''}")
            else:
                print(f"    Standalone: {title[:60]}{'...' if len(title) > 60 else ''}")
            print(f"    Type: {link_mode} | Content: {content_type}")
            print(f"    Added: {date}")
            if path:
                display_path = path[:80] + "..." if len(path) > 80 else path
                print(f"    Path: {display_path}")
                
                # Check if file exists
                if att['linkMode'] == 1 and path:  # Linked file
                    exists = "✅" if os.path.exists(path) else "❌"
                    print(f"    Exists: {exists}")
                elif att['linkMode'] == 0 and path and path.startswith("storage:"):
                    # Stored file - check in storage folder
                    parts = path.split(":")
                    if len(parts) >= 3:
                        key = parts[1]
                        filename = ":".join(parts[2:])
                        storage_path = Path("/Users/travisross/Zotero/storage") / key / filename
                        exists = "✅" if storage_path.exists() else "❌"
                        print(f"    Storage: {exists} {storage_path}")
            
    except Exception as e:
        print(f"❌ Error fetching attachments: {e}")

def explore_collections(conn, limit=10):
    """Show collections"""
    print(f"\n" + "="*60)
    print("COLLECTIONS")
    print("="*60)
    
    query = """
        SELECT 
            collectionID,
            collectionName,
            parentCollectionID,
            (SELECT COUNT(*) FROM collectionItems ci WHERE ci.collectionID = c.collectionID) as item_count
        FROM collections c
        ORDER BY collectionName
        LIMIT ?
    """
    
    try:
        cursor = conn.execute(query, (limit,))
        collections = cursor.fetchall()
        
        for coll in collections:
            coll_id = coll['collectionID']
            name = coll['collectionName']
            parent_id = coll['parentCollectionID']
            item_count = coll['item_count']
            
            parent_info = f" (parent: {parent_id})" if parent_id else ""
            print(f"  📁 {name} - {item_count} items{parent_info}")
            
    except Exception as e:
        print(f"❌ Error fetching collections: {e}")

def main():
    """Main exploration function"""
    print("🔍 Zotero Database Explorer")
    print("="*60)
    
    if not os.path.exists(ZOTERO_DB_PATH):
        print(f"❌ Database not found: {ZOTERO_DB_PATH}")
        return
    
    try:
        with safe_zotero_connection(ZOTERO_DB_PATH) as conn:
            # Run all explorations
            explore_database_schema(conn)
            explore_basic_stats(conn)
            explore_recent_items(conn)
            explore_attachments(conn)
            explore_collections(conn)
            
            print(f"\n" + "="*60)
            print("✅ Database exploration complete!")
            print("="*60)
            
    except Exception as e:
        print(f"❌ Failed to explore database: {e}")

if __name__ == "__main__":
    main()