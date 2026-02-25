#!/usr/bin/env python3
"""
Internet Archive Wayback Machine extraction module.

Provides WaybackExtractor class for extracting article content from
archived snapshots on archive.org. Useful fallback for paywalled
content, deleted pages, or when current extraction methods fail.
"""

import logging
from datetime import datetime
from typing import Optional

import requests

from article_extraction import ArticleExtractor, ExtractionResult


logger = logging.getLogger(__name__)


class WaybackExtractor:
    """Extract article content from Internet Archive Wayback Machine."""

    WAYBACK_API_URL = "https://archive.org/wayback/available"

    def __init__(self, timeout: int = 15, prefer_recent: bool = True):
        """
        Initialize Wayback Machine extractor.

        Args:
            timeout: HTTP request timeout in seconds
            prefer_recent: If True, prefer most recent snapshot; if False, prefer earliest
        """
        self.timeout = timeout
        self.prefer_recent = prefer_recent
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })

    def get_latest_snapshot(self, url: str, before_date: Optional[str] = None) -> Optional[dict]:
        """
        Get latest (or earliest) archived snapshot for a URL.

        Args:
            url: URL to look up in Wayback Machine
            before_date: Optional date string (YYYYMMDD format) to get snapshot before this date

        Returns:
            Snapshot info dict with 'url', 'timestamp', 'status', or None if not found
        """
        try:
            params = {'url': url}
            if before_date:
                params['timestamp'] = before_date

            logger.debug(f"Querying Wayback API for: {url}")
            resp = self.session.get(
                self.WAYBACK_API_URL,
                params=params,
                timeout=self.timeout
            )
            resp.raise_for_status()

            data = resp.json()

            # Check if snapshot is available
            if not data.get('archived_snapshots'):
                logger.debug("No archived snapshots found")
                return None

            closest = data['archived_snapshots'].get('closest')
            if not closest or not closest.get('available'):
                logger.debug("No available snapshots")
                return None

            snapshot_url = closest['url']
            timestamp = closest['timestamp']  # Format: YYYYMMDDhhmmss
            status = closest.get('status', '200')

            logger.info(f"Found archived snapshot from {timestamp}: {snapshot_url}")

            return {
                'url': snapshot_url,
                'timestamp': timestamp,
                'status': status
            }

        except requests.RequestException as e:
            logger.warning(f"Failed to query Wayback API: {e}")
            return None
        except (KeyError, ValueError) as e:
            logger.warning(f"Error parsing Wayback API response: {e}")
            return None

    def _parse_timestamp(self, timestamp: str) -> Optional[str]:
        """
        Parse Wayback timestamp to ISO date format.

        Args:
            timestamp: Wayback timestamp (YYYYMMDDhhmmss)

        Returns:
            ISO date string (YYYY-MM-DD) or None if parsing fails
        """
        try:
            # Extract date part (YYYYMMDD)
            date_str = timestamp[:8]
            dt = datetime.strptime(date_str, '%Y%m%d')
            return dt.strftime('%Y-%m-%d')
        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to parse timestamp {timestamp}: {e}")
            return None

    def extract_from_archive(self, url: str, before_date: Optional[str] = None) -> Optional[ExtractionResult]:
        """
        Extract article content from Wayback Machine archive.

        Args:
            url: Original article URL
            before_date: Optional date to get snapshot before (YYYYMMDD format)

        Returns:
            ExtractionResult or None if extraction fails
        """
        logger.info("Attempting Wayback Machine archive extraction")

        # Get snapshot info
        snapshot = self.get_latest_snapshot(url, before_date)
        if not snapshot:
            logger.info("No archived snapshot available")
            return None

        snapshot_url = snapshot['url']
        timestamp = snapshot['timestamp']

        # Fetch archived HTML
        try:
            logger.debug(f"Fetching archived HTML from: {snapshot_url}")
            resp = self.session.get(snapshot_url, timeout=self.timeout)
            resp.raise_for_status()

            html = resp.text
            if not html or len(html) < 1000:
                logger.warning(f"Retrieved HTML is too short ({len(html)} bytes)")
                return None

            logger.debug(f"Retrieved {len(html)} bytes of archived HTML")

        except requests.RequestException as e:
            logger.warning(f"Failed to fetch archived HTML: {e}")
            return None

        # Use existing ArticleExtractor to parse the archived HTML
        logger.debug("Parsing archived HTML with ArticleExtractor")
        extractor = ArticleExtractor()
        result = extractor.extract(url, html=html)

        if not result:
            logger.warning("ArticleExtractor failed to parse archived HTML")
            return None

        # Parse archived date
        archived_date = self._parse_timestamp(timestamp)

        # Apply Wayback quality adjustment (×0.85)
        result.quality_score *= 0.85
        result.engine = 'wayback'

        # Add archived_date to metadata
        result.metadata['archived_date'] = archived_date
        result.metadata['wayback_url'] = snapshot_url

        logger.info(f"Wayback extraction successful: archived {archived_date}, quality {result.quality_score:.2f}")

        return result


def main():
    """Simple test for command-line usage."""
    import sys
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
        extractor = WaybackExtractor()
        result = extractor.extract_from_archive(test_url)
        if result:
            print(f"Title: {result.title}")
            print(f"Authors: {', '.join(result.authors)}")
            print(f"Archived: {result.metadata.get('archived_date', 'unknown')}")
            print(f"Quality: {result.quality_score:.2f}")
            print(f"Paragraphs: {len(result.paragraphs)}")
        else:
            print("Wayback extraction failed")
    else:
        print("Usage: python wayback_extractor.py <url>")


if __name__ == '__main__':
    main()
