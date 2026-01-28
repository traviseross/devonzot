#!/usr/bin/env python3
"""
Check what metadata DEVONthink can actually see and use
"""

import subprocess

def execute_applescript(script: str) -> str:
    """Execute AppleScript and return result"""
    try:
        result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return f"ERROR: {e.stderr}"

def check_devonthink_metadata_access(uuid: str):
    """Check what metadata fields DEVONthink can access"""
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            set result to "=== DEVONthink Accessible Properties ==="
            set result to result & "
Name: " & (name of theRecord)
            set result to result & "
Kind: " & (kind of theRecord)
            set result to result & "
Size: " & (size of theRecord)
            set result to result & "
Creation Date: " & (creation date of theRecord)
            set result to result & "
Modification Date: " & (modification date of theRecord)
            set result to result & "
Tags: " & (tags of theRecord as string)
            set result to result & "
Comment: " & (comment of theRecord)
            set result to result & "
URL: " & (URL of theRecord)
            set result to result & "
Path: " & (path of theRecord)
            
            -- Try to access some common metadata properties
            try
                set result to result & "
Author (if accessible): " & (author of theRecord)
            on error
                set result to result & "
Author: Not accessible"
            end try
            
            return result
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def check_devonthink_search_metadata(uuid: str):
    """Check if DEVONthink can search by the metadata we set"""
    
    # Try searching for the author we set
    script = '''
    tell application "DEVONthink 3"
        try
            -- Search for "Leon Henderson" to see if it finds our item
            set searchResults to search "Leon Henderson"
            set resultCount to count of searchResults
            
            set result to "Search Results for 'Leon Henderson': " & resultCount & " items found"
            
            if resultCount > 0 then
                repeat with i from 1 to (count of searchResults)
                    set searchItem to item i of searchResults
                    set result to result & "
Item " & i & ": " & (name of searchItem) & " (UUID: " & (uuid of searchItem) & ")"
                    if i > 5 then exit repeat -- Limit to first 5 results
                end repeat
            end if
            
            return result
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def test_devonthink_metadata_recognition():
    """Test if DEVONthink recognizes our metadata"""
    uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    
    print("🔍 Testing DEVONthink Metadata Recognition")
    print("=" * 60)
    
    print("📋 Checking what properties DEVONthink can access...")
    properties = check_devonthink_metadata_access(uuid)
    print(properties)
    
    print(f"\n🔍 Testing if DEVONthink can find our metadata via search...")
    search_results = check_devonthink_search_metadata(uuid)
    print(search_results)
    
    print(f"\n💡 Key Questions:")
    print(f"1. Are the tags we set showing up? (Look for zotero-synced, economics, etc.)")
    print(f"2. Can DEVONthink search find 'Leon Henderson'?")
    print(f"3. Does the Info panel in DEVONthink show author metadata?")
    
    print(f"\n🔗 Open this in DEVONthink and check Info panel:")
    print(f"   x-devonthink-item://{uuid}")
    
    print(f"\n📝 Instructions:")
    print(f"   1. Click the link above to open in DEVONthink")
    print(f"   2. Press Cmd+I or View > Show Info to open Info panel")
    print(f"   3. Look for 'Author', 'Title', or other metadata fields")
    print(f"   4. Try searching for 'Leon Henderson' in DEVONthink")

if __name__ == "__main__":
    test_devonthink_metadata_recognition()