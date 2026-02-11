#!/usr/bin/env python3
"""
Test metadata sync using known DEVONthink UUIDs from our search
"""

import sys
sys.path.append('/Users/travisross/DEVONzot')
from production_metadata_sync import ZoteroDevonthinkMetadataSync
import subprocess

# Known UUIDs from our earlier search_uuid_links.py results
TEST_RECORDS = [
    {
        'devonthink_uuid': '487E8743-2338-4D74-B474-BE315BCFBE4E',
        'name': 'Henderson - How Black Is Our Market - The Atlantic - 1946',
        'expected_zotero_key': 'W36FXJSJ'  # Based on the URL pattern we saw earlier
    },
    # Add more test records if we know their UUIDs
]

def get_record_info(uuid: str):
    """Get full record info from DEVONthink"""
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            set result to "Name: " & (name of theRecord)
            set result to result & "\\nURL: " & (URL of theRecord)
            set result to result & "\\nReference URL: " & (reference URL of theRecord)
            set result to result & "\\nPath: " & (path of theRecord)
            set result to result & "\\nComment: " & (comment of theRecord)
            set result to result & "\\nTags: " & (tags of theRecord as string)
            
            return result
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    try:
        result = subprocess.run(['osascript', '-e', script], 
                              capture_output=True, text=True, check=True, timeout=30)
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"

def manual_test_metadata_sync():
    """Test metadata sync on known records"""
    syncer = ZoteroDevonthinkMetadataSync()
    
    print("🧪 Manual Test of Metadata Sync")
    print("=" * 50)
    
    for i, record in enumerate(TEST_RECORDS, 1):
        print(f"\n--- Test Record {i} ---")
        
        # Get current DEVONthink record info
        print("📋 Current DEVONthink record:")
        record_info = get_record_info(record['devonthink_uuid'])
        print(record_info)
        
        # Try to extract Zotero key from any URLs we find
        zotero_key = record['expected_zotero_key']
        if 'library/items/' in record_info:
            # Extract from the info if it contains the pattern
            import re
            match = re.search(r'library/items/([A-Z0-9]{8})', record_info)
            if match:
                zotero_key = match.group(1)
        
        print(f"\n🔑 Using Zotero key: {zotero_key}")
        
        # Get Zotero metadata
        print("📄 Zotero metadata:")
        metadata = syncer.get_zotero_metadata(zotero_key)
        if metadata:
            print(f"   Title: {metadata['title']}")
            print(f"   Author: {metadata['author']}")
            print(f"   Publication: {metadata['publication']}")
            print(f"   Year: {metadata['year']}")
            print(f"   Type: {metadata['type']}")
        else:
            print("   ❌ No Zotero metadata found")
            continue
        
        # Create test record object
        test_record = {
            'devonthink_uuid': record['devonthink_uuid'],
            'name': record['name'],
            'zotero_key': zotero_key,
            'path': f"/Users/travisross/DEVONthink/Articles.dtBase2/Files.noindex/pdf/19/{record['name']}.pdf"
        }
        
        # Ask if we should apply the sync
        print(f"\n❓ Apply metadata sync to this record?")
        response = input("   (y/n): ").lower().strip()
        
        if response == 'y':
            print("🚀 Applying sync...")
            success = syncer.sync_metadata_for_record(test_record)
            if success:
                print("✅ Sync successful!")
                
                # Show updated record info
                print("\n📋 Updated DEVONthink record:")
                updated_info = get_record_info(record['devonthink_uuid'])
                print(updated_info)
            else:
                print("❌ Sync failed")
        else:
            print("⏭️  Skipped")

if __name__ == "__main__":
    manual_test_metadata_sync()