#!/usr/bin/env python3
"""
Check all possible fields where Zotero links might be stored
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

def check_all_fields_for_zotero():
    """Check all possible fields that might contain Zotero links"""
    test_uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{test_uuid}"
            
            set result to "=== All Fields Check ==="
            set result to result & "
Name: " & (name of theRecord)
            set result to result & "
URL: [" & (URL of theRecord) & "]"
            set result to result & "
Path: " & (path of theRecord)
            set result to result & "
Comment: [" & (comment of theRecord) & "]"
            set result to result & "
Tags: " & (tags of theRecord as string)
            
            try
                set result to result & "
Reference URL: " & (reference URL of theRecord)
            on error
                set result to result & "
Reference URL: Not available"
            end try
            
            try
                set result to result & "
Annotation: " & (annotation of theRecord)
            on error
                set result to result & "
Annotation: Not available"
            end try
            
            return result
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def search_for_uuid_in_comments():
    """Search for records that might have UUIDs in comments"""
    
    script = '''
    tell application "DEVONthink 3"
        try
            set currentDB to current database
            set allRecords to every record of currentDB
            set resultText to ""
            set foundCount to 0
            
            repeat with theRecord in allRecords
                set recordComment to comment of theRecord
                set recordURL to URL of theRecord
                
                -- Check if comment or URL contains some Zotero-like pattern
                if (recordComment contains "zotero" or recordComment contains "library/items" or recordURL contains "zotero" or recordURL contains "library/items") then
                    set foundCount to foundCount + 1
                    set recordUUID to uuid of theRecord
                    set recordName to name of theRecord
                    
                    set resultText to resultText & "RECORD " & foundCount & ":\\n"
                    set resultText to resultText & "Name: " & recordName & "\\n"
                    set resultText to resultText & "UUID: " & recordUUID & "\\n"
                    set resultText to resultText & "URL: " & recordURL & "\\n"
                    set resultText to resultText & "Comment: " & recordComment & "\\n\\n"
                    
                    -- Stop after finding 3 for testing
                    if foundCount >= 3 then exit repeat
                end if
            end repeat
            
            if foundCount = 0 then
                return "No records found with Zotero patterns"
            else
                return resultText
            end if
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def main():
    print("🔍 Comprehensive Field Check for Zotero Links")
    print("=" * 60)
    
    print("1️⃣ Checking all fields on our test record...")
    fields_result = check_all_fields_for_zotero()
    print(fields_result)
    
    print("\n2️⃣ Searching for Zotero patterns in all records...")
    search_result = search_for_uuid_in_comments()
    print(search_result)

if __name__ == "__main__":
    main()