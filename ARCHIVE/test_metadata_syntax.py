#!/usr/bin/env python3
"""
Test DEVONthink metadata access with correct syntax
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

def test_metadata_syntax():
    """Test different ways to access/set metadata in DEVONthink"""
    uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    
    # Test 1: Try to read existing metadata
    read_script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            -- Get basic properties that definitely work
            set itemName to name of theRecord
            set itemKind to kind of theRecord
            
            -- Try different ways to access metadata
            set result to itemName & " | " & itemKind
            
            return result
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    print("🔍 Test 1: Basic properties")
    result1 = execute_applescript(read_script)
    print(f"Result: {result1}")
    
    # Test 2: Try setting a simple field using add custom meta data
    set_script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            -- Try the add custom meta data command
            add custom meta data "zotero_test" with value "test_value" to theRecord
            
            return "SUCCESS: Added custom metadata"
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    print("\n🔍 Test 2: Add custom metadata")
    result2 = execute_applescript(set_script)
    print(f"Result: {result2}")
    
    # Test 3: Try accessing predefined metadata fields differently
    predefined_script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            -- Try to set Author field (this is a predefined field)
            set author of theRecord to "Leon Henderson"
            
            return "SUCCESS: Set author field"
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    print("\n🔍 Test 3: Set author field")
    result3 = execute_applescript(predefined_script)
    print(f"Result: {result3}")
    
    # Test 4: Try using the spotlight name format
    spotlight_script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            -- Try setting spotlight metadata
            set spotlight metadata of theRecord for "com_adobe_pdf_Author" to "Leon Henderson"
            
            return "SUCCESS: Set spotlight metadata"
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    print("\n🔍 Test 4: Set spotlight metadata")
    result4 = execute_applescript(spotlight_script)
    print(f"Result: {result4}")

if __name__ == "__main__":
    test_metadata_syntax()