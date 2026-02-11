#!/usr/bin/env python3
"""
Test the API conversion on just a few items to verify it works
"""
import asyncio
import sys
import os

# Add current directory to path so we can import our service
sys.path.append('/Users/travisross/DEVONzot')

from devonzot_api_service import DEVONzotAPIService
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_conversion():
    """Test conversion on first 5 items only"""
    service = DEVONzotAPIService()
    
    logger.info("🧪 Testing API conversion on first few items...")
    
    # Get just a small batch for testing
    all_attachments = []
    start = 0
    limit = 50  # Just get first 50 to find some file attachments
    
    params = {
        'itemType': 'attachment',
        'limit': limit,
        'start': start,
        'format': 'json'
    }
    
    import requests
    url = f'{service.zotero.session.get("base_url", "https://api.zotero.org")}/users/{service.zotero.user_id}/items'
    
    logger.info("Getting first 50 attachments...")
    response = requests.get(
        f"https://api.zotero.org/users/{service.zotero.user_id}/items",
        params=params,
        headers=service.zotero.session.headers
    )
    
    if response.status_code == 200:
        items = response.json()
        logger.info(f"Retrieved {len(items)} items")
        
        # Find file attachments
        file_attachments = []
        for item_data in items:
            data = item_data.get('data', {})
            if data.get('linkMode') == 'linked_file':
                file_attachments.append({
                    'key': data.get('key', ''),
                    'title': data.get('title', ''),
                    'path': data.get('path', ''),
                })
        
        logger.info(f"Found {len(file_attachments)} file attachments to test")
        
        # Test first 3 file attachments
        for i, attachment in enumerate(file_attachments[:3]):
            logger.info(f"\n--- Testing item {i+1}/3 ---")
            logger.info(f"Title: {attachment['title']}")
            logger.info(f"Current path: {attachment['path']}")
            
            # Search DEVONthink
            uuid = await service.devonthink.search_for_item_async(attachment['title'])
            
            if uuid:
                logger.info(f"✅ Found DEVONthink UUID: {uuid}")
                new_url = f"x-devonthink-item://{uuid}"
                logger.info(f"Would convert to: {new_url}")
            else:
                logger.info("❌ No DEVONthink match found")
        
        logger.info("\n🎯 Test complete! API integration working.")
    else:
        logger.error(f"API error: {response.status_code}")

if __name__ == "__main__":
    asyncio.run(test_conversion())