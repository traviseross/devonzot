#!/usr/bin/env python3
"""
DEVONzot Continuous Creator v3.0
Process A: Continuously creates UUID attachments for file attachments
Runs safely in background, never deletes anything
"""

import asyncio
import os
import json
import logging
import time
import requests
from typing import List, Dict, Optional
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
RATE_LIMIT_DELAY = float(os.environ.get("RATE_LIMIT_DELAY", 2.0))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", 5))
CYCLE_DELAY = int(os.environ.get("CYCLE_DELAY", 60))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - CREATOR - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.environ.get("CREATOR_LOG_PATH", "creator.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DEVONthinkInterface:
    """DEVONthink search interface"""
    
    async def search_for_item_async(self, title: str) -> Optional[str]:
        """Smart search with keyword extraction"""
        search_terms = self._extract_search_terms(title)
        
        for search_term in search_terms:
            try:
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
                        logger.debug(f"Found match with '{search_term}': {uuid[:8]}...")
                        return uuid
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"DEVONthink search error: {e}")
                continue
                
        return None
    
    def _extract_search_terms(self, title: str) -> List[str]:
        """Extract meaningful search terms"""
        clean_title = re.sub(r'\.(pdf|docx?|txt|html?)$', '', title, flags=re.IGNORECASE)
        terms = []
        
        # Author patterns
        author_match = re.match(r'^([A-Z][a-z]+(?:\s+et\s+al)?)', clean_title)
        if author_match:
            terms.append(author_match.group(1))
        
        # Key words
        words = re.findall(r'\b[A-Z][a-z]{4,}\b', clean_title)
        stop_words = {'Journal', 'Article', 'Document', 'Report', 'History', 'Review'}
        meaningful_words = [w for w in words if w not in stop_words]
        terms.extend(meaningful_words[:2])
        
        return terms[:3]

class ZoteroCreatorAPI:
    """Zotero API client for creating UUID attachments only"""
    
    def __init__(self, api_key: str, user_id: str):
        self.api_key = api_key
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers.update({
            'Zotero-API-Version': API_VERSION,
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'DEVONzot-Creator/3.0'
        })
    
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
    
    def get_file_attachments_needing_uuids(self, limit: int = 50) -> List[Dict]:
        """Get file attachments that don't have UUID counterparts yet"""
        params = {
            'itemType': 'attachment',
            'limit': limit,
            'format': 'json',
            'sort': 'dateModified',
            'direction': 'desc'
        }
        
        response = self._safe_request('GET', f'{ZOTERO_API_BASE}/users/{self.user_id}/items', params=params)
        
        if not response or response.status_code != 200:
            return []
        
        items = response.json()
        
        # Group by parent item to check for existing UUID attachments
        parent_groups = {}
        for item in items:
            data = item['data']
            parent_key = data.get('parentItem')
            if parent_key:
                if parent_key not in parent_groups:
                    parent_groups[parent_key] = {'file': [], 'uuid': []}
                
                if data.get('linkMode') == 'linked_file':
                    parent_groups[parent_key]['file'].append(data)
                elif data.get('linkMode') == 'linked_url' and 'x-devonthink-item://' in data.get('url', ''):
                    parent_groups[parent_key]['uuid'].append(data)
        
        # Find items with file attachments but no UUID attachments
        candidates = []
        for parent_key, attachments in parent_groups.items():
            if attachments['file'] and not attachments['uuid']:
                for file_att in attachments['file'][:1]:  # Process one per parent
                    candidates.append({
                        'key': file_att['key'],
                        'title': file_att['title'],
                        'parent_key': parent_key,
                        'path': file_att.get('path')
                    })
        
        return candidates
    
    def create_uuid_attachments(self, attachments: list) -> list:
        """Batch create new UUID attachments. Each item in attachments is a dict with parent_key, title, uuid_url."""
        batch = [
            {
                "itemType": "attachment",
                "parentItem": att["parent_key"],
                "linkMode": "linked_url",
                "title": att["title"],
                "url": att["uuid_url"],
                "contentType": "application/pdf"
            }
            for att in attachments
        ]
        response = self._safe_request('POST', f'{ZOTERO_API_BASE}/users/{self.user_id}/items', json=batch)
        results = []
        if response and response.status_code == 200:
            created_items = response.json()
            # Map results by index
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
    
    def get_parent_title(self, parent_key: str) -> str:
        """Get parent item title"""
        response = self._safe_request('GET', f'{ZOTERO_API_BASE}/users/{self.user_id}/items/{parent_key}')
        if response and response.status_code == 200:
            return response.json()['data'].get('title', 'Unknown')
        return 'Unknown'

class ContinuousCreator:
    """Continuously creates UUID attachments for file attachments"""
    
    def __init__(self):
        self.zotero = ZoteroCreatorAPI(ZOTERO_API_KEY, ZOTERO_USER_ID)
        self.devonthink = DEVONthinkInterface()
        self.stats = {'created': 0, 'skipped': 0, 'errors': 0, 'cycles': 0}
        self.running = False
    
    async def process_batch(self) -> Dict[str, int]:
        """Process one batch of file attachments using batching"""
        results = {'created': 0, 'skipped': 0, 'errors': 0}
        try:
            # Get file attachments needing UUIDs
            candidates = self.zotero.get_file_attachments_needing_uuids(BATCH_SIZE * 2)
            if not candidates:
                logger.debug("No file attachments needing UUID conversion found")
                return results
            logger.info(f"Processing {len(candidates)} candidates...")
            # Search DEVONthink for UUIDs in parallel (sequentially for now)
            batch_to_create = []
            candidate_map = {}
            for candidate in candidates[:BATCH_SIZE]:
                if not self.running:
                    break
                try:
                    uuid = await self.devonthink.search_for_item_async(candidate['title'])
                    if uuid:
                        batch_to_create.append({
                            "parent_key": candidate['parent_key'],
                            "title": candidate['title'],
                            "uuid_url": f"x-devonthink-item://{uuid}"
                        })
                        candidate_map[candidate['title']] = candidate
                    else:
                        logger.debug(f"❓ No DEVONthink match: {candidate['title'][:50]}...")
                        results['skipped'] += 1
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"Error processing {candidate['key']}: {e}")
                    results['errors'] += 1
            # Batch create UUID attachments
            if batch_to_create:
                batch_results = self.zotero.create_uuid_attachments(batch_to_create)
                for res in batch_results:
                    candidate = candidate_map.get(res['input']['title'])
                    if res['new_key']:
                        parent_title = self.zotero.get_parent_title(candidate['parent_key']) if candidate else ''
                        logger.info(f"✅ Created UUID attachment: {res['input']['title'][:50]}... for '{parent_title[:30]}...'")
                        results['created'] += 1
                    else:
                        logger.warning(f"❌ Failed to create UUID attachment for: {res['input']['title'][:50]}...")
                        results['errors'] += 1
        except Exception as e:
            logger.error(f"Error in batch processing: {e}")
            results['errors'] += 1
        return results
    
    async def run_continuous(self):
        """Run continuously in background"""
        self.running = True
        logger.info("🔄 Starting continuous UUID attachment creation...")
        
        try:
            while self.running:
                cycle_start = datetime.now()
                
                # Process one batch
                batch_results = await self.process_batch()
                
                # Update stats
                self.stats['created'] += batch_results['created']
                self.stats['skipped'] += batch_results['skipped']
                self.stats['errors'] += batch_results['errors']
                self.stats['cycles'] += 1
                
                # Log cycle summary
                cycle_time = (datetime.now() - cycle_start).total_seconds()
                if batch_results['created'] > 0 or self.stats['cycles'] % 10 == 0:
                    logger.info(
                        f"📊 Cycle {self.stats['cycles']}: "
                        f"Created={batch_results['created']}, "
                        f"Skipped={batch_results['skipped']}, "
                        f"Errors={batch_results['errors']}, "
                        f"Time={cycle_time:.1f}s"
                    )
                    logger.info(
                        f"📈 Total: Created={self.stats['created']}, "
                        f"Skipped={self.stats['skipped']}, "
                        f"Errors={self.stats['errors']}"
                    )
                
                # Wait before next cycle (unless we created items)
                if batch_results['created'] == 0:
                    logger.debug(f"💤 Waiting {CYCLE_DELAY}s before next cycle...")
                    await asyncio.sleep(CYCLE_DELAY)
                else:
                    await asyncio.sleep(5)  # Short delay when active
                    
        except KeyboardInterrupt:
            logger.info("🛑 Stopped by user")
        except Exception as e:
            logger.error(f"💥 Unexpected error: {e}")
        finally:
            self.running = False
            logger.info(f"📊 Final stats: Created={self.stats['created']}, Skipped={self.stats['skipped']}, Errors={self.stats['errors']}")

async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="DEVONzot Continuous Creator")
    parser.add_argument('--test', action='store_true', help='Run one test batch')  
    parser.add_argument('--once', action='store_true', help='Run one cycle then exit')
    parser.add_argument('--daemon', action='store_true', help='Run continuously in background')
    
    args = parser.parse_args()
    
    creator = ContinuousCreator()
    
    if args.test or args.once:
        logger.info("🧪 Running single batch...")
        results = await creator.process_batch()
        print(f"Results: {results}")
    elif args.daemon:
        logger.info("🚀 Starting continuous daemon mode...")
        await creator.run_continuous()
    else:
        print("Usage:")
        print("  --test    Run one test batch")
        print("  --once    Run one cycle then exit") 
        print("  --daemon  Run continuously in background")

if __name__ == "__main__":
    asyncio.run(main())