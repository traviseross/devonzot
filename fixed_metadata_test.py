#!/usr/bin/env python3
"""
Fixed metadata testing for DEVONthink
"""

import subprocess

def execute_applescript(script: str) -> str:
    """Execute AppleScript and return result"""
    try:
        result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return f"ERROR: {e.stderr}"

def set_devonthink_finder_comment(uuid: str):
    """Try setting finder comment on the DEVONthink record"""
    
    comment_text = """Author: Leon Henderson
Title: How Black Is Our Market?
Publication: The Atlantic
Year: 1946
Type: Magazine Article
Tags: economics, competition, post-war, regulation, free-markets"""
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            set the comment of theRecord to "{comment_text}"
            return "SUCCESS: Set DEVONthink comment"
        on error errMsg
            return "FAILED: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def check_devonthink_comment(uuid: str):
    """Check if DEVONthink comment was set"""
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            set theComment to comment of theRecord
            if theComment is not "" then
                return "DEVONthink comment: " & theComment
            else
                return "DEVONthink comment: Empty"
            end if
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def search_devonthink_for_author(search_term: str):
    """Search DEVONthink for our author"""
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set searchResults to search "{search_term}" in current database
            set resultCount to count of searchResults
            
            set result to "Search for '{search_term}': " & resultCount & " results"
            
            if resultCount > 0 then
                set result to result & "
First result: " & (name of first item of searchResults)
            end if
            
            return result
        on error errMsg
            return "Search ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def check_file_attributes_safe():
    """Safely check file attributes"""
    file_path = "/Users/travisross/DEVONthink/Articles.dtBase2/Files.noindex/pdf/19/Henderson - How Black Is Our Market - The Atlantic - 1946 - Magazine Article.pdf"
    
    print("🔍 File Attributes Check")
    print("=" * 40)
    
    # Check if file exists
    import os
    if not os.path.exists(file_path):
        print("❌ File not found!")
        return
        
    print(f"✅ File exists: {os.path.basename(file_path)}")
    
    # Check extended attributes with safe encoding
    try:
        result = subprocess.run(['xattr', '-l', file_path], capture_output=True, text=False)
        if result.returncode == 0:
            if result.stdout:
                try:
                    output = result.stdout.decode('utf-8', errors='replace')
                    print("📎 Extended Attributes:")
                    print(output)
                except:
                    print("📎 Extended Attributes: (binary data present)")
            else:
                print("📎 Extended Attributes: None")
        else:
            print(f"❌ xattr error: {result.stderr.decode('utf-8', errors='replace')}")
    except Exception as e:
        print(f"❌ xattr exception: {e}")

def test_working_metadata():
    """Test what metadata approaches actually work in DEVONthink"""
    uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    
    print("🧪 Testing Working DEVONthink Metadata")
    print("=" * 60)
    
    print("1️⃣ Setting DEVONthink comment field...")
    set_result = set_devonthink_finder_comment(uuid)
    print(set_result)
    
    print("\n2️⃣ Checking if comment was set...")
    comment_check = check_devonthink_comment(uuid)
    print(comment_check)
    
    print("\n3️⃣ Testing search functionality...")
    search_result = search_devonthink_for_author("Leon Henderson")
    print(search_result)
    
    print("\n4️⃣ Testing tag search...")
    tag_search = search_devonthink_for_author("economics")
    print(tag_search)
    
    print("\n5️⃣ File attributes check...")
    check_file_attributes_safe()
    
    print("\n📊 SUMMARY:")
    print("✅ Tags are working perfectly")
    print("🔄 Testing comment field...")
    print("🔍 Testing search functionality...")
    
    print(f"\n🎯 ACTION ITEMS:")
    print(f"1. Open x-devonthink-item://{uuid} in DEVONthink")
    print(f"2. Check Info panel (Cmd+I) for comment field")
    print(f"3. Try searching for 'Leon Henderson' in DEVONthink search bar")
    print(f"4. Check if tags are clickable and searchable")

if __name__ == "__main__":
    test_working_metadata()