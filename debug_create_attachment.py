#!/usr/bin/env python3
"""
Debug API attachment creation response
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
    'User-Agent': 'DEVONzot-Debug/1.0',
    'Content-Type': 'application/json'
}

def debug_create_attachment():
    """Debug the attachment creation API"""
    
    # Test with a known parent item - get first few regular items
    params = {'itemType': '-attachment', 'limit': 1, 'format': 'json'}
    url = f"{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items"
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        items = response.json()
        if items:
            parent_key = items[0]['data']['key']
            parent_title = items[0]['data'].get('title', 'Test Item')
            
            print(f"Using parent item: {parent_title} ({parent_key})")
            
            # Create test attachment
            attachment_data = {
                "itemType": "attachment",
                "parentItem": parent_key,
                "linkMode": "linked_url",
                "title": "TEST: DEVONthink UUID Link",
                "url": "x-devonthink-item://TEST-UUID-12345",
                "contentType": "application/pdf",
                "tags": [{"tag": "TEST"}, {"tag": "DEVONthink-UUID"}]
            }
            
            print(f"\nCreating test attachment...")
            print(f"Data: {json.dumps(attachment_data, indent=2)}")
            
            create_response = requests.post(
                f"{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items",
                json=[attachment_data],
                headers=headers
            )
            
            print(f"\nResponse Status: {create_response.status_code}")
            print(f"Response Headers: {dict(create_response.headers)}")
            
            if create_response.text:
                try:
                    response_data = create_response.json()
                    print(f"Response Data: {json.dumps(response_data, indent=2)}")
                except:
                    print(f"Response Text: {create_response.text}")
            else:
                print("Empty response")
                
    else:
        print(f"Failed to get parent items: {response.status_code}")

if __name__ == "__main__":
    debug_create_attachment()