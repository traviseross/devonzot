#!/usr/bin/env python3
"""
DEVONzot API Service v1.1.0

Safe API-based Zotero-DEVONthink integration using Zotero Web API.
Can run while Zotero is open without database conflicts.

This version uses Zotero's Web API for all operations:
- Read attachments via API (safe while running)
- Update attachment links via API (no database locks)
- Full sync compatibility across all devices
"""

import asyncio
import json
import logging
import os
import time
import requests
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
import subprocess
from datetime import datetime

# Configuration
load_dotenv(Path(__file__).resolve().parent / '.env')

ZOTERO_API_KEY = os.environ["ZOTERO_API_KEY"]
ZOTERO_USER_ID = os.environ["ZOTERO_USER_ID"]
ZOTERO_API_BASE = "https://api.zotero.org"
API_VERSION = "3"
RATE_LIMIT_DELAY = 1.0  # Seconds between API calls

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/Users/travisross/DEVONzot/api_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class ZoteroAPIAttachment:
    """Zotero attachment from API"""
    key: str
    version: int
    parent_key: Optional[str]
    item_type: str
    title: str
    link_mode: str
    content_type: str
    filename: Optional[str]
    url: Optional[str]
    path: Optional[str]
    tags: List[str]
    date_added: str
    date_modified: str

@dataclass
class ZoteroAPIItem:
    """Zotero item from API"""
    key: str
    version: int
    item_type: str
    title: str
    creators: List[Dict[str, str]]
    date: Optional[str]
    collections: List[str]
    tags: List[str]
    extra: Optional[str]

class ZoteroAPIClient:
    """Zotero Web API client"""
    
    def __init__(self, api_key: str, user_id: Optional[str] = None):
        self.api_key = api_key
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers.update({
            'Zotero-API-Version': API_VERSION,
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'DEVONzot-API-Service/1.1.0'
        })
        
        # Detect user ID if not provided
        if not self.user_id:
            self.user_id = self._get_user_id()
    
    def _get_user_id(self) -> str:
        """Get user ID from API key"""
        try:
            response = self.session.get(f'{ZOTERO_API_BASE}/keys/{self.api_key}')
            if response.status_code == 200:
                key_info = response.json()
                return str(key_info['userID'])
            else:
                raise Exception(f"Failed to get user ID: {response.status_code}")
        except Exception as e:
            logger.error(f"Error getting user ID: {e}")
            # Fallback - try to detect from a test call
            return self._detect_user_id()
    
    def _detect_user_id(self) -> str:
        """Try to detect user ID from test API call"""
        # This is a fallback - you might need to provide your user ID manually
        # For now, let's try a common approach
        logger.warning("Could not auto-detect user ID. You may need to set it manually.")
        return "YOUR_USER_ID"  # User will need to replace this
    
    def _rate_limit(self):
        """Respect API rate limits"""
        time.sleep(RATE_LIMIT_DELAY)
    
    def get_attachments(self, item_type: str = "attachment", limit: int = 100) -> List[ZoteroAPIAttachment]:
        """Get attachments from Zotero API"""
        attachments = []
        start = 0
        
        while True:
            self._rate_limit()
            
            params = {
                'itemType': item_type,
                'limit': limit,
                'start': start,
                'format': 'json'
            }
            
            url = f'{ZOTERO_API_BASE}/users/{self.user_id}/items'
            response = self.session.get(url, params=params)
            
            if response.status_code != 200:
                logger.error(f"API error: {response.status_code} - {response.text}")
                break
            
            items = response.json()
            if not items:
                break
                
            for item_data in items:
                data = item_data.get('data', {})
                attachment = ZoteroAPIAttachment(
                    key=data.get('key', ''),
                    version=data.get('version', 0),
                    parent_key=data.get('parentItem'),
                    item_type=data.get('itemType', ''),
                    title=data.get('title', ''),
                    link_mode=data.get('linkMode', ''),
                    content_type=data.get('contentType', ''),
                    filename=data.get('filename'),
                    url=data.get('url'),
                    path=data.get('path'),
                    tags=[tag['tag'] for tag in data.get('tags', [])],
                    date_added=data.get('dateAdded', ''),
                    date_modified=data.get('dateModified', '')
                )
                attachments.append(attachment)
            
            start += limit
            logger.info(f"Retrieved {len(attachments)} attachments so far...")
            
        return attachments
    
    def get_item(self, item_key: str) -> Optional[ZoteroAPIItem]:
        """Get a specific item by key"""
        self._rate_limit()
        
        url = f'{ZOTERO_API_BASE}/users/{self.user_id}/items/{item_key}'
        response = self.session.get(url)
        
        if response.status_code != 200:
            return None
            
        item_data = response.json()
        data = item_data.get('data', {})
        
        return ZoteroAPIItem(
            key=data.get('key', ''),
            version=data.get('version', 0),
            item_type=data.get('itemType', ''),
            title=data.get('title', ''),
            creators=data.get('creators', []),
            date=data.get('date'),
            collections=data.get('collections', []),
            tags=[tag['tag'] for tag in data.get('tags', [])],
            extra=data.get('extra')
        )
    
    def update_attachment(self, attachment_key: str, new_url: str, version: int) -> bool:
        """Update attachment URL via API"""
        self._rate_limit()
        
        # First get current attachment data
        url = f'{ZOTERO_API_BASE}/users/{self.user_id}/items/{attachment_key}'
        response = self.session.get(url)
        
        if response.status_code != 200:
            logger.error(f"Failed to get attachment {attachment_key}: {response.status_code}")
            return False
        
        current_data = response.json()['data']
        
        # Update the URL and linkMode
        current_data['url'] = new_url
        current_data['linkMode'] = 'linked_url'
        
        # Remove path if it exists (switching from file to URL)
        if 'path' in current_data:
            del current_data['path']
        
        # Prepare update payload
        update_data = {
            'key': attachment_key,
            'version': version,
            **current_data
        }
        
        # Send update
        headers = {'If-Unmodified-Since-Version': str(version)}
        response = self.session.put(
            url, 
            json=update_data,
            headers=headers
        )
        
        if response.status_code == 204:
            logger.info(f"Successfully updated attachment {attachment_key} with UUID URL")
            return True
        else:
            logger.error(f"Failed to update attachment {attachment_key}: {response.status_code} - {response.text}")
            return False

class DEVONthinkAPIInterface:
    """DEVONthink interface for API service"""
    
    def __init__(self, wait_time: int = 3):
        self.wait_time = wait_time
    
    def search_for_item(self, title: str) -> Optional[str]:
        """Search DEVONthink for item and return UUID if found"""
        try:
            # Clean title for search
            search_title = title.replace('"', '\\"')
            
            script = f'''
            tell application "DEVONthink 3"
                set searchResults to search "{search_title}" in current database
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
            
            if result.returncode == 0:
                uuid = result.stdout.strip()
                return uuid if uuid else None
            else:
                logger.error(f"AppleScript error: {result.stderr}")
                return None
                
        except Exception as e:
            logger.error(f"Error searching DEVONthink: {e}")
            return None
    
    async def search_for_item_async(self, title: str) -> Optional[str]:
        """Async version of DEVONthink search"""
        try:
            search_title = title.replace('"', '\\"')
            
            script = f'''
            tell application "DEVONthink 3"
                set searchResults to search "{search_title}" in current database
                if (count of searchResults) > 0 then
                    set firstResult to item 1 of searchResults
                    return uuid of firstResult
                else
                    return ""
                end if
            end tell
            '''
            
            process = await asyncio.create_subprocess_exec(
                'osascript', '-e', script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                uuid = stdout.decode().strip()
                return uuid if uuid else None
            else:
                logger.error(f"AppleScript error: {stderr.decode()}")
                return None
                
        except Exception as e:
            logger.error(f"Error searching DEVONthink: {e}")
            return None

class DEVONzotAPIService:
    """Main API-based service for Zotero-DEVONthink integration"""
    
    def __init__(self):
        self.zotero = ZoteroAPIClient(ZOTERO_API_KEY)
        self.devonthink = DEVONthinkAPIInterface()
        self.processed_items = set()
        
    def run_dry_run(self) -> Dict[str, Any]:
        """Run analysis of what would be processed"""
        logger.info("🧪 Starting API-based dry run analysis...")
        
        results = {
            'attachments_found': 0,
            'linked_files': 0,
            'linked_urls': 0,
            'devonthink_matches': 0,
            'conversion_candidates': [],
            'errors': []
        }
        
        try:
            # Get all attachments via API
            attachments = self.zotero.get_attachments()
            results['attachments_found'] = len(attachments)
            
            logger.info(f"Found {len(attachments)} attachments via API")
            
            # Analyze attachment types
            for attachment in attachments:
                if attachment.link_mode == 'linked_file':
                    results['linked_files'] += 1
                elif attachment.link_mode == 'linked_url':
                    results['linked_urls'] += 1
                
                # Check if this is a candidate for conversion
                if (attachment.link_mode == 'linked_file' and 
                    attachment.path and 
                    not attachment.url):
                    
                    results['conversion_candidates'].append({
                        'key': attachment.key,
                        'title': attachment.title,
                        'path': attachment.path
                    })
            
            logger.info(f"Analysis complete:")
            logger.info(f"  Linked files: {results['linked_files']}")
            logger.info(f"  Linked URLs: {results['linked_urls']}")
            logger.info(f"  Conversion candidates: {len(results['conversion_candidates'])}")
            
        except Exception as e:
            logger.error(f"Error in dry run: {e}")
            results['errors'].append(str(e))
        
        return results
    
    async def convert_file_links_to_uuids(self, dry_run: bool = False) -> Dict[str, int]:
        """Convert file-based attachments to DEVONthink UUID links"""
        logger.info("🔗 Starting conversion of file links to UUID links...")
        
        results = {'success': 0, 'error': 0, 'skipped': 0}
        
        try:
            # Get all linked file attachments
            attachments = self.zotero.get_attachments()
            file_attachments = [a for a in attachments if a.link_mode == 'linked_file']
            
            logger.info(f"Processing {len(file_attachments)} file attachments...")
            
            for attachment in file_attachments:
                try:
                    # Skip if already has URL
                    if attachment.url and 'x-devonthink-item://' in attachment.url:
                        results['skipped'] += 1
                        continue
                    
                    # Search DEVONthink for this item
                    uuid = await self.devonthink.search_for_item_async(attachment.title)
                    
                    if uuid:
                        logger.info(f"Found DEVONthink match for: {attachment.title}")
                        
                        if not dry_run:
                            # Update via API
                            new_url = f"x-devonthink-item://{uuid}"
                            success = self.zotero.update_attachment(
                                attachment.key, 
                                new_url, 
                                attachment.version
                            )
                            
                            if success:
                                results['success'] += 1
                            else:
                                results['error'] += 1
                        else:
                            logger.info(f"DRY RUN: Would update {attachment.title} with UUID {uuid}")
                            results['success'] += 1
                    else:
                        logger.warning(f"No DEVONthink match found for: {attachment.title}")
                        results['skipped'] += 1
                    
                    # Small delay between items
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    logger.error(f"Error processing attachment {attachment.key}: {e}")
                    results['error'] += 1
                    
        except Exception as e:
            logger.error(f"Error in conversion process: {e}")
            results['error'] += 1
        
        return results
    
    async def run_service_cycle(self, dry_run: bool = False) -> bool:
        """Run one complete service cycle"""
        logger.info("🔄 Running API-based service cycle...")
        
        try:
            # Convert file links to UUID links
            conversion_results = await self.convert_file_links_to_uuids(dry_run)
            logger.info(f"Conversion results: {conversion_results}")
            
            return True
            
        except Exception as e:
            logger.error(f"Service cycle failed: {e}")
            return False

async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="DEVONzot API Service - Safe Zotero-DEVONthink Integration")
    parser.add_argument('--dry-run', action='store_true', help='Analyze without making changes')
    parser.add_argument('--once', action='store_true', help='Run once then exit')
    
    args = parser.parse_args()
    
    service = DEVONzotAPIService()
    
    if args.dry_run:
        logger.info("🧪 Running dry-run analysis...")
        results = service.run_dry_run()
        print(f"\n{'='*50}")
        print("🧪 API DRY RUN ANALYSIS")
        print(f"{'='*50}")
        print(f"Total attachments: {results['attachments_found']}")
        print(f"Linked files: {results['linked_files']}")
        print(f"Linked URLs: {results['linked_urls']}")
        print(f"Conversion candidates: {len(results['conversion_candidates'])}")
        print(f"{'='*50}\n")
    else:
        success = await service.run_service_cycle(dry_run=False)
        if success:
            logger.info("✅ Service cycle completed successfully")
        else:
            logger.error("❌ Service cycle failed")

if __name__ == "__main__":
    asyncio.run(main())