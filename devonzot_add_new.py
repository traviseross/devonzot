#!/usr/bin/env python3
"""
DEVONzot API Service v2.0 - Add New URL Attachments
Creates NEW DEVONthink UUID attachments instead of modifying existing file links.
Includes confirmation workflow and cleanup of old file attachments.
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
import subprocess
from datetime import datetime
import re

# Configuration
ZOTERO_API_KEY = "Iy9J3VIgfoXUHrHIGkRgzTEJ"
ZOTERO_USER_ID = "617019"
ZOTERO_API_BASE = "https://api.zotero.org"
API_VERSION = "3"
RATE_LIMIT_DELAY = 1.0

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/Users/travisross/DEVONzot/api_v2_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class AttachmentPair:
    """Track paired old/new attachments"""
    old_key: str
    old_title: str
    old_path: str
    new_key: Optional[str]
    new_url: str
    parent_key: str
    parent_title: str
    uuid: str
    timestamp: str
    confirmed: bool = False
    old_deleted: bool = False

class ZoteroAPIClient:
    """Enhanced Zotero API client for add-new-attachment workflow"""
    
    def __init__(self, api_key: str, user_id: str):
        self.api_key = api_key
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers.update({
            'Zotero-API-Version': API_VERSION,
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'DEVONzot-AddNew-Service/2.0'
        })
        self.attachment_pairs = []
    
    def _rate_limit(self):
        """Respect API rate limits"""
        time.sleep(RATE_LIMIT_DELAY)
    
    def get_attachment_batch(self, start: int = 0, limit: int = 50) -> List[Dict]:
        """Get batch of file attachments needing UUID conversion"""
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
        """Get specific item by key"""
        self._rate_limit()
        
        url = f'{ZOTERO_API_BASE}/users/{self.user_id}/items/{item_key}'
        response = self.session.get(url)
        
        return response.json() if response.status_code == 200 else None
    
    def create_url_attachment(self, parent_key: str, title: str, url: str) -> Optional[str]:
        """Create new URL attachment and return its key"""
        self._rate_limit()
        
        attachment_data = {
            "itemType": "attachment",
            "parentItem": parent_key,
            "linkMode": "linked_url",
            "title": title,  # Use original title without prefix
            "url": url,
            "contentType": "application/pdf"
            # No tags - keep it clean
        }
        
        url_endpoint = f'{ZOTERO_API_BASE}/users/{self.user_id}/items'
        response = self.session.post(url_endpoint, json=[attachment_data])
        
        if response.status_code == 200:
            created_items = response.json()
            
            # Try different response formats
            new_key = None
            
            # Format 1: successful["0"]["key"]
            if created_items.get('successful') and '0' in created_items['successful']:
                new_key = created_items['successful']['0']['key']
            
            # Format 2: success["0"]
            elif created_items.get('success') and '0' in created_items['success']:
                new_key = created_items['success']['0']
            
            if new_key:
                logger.info(f"✅ Created UUID attachment: {title} (key: {new_key})")
                return new_key
            else:
                logger.error(f"Could not extract key from response: {created_items}")
        else:
            logger.error(f"API error creating attachment: {response.status_code} - {response.text}")
        
        return None
    
    def delete_attachment(self, attachment_key: str, version: int) -> bool:
        """Delete old file attachment"""
        self._rate_limit()
        
        url = f'{ZOTERO_API_BASE}/users/{self.user_id}/items/{attachment_key}'
        headers = {'If-Unmodified-Since-Version': str(version)}
        response = self.session.delete(url, headers=headers)
        
        if response.status_code == 204:
            logger.info(f"🗑️ Deleted old file attachment: {attachment_key}")
            return True
        else:
            logger.error(f"Failed to delete attachment {attachment_key}: {response.status_code}")
            return False

class DEVONthinkAPIInterface:
    """DEVONthink interface with smart search"""
    
    def __init__(self, wait_time: int = 2):
        self.wait_time = wait_time
    
    async def search_for_item_async(self, title: str) -> Optional[str]:
        """Search DEVONthink with smart keyword extraction"""
        try:
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
                        logger.info(f"🎯 Found match with '{search_term}': {uuid}")
                        return uuid
                
                await asyncio.sleep(0.5)
            
            return None
                
        except Exception as e:
            logger.error(f"Error searching DEVONthink: {e}")
            return None
    
    def _extract_search_terms(self, title: str) -> List[str]:
        """Extract meaningful search terms from filename"""
        clean_title = re.sub(r'\.(pdf|docx?|txt|html?)$', '', title, flags=re.IGNORECASE)
        terms = []
        
        # Author patterns
        author_match = re.match(r'^([A-Z][a-z]+(?:\s+et\s+al)?)', clean_title)
        if author_match:
            terms.append(author_match.group(1))
        
        # Key words
        words = re.findall(r'\b[A-Z][a-z]{3,}\b', clean_title)
        stop_words = {'Journal', 'Article', 'Document', 'Report', 'History', 'Review', 'Magazine', 'Book'}
        meaningful_words = [w for w in words if w not in stop_words and len(w) > 4]
        terms.extend(meaningful_words[:3])
        
        # Fallback
        if not terms:
            first_words = clean_title.split()[:2]
            terms.extend([w for w in first_words if len(w) > 3])
        
        return terms[:4]

class DEVONzotAddNewService:
    """Service that adds new UUID attachments instead of modifying existing ones"""
    
    def __init__(self):
        self.zotero = ZoteroAPIClient(ZOTERO_API_KEY, ZOTERO_USER_ID)
        self.devonthink = DEVONthinkAPIInterface()
        self.callback_file = Path('/Users/travisross/DEVONzot/attachment_pairs.json')
        self.load_attachment_pairs()
    
    def load_attachment_pairs(self):
        """Load previously created attachment pairs"""
        if self.callback_file.exists():
            try:
                with open(self.callback_file, 'r') as f:
                    data = json.load(f)
                    self.zotero.attachment_pairs = [
                        AttachmentPair(**item) for item in data
                    ]
                logger.info(f"Loaded {len(self.zotero.attachment_pairs)} attachment pairs")
            except Exception as e:
                logger.error(f"Error loading attachment pairs: {e}")
    
    def save_attachment_pairs(self):
        """Save attachment pairs for callback processing"""
        try:
            data = [
                {
                    'old_key': pair.old_key,
                    'old_title': pair.old_title,
                    'old_path': pair.old_path,
                    'new_key': pair.new_key,
                    'new_url': pair.new_url,
                    'parent_key': pair.parent_key,
                    'parent_title': pair.parent_title,
                    'uuid': pair.uuid,
                    'timestamp': pair.timestamp,
                    'confirmed': pair.confirmed,
                    'old_deleted': pair.old_deleted
                }
                for pair in self.zotero.attachment_pairs
            ]
            
            with open(self.callback_file, 'w') as f:
                json.dump(data, f, indent=2)
                
            logger.info(f"Saved {len(data)} attachment pairs to {self.callback_file}")
            
        except Exception as e:
            logger.error(f"Error saving attachment pairs: {e}")
    
    async def add_uuid_attachments(self, max_items: int = 10) -> Dict[str, int]:
        """Add new UUID attachments for file attachments"""
        logger.info(f"🔗 Adding UUID attachments for up to {max_items} file attachments...")
        
        results = {'added': 0, 'error': 0, 'skipped': 0}
        
        try:
            # Get file attachments that don't already have UUID counterparts
            items = self.zotero.get_attachment_batch(start=0, limit=100)
            
            candidates = []
            for item_data in items:
                data = item_data.get('data', {})
                
                if (data.get('linkMode') == 'linked_file' and 
                    data.get('path') and 
                    data.get('parentItem')):
                    
                    # Check if we already created a UUID attachment for this
                    existing_pair = next(
                        (p for p in self.zotero.attachment_pairs 
                         if p.old_key == data.get('key')), 
                        None
                    )
                    
                    if not existing_pair:
                        candidates.append({
                            'key': data.get('key', ''),
                            'version': data.get('version', 0),
                            'title': data.get('title', ''),
                            'path': data.get('path', ''),
                            'parent_key': data.get('parentItem')
                        })
            
            logger.info(f"Found {len(candidates)} new candidates for UUID attachment creation")
            
            # Process candidates
            for candidate in candidates[:max_items]:
                try:
                    # Search DEVONthink
                    uuid = await self.devonthink.search_for_item_async(candidate['title'])
                    
                    if uuid:
                        # Get parent item info
                        parent_item = self.zotero.get_item(candidate['parent_key'])
                        parent_title = "Unknown"
                        if parent_item:
                            parent_title = parent_item.get('data', {}).get('title', 'Unknown')
                        
                        # Create new UUID attachment
                        new_url = f"x-devonthink-item://{uuid}"
                        new_key = self.zotero.create_url_attachment(
                            candidate['parent_key'],
                            candidate['title'],
                            new_url
                        )
                        
                        if new_key:
                            # Track this pair
                            pair = AttachmentPair(
                                old_key=candidate['key'],
                                old_title=candidate['title'],
                                old_path=candidate['path'],
                                new_key=new_key,
                                new_url=new_url,
                                parent_key=candidate['parent_key'],
                                parent_title=parent_title,
                                uuid=uuid,
                                timestamp=datetime.now().isoformat()
                            )
                            
                            self.zotero.attachment_pairs.append(pair)
                            results['added'] += 1
                            
                            logger.info(f"📎 Added UUID attachment for: {candidate['title']}")
                        else:
                            results['error'] += 1
                    else:
                        logger.warning(f"❌ No DEVONthink match: {candidate['title']}")
                        results['skipped'] += 1
                    
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.error(f"Error processing {candidate['key']}: {e}")
                    results['error'] += 1
            
            # Save results
            self.save_attachment_pairs()
            
        except Exception as e:
            logger.error(f"Error in add process: {e}")
            results['error'] += 1
        
        return results
    
    def show_confirmation_report(self):
        """Show attachment pairs for user confirmation"""
        if not self.zotero.attachment_pairs:
            print("📋 No attachment pairs to review.")
            return
        
        unconfirmed = [p for p in self.zotero.attachment_pairs if not p.confirmed]
        
        if not unconfirmed:
            print("✅ All attachment pairs already confirmed.")
            return
        
        print(f"\n{'='*70}")
        print(f"🔍 ATTACHMENT PAIR REVIEW - {len(unconfirmed)} items to confirm")
        print(f"{'='*70}")
        
        for i, pair in enumerate(unconfirmed, 1):
            print(f"\n--- Pair {i} ---")
            print(f"Parent: {pair.parent_title}")
            print(f"Original: {pair.old_title}")
            print(f"File Path: {pair.old_path}")
            print(f"NEW UUID: {pair.new_url}")
            print(f"Old Zotero Link: zotero://select/items/{self.zotero.user_id}_{pair.old_key}")
            print(f"New Zotero Link: zotero://select/items/{self.zotero.user_id}_{pair.new_key}")
            print(f"Created: {pair.timestamp}")
        
        print(f"\n{'='*70}")
        print("🎯 NEXT STEPS:")
        print("1. Click 'New Zotero Link' above to test UUID attachments")
        print("2. Verify they open correctly in DEVONthink")
        print("3. Turn ON Zotero sync if currently disabled")
        print("4. Run with --confirm to delete old file attachments")
        print("5. Use --rollback if you want to undo and delete UUID attachments")
        print(f"{'='*70}")
    
    async def confirm_and_cleanup(self) -> Dict[str, int]:
        """Confirm UUID attachments work and delete old file attachments"""
        results = {'confirmed': 0, 'deleted': 0, 'error': 0}
        
        unconfirmed = [p for p in self.zotero.attachment_pairs if not p.confirmed]
        
        for pair in unconfirmed:
            try:
                # Get current version of old attachment for deletion
                old_item = self.zotero.get_item(pair.old_key)
                if old_item and not pair.old_deleted:
                    version = old_item.get('data', {}).get('version', 0)
                    
                    # Delete old file attachment
                    if self.zotero.delete_attachment(pair.old_key, version):
                        pair.old_deleted = True
                        results['deleted'] += 1
                    else:
                        results['error'] += 1
                        continue
                
                # Mark as confirmed
                pair.confirmed = True
                results['confirmed'] += 1
                
                logger.info(f"✅ Confirmed and cleaned up: {pair.old_title}")
                
            except Exception as e:
                logger.error(f"Error confirming pair {pair.old_key}: {e}")
                results['error'] += 1
        
        self.save_attachment_pairs()
        return results
    
    async def rollback_uuid_attachments(self) -> Dict[str, int]:
        """Rollback - delete UUID attachments and keep original files"""
        results = {'rolled_back': 0, 'error': 0}
        
        for pair in self.zotero.attachment_pairs:
            if pair.new_key and not pair.confirmed:
                try:
                    # Get current version of new UUID attachment
                    new_item = self.zotero.get_item(pair.new_key)
                    if new_item:
                        version = new_item.get('data', {}).get('version', 0)
                        
                        # Delete UUID attachment
                        if self.zotero.delete_attachment(pair.new_key, version):
                            results['rolled_back'] += 1
                            logger.info(f"🔄 Rolled back UUID attachment: {pair.old_title}")
                        else:
                            results['error'] += 1
                    
                except Exception as e:
                    logger.error(f"Error rolling back {pair.new_key}: {e}")
                    results['error'] += 1
        
        # Clear unconfirmed pairs
        self.zotero.attachment_pairs = [p for p in self.zotero.attachment_pairs if p.confirmed]
        self.save_attachment_pairs()
        
        return results

async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="DEVONzot Add-New UUID Attachments Service")
    parser.add_argument('--add', type=int, default=0, help='Add UUID attachments for N items')
    parser.add_argument('--review', action='store_true', help='Review attachment pairs created')
    parser.add_argument('--confirm', action='store_true', help='Confirm UUID attachments and delete old files')
    parser.add_argument('--rollback', action='store_true', help='Rollback - delete UUID attachments')
    
    args = parser.parse_args()
    
    service = DEVONzotAddNewService()
    
    if args.add > 0:
        logger.info(f"🔗 Adding UUID attachments for {args.add} items...")
        results = await service.add_uuid_attachments(max_items=args.add)
        print(f"\n{'='*40}")
        print("📊 ADD RESULTS")
        print(f"{'='*40}")
        print(f"Added: {results['added']}")
        print(f"Skipped: {results['skipped']}")
        print(f"Errors: {results['error']}")
        print(f"{'='*40}")
        
    elif args.review:
        service.show_confirmation_report()
        
    elif args.confirm:
        logger.info("✅ Confirming UUID attachments and cleaning up...")
        results = await service.confirm_and_cleanup()
        print(f"\n{'='*40}")
        print("📊 CONFIRMATION RESULTS")
        print(f"{'='*40}")
        print(f"Confirmed: {results['confirmed']}")
        print(f"Deleted: {results['deleted']}")
        print(f"Errors: {results['error']}")
        print(f"{'='*40}")
        
    elif args.rollback:
        logger.info("🔄 Rolling back UUID attachments...")
        results = await service.rollback_uuid_attachments()
        print(f"\n{'='*40}")
        print("📊 ROLLBACK RESULTS")
        print(f"{'='*40}")
        print(f"Rolled back: {results['rolled_back']}")
        print(f"Errors: {results['error']}")
        print(f"{'='*40}")
        
    else:
        print("Usage:")
        print("  --add N      Add UUID attachments for N file attachments")
        print("  --review     Review attachment pairs and get confirmation links")
        print("  --confirm    Confirm UUID attachments work and delete old files")
        print("  --rollback   Delete UUID attachments and keep original files")

if __name__ == "__main__":
    asyncio.run(main())