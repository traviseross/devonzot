#!/usr/bin/env python3
"""
Shared Zotero Web API client for all DEVONzot operations.

Replaces all direct SQLite database queries with Zotero Web API v3 calls.
Used by devonzot_service.py, devonzot_add_new.py, and diagnose_attachments.py.
"""

import logging
import re
import time
import requests
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

# linkMode mapping: API strings <-> internal integer codes
LINK_MODE_MAP = {
    'imported_file': 0,
    'imported_url': 1,
    'linked_file': 2,
    'linked_url': 3,
}


class ZoteroAPIClient:
    """Unified Zotero Web API client for all DEVONzot operations."""

    # Default hosted Zotero translation server (same one used by zotero.org/save)
    DEFAULT_TRANSLATION_SERVER = "https://t0guvf0w17.execute-api.us-east-1.amazonaws.com/Prod"

    def __init__(self, api_key: str, user_id: str,
                 api_base: str = "https://api.zotero.org",
                 api_version: str = "3",
                 rate_limit_delay: float = 0.0,
                 translation_server_url: str = None,
                 translation_timeout: float = 30.0):
        self.api_key = api_key
        self.user_id = user_id
        self.api_base = api_base.rstrip('/')
        self.rate_limit_delay = rate_limit_delay
        self.translation_server_url = (
            translation_server_url or self.DEFAULT_TRANSLATION_SERVER
        ).rstrip('/')
        self.translation_timeout = translation_timeout
        self.session = requests.Session()
        self.session.headers.update({
            'Zotero-API-Version': api_version,
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'DEVONzot-Service/2.0',
        })
        self.last_library_version: Optional[int] = None
        self._attachment_cache: Optional[List[Dict]] = None
        self._collection_name_cache: Optional[Dict[str, str]] = None
        self.attachment_pairs = []  # Used by devonzot_add_new.py workflow

    # ── Rate limiting & request helpers ────────────────────────────

    def _rate_limit(self, seconds=None):
        """Respect API rate limits."""
        time.sleep(seconds if seconds is not None else self.rate_limit_delay)

    def _safe_request(self, method: str, url: str, **kwargs):
        """API request with rate limiting, backoff, and retry-after handling."""
        max_retries = 5
        delay = self.rate_limit_delay
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

            # Handle Retry-After header (429/503)
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

            return response

        logger.error("Max retries reached for Zotero API request.")
        return None

    # ── Pagination ─────────────────────────────────────────────────

    def _get_all_items_paginated(self, params: Dict[str, Any]) -> List[Dict]:
        """Fetch all items matching params, handling pagination (max 100 per page)."""
        all_items = []
        start = 0
        limit = 100  # API max

        while True:
            self._rate_limit()
            page_params = {**params, 'start': start, 'limit': limit, 'format': 'json'}
            url = f'{self.api_base}/users/{self.user_id}/items'
            response = self._safe_request('GET', url, params=page_params)

            if not response or response.status_code != 200:
                logger.error(f"Pagination request failed at start={start}")
                break

            version = response.headers.get('Last-Modified-Version')
            if version:
                self.last_library_version = int(version)

            items = response.json()
            all_items.extend(items)

            total_results = int(response.headers.get('Total-Results', len(items)))
            logger.info(f"Fetched {len(all_items)}/{total_results} items...")
            if start + limit >= total_results:
                break

            start += limit

        return all_items

    def _get_all_collections_paginated(self) -> List[Dict]:
        """Fetch all collections, handling pagination."""
        all_collections = []
        start = 0
        limit = 100

        while True:
            self._rate_limit()
            params = {'start': start, 'limit': limit, 'format': 'json'}
            url = f'{self.api_base}/users/{self.user_id}/collections'
            response = self._safe_request('GET', url, params=params)

            if not response or response.status_code != 200:
                logger.error(f"Collections pagination failed at start={start}")
                break

            collections = response.json()
            all_collections.extend(collections)

            total_results = int(response.headers.get('Total-Results', len(collections)))
            if start + limit >= total_results:
                break

            start += limit

        return all_collections

    # ── Caching ────────────────────────────────────────────────────

    def invalidate_caches(self):
        """Invalidate all caches. Call at the start of each service cycle."""
        self._attachment_cache = None
        self._collection_name_cache = None

    def _get_all_attachments_cached(self) -> List[Dict]:
        """Fetch all attachments once per service cycle."""
        if self._attachment_cache is None:
            self._attachment_cache = self._get_all_items_paginated({'itemType': 'attachment'})
        return self._attachment_cache

    def get_collection_name_map(self) -> Dict[str, str]:
        """Get a mapping of collection keys to collection names. Cached per cycle."""
        if self._collection_name_cache is None:
            collections = self._get_all_collections_paginated()
            self._collection_name_cache = {}
            for coll in collections:
                data = coll.get('data', coll)
                self._collection_name_cache[data.get('key', '')] = data.get('name', '')
        return self._collection_name_cache

    # ── Conversion helpers ─────────────────────────────────────────

    def _api_item_to_zotero_item(self, api_data: Dict) -> 'ZoteroItem':
        """Convert Zotero API JSON response to ZoteroItem dataclass.

        Import is deferred to avoid circular imports — callers in
        devonzot_service.py define ZoteroItem.
        """
        from devonzot_service import ZoteroItem

        data = api_data.get('data', api_data)

        # Parse year from date
        year = None
        date_str = data.get('date', '') or ''
        if date_str:
            year_match = re.search(r'\b(19|20)\d{2}\b', date_str)
            if year_match:
                year = int(year_match.group())

        # Resolve collection keys to names
        collection_keys = data.get('collections', [])
        collection_names = []
        if collection_keys:
            name_map = self.get_collection_name_map()
            collection_names = [name_map.get(k, k) for k in collection_keys]

        return ZoteroItem(
            key=data.get('key', ''),
            title=data.get('title', ''),
            creators=data.get('creators', []),
            item_type=data.get('itemType', ''),
            publication=data.get('publicationTitle'),
            date=data.get('date'),
            year=year,
            doi=data.get('DOI'),
            url=data.get('url'),
            abstract=data.get('abstractNote'),
            tags=[t.get('tag', '') for t in data.get('tags', [])],
            collections=collection_names,
            date_added=data.get('dateAdded', ''),
            date_modified=data.get('dateModified', ''),
            version=data.get('version', 0),
        )

    def _api_item_to_zotero_attachment(self, api_data: Dict) -> 'ZoteroAttachment':
        """Convert Zotero API attachment JSON to ZoteroAttachment dataclass."""
        from devonzot_service import ZoteroAttachment

        data = api_data.get('data', api_data)

        return ZoteroAttachment(
            key=data.get('key', ''),
            parent_key=data.get('parentItem'),
            link_mode=LINK_MODE_MAP.get(data.get('linkMode', ''), -1),
            content_type=data.get('contentType', ''),
            path=data.get('path'),
            storage_hash=data.get('md5'),
            version=data.get('version', 0),
            filename=data.get('filename'),
            url=data.get('url'),
        )

    # ── Item retrieval (replaces ZoteroDatabase methods) ───────────

    def get_item(self, item_key: str) -> Optional['ZoteroItem']:
        """Get complete item metadata by key.

        Replaces ZoteroDatabase.get_item_by_id().
        Returns a ZoteroItem dataclass or None.
        """
        self._rate_limit()
        url = f'{self.api_base}/users/{self.user_id}/items/{item_key}'
        response = self._safe_request('GET', url)

        if not response or response.status_code != 200:
            return None

        return self._api_item_to_zotero_item(response.json())

    def get_item_raw(self, item_key: str) -> Optional[Dict]:
        """Get raw API JSON for an item by key.

        Used by devonzot_add_new.py which expects raw dicts.
        """
        self._rate_limit()
        url = f'{self.api_base}/users/{self.user_id}/items/{item_key}'
        response = self._safe_request('GET', url)
        return response.json() if response and response.status_code == 200 else None

    def get_items_needing_sync(self, since_version: int = None,
                              processed_items: List[str] = None) -> List['ZoteroItem']:
        """Get items that need syncing to DEVONthink.

        Replaces ZoteroDatabase.get_items_needing_sync().
        Fetches non-attachment, non-note items not yet processed.
        DEVONthink links are stored as linked_url child attachments,
        so we check processed_items rather than the parent URL field.
        """
        params = {}
        if since_version is not None:
            params['since'] = since_version

        all_items = self._get_all_items_paginated(params)
        processed = set(processed_items or [])

        result = []
        for api_item in all_items:
            data = api_item.get('data', {})
            item_type = data.get('itemType', '')

            # Skip notes and attachments
            if item_type in ('note', 'attachment'):
                continue

            # Skip already-processed items
            item_key = data.get('key', '')
            if item_key in processed:
                continue

            # Also skip if parent URL still has legacy DEVONthink link
            url = data.get('url', '') or ''
            if url.startswith('x-devonthink-item://'):
                continue

            result.append(self._api_item_to_zotero_item(api_item))

        return result

    def get_stored_attachments(self) -> List['ZoteroAttachment']:
        """Get imported_file attachments in Zotero storage (linkMode=0 only).

        imported_url (linkMode=1) items are handled separately by
        get_imported_url_attachments() and deleted in Phase 0.

        Matches by storage: path prefix OR by filename field (API v3
        returns filename instead of path for imported attachments).
        """
        all_attachments = self._get_all_attachments_cached()

        result = []
        for api_item in all_attachments:
            data = api_item.get('data', {})
            link_mode = data.get('linkMode', '')
            path = data.get('path', '') or ''
            filename = data.get('filename', '') or ''

            if link_mode == 'imported_file' and (
                (path and path.startswith('storage:')) or filename
            ):
                result.append(self._api_item_to_zotero_attachment(api_item))

        return result

    def get_imported_url_attachments(self) -> List['ZoteroAttachment']:
        """Get imported_url attachments (linkMode=1) for deletion.

        These are URL snapshots stored in Zotero storage.
        """
        all_attachments = self._get_all_attachments_cached()

        result = []
        for api_item in all_attachments:
            data = api_item.get('data', {})
            link_mode = data.get('linkMode', '')

            if link_mode == 'imported_url':
                result.append(self._api_item_to_zotero_attachment(api_item))

        return result

    def get_zotfile_symlinks(self) -> List['ZoteroAttachment']:
        """Get ZotFile linked file attachments.

        Replaces ZoteroDatabase.get_zotfile_symlinks().
        """
        all_attachments = self._get_all_attachments_cached()

        result = []
        for api_item in all_attachments:
            data = api_item.get('data', {})
            link_mode = data.get('linkMode', '')

            if link_mode == 'linked_file':
                result.append(self._api_item_to_zotero_attachment(api_item))

        return result

    # ── Item mutation ──────────────────────────────────────────────

    def update_item_url(self, item_key: str, devonthink_uuid: str, dry_run=False) -> bool:
        """Update item URL to DEVONthink UUID link via PATCH.

        Replaces ZoteroDatabase.update_item_url().
        Uses optimistic concurrency with If-Unmodified-Since-Version.
        """
        devonthink_url = f"x-devonthink-item://{devonthink_uuid}"

        if dry_run:
            logger.info(f"[DRY RUN] Would update item {item_key} URL to {devonthink_url}")
            return True

        # Fetch current version
        self._rate_limit()
        item_url = f'{self.api_base}/users/{self.user_id}/items/{item_key}'
        response = self._safe_request('GET', item_url)

        if not response or response.status_code != 200:
            logger.error(f"Failed to fetch item {item_key} for URL update")
            return False

        current_version = response.json().get('version', 0)

        # PATCH the URL field
        self._rate_limit()
        patch_data = {'url': devonthink_url}
        headers = {'If-Unmodified-Since-Version': str(current_version)}

        response = self._safe_request('PATCH', item_url, json=patch_data, headers=headers)

        if response and response.status_code in (200, 204):
            logger.info(f"Updated item {item_key} with DEVONthink UUID: {devonthink_uuid}")
            return True
        elif response and response.status_code == 412:
            logger.warning(f"Version conflict updating item {item_key} — item was modified externally")
            return False
        else:
            status = response.status_code if response else 'No response'
            logger.error(f"Failed to update item {item_key} URL: {status}")
            return False

    # ── Translation server (metadata extraction from URLs/identifiers) ──

    def _translation_headers(self) -> Dict[str, str]:
        """Headers for translation server requests (no Zotero API auth)."""
        return {
            'Content-Type': 'text/plain',
            'User-Agent': 'DEVONzot-Service/2.0',
        }

    def translate_url(self, url: str) -> Optional[Dict]:
        """Translate a URL into structured Zotero metadata via the translation server.

        Uses the same translators as the Zotero browser connector.
        Returns the first item dict or None on failure.
        """
        endpoint = f'{self.translation_server_url}/web?single=1'
        try:
            response = requests.post(
                endpoint,
                data=url,
                headers=self._translation_headers(),
                timeout=self.translation_timeout,
            )
            if response.status_code == 200:
                items = response.json()
                if items and isinstance(items, list):
                    logger.info(f"Translation server returned {items[0].get('itemType', '?')}: "
                                f"{items[0].get('title', '?')}")
                    return items[0]
            else:
                logger.warning(f"Translation server returned {response.status_code} for {url}")
        except requests.Timeout:
            logger.warning(f"Translation server timed out after {self.translation_timeout}s for {url}")
        except Exception as e:
            logger.warning(f"Translation server error for {url}: {e}")
        return None

    def translate_identifier(self, identifier: str) -> Optional[Dict]:
        """Look up structured metadata by DOI, ISBN, PMID, or arXiv ID.

        Uses the translation server /search endpoint.
        Returns the first item dict or None on failure.
        """
        endpoint = f'{self.translation_server_url}/search'
        try:
            response = requests.post(
                endpoint,
                data=identifier,
                headers=self._translation_headers(),
                timeout=self.translation_timeout,
            )
            if response.status_code == 200:
                items = response.json()
                if items and isinstance(items, list):
                    logger.info(f"Identifier lookup returned {items[0].get('itemType', '?')}: "
                                f"{items[0].get('title', '?')}")
                    return items[0]
            else:
                logger.warning(f"Identifier lookup returned {response.status_code} for {identifier}")
        except requests.Timeout:
            logger.warning(f"Identifier lookup timed out after {self.translation_timeout}s")
        except Exception as e:
            logger.warning(f"Identifier lookup error for {identifier}: {e}")
        return None

    # ── Item creation ─────────────────────────────────────────────

    def create_item_from_url(self, url: str) -> Optional[Dict]:
        """Create a Zotero item from a URL with rich metadata.

        First tries the translation server for proper itemType, creators, title,
        date, abstract, etc. Falls back to a bare journalArticle if translation
        fails. Returns the created item's raw API data, with an extra
        '_translated_metadata' key containing the translation server response
        (or None if translation was skipped).
        """
        # Try translation server first
        translated = self.translate_url(url)

        if translated:
            # Remove keys that the Zotero API doesn't accept on creation
            item = {k: v for k, v in translated.items()
                    if k not in ('key', 'version')}
        else:
            logger.info(f"Translation unavailable, creating bare item for {url}")
            item = {"itemType": "journalArticle", "url": url}

        self._rate_limit()
        endpoint = f'{self.api_base}/users/{self.user_id}/items'
        response = self._safe_request('POST', endpoint, json=[item])
        if response and response.status_code == 200:
            created = response.json()
            if created.get('successful') and '0' in created['successful']:
                created_data = created['successful']['0']
                key = created_data['key']
                raw = self.get_item_raw(key)
                if not raw:
                    # Item was created but fetch-back failed — return what we have
                    # to prevent the retry wrapper from creating a duplicate
                    logger.warning(f"Item {key} created but fetch-back failed, using creation response")
                    raw = created_data
                raw['_translated_metadata'] = translated
                return raw
        return None

    def create_item_from_identifier(self, identifier: str) -> Optional[Dict]:
        """Create a Zotero item from a DOI, ISBN, PMID, or arXiv ID.

        Uses the translation server /search endpoint. Unlike URL creation,
        there is no fallback — identifier lookup requires the translation server.
        Returns the created item's raw API data with '_translated_metadata'.
        """
        translated = self.translate_identifier(identifier)
        if not translated:
            logger.error(f"Could not resolve identifier: {identifier}")
            return None

        item = {k: v for k, v in translated.items()
                if k not in ('key', 'version')}

        self._rate_limit()
        endpoint = f'{self.api_base}/users/{self.user_id}/items'
        response = self._safe_request('POST', endpoint, json=[item])
        if response and response.status_code == 200:
            created = response.json()
            if created.get('successful') and '0' in created['successful']:
                created_data = created['successful']['0']
                key = created_data['key']
                raw = self.get_item_raw(key)
                if not raw:
                    logger.warning(f"Item {key} created but fetch-back failed, using creation response")
                    raw = created_data
                raw['_translated_metadata'] = translated
                return raw
        return None

    def create_url_attachments(self, attachments: list) -> list:
        """Batch create new URL attachments.

        Each item in attachments is a dict with parent_key, title, url.
        """
        batch = [
            {
                "itemType": "attachment",
                "parentItem": att["parent_key"],
                "linkMode": "linked_url",
                "title": att["title"],
                "url": att["url"],
                "contentType": "application/pdf",
            }
            for att in attachments
        ]
        url_endpoint = f'{self.api_base}/users/{self.user_id}/items'
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

    def get_attachment_batch(self, start: int = 0, limit: int = 50) -> List[Dict]:
        """Get batch of file attachments (raw API response)."""
        self._rate_limit()
        params = {
            'itemType': 'attachment',
            'limit': limit,
            'start': start,
            'format': 'json',
        }
        url = f'{self.api_base}/users/{self.user_id}/items'
        response = self.session.get(url, params=params)
        if response.status_code != 200:
            logger.error(f"API error: {response.status_code} - {response.text}")
            return []
        return response.json()

    # ── Incremental sync (streaming support) ──────────────────────

    def get_changed_item_versions(self, since_version: int,
                                   item_type: str = None) -> Dict[str, int]:
        """Fetch key->version map for items changed since a library version.

        Uses ?since=<version>&format=versions for a lightweight single-request
        response (no pagination needed for format=versions).

        Args:
            since_version: Library version to diff against.
            item_type: Optional filter (e.g. 'attachment').

        Returns:
            Dict mapping item keys to their current versions.
        """
        self._rate_limit()
        url = f'{self.api_base}/users/{self.user_id}/items'
        params: Dict[str, Any] = {
            'since': since_version,
            'format': 'versions',
        }
        if item_type:
            params['itemType'] = item_type

        response = self._safe_request('GET', url, params=params)

        if not response or response.status_code != 200:
            logger.error(f"Failed to fetch changed items since version {since_version}")
            return {}

        version = response.headers.get('Last-Modified-Version')
        if version:
            self.last_library_version = int(version)

        return response.json()  # {key: version, ...}

    def get_items_by_keys(self, keys: List[str]) -> List[Dict]:
        """Fetch full item data for a list of keys.

        Uses ?itemKey=K1,K2,... (max 50 per request per Zotero API limit).
        """
        all_items: List[Dict] = []
        for i in range(0, len(keys), 50):
            batch = keys[i:i + 50]
            self._rate_limit()
            url = f'{self.api_base}/users/{self.user_id}/items'
            params = {
                'itemKey': ','.join(batch),
                'format': 'json',
            }
            response = self._safe_request('GET', url, params=params)
            if response and response.status_code == 200:
                version = response.headers.get('Last-Modified-Version')
                if version:
                    self.last_library_version = int(version)
                all_items.extend(response.json())
            else:
                status = response.status_code if response else 'No response'
                logger.error(f"Failed to fetch items by keys (batch {i//50}): {status}")

        return all_items

    def get_deleted_since(self, since_version: int) -> Dict[str, List[str]]:
        """Fetch keys of items deleted since a library version.

        Returns dict with 'items', 'collections', 'searches', 'tags' lists.
        """
        self._rate_limit()
        url = f'{self.api_base}/users/{self.user_id}/deleted'
        params = {'since': since_version}
        response = self._safe_request('GET', url, params=params)

        if not response or response.status_code != 200:
            logger.error(f"Failed to fetch deleted items since version {since_version}")
            return {'items': [], 'collections': [], 'searches': [], 'tags': []}

        version = response.headers.get('Last-Modified-Version')
        if version:
            self.last_library_version = int(version)

        return response.json()

    def delete_attachment(self, attachment_key: str, version: int,
                         dry_run: bool = False) -> bool:
        """Delete a single attachment item from Zotero."""
        if dry_run:
            logger.info(f"[DRY RUN] Would delete attachment {attachment_key}")
            return True
        self._rate_limit()
        url = f'{self.api_base}/users/{self.user_id}/items/{attachment_key}'
        headers = {'If-Unmodified-Since-Version': str(version)}
        response = self._safe_request('DELETE', url, headers=headers)

        if response and response.status_code == 204:
            logger.info(f"Deleted attachment item: {attachment_key}")
            return True
        elif response and response.status_code == 412:
            logger.warning(f"Version conflict deleting attachment {attachment_key}")
            return False
        else:
            status = response.status_code if response else 'No response'
            logger.error(f"Failed to delete attachment {attachment_key}: {status}")
            return False

    def delete_items_batch(self, item_keys: List[str], library_version: int,
                           dry_run: bool = False) -> Dict[str, Any]:
        """Batch delete multiple items via the Zotero API.

        Uses DELETE /users/{userId}/items?itemKey=KEY1,KEY2,...
        Max 50 keys per request.
        """
        results = {'deleted': 0, 'would_delete': 0, 'failed': [], 'version_conflict': False}

        if not item_keys:
            return results

        if dry_run:
            logger.info(f"[DRY RUN] Would batch delete {len(item_keys)} items")
            results['would_delete'] = len(item_keys)
            return results

        for i in range(0, len(item_keys), 50):
            batch = item_keys[i:i + 50]
            self._rate_limit()

            url = f'{self.api_base}/users/{self.user_id}/items'
            params = {'itemKey': ','.join(batch)}
            headers = {'If-Unmodified-Since-Version': str(library_version)}

            response = self._safe_request('DELETE', url, params=params, headers=headers)

            if response and response.status_code == 204:
                results['deleted'] += len(batch)
                new_version = response.headers.get('Last-Modified-Version')
                if new_version:
                    library_version = int(new_version)
                    self.last_library_version = library_version
                logger.info(f"Batch deleted {len(batch)} items (batch {i // 50 + 1})")
            elif response and response.status_code == 412:
                logger.warning(f"Version conflict on batch delete (batch {i // 50 + 1})")
                results['version_conflict'] = True
                results['failed'].extend(batch)
                break
            else:
                status = response.status_code if response else 'No response'
                logger.error(f"Batch delete failed (batch {i // 50 + 1}): {status}")
                results['failed'].extend(batch)

        return results
