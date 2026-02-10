#!/usr/bin/env python3
"""
DEVONzot API Service with Inspection Mode
- Converts file links to DEVONthink UUIDs via API
- Tracks all modified items for inspection in Zotero
- Outputs Zotero URLs for easy access to changed items
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
ZOTERO_API_BASE = os.environ.get("ZOTERO_API_BASE", "https://api.zotero.org")
API_VERSION = os.environ.get("API_VERSION", "3")
RATE_LIMIT_DELAY = float(os.environ.get("RATE_LIMIT_DELAY", 1.0))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.environ.get("INSPECTOR_LOG_PATH", "api_service.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class ModifiedItem:
    """Track items modified by the service"""
    attachment_key: str
    parent_key: Optional[str]
    attachment_title: str
    parent_title: Optional[str]
    old_path: Optional[str]
    new_url: str
    zotero_url: str
    timestamp: str

class ZoteroAPIClient:
    """Zotero Web API client with modification tracking"""
    
    def __init__(self, api_key: str, user_id: str):
        self.api_key = api_key
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers.update({
            'Zotero-API-Version': API_VERSION,
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'DEVONzot-API-Service/1.1.0'
        })
        self.modified_items = []
    
    def _rate_limit(self):
        """Respect API rate limits"""
        time.sleep(RATE_LIMIT_DELAY)
    
    def get_attachment_batch(self, start: int = 0, limit: int = 50) -> List[Dict]:
        """Get a batch of attachments from API"""
        self._rate_limit()
        
        params = {
            'itemType': 'attachment',
            'limit': limit,
            'start': start,
            'format': 'json'
        }
        
        url = f'{ZOTERO_API_BASE}/users/{self.user_id}/items'
        response = self.session.get(url, params=params)
        
        if response.status_code != 200:
            logger.error(f"API error: {response.status_code} - {response.text}")
            return []
            
        return response.json()
    
    def get_item(self, item_key: str) -> Optional[Dict]:
        """Get a specific item by key"""
        self._rate_limit()
        
        url = f'{ZOTERO_API_BASE}/users/{self.user_id}/items/{item_key}'
        response = self.session.get(url)
        
        if response.status_code != 200:
            return None
            
        return response.json()
    
    def update_attachment_url(self, attachment_key: str, new_url: str, version: int, 
                            attachment_title: str, parent_key: Optional[str] = None) -> bool:
        """Update attachment URL via API and track the change"""
        self._rate_limit()
        
        # First get current attachment data
        url = f'{ZOTERO_API_BASE}/users/{self.user_id}/items/{attachment_key}'
        response = self.session.get(url)
        
        if response.status_code != 200:
            logger.error(f"Failed to get attachment {attachment_key}: {response.status_code}")
            return False
        
        current_data = response.json()['data']
        old_path = current_data.get('path')
        
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
            logger.info(f"✅ Updated attachment: {attachment_title}")
            
            # Get parent item info if available
            parent_title = None
            if parent_key:
                parent_item = self.get_item(parent_key)
                if parent_item:
                    parent_title = parent_item.get('data', {}).get('title', 'Unknown')
            
            # Track this modification
            modified_item = ModifiedItem(
                attachment_key=attachment_key,
                parent_key=parent_key,
                attachment_title=attachment_title,
                parent_title=parent_title,
                old_path=old_path,
                new_url=new_url,
                zotero_url=f"zotero://select/items/{self.user_id}_{attachment_key}",
                timestamp=datetime.now().isoformat()
            )
            self.modified_items.append(modified_item)
            
            return True
        else:
            logger.error(f"Failed to update attachment {attachment_key}: {response.status_code}")
            return False

class DEVONthinkAPIInterface:
    """DEVONthink interface for API service"""
    
    def __init__(self, wait_time: int = 3):
        self.wait_time = wait_time
    
    async def search_for_item_async(self, title: str) -> Optional[str]:
        """Async DEVONthink search with smart keyword extraction"""
        try:
            # Extract meaningful search terms from filename
            search_terms = self._extract_search_terms(title)
            
            for search_term in search_terms:
                search_clean = search_term.replace('"', '\\"')
                
                script = f'''
                tell application "DEVONthink 3"
                    set searchResults to search "{search_clean}"
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
                    if uuid:
                        logger.info(f"🎯 Found match with term '{search_term}': {uuid}")
                        return uuid
                else:
                    logger.debug(f"Search failed for term '{search_term}': {stderr.decode()}")
                    
                # Small delay between searches
                await asyncio.sleep(0.5)
            
            return None
                
        except Exception as e:
            logger.error(f"Error searching DEVONthink: {e}")
            return None
    
    def _extract_search_terms(self, title: str) -> List[str]:
        """Extract meaningful search terms from filename"""
        import re
        
        # Remove file extension
        clean_title = re.sub(r'\.(pdf|docx?|txt|html?)$', '', title, flags=re.IGNORECASE)
        
        # Extract potential author names (first part before dash or common patterns)
        terms = []
        
        # Look for author patterns like "Smith - Title" or "Smith et al - Title"
        author_match = re.match(r'^([A-Z][a-z]+(?:\s+et\s+al)?)', clean_title)
        if author_match:
            terms.append(author_match.group(1))
        
        # Extract key words (capitalize words, remove common terms)
        words = re.findall(r'\b[A-Z][a-z]{3,}\b', clean_title)
        stop_words = {'Journal', 'Article', 'Document', 'Report', 'History', 'Review', 'Magazine', 'Book'}
        meaningful_words = [w for w in words if w not in stop_words and len(w) > 4]
        
        # Add top meaningful words
        terms.extend(meaningful_words[:3])
        
        # If no good terms found, try the first few words
        if not terms:
            first_words = clean_title.split()[:2]
            terms.extend([w for w in first_words if len(w) > 3])
        
        logger.debug(f"Extracted search terms from '{title}': {terms}")
        return terms[:4]  # Limit to 4 terms max

class DEVONzotAPIService:
    """Main API-based service with inspection tracking"""
    
    def __init__(self):
        self.zotero = ZoteroAPIClient(ZOTERO_API_KEY, ZOTERO_USER_ID)
        self.devonthink = DEVONthinkAPIInterface()
    
    async def convert_batch(self, max_items: int = 10, dry_run: bool = False) -> Dict[str, int]:
        """Convert a small batch of file links for testing"""
        logger.info(f"🔗 Converting up to {max_items} file links to UUID links...")
        
        results = {'success': 0, 'error': 0, 'skipped': 0, 'processed': 0}
        
        try:
            # Get first batch of attachments
            items = self.zotero.get_attachment_batch(start=0, limit=100)
            
            # Filter for linked files that need conversion
            conversion_candidates = []
            for item_data in items:
                data = item_data.get('data', {})
                
                if (data.get('linkMode') == 'linked_file' and 
                    data.get('path') and 
                    not (data.get('url') and 'x-devonthink-item://' in data.get('url', ''))):
                    
                    conversion_candidates.append({
                        'key': data.get('key', ''),
                        'version': data.get('version', 0),
                        'title': data.get('title', ''),
                        'path': data.get('path', ''),
                        'parent_key': data.get('parentItem')
                    })
            
            logger.info(f"Found {len(conversion_candidates)} conversion candidates")
            
            # Process up to max_items
            for candidate in conversion_candidates[:max_items]:
                try:
                    # Search DEVONthink for this item
                    uuid = await self.devonthink.search_for_item_async(candidate['title'])
                    
                    if uuid:
                        logger.info(f"📍 Found DEVONthink match: {candidate['title']}")
                        
                        if not dry_run:
                            # Update via API
                            new_url = f"x-devonthink-item://{uuid}"
                            success = self.zotero.update_attachment_url(
                                candidate['key'], 
                                new_url, 
                                candidate['version'],
                                candidate['title'],
                                candidate['parent_key']
                            )
                            
                            if success:
                                results['success'] += 1
                            else:
                                results['error'] += 1
                        else:
                            logger.info(f"DRY RUN: Would convert {candidate['title']}")
                            results['success'] += 1
                    else:
                        logger.warning(f"❌ No DEVONthink match: {candidate['title']}")
                        results['skipped'] += 1
                    
                    results['processed'] += 1
                    
                    # Small delay between items
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.error(f"Error processing {candidate['key']}: {e}")
                    results['error'] += 1
                    
        except Exception as e:
            logger.error(f"Error in conversion process: {e}")
            results['error'] += 1
        
        return results
    
    def print_inspection_report(self):
        """Print a report of modified items for Zotero inspection"""
        if not self.zotero.modified_items:
            print("\n📋 No items were modified.")
            return
        
        print(f"\n{'='*60}")
        print(f"🔍 INSPECTION REPORT - {len(self.zotero.modified_items)} items modified")
        print(f"{'='*60}")
        
        for i, item in enumerate(self.zotero.modified_items, 1):
            print(f"\n--- Modified Item {i} ---")
            print(f"Attachment: {item.attachment_title}")
            if item.parent_title:
                print(f"Parent Item: {item.parent_title}")
            print(f"Changed: File → DEVONthink UUID")
            print(f"New URL: {item.new_url}")
            print(f"Zotero Link: {item.zotero_url}")
            print(f"Time: {item.timestamp}")
        
        print(f"\n{'='*60}")
        print("🎯 To inspect in Zotero:")
        print("1. Copy any 'Zotero Link' above")
        print("2. Paste into browser address bar")
        print("3. Zotero will open and highlight the item")
        print(f"{'='*60}\n")

async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="DEVONzot API Service with Inspection")
    parser.add_argument('--dry-run', action='store_true', help='Analyze without making changes')
    parser.add_argument('--test', type=int, default=5, help='Test conversion on N items (default: 5)')
    
    args = parser.parse_args()
    
    service = DEVONzotAPIService()
    
    if args.dry_run:
        logger.info(f"🧪 Testing conversion on {args.test} items (dry run)...")
    else:
        logger.info(f"🔄 Converting {args.test} items...")
    
    # Run conversion
    results = await service.convert_batch(max_items=args.test, dry_run=args.dry_run)
    
    # Show results
    print(f"\n{'='*40}")
    print("📊 CONVERSION RESULTS")
    print(f"{'='*40}")
    print(f"Processed: {results['processed']}")
    print(f"Successful: {results['success']}")
    print(f"Skipped: {results['skipped']}")
    print(f"Errors: {results['error']}")
    print(f"{'='*40}")
    
    # Show inspection report
    service.print_inspection_report()

if __name__ == "__main__":
    asyncio.run(main())