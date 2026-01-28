#!/usr/bin/env python3
"""
Test using osascript to set Finder metadata (which DEVONthink should pick up)
"""

import subprocess

def execute_applescript(script: str) -> str:
    """Execute AppleScript and return result"""
    try:
        result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return f"ERROR: {e.stderr}"

def set_finder_metadata(file_path: str):
    """Use Finder/System Events to set file metadata"""
    
    # Convert to POSIX path for AppleScript
    script = f'''
    tell application "System Events"
        try
            set theFile to POSIX file "{file_path}"
            
            -- Try to set Spotlight comments (which often shows up as description)
            set comment of theFile to "METADATA TEST: Article about market economics and regulation during post-WWII America by Leon Henderson, published in The Atlantic in 1946."
            
            return "SUCCESS: Set Finder comment"
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    print("🔄 Setting Finder comment...")
    result = execute_applescript(script)
    print(f"Result: {result}")
    
    # Try using Finder directly
    finder_script = f'''
    tell application "Finder"
        try
            set theFile to POSIX file "{file_path}" as alias
            set comment of theFile to "Zotero Sync: How Black Is Our Market? by Leon Henderson (The Atlantic, 1946) - Economic regulation analysis"
            return "SUCCESS: Set Finder comment"
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    print("🔄 Setting Finder comment directly...")
    result = execute_applescript(finder_script)
    print(f"Result: {result}")

def check_file_metadata_in_devonthink(uuid: str):
    """Check what metadata DEVONthink now shows"""
    
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            
            -- Get various properties
            set itemName to name of theRecord
            set itemComment to comment of theRecord
            set itemTags to tags of theRecord
            
            -- Try to get any metadata DEVONthink sees
            return "Name: " & itemName & "
Comment: " & itemComment & "
Tags: " & (itemTags as string)
            
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    return execute_applescript(script)

def main():
    """Test Finder metadata setting"""
    uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    file_path = "/Users/travisross/DEVONthink/Articles.dtBase2/Files.noindex/pdf/19/Henderson - How Black Is Our Market - The Atlantic - 1946 - Magazine Article.pdf"
    
    print("🧪 Testing Finder Metadata Setting")
    print("=" * 50)
    
    # Set metadata using Finder
    set_finder_metadata(file_path)
    
    # Check what DEVONthink sees
    print(f"\n📋 Checking DEVONthink metadata...")
    dt_result = check_file_metadata_in_devonthink(uuid)
    print(dt_result)
    
    # Check system metadata
    print(f"\n📋 Checking system metadata...")
    try:
        result = subprocess.run(['mdls', file_path], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        relevant_lines = [line for line in lines if any(keyword in line.lower() 
                         for keyword in ['author', 'title', 'comment', 'description', 'subject'])]
        
        print("Relevant metadata fields:")
        for line in relevant_lines[:10]:  # Show first 10 relevant lines
            print(f"  {line}")
            
    except Exception as e:
        print(f"Error checking metadata: {e}")
    
    print(f"\n🔗 Check DEVONthink: x-devonthink-item://{uuid}")
    print("Look for the metadata in DEVONthink's Info panel!")

if __name__ == "__main__":
    main()