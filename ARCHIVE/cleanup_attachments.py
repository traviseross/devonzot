#!/usr/bin/env python3
"""
Cleanup and recreate UUID attachments with clean titles
"""
import requests
import json
import asyncio

# API setup
ZOTERO_API_KEY = "Iy9J3VIgfoXUHrHIGkRgzTEJ"
ZOTERO_USER_ID = "617019"
ZOTERO_API_BASE = "https://api.zotero.org"

headers = {
    'Zotero-API-Version': '3',
    'Authorization': f'Bearer {ZOTERO_API_KEY}',
    'User-Agent': 'DEVONzot-Cleanup/1.0'
}

async def cleanup_test_attachments():
    """Remove test and poorly named attachments"""
    
    print("🧹 Cleaning up test/poorly named attachments...")
    
    # Get attachments with DEVONthink tags
    params = {
        'itemType': 'attachment',
        'tag': 'DEVONthink-UUID',
        'limit': 20,
        'format': 'json'
    }
    
    url = f"{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items"
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        items = response.json()
        
        # Delete test items and items with "DEVONthink:" prefix
        for item in items:
            data = item['data']
            title = data.get('title', '')
            key = data.get('key')
            version = data.get('version')
            
            should_delete = (
                title.startswith('TEST:') or 
                title.startswith('DEVONthink:') or
                'Auto-Generated' in [tag['tag'] for tag in data.get('tags', [])]
            )
            
            if should_delete:
                print(f"🗑️ Deleting: {title} ({key})")
                
                delete_url = f"{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items/{key}"
                delete_headers = headers.copy()
                delete_headers['If-Unmodified-Since-Version'] = str(version)
                
                delete_response = requests.delete(delete_url, headers=delete_headers)
                
                if delete_response.status_code == 204:
                    print(f"✅ Deleted {key}")
                else:
                    print(f"❌ Failed to delete {key}: {delete_response.status_code}")
                
                await asyncio.sleep(1)  # Rate limiting
    
    # Clear the attachment pairs file
    try:
        with open('/Users/travisross/DEVONzot/attachment_pairs.json', 'w') as f:
            json.dump([], f)
        print("🧹 Cleared attachment pairs file")
    except Exception as e:
        print(f"Error clearing pairs file: {e}")
    
    print("\n✅ Cleanup complete! Ready for fresh start.")

if __name__ == "__main__":
    asyncio.run(cleanup_test_attachments())