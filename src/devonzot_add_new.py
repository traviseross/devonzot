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
from dotenv import load_dotenv
import subprocess
from datetime import datetime
import re

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
        logging.FileHandler(os.environ.get("ADDNEW_LOG_PATH", "api_v2_service.log")),
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
    
    def _rate_limit(self, seconds=None):
        """Respect API rate limits"""
        time.sleep(seconds if seconds is not None else RATE_LIMIT_DELAY)
    
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
    
    def create_url_attachments(self, attachments: list) -> list:
        """Batch create new URL attachments. Each item in attachments is a dict with parent_key, title, url."""
        batch = [
            {
                "itemType": "attachment",
                "parentItem": att["parent_key"],
                "linkMode": "linked_url",
                "title": att["title"],
                "url": att["url"],
                "contentType": "application/pdf"
            }
            for att in attachments
        ]
        url_endpoint = f'{ZOTERO_API_BASE}/users/{self.user_id}/items'
        response = self._safe_request('POST', url_endpoint, json=batch)
        results = []
        if response and response.status_code == 200:
            created_items = response.json()
            for idx, att in enumerate(attachments):
                key = None
                if created_items.get('successful') and str(idx) in created_items['successful']:
                    key = created_items['successful'][str(idx)]['key']
                results.append({"input": att, "new_key": key})
        else:
            logger.error(f"Batch create failed: {response.status_code if response else 'No response'}")
            for att in attachments:
                results.append({"input": att, "new_key": None})
        return results

    def _safe_request(self, method: str, url: str, **kwargs):
        """API request with rate limiting, backoff, and retry-after handling"""
        max_retries = 5
        delay = RATE_LIMIT_DELAY
        for attempt in range(max_retries):
            response = None
            try:
                response = self.session.request(method, url, timeout=30, **kwargs)
            except Exception as e:
                logger.error(f"API request failed: {e}")
                time.sleep(delay)
                continue

            # Handle Backoff header
            backoff = response.headers.get("Backoff")
            if backoff:
                logger.warning(f"Received Backoff header: waiting {backoff} seconds")
                time.sleep(float(backoff))

            # Handle Retry-After header (429/503 or any response)
            if response.status_code in (429, 503):
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    logger.warning(f"Rate limited ({response.status_code}): waiting {retry_after} seconds")
                    time.sleep(float(retry_after))
                else:
                    logger.warning(f"Rate limited ({response.status_code}): exponential backoff {delay} seconds")
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
                continue

            # If not rate limited, return response
            return response
        logger.error("Max retries reached for Zotero API request.")
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
        self.callback_file = Path(os.environ.get("ATTACHMENT_PAIRS_PATH", "attachment_pairs.json"))
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
        """Add new UUID attachments for file attachments using batching"""
        logger.info(f"🔗 Adding UUID attachments for up to {max_items} file attachments...")
        results = {'added': 0, 'error': 0, 'skipped': 0}
        try:
            # Get file attachments that don't already have UUID counterparts
            items = self.zotero.get_attachment_batch(start=0, limit=100)
            candidates = []
            for item_data in items:
                data = item_data.get('data', {})
                if (data.get('linkMode') == 'linked_file' and data.get('path') and data.get('parentItem')):
                    existing_pair = next((p for p in self.zotero.attachment_pairs if p.old_key == data.get('key')), None)
                    if not existing_pair:
                        candidates.append({
                            'key': data.get('key', ''),
                            'version': data.get('version', 0),
                            'title': data.get('title', ''),
                            'path': data.get('path', ''),
                            'parent_key': data.get('parentItem')
                        })
            logger.info(f"Found {len(candidates)} new candidates for UUID attachment creation")
            # Search DEVONthink for UUIDs
            batch_to_create = []
            candidate_map = {}
            for candidate in candidates[:max_items]:
                try:
                    uuid = await self.devonthink.search_for_item_async(candidate['title'])
                    if uuid:
                        batch_to_create.append({
                            "parent_key": candidate['parent_key'],
                            "title": candidate['title'],
                            "url": f"x-devonthink-item://{uuid}"
                        })
                        candidate_map[candidate['title']] = (candidate, uuid)
                    else:
                        logger.warning(f"❌ No DEVONthink match: {candidate['title']}")
                        results['skipped'] += 1
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"Error processing {candidate['key']}: {e}")
                    results['error'] += 1
            # Batch create UUID attachments
            if batch_to_create:
                batch_results = self.zotero.create_url_attachments(batch_to_create)
                changed_files = []
                for res in batch_results:
                    candidate, uuid = candidate_map.get(res['input']['title'], (None, None))
                    if res['new_key'] and candidate:
                        parent_item = self.zotero.get_item(candidate['parent_key'])
                        parent_title = parent_item.get('data', {}).get('title', 'Unknown') if parent_item else 'Unknown'
                        pair = AttachmentPair(
                            old_key=candidate['key'],
                            old_title=candidate['title'],
                            old_path=candidate['path'],
                            new_key=res['new_key'],
                            new_url=res['input']['url'],
                            parent_key=candidate['parent_key'],
                            parent_title=parent_title,
                            uuid=uuid,
                            timestamp=datetime.now().isoformat()
                        )
                        self.zotero.attachment_pairs.append(pair)
                        results['added'] += 1
                        logger.info(f"📎 Added UUID attachment for: {candidate['title']}")
                        changed_files.append({
                            'title': candidate['title'],
                            'old_key': candidate['key'],
                            'new_key': res['new_key'],
                            'parent_key': candidate['parent_key'],
                            'timestamp': pair.timestamp
                        })
                        # Delete previous linked file attachment immediately
                        old_item = self.zotero.get_item(candidate['key'])
                        if old_item:
                            version = old_item.get('data', {}).get('version', 0)
                            if self.zotero.delete_attachment(candidate['key'], version):
                                logger.info(f"🗑️ Deleted old file attachment: {candidate['key']}")
                                pair.old_deleted = True
                    else:
                        results['error'] += 1
                # Write changed files to a log for inspection
                if changed_files:
                    with open('changed_files_log.json', 'a') as f:
                        for entry in changed_files:
                            f.write(json.dumps(entry) + '\n')
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
    parser.add_argument('--loop15', action='store_true', help='Run batch every 2 minutes for 15 minutes')

    args = parser.parse_args()

    service = DEVONzotAddNewService()

    if args.loop15:
        import time
        start = time.time()
        end = start + 15*60
        cycle = 1
        while time.time() < end:
            logger.info(f"⏳ Loop cycle {cycle} (15-min watch mode)")
            results = await service.add_uuid_attachments(max_items=3)
            print(f"Cycle {cycle}: Added={results['added']} Skipped={results['skipped']} Errors={results['error']}")
            cycle += 1
            if time.time() < end:
                time.sleep(120)
        print("15-minute loop complete.")
    elif args.add > 0:
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
        print("  --loop15     Run batch every 2 minutes for 15 minutes (watch mode)")

if __name__ == "__main__":
    asyncio.run(main())