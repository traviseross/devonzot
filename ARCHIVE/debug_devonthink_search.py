#!/usr/bin/env python3
"""
Debug the DEVONthink record search
"""

import subprocess

def execute_applescript(script: str) -> str:
    """Execute AppleScript and return result"""
    try:
        result = subprocess.run(['osascript', '-e', script], 
                              capture_output=True, text=True, check=True, timeout=30)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return f"ERROR: {e.stderr}"

def debug_devonthink_search():
    """Debug what we can find in DEVONthink"""
    
    print("🔍 Debugging DEVONthink Search")
    print("=" * 40)
    
    # First, just try to get current database info
    script1 = '''
    tell application "DEVONthink 3"
        try
            set currentDB to current database
            set dbName to name of currentDB
            set recordCount to count of records of currentDB
            return "Database: " & dbName & ", Records: " & recordCount
        on error errMsg
            return "ERROR getting DB info: " & errMsg
        end try
    end tell
    '''
    
    print("1️⃣ Getting database info...")
    result1 = execute_applescript(script1)
    print(result1)
    
    # Try to find records with "zotero" in URL (broader search)
    script2 = '''
    tell application "DEVONthink 3"
        try
            set allRecords to search "zotero" in current database
            set resultCount to count of allRecords
            
            set result to "Found " & resultCount & " records with 'zotero'"
            
            if resultCount > 0 then
                repeat with i from 1 to (minimum of {resultCount, 3})
                    set theRecord to item i of allRecords
                    set result to result & "
Record " & i & ": " & (name of theRecord)
                    set result to result & "
  URL: " & (URL of theRecord)
                end repeat
            end if
            
            return result
        on error errMsg
            return "ERROR searching: " & errMsg
        end try
    end tell
    '''
    
    print("\n2️⃣ Searching for 'zotero' in database...")
    result2 = execute_applescript(script2)
    print(result2)
    
    # Try our test UUID directly
    test_uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    script3 = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{test_uuid}"
            set recordURL to URL of theRecord
            
            return "Test record URL: " & recordURL
        on error errMsg
            return "ERROR getting test record: " & errMsg
        end try
    end tell
    '''
    
    print(f"\n3️⃣ Checking our test record ({test_uuid})...")
    result3 = execute_applescript(script3)
    print(result3)

if __name__ == "__main__":
    debug_devonthink_search()