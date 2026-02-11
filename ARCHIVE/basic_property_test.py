#!/usr/bin/env python3
"""
Basic DEVONthink property test
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

def test_basic_properties(uuid: str):
    """Test basic property access and modification"""
    
    # First, just get basic info
    info_script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            set itemName to name of theRecord
            set itemKind to kind of theRecord
            set itemComment to comment of theRecord
            set itemTags to tags of theRecord
            
            return itemName & "|" & itemKind & "|" & itemComment & "|" & (itemTags as string)
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    print("📋 Getting current item info...")
    result = execute_applescript(info_script)
    print(f"Info: {result}")
    
    if not result.startswith("ERROR"):
        # Try adding a comment (this should work on any edition)
        comment_script = f'''
        tell application "DEVONthink 3"
            try
                set theRecord to get record with uuid "{uuid}"
                set comment of theRecord to "Zotero sync test - " & (current date)
                return "SUCCESS: Added comment"
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''
        
        print("\n🔄 Adding comment...")
        comment_result = execute_applescript(comment_script)
        print(f"Comment result: {comment_result}")
        
        # Try adding a tag (this should also work)
        tag_script = f'''
        tell application "DEVONthink 3"
            try
                set theRecord to get record with uuid "{uuid}"
                set tags of theRecord to {{"zotero-sync-test"}}
                return "SUCCESS: Added tag"
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
        '''
        
        print("\n🏷️ Adding tag...")
        tag_result = execute_applescript(tag_script)
        print(f"Tag result: {tag_result}")
        
        # Get updated info
        print("\n📋 Getting updated item info...")
        updated_result = execute_applescript(info_script)
        print(f"Updated info: {updated_result}")

def main():
    """Test basic property updates"""
    # Use the Henderson article UUID
    test_uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    
    print("🧪 Testing Basic DEVONthink Properties")
    print("=" * 50)
    
    test_basic_properties(test_uuid)
    
    print(f"\n🔗 Open in DEVONthink: x-devonthink-item://{test_uuid}")

if __name__ == "__main__":
    main()