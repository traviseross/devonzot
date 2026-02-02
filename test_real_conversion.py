#!/usr/bin/env python3
"""
Live test conversion with real DEVONthink match
"""

import asyncio
import os
import requests
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(Path(__file__).resolve().parent / '.env')

# Your API setup
ZOTERO_API_KEY = os.environ['ZOTERO_API_KEY']
ZOTERO_USER_ID = os.environ['ZOTERO_USER_ID']
ZOTERO_API_BASE = "https://api.zotero.org"

headers = {
    'Zotero-API-Version': '3',
    'Authorization': f'Bearer {ZOTERO_API_KEY}',
    'User-Agent': 'DEVONzot-Live-Test/1.0'
}

async def test_real_conversion():
    """Test actual conversion with a known match"""
    
    print("🧪 Testing REAL conversion with DEVONthink match...")
    
    # Get first few file attachments
    params = {'itemType': 'attachment', 'limit': 20, 'format': 'json'}
    url = f"{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items"
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code != 200:
        print(f"API Error: {response.status_code}")
        return
    
    items = response.json()
    
    # Find a file attachment that might match something in DEVONthink
    candidate = None
    for item in items:
        data = item.get('data', {})
        if (data.get('linkMode') == 'linked_file' and 
            'Egholm' in data.get('title', '')):
            candidate = item
            break
    
    if not candidate:
        print("❌ No suitable candidate found")
        return
    
    data = candidate.get('data', {})
    print(f"\n📄 Found candidate:")
    print(f"Title: {data.get('title')}")
    print(f"Path: {data.get('path')}")
    print(f"Key: {data.get('key')}")
    
    # Search DEVONthink for "Egholm" (we know this works)
    import subprocess
    script = '''
    tell application "DEVONthink 3"
        set searchResults to search "Egholm"
        if (count of searchResults) > 0 then
            set firstResult to item 1 of searchResults  
            return uuid of firstResult
        else
            return ""
        end if
    end tell
    '''
    
    result = subprocess.run(['osascript', '-e', script], 
                          capture_output=True, text=True)
    
    if result.returncode == 0 and result.stdout.strip():
        uuid = result.stdout.strip()
        print(f"✅ Found DEVONthink UUID: {uuid}")
        
        # Show what the conversion would look like
        new_url = f"x-devonthink-item://{uuid}"
        zotero_link = f"zotero://select/items/{ZOTERO_USER_ID}_{data.get('key')}"
        
        print(f"\n{'='*60}")
        print("🔍 CONVERSION PREVIEW")
        print(f"{'='*60}")
        print(f"Attachment: {data.get('title')}")
        print(f"Current: File link → {data.get('path')}")
        print(f"New: DEVONthink UUID → {new_url}")
        print(f"Zotero Link: {zotero_link}")
        print(f"{'='*60}")
        
        print(f"\n🎯 To inspect this item in Zotero:")
        print(f"Copy this link: {zotero_link}")
        print(f"Paste in browser - Zotero will open and highlight the item")
        
        # Ask if user wants to do the actual conversion
        print(f"\n❓ Ready to do ACTUAL conversion? (This will modify Zotero!)")
        print(f"Enter 'yes' to proceed, anything else to cancel:")
        
        # For now, just show preview
        print(f"\n🛡️  Preview mode - no changes made")
        
    else:
        print("❌ No DEVONthink match found")

if __name__ == "__main__":
    asyncio.run(test_real_conversion())