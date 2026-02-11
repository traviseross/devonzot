#!/usr/bin/env python3
"""
Fixed approach to find DEVONthink records with Zotero links
"""

import subprocess

def execute_applescript(script: str) -> str:
    """Execute AppleScript and return result"""
    try:
        result = subprocess.run(['osascript', '-e', script], 
                              capture_output=True, text=True, check=True, timeout=60)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return f"ERROR: {e.stderr}"

def find_records_with_zotero_links():
    """Find records that have Zotero links by checking all records"""
    
    script = '''
    tell application "DEVONthink 3"
        try
            set currentDB to current database
            set allRecords to every record of currentDB
            set resultText to ""
            set foundCount to 0
            
            repeat with theRecord in allRecords
                set recordURL to URL of theRecord
                if recordURL contains "zotero://select/" then
                    set foundCount to foundCount + 1
                    set recordUUID to uuid of theRecord
                    set recordName to name of theRecord
                    set recordPath to path of theRecord
                    
                    set resultText to resultText & recordUUID & "|" & recordName & "|" & recordURL & "|" & recordPath & "\\n"
                    
                    -- Stop after finding 5 for testing
                    if foundCount >= 5 then exit repeat
                end if
            end repeat
            
            if foundCount = 0 then
                return "No records found with zotero:// URLs"
            else
                return "FOUND:" & foundCount & "\\n" & resultText
            end if
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    print("🔍 Searching for records with zotero:// URLs...")
    result = execute_applescript(script)
    return result

def test_specific_record():
    """Test our known record to see what it has"""
    test_uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{test_uuid}"
            
            set result to "=== Record Info ==="
            set result to result & "
Name: " & (name of theRecord)
            set result to result & "
URL: " & (URL of theRecord)
            set result to result & "
Path: " & (path of theRecord)
            set result to result & "
Tags: " & (tags of theRecord as string)
            
            return result
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    print(f"🧪 Testing specific record: {test_uuid}")
    result = execute_applescript(script)
    return result

def main():
    print("🔧 Fixed DEVONthink Record Search")
    print("=" * 50)
    
    # Test our specific record first
    print("1️⃣ Testing known record...")
    test_result = test_specific_record()
    print(test_result)
    
    print("\n2️⃣ Searching for records with Zotero links...")
    search_result = find_records_with_zotero_links()
    print(search_result)
    
    # Parse results if found
    if search_result.startswith("FOUND:"):
        lines = search_result.split("\\n")
        count_line = lines[0]
        count = int(count_line.split(":")[1])
        
        print(f"\n✅ Found {count} records with Zotero links!")
        print("\nFirst few records:")
        
        for i in range(1, min(4, len(lines))):
            if lines[i]:
                parts = lines[i].split("|")
                if len(parts) >= 4:
                    print(f"\n--- Record {i} ---")
                    print(f"Name: {parts[1]}")
                    print(f"UUID: {parts[0]}")
                    print(f"Zotero URL: {parts[2]}")

if __name__ == "__main__":
    main()