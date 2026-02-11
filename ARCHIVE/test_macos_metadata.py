#!/usr/bin/env python3
"""
Test setting macOS file metadata that DEVONthink will recognize
"""

import subprocess
import os
from pathlib import Path

def execute_command(cmd):
    """Execute shell command and return result"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        return f"SUCCESS: {result.stdout.strip()}"
    except subprocess.CalledProcessError as e:
        return f"ERROR: {e.stderr.strip()}"

def get_devonthink_file_path(uuid: str):
    """Get the actual file path for a DEVONthink item"""
    script = f'''
    tell application "DEVONthink 3"
        try
            set theRecord to get record with uuid "{uuid}"
            set thePath to path of theRecord
            return thePath
        on error errMsg
            return "ERROR: " & errMsg
        end try
    end tell
    '''
    
    try:
        result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return f"ERROR: {e.stderr}"

def test_macos_metadata_tools():
    """Test different ways to set macOS metadata"""
    
    # First get the file path
    uuid = "487E8743-2338-4D74-B474-BE315BCFBE4E"
    print("🔍 Getting file path from DEVONthink...")
    
    file_path = get_devonthink_file_path(uuid)
    print(f"File path: {file_path}")
    
    if file_path.startswith("ERROR"):
        print("❌ Could not get file path")
        return
    
    # Check if file exists and we can access it
    if not os.path.exists(file_path):
        print(f"❌ File does not exist at: {file_path}")
        return
    
    print(f"✅ File exists: {file_path}")
    
    # Test 1: Using xattr (extended attributes)
    print(f"\n🧪 Test 1: Using xattr for extended attributes")
    
    # Set some basic extended attributes
    xattr_commands = [
        f'xattr -w "com.apple.metadata:kMDItemAuthors" "Leon Henderson" "{file_path}"',
        f'xattr -w "com.apple.metadata:kMDItemTitle" "How Black Is Our Market?" "{file_path}"',
        f'xattr -w "com.apple.metadata:kMDItemDescription" "Article about market economics" "{file_path}"'
    ]
    
    for cmd in xattr_commands:
        print(f"   Running: {cmd}")
        result = execute_command(cmd)
        print(f"   Result: {result}")
    
    # Test 2: Using mdutil to update spotlight index
    print(f"\n🧪 Test 2: Update Spotlight metadata")
    spotlight_cmd = f'mdimport "{file_path}"'
    print(f"   Running: {spotlight_cmd}")
    result = execute_command(spotlight_cmd)
    print(f"   Result: {result}")
    
    # Test 3: Check what metadata is now set
    print(f"\n🧪 Test 3: Check current metadata")
    check_cmd = f'mdls "{file_path}"'
    print(f"   Running: {check_cmd}")
    result = execute_command(check_cmd)
    print(f"   Current metadata: {result[:500]}...")
    
    # Test 4: List extended attributes
    print(f"\n🧪 Test 4: List extended attributes")
    xattr_list_cmd = f'xattr -l "{file_path}"'
    result = execute_command(xattr_list_cmd)
    print(f"   Extended attributes: {result}")
    
    print(f"\n✅ Tests complete. Check DEVONthink to see if metadata appears!")
    print(f"🔗 x-devonthink-item://{uuid}")

if __name__ == "__main__":
    test_macos_metadata_tools()