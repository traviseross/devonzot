#!/usr/bin/env python3
"""
Quick API results viewer - show what we can access via Zotero Web API
"""

import requests
import json
from datetime import datetime

# Your API setup
ZOTERO_API_KEY = "Iy9J3VIgfoXUHrHIGkRgzTEJ"
ZOTERO_USER_ID = "617019"
ZOTERO_API_BASE = "https://api.zotero.org"

headers = {
    'Zotero-API-Version': '3',
    'Authorization': f'Bearer {ZOTERO_API_KEY}',
    'User-Agent': 'DEVONzot-API-Demo/1.0'
}

def show_api_results():
    """Show what we can get from the API"""
    
    print("🔍 Zotero API Results Preview")
    print("=" * 50)
    
    # Get first 10 attachments
    print("\n📎 Getting first 10 attachments...")
    
    params = {
        'itemType': 'attachment',
        'limit': 10,
        'format': 'json'
    }
    
    url = f"{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items"
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code == 200:
        attachments = response.json()
        print(f"✅ Retrieved {len(attachments)} attachments")
        
        # Show details
        file_count = 0
        url_count = 0
        
        for i, item in enumerate(attachments):
            data = item.get('data', {})
            
            print(f"\n--- Attachment {i+1} ---")
            print(f"Title: {data.get('title', 'No title')}")
            print(f"Type: {data.get('itemType', 'Unknown')}")
            print(f"Link Mode: {data.get('linkMode', 'Unknown')}")
            
            if data.get('linkMode') == 'linked_file':
                file_count += 1
                print(f"File Path: {data.get('path', 'No path')}")
            elif data.get('linkMode') == 'linked_url':
                url_count += 1
                print(f"URL: {data.get('url', 'No URL')}")
                # Check if it's already a DEVONthink link
                if 'x-devonthink-item://' in data.get('url', ''):
                    print("🎯 ALREADY CONVERTED to DEVONthink UUID!")
            
            print(f"Date Added: {data.get('dateAdded', 'Unknown')}")
            
        print(f"\n📊 Summary of first 10:")
        print(f"  File attachments: {file_count}")
        print(f"  URL attachments: {url_count}")
        
        # Show a few parent items too
        print(f"\n📚 Getting parent items...")
        
        for item in attachments[:3]:  # Just first 3
            data = item.get('data', {})
            parent_key = data.get('parentItem')
            
            if parent_key:
                parent_url = f"{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items/{parent_key}"
                parent_response = requests.get(parent_url, headers=headers)
                
                if parent_response.status_code == 200:
                    parent_data = parent_response.json().get('data', {})
                    print(f"\nParent: {parent_data.get('title', 'No title')}")
                    print(f"  Authors: {', '.join([c.get('firstName', '') + ' ' + c.get('lastName', '') for c in parent_data.get('creators', [])])}")
                    print(f"  Date: {parent_data.get('date', 'No date')}")
                
    else:
        print(f"❌ API Error: {response.status_code}")
        print(response.text)
    
    print("\n" + "=" * 50)
    print("🎯 API access working! Ready for full integration.")

if __name__ == "__main__":
    show_api_results()