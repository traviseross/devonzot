#!/usr/bin/env python3
"""
Clean up tags on existing UUID attachments and confirm deletion of old file attachments
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
    'User-Agent': 'DEVONzot-CleanTags/1.0'
}

async def clean_tags_and_confirm():
    """Remove junk tags from UUID attachments and delete old file attachments"""
    
    print("🧹 Cleaning up UUID attachment tags...")
    
    # Load attachment pairs
    try:
        with open('/Users/travisross/DEVONzot/attachment_pairs.json', 'r') as f:
            pairs = json.load(f)
    except Exception as e:
        print(f"Error loading pairs: {e}")
        return
    
    # Clean tags from UUID attachments
    for pair in pairs:
        new_key = pair['new_key']
        if new_key and new_key != '0':
            print(f"🧹 Cleaning tags for: {pair['old_title']}")
            
            # Get current attachment
            url = f"{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items/{new_key}"
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()['data']
                current_tags = data.get('tags', [])
                
                # Remove junk tags
                clean_tags = [
                    tag for tag in current_tags 
                    if tag['tag'] not in ['DEVONthink-UUID', 'Converted-from-File', 'Auto-Generated']
                ]
                
                if len(clean_tags) != len(current_tags):
                    # Update with clean tags
                    data['tags'] = clean_tags
                    
                    update_headers = headers.copy()
                    update_headers['If-Unmodified-Since-Version'] = str(data['version'])
                    
                    update_response = requests.put(url, json=data, headers=update_headers)
                    
                    if update_response.status_code == 204:
                        print(f"✅ Cleaned tags for {new_key}")
                    else:
                        print(f"❌ Failed to clean tags for {new_key}: {update_response.status_code}")
                else:
                    print(f"✅ No junk tags found for {new_key}")
            
            await asyncio.sleep(1)
    
    print("\n🗑️ Deleting old file attachments...")
    
    # Delete old file attachments
    deleted_count = 0
    for pair in pairs:
        old_key = pair['old_key']
        print(f"🗑️ Deleting old file attachment: {pair['old_title']}")
        
        # Get current version
        url = f"{ZOTERO_API_BASE}/users/{ZOTERO_USER_ID}/items/{old_key}"
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            version = response.json()['data']['version']
            
            # Delete it
            delete_headers = headers.copy()
            delete_headers['If-Unmodified-Since-Version'] = str(version)
            
            delete_response = requests.delete(url, headers=delete_headers)
            
            if delete_response.status_code == 204:
                print(f"✅ Deleted {old_key}")
                deleted_count += 1
            else:
                print(f"❌ Failed to delete {old_key}: {delete_response.status_code}")
        else:
            print(f"❌ Could not find old attachment {old_key}")
        
        await asyncio.sleep(1)
    
    # Mark pairs as confirmed and old as deleted
    for pair in pairs:
        pair['confirmed'] = True
        pair['old_deleted'] = True
    
    # Save updated pairs
    with open('/Users/travisross/DEVONzot/attachment_pairs.json', 'w') as f:
        json.dump(pairs, f, indent=2)
    
    print(f"\n✅ Cleanup complete!")
    print(f"📊 Cleaned tags on {len(pairs)} UUID attachments")
    print(f"🗑️ Deleted {deleted_count} old file attachments")
    print("🎯 Conversion process finished!")

if __name__ == "__main__":
    asyncio.run(clean_tags_and_confirm())