#!/usr/bin/env python3
"""
Check what attachment titles were actually created
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
    'User-Agent': 'DEVONzot-Check/1.0'
}

def check_created_attachments():
    """Check what UUID attachments we actually created"""
    
    # Load the attachment pairs
    try:
        with open('/Users/travisross/DEVONzot/attachment_pairs.json', 'r') as f:
            pairs = json.load(f)
    except Exception as e:
        print(f"Error loading pairs: {e}")
        return
    
    print("🔍 Checking created UUID attachments...")
    print("=" * 60)
    
    for i, pair in enumerate(pairs, 1):
        print(f"\n--- Pair {i} ---")
        print(f"Original title: {pair['old_title']}")
        print(f"Parent: {pair['parent_title']}")
        print(f"New key: {pair['new_key']}")
        
        if pair['new_key'] and pair['new_key'] != '0':
            # Get the actual attachment details
            url = f"{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items/{pair['new_key']}"
            
            try:
                response = requests.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json()['data']
                    print(f"✅ Created attachment found:")
                    print(f"   Title: {data.get('title')}")
                    print(f"   URL: {data.get('url')}")
                    print(f"   Link Mode: {data.get('linkMode')}")
                    print(f"   Zotero Link: zotero://select/items/{ZOTERO_USER_ID}_{pair['new_key']}")
                else:
                    print(f"❌ Could not find attachment: {response.status_code}")
            except Exception as e:
                print(f"❌ Error checking attachment: {e}")
        else:
            print(f"❌ No valid key - creation likely failed")
    
    print("\n" + "=" * 60)
    
    # Also check recent attachments with DEVONthink tag
    print("\n🔍 Checking recent DEVONthink-tagged attachments...")
    
    params = {
        'itemType': 'attachment',
        'tag': 'DEVONthink-UUID',
        'limit': 10,
        'sort': 'dateModified',
        'direction': 'desc',
        'format': 'json'
    }
    
    try:
        url = f"{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items"
        response = requests.get(url, params=params, headers=headers)
        
        if response.status_code == 200:
            items = response.json()
            if items:
                print(f"Found {len(items)} recent DEVONthink attachments:")
                for item in items:
                    data = item['data']
                    print(f"  • {data.get('title')} ({data.get('key')})")
                    print(f"    Link: zotero://select/items/{ZOTERO_USER_ID}_{data.get('key')}")
            else:
                print("No DEVONthink-tagged attachments found")
        else:
            print(f"Error searching: {response.status_code}")
    except Exception as e:
        print(f"Error searching recent attachments: {e}")

if __name__ == "__main__":
    check_created_attachments()