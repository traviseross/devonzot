#!/usr/bin/env python3
"""
Proper metadata sync using DEVONthink's predefined metadata fields
"""

import subprocess
import sqlite3
from contextlib import contextmanager

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

def get_zotero_item_full_metadata(item_id: int):
    """Get complete Zotero metadata for an item"""
    ZOTERO_DB_PATH = "/Users/travisross/Zotero/zotero.sqlite"
    
    with sqlite3.connect(ZOTERO_DB_PATH, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        
        # Get all field data
        cursor = conn.execute("""
            SELECT 
                f.fieldName,
                idv.value
            FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE id.itemID = ?
        """, (item_id,))
        
        metadata = dict(cursor.fetchall())
        
        # Get authors
        authors_cursor = conn.execute("""
            SELECT c.firstName, c.lastName
            FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            WHERE ic.itemID = ?
            ORDER BY ic.orderIndex
        """, (item_id,))
        
        authors = []
        for row in authors_cursor.fetchall():
            first = row['firstName'] or ""
            last = row['lastName'] or ""
            name = f"{first} {last}".strip()
            if name:
                authors.append(name)
        
        metadata['authors'] = authors
        
        # Get tags
        tags_cursor = conn.execute("""
            SELECT t.name
            FROM itemTags it
            JOIN tags t ON it.tagID = t.tagID
            WHERE it.itemID = ?
        """, (item_id,))
        
        metadata['tags'] = [row['name'] for row in tags_cursor.fetchall()]
        
        return metadata

def sync_metadata_to_devonthink(uuid: str, zotero_metadata: dict):
    """Sync Zotero metadata to DEVONthink using predefined metadata fields"""
    
    # Extract and clean metadata
    title = (zotero_metadata.get('title', '') or '').replace('"', '\\"').replace('\\', '\\\\')
    authors = ', '.join(zotero_metadata.get('authors', [])).replace('"', '\\"')
    abstract = (zotero_metadata.get('abstractNote', '') or '').replace('"', '\\"')[:500]  # Limit length
    doi = (zotero_metadata.get('DOI', '') or '').replace('"', '\\"')
    date = (zotero_metadata.get('date', '') or '').replace('"', '\\"')
    publication = (zotero_metadata.get('publicationTitle', '') or '').replace('"', '\\"')
    url = (zotero_metadata.get('url', '') or '').replace('"', '\\"')
    
    # Build tags
    tags = zotero_metadata.get('tags', [])
    tags_applescript = "{" + ", ".join(f'"{tag.replace('"', '\\"')}"' for tag in tags if tag) + "}"
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            set resultMessage to ""
            
            -- Update predefined metadata fields
            if "{authors}" is not "" then
                set meta data of theRecord for "mdimporter-author" to "{authors}"
                set resultMessage to resultMessage & "Author: " & "{authors}" & "; "
            end if
            
            if "{abstract}" is not "" then
                set meta data of theRecord for "mdimporter-abstract" to "{abstract}"
                set resultMessage to resultMessage & "Abstract: added; "
            end if
            
            if "{doi}" is not "" then
                set meta data of theRecord for "mdimporter-doi" to "{doi}"
                set resultMessage to resultMessage & "DOI: " & "{doi}" & "; "
            end if
            
            if "{publication}" is not "" then
                set meta data of theRecord for "mdimporter-category" to "{publication}"
                set resultMessage to resultMessage & "Category: " & "{publication}" & "; "
            end if
            
            if "{date}" is not "" then
                set meta data of theRecord for "mdimporter-date" to "{date}"
                set resultMessage to resultMessage & "Date: " & "{date}" & "; "
            end if
            
            -- Update tags
            if "{len(tags)}" > "0" then
                set tags of theRecord to {tags_applescript}
                set resultMessage to resultMessage & "Tags: {len(tags)} added; "
            end if
            
            -- Add sync info to comment
            set comment of theRecord to "Synced from Zotero on " & (current date) & ". " & (comment of theRecord)
            
            return "SUCCESS: " & resultMessage
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def test_proper_metadata_sync():
    """Test with the Henderson article"""
    uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    item_id = 20061  # From our previous test
    
    print("🧪 Testing Proper Metadata Sync with Predefined Fields")
    print("=" * 60)
    
    # Get Zotero metadata (even though this item has "No Title", let's see what's there)
    print("📋 Getting Zotero metadata...")
    zotero_meta = get_zotero_item_full_metadata(item_id)
    
    print("Zotero metadata found:")
    for key, value in zotero_meta.items():
        if value:  # Only show non-empty values
            print(f"  {key}: {value}")
    
    # Since this item has minimal Zotero metadata, let's create test metadata
    test_metadata = {
        'title': 'How Black Is Our Market?',
        'authors': ['Leon Henderson'],
        'publicationTitle': 'The Atlantic',
        'date': '1946-07-01',
        'abstractNote': 'Article about market economics and regulation during post-WWII America.',
        'DOI': '',
        'tags': ['economics', 'post-war', 'regulation', 'zotero-synced']
    }
    
    print(f"\n🔄 Syncing test metadata to DEVONthink...")
    print("Test metadata:")
    for key, value in test_metadata.items():
        if value:
            print(f"  {key}: {value}")
    
    result = sync_metadata_to_devonthink(uuid, test_metadata)
    print(f"\n📝 Sync result: {result}")
    
    # Get updated DEVONthink info
    info_script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            set itemName to name of theRecord
            set itemComment to comment of theRecord
            set itemTags to tags of theRecord
            
            -- Try to get some metadata fields
            set authorMeta to ""
            set doiMeta to ""
            set categoryMeta to ""
            
            try
                set authorMeta to meta data of theRecord for "mdimporter-author"
            end try
            try  
                set doiMeta to meta data of theRecord for "mdimporter-doi"
            end try
            try
                set categoryMeta to meta data of theRecord for "mdimporter-category"
            end try
            
            return itemName & "|COMMENT|" & itemComment & "|TAGS|" & (itemTags as string) & "|AUTHOR|" & authorMeta & "|DOI|" & doiMeta & "|CATEGORY|" & categoryMeta
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    print(f"\n📋 Updated DEVONthink info:")
    updated_info = execute_applescript(info_script)
    if not updated_info.startswith("ERROR"):
        parts = updated_info.split("|")
        if len(parts) >= 8:
            print(f"  Name: {parts[0]}")
            print(f"  Comment: {parts[2][:100]}{'...' if len(parts[2]) > 100 else ''}")
            print(f"  Tags: {parts[4]}")
            print(f"  Author metadata: {parts[6]}")
            print(f"  DOI metadata: {parts[8]}")
            print(f"  Category metadata: {parts[10] if len(parts) > 10 else 'N/A'}")
    else:
        print(f"  Error getting info: {updated_info}")
    
    print(f"\n🔗 Open in DEVONthink: x-devonthink-item://{uuid}")

if __name__ == "__main__":
    test_proper_metadata_sync()