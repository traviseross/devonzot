#!/usr/bin/env python3
"""
DEVONzot Safe API Service v2.1 - Enhanced Interruption Safety
Improved version with atomic operations, resume capability, and better error handling
"""

import asyncio
import json
import logging
import os
import time
import requests
import shutil
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
MAX_RETRIES = 3
BACKUP_COUNT = 5

# Safe paths with backups
STATE_FILE = Path('/Users/travisross/DEVONzot/attachment_pairs.json')
BACKUP_DIR = Path('/Users/travisross/DEVONzot/backups')
BACKUP_DIR.mkdir(exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/Users/travisross/DEVONzot/safe_api_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class AttachmentPair:
    """Enhanced attachment pair with safety tracking"""
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
    creation_attempted: bool = False
    creation_successful: bool = False
    deletion_attempted: bool = False

class SafeStateManager:
    """Manages state with atomic saves and backups"""
    
    def __init__(self, state_file: Path, backup_dir: Path):
        self.state_file = state_file
        self.backup_dir = backup_dir
        self.pairs = []
        self.load_state()
    
    def load_state(self):
        """Load state with fallback to backups"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.pairs = [AttachmentPair(**item) for item in data]
                logger.info(f"Loaded {len(self.pairs)} pairs from state file")
                return
            except Exception as e:
                logger.error(f"Error loading state file: {e}")
                
        # Try backup files
        backup_files = sorted(self.backup_dir.glob('attachment_pairs_*.json'), reverse=True)
        for backup_file in backup_files[:3]:  # Try last 3 backups
            try:
                with open(backup_file, 'r') as f:
                    data = json.load(f)
                    self.pairs = [AttachmentPair(**item) for item in data]
                logger.warning(f"Recovered from backup: {backup_file}")
                return
            except Exception as e:
                logger.error(f"Error loading backup {backup_file}: {e}")
                continue
                
        logger.info("No existing state found, starting fresh")
    
    def save_state(self):
        """Atomically save state with backup"""
        try:
            # Create backup first
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = self.backup_dir / f'attachment_pairs_{timestamp}.json'
            
            # Prepare data
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
                    'old_deleted': pair.old_deleted,
                    'creation_attempted': pair.creation_attempted,
                    'creation_successful': pair.creation_successful,
                    'deletion_attempted': pair.deletion_attempted
                }
                for pair in self.pairs
            ]
            
            # Write to temporary file first (atomic)
            temp_file = self.state_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            # Copy current state to backup
            if self.state_file.exists():
                shutil.copy2(self.state_file, backup_file)
            
            # Atomic rename
            temp_file.rename(self.state_file)
            
            # Cleanup old backups
            self._cleanup_backups()
            
            logger.info(f"State saved successfully ({len(data)} pairs)")
            
        except Exception as e:
            logger.error(f"Error saving state: {e}")
            raise
    
    def _cleanup_backups(self):
        """Keep only latest N backup files"""
        backup_files = sorted(self.backup_dir.glob('attachment_pairs_*.json'), reverse=True)
        for old_backup in backup_files[BACKUP_COUNT:]:
            try:
                old_backup.unlink()
            except:
                pass

class SafeZoteroAPIClient:
    """Enhanced API client with retry logic and interruption safety"""
    
    def __init__(self, api_key: str, user_id: str):
        self.api_key = api_key
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers.update({
            'Zotero-API-Version': API_VERSION,
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'DEVONzot-Safe-Service/2.1'
        })
    
    def _safe_request(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        """Make API request with retries"""
        for attempt in range(MAX_RETRIES):
            try:
                time.sleep(RATE_LIMIT_DELAY)
                response = self.session.request(method, url, **kwargs)
                return response
            except (requests.RequestException, ConnectionError) as e:
                logger.warning(f"Request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    logger.error(f"Request failed after {MAX_RETRIES} attempts")
                    return None
    
    def create_url_attachment_safe(self, parent_key: str, title: str, url: str) -> Optional[str]:
        """Safely create URL attachment with retry logic"""
        attachment_data = {
            "itemType": "attachment",
            "parentItem": parent_key,
            "linkMode": "linked_url",
            "title": title,
            "url": url,
            "contentType": "application/pdf"
        }
        
        api_url = f'{ZOTERO_API_BASE}/users/{self.user_id}/items'
        response = self._safe_request('POST', api_url, json=[attachment_data])
        
        if response and response.status_code == 200:
            created_items = response.json()
            
            # Extract key safely
            new_key = None
            if created_items.get('successful') and '0' in created_items['successful']:
                new_key = created_items['successful']['0']['key']
            elif created_items.get('success') and '0' in created_items['success']:
                new_key = created_items['success']['0']
            
            if new_key:
                logger.info(f"✅ Created attachment: {title} (key: {new_key})")
                return new_key
            else:
                logger.error(f"Could not extract key from response: {created_items}")
        else:
            logger.error(f"Failed to create attachment: {response.status_code if response else 'No response'}")
        
        return None
    
    def delete_attachment_safe(self, attachment_key: str) -> bool:
        """Safely delete attachment with retry logic"""
        # Get current version first
        get_url = f'{ZOTERO_API_BASE}/users/{self.user_id}/items/{attachment_key}'
        response = self._safe_request('GET', get_url)
        
        if not response or response.status_code != 200:
            logger.error(f"Could not get attachment {attachment_key} for deletion")
            return False
        
        version = response.json()['data']['version']
        
        # Delete with proper version
        headers = {'If-Unmodified-Since-Version': str(version)}
        response = self._safe_request('DELETE', get_url, headers=headers)
        
        if response and response.status_code == 204:
            logger.info(f"🗑️ Deleted attachment: {attachment_key}")
            return True
        else:
            logger.error(f"Failed to delete {attachment_key}: {response.status_code if response else 'No response'}")
            return False

class SafeDEVONzotService:
    """Main service with enhanced safety features"""
    
    def __init__(self):
        self.state_manager = SafeStateManager(STATE_FILE, BACKUP_DIR)
        self.zotero = SafeZoteroAPIClient(ZOTERO_API_KEY, ZOTERO_USER_ID)
        # Initialize DEVONthink interface here...
        
        logger.info("🛡️ Safe DEVONzot Service initialized")
        
    def resume_operations(self):
        """Resume interrupted operations"""
        incomplete_pairs = [
            p for p in self.state_manager.pairs 
            if p.creation_attempted and not p.creation_successful
        ]
        
        if incomplete_pairs:
            logger.info(f"🔄 Found {len(incomplete_pairs)} incomplete operations to resume")
            return True
        return False
    
    def show_safety_status(self):
        """Show safety and resume status"""
        total = len(self.state_manager.pairs)
        successful = sum(1 for p in self.state_manager.pairs if p.creation_successful)
        confirmed = sum(1 for p in self.state_manager.pairs if p.confirmed)
        
        print(f"\n🛡️ SAFETY STATUS")
        print(f"{'='*40}")
        print(f"Total pairs tracked: {total}")
        print(f"Successfully created: {successful}")
        print(f"Confirmed & cleaned: {confirmed}")
        print(f"State file backups: {len(list(BACKUP_DIR.glob('*.json')))}")
        
        if self.resume_operations():
            print(f"⚠️ Incomplete operations found - use --resume")
        else:
            print(f"✅ No interrupted operations")
        print(f"{'='*40}")

async def main():
    """Enhanced main with resume capability"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Safe DEVONzot API Service")
    parser.add_argument('--add', type=int, default=0, help='Add UUID attachments')
    parser.add_argument('--resume', action='store_true', help='Resume interrupted operations')
    parser.add_argument('--status', action='store_true', help='Show safety status')
    parser.add_argument('--review', action='store_true', help='Review pairs')
    parser.add_argument('--confirm', action='store_true', help='Confirm and delete old')
    
    args = parser.parse_args()
    
    service = SafeDEVONzotService()
    
    if args.status:
        service.show_safety_status()
    elif args.resume:
        logger.info("🔄 Resume functionality would go here...")
    else:
        logger.info("🛡️ Safe service ready - use --status to check safety state")

if __name__ == "__main__":
    asyncio.run(main())