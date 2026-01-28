#!/usr/bin/env python3
"""
Enhanced metadata checking for DEVONthink
"""

import subprocess

def execute_applescript(script: str) -> str:
    """Execute AppleScript and return result"""
    try:
        result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return f"ERROR: {e.stderr}"

def check_devonthink_extended_properties(uuid: str):
    """Check extended properties DEVONthink might have"""
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            set result to "=== Extended DEVONthink Properties ==="
            
            try
                set result to result & "
Custom Meta Data: " & (custom meta data of theRecord as string)
            on error
                set result to result & "
Custom Meta Data: Not accessible or empty"
            end try
            
            try
                set result to result & "
Finder Comment: " & (finder comment of theRecord)
            on error
                set result to result & "
Finder Comment: Not accessible"
            end try
            
            try
                set result to result & "
Spotlight Comments: " & (spotlight comments of theRecord)
            on error
                set result to result & "
Spotlight Comments: Not accessible"
            end try
            
            return result
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def set_devonthink_custom_metadata(uuid: str):
    """Try setting metadata directly on the DEVONthink record"""
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            -- Try setting finder comment which might be accessible
            try
                set finder comment of theRecord to "Author: Leon Henderson
Title: How Black Is Our Market?
Publication: The Atlantic
Year: 1946
Type: Magazine Article
Tags: economics, competition, post-war, regulation, free-markets"
                set result to "SUCCESS: Set finder comment"
            on error errMsg
                set result to "FAILED to set finder comment: " & errMsg
            end try
            
            return result
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def check_file_metadata_direct():
    """Check the actual file metadata using macOS tools"""
    file_path = "/Users/travisross/DEVONthink/Articles.dtBase2/Files.noindex/pdf/19/Henderson - How Black Is Our Market - The Atlantic - 1946 - Magazine Article.pdf"
    
    print("🔍 Direct File Metadata Check")
    print("=" * 40)
    
    # Check extended attributes
    print("📎 Extended Attributes (xattr):")
    result = subprocess.run(['xattr', '-l', file_path], capture_output=True, text=True)
    if result.returncode == 0:
        print(result.stdout if result.stdout else "No extended attributes")
    else:
        print(f"Error: {result.stderr}")
    
    # Check file info
    print("\n📄 File Info (file command):")
    result = subprocess.run(['file', file_path], capture_output=True, text=True)
    print(result.stdout.strip())
    
    # Check if it has PDF metadata
    print("\n📋 PDF Metadata (if available):")
    result = subprocess.run(['mdls', '-name', 'kMDItemAuthor', '-name', 'kMDItemTitle', '-name', 'kMDItemKeywords', file_path], 
                          capture_output=True, text=True)
    print(result.stdout)

def test_comprehensive_metadata():
    """Comprehensive metadata test"""
    uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    
    print("🔬 Comprehensive Metadata Analysis")
    print("=" * 60)
    
    print("1️⃣ Setting DEVONthink finder comment...")
    set_result = set_devonthink_custom_metadata(uuid)
    print(set_result)
    
    print("\n2️⃣ Checking extended DEVONthink properties...")
    properties = check_devonthink_extended_properties(uuid)
    print(properties)
    
    print("\n3️⃣ Checking file metadata directly...")
    check_file_metadata_direct()
    
    print("\n✅ WORKING FEATURES:")
    print("   • Tags are fully working and visible in DEVONthink")
    print("   • File identification and basic properties work")
    
    print("\n🔍 TESTING NEEDED:")
    print("   • Check DEVONthink Info panel after running this")
    print("   • Try searching for 'Leon Henderson' in DEVONthink")
    print("   • See if finder comment appears in Info panel")
    
    print(f"\n🔗 Test link: x-devonthink-item://{uuid}")

if __name__ == "__main__":
    test_comprehensive_metadata()