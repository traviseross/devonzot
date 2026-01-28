#!/usr/bin/env python3
"""
Working metadata sync using comments and tags with structured format
"""

import subprocess
import json
from datetime import datetime

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

def sync_metadata_via_comment_and_tags(uuid: str, metadata: dict):
    """Sync metadata using comments (structured) and tags"""
    
    # Create structured metadata comment
    authors = ', '.join(metadata.get('authors', []))
    
    metadata_lines = []
    metadata_lines.append("=== ZOTERO METADATA ===")
    if metadata.get('title'):
        metadata_lines.append(f"Title: {metadata['title']}")
    if authors:
        metadata_lines.append(f"Authors: {authors}")
    if metadata.get('publicationTitle'):
        metadata_lines.append(f"Publication: {metadata['publicationTitle']}")
    if metadata.get('date'):
        metadata_lines.append(f"Date: {metadata['date']}")
    if metadata.get('DOI'):
        metadata_lines.append(f"DOI: {metadata['DOI']}")
    if metadata.get('abstractNote'):
        abstract = metadata['abstractNote'][:200] + "..." if len(metadata['abstractNote']) > 200 else metadata['abstractNote']
        metadata_lines.append(f"Abstract: {abstract}")
    
    metadata_lines.append(f"Sync Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    metadata_lines.append("=" * 25)
    
    comment_text = "\\n".join(metadata_lines).replace('"', '\\"')
    
    # Prepare tags - combine Zotero tags with sync tags
    all_tags = metadata.get('tags', []).copy()
    all_tags.extend(['zotero-synced', 'metadata-updated'])
    
    # Remove duplicates and clean
    unique_tags = list(set(tag.strip() for tag in all_tags if tag.strip()))
    tags_applescript = "{" + ", ".join(f'"{tag.replace('"', '\\"')}"' for tag in unique_tags) + "}"
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            -- Update comment with structured metadata
            set comment of theRecord to "{comment_text}"
            
            -- Update tags
            set tags of theRecord to {tags_applescript}
            
            -- Get updated info for confirmation
            set updatedComment to comment of theRecord
            set updatedTags to tags of theRecord
            
            return "SUCCESS: Updated comment (" & (length of updatedComment) & " chars) and " & (count of updatedTags) & " tags"
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def view_updated_item(uuid: str):
    """View the updated item details"""
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            set itemName to name of theRecord
            set itemComment to comment of theRecord
            set itemTags to tags of theRecord
            
            return itemName & "|SPLIT|" & itemComment & "|SPLIT|" & (itemTags as string)
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    result = execute_applescript(script)
    
    if not result.startswith("ERROR"):
        parts = result.split("|SPLIT|")
        if len(parts) >= 3:
            print(f"📄 Name: {parts[0]}")
            print(f"🗒️  Comment:")
            print(f"   {parts[1].replace('\\n', chr(10))}")
            print(f"🏷️  Tags: {parts[2]}")
    else:
        print(f"❌ Error: {result}")

def test_working_metadata_sync():
    """Test the working metadata sync approach"""
    uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    
    print("🧪 Testing Working Metadata Sync via Comments & Tags")
    print("=" * 60)
    
    # Test metadata
    test_metadata = {
        'title': 'How Black Is Our Market?',
        'authors': ['Leon Henderson'],
        'publicationTitle': 'The Atlantic',
        'date': '1946-07-01',
        'abstractNote': 'This article examines the state of market economics and regulation during post-WWII America, discussing the challenges of maintaining free markets while ensuring fair competition and preventing monopolistic practices.',
        'DOI': '10.1000/example.doi',
        'tags': ['economics', 'post-war', 'regulation', 'free-markets', 'competition']
    }
    
    print("📋 Syncing this metadata:")
    for key, value in test_metadata.items():
        print(f"  {key}: {value}")
    
    print(f"\n🔄 Executing sync...")
    result = sync_metadata_via_comment_and_tags(uuid, test_metadata)
    print(f"📝 Sync result: {result}")
    
    if "SUCCESS" in result:
        print(f"\n✅ Success! Viewing updated item:")
        view_updated_item(uuid)
        
        print(f"\n🔗 Open in DEVONthink to see the metadata:")
        print(f"   x-devonthink-item://{uuid}")
        
        print(f"\n💡 The metadata is now stored in:")
        print(f"   • Comment field: Structured metadata in readable format")
        print(f"   • Tags: All Zotero tags plus sync tracking tags")
        print(f"   • This approach works with any DEVONthink edition!")

if __name__ == "__main__":
    test_working_metadata_sync()