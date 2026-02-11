#!/usr/bin/env python3
"""
Debug the API update issue
"""
import requests
import json

# API setup
ZOTERO_API_KEY = "Iy9J3VIgfoXUHrHIGkRgzTEJ"
ZOTERO_USER_ID = "617019" 
ZOTERO_API_BASE = "https://api.zotero.org"

headers = {
    'Zotero-API-Version': '3',
    'Authorization': f'Bearer {ZOTERO_API_KEY}',
    'User-Agent': 'DEVONzot-Debug/1.0'
}

def debug_api_update():
    """Debug why API updates are failing"""
    
    # Get the Egholm attachment that we know exists
    attachment_key = "MUHKXLKG"
    
    print(f"🔍 Debugging API update for attachment: {attachment_key}")
    
    # Get current attachment data
    url = f'{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items/{attachment_key}'
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        current_data = response.json()
        data = current_data['data']
        
        print(f"✅ Current attachment data:")
        print(f"  Title: {data.get('title')}")
        print(f"  Link Mode: {data.get('linkMode')}")
        print(f"  Version: {data.get('version')}")
        print(f"  Path: {data.get('path', 'None')}")
        print(f"  URL: {data.get('url', 'None')}")
        
        # Try a minimal update - just change URL
        new_data = data.copy()
        new_data['url'] = 'x-devonthink-item://79C3E8F4-0C11-4584-ACDD-2C1BFB5EB7E7'
        new_data['linkMode'] = 'linked_url'
        
        # Remove path when switching to URL
        if 'path' in new_data:
            del new_data['path']
        
        print(f"\n🔄 Attempting update...")
        print(f"  New URL: {new_data['url']}")
        print(f"  New Link Mode: {new_data['linkMode']}")
        
        # Try the update
        version = data.get('version')
        headers_update = headers.copy()
        headers_update['If-Unmodified-Since-Version'] = str(version)
        headers_update['Content-Type'] = 'application/json'
        
        update_response = requests.put(
            url,
            json=new_data,
            headers=headers_update
        )
        
        print(f"\n📊 Update Result:")
        print(f"  Status Code: {update_response.status_code}")
        print(f"  Response: {update_response.text}")
        
        if update_response.status_code != 204:
            print(f"❌ Update failed")
            try:
                error_data = update_response.json()
                print(f"  Error details: {json.dumps(error_data, indent=2)}")
            except:
                print(f"  Raw error: {update_response.text}")
        else:
            print(f"✅ Update successful!")
            
    else:
        print(f"❌ Failed to get attachment: {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    debug_api_update()