#!/usr/bin/env python3
"""
Simple test to add metadata to one DEVONthink item
"""

import subprocess

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

def test_simple_metadata_update(uuid: str):
    """Test simple metadata addition"""
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            -- Get current name for reference
            set currentName to name of theRecord
            
            -- Add a simple custom metadata field
            set custom meta data of theRecord to {{zotero_test:"sync_test_2026-01-27", zotero_id:"20061"}}
            
            -- Add a tag
            set tags of theRecord to {{"zotero_sync_test"}}
            
            return "SUCCESS: Updated " & currentName
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def get_item_metadata(uuid: str):
    """Get current metadata to verify changes"""
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            set itemName to name of theRecord
            set itemTags to tags of theRecord
            set itemMeta to custom meta data of theRecord
            
            return itemName & "|TAGS|" & (itemTags as string) & "|META|" & (itemMeta as string)
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def main():
    """Test simple metadata update"""
    # Use the Henderson article UUID from our previous test
    test_uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    
    print("🧪 Testing Simple Metadata Update")
    print("=" * 50)
    
    print("📋 Before update:")
    before = get_item_metadata(test_uuid)
    print(before)
    
    print(f"\n🔄 Adding test metadata to DEVONthink item {test_uuid}...")
    result = test_simple_metadata_update(test_uuid)
    print(f"Result: {result}")
    
    print(f"\n📋 After update:")
    after = get_item_metadata(test_uuid)
    print(after)
    
    print(f"\n🔗 Open in DEVONthink: x-devonthink-item://{test_uuid}")

if __name__ == "__main__":
    main()