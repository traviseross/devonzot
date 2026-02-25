#!/usr/bin/env python3
"""
RSS/Atom feed extraction module.

Provides RSSExtractor class for detecting and extracting article content
from RSS and Atom feeds. Useful fallback for sites like Substack, Medium,
and WordPress that provide full-content feeds.
"""

import logging
import re
from difflib import SequenceMatcher
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from article_extraction import ExtractionResult


logger = logging.getLogger(__name__)


class RSSExtractor:
    """Extract article content from RSS/Atom feeds."""

    def __init__(self, timeout: int = 15):
        """
        Initialize RSS extractor.

        Args:
            timeout: HTTP request timeout in seconds
        """
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })

    def detect_feeds(self, url: str, html: str) -> List[str]:
        """
        Detect RSS/Atom feed URLs from HTML.

        Args:
            url: Original article URL
            html: HTML content of the page

        Returns:
            List of detected feed URLs
        """
        feeds = []
        soup = BeautifulSoup(html, 'html.parser')
        base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

        # Find feeds in <link> tags
        for link in soup.find_all('link', type=['application/rss+xml', 'application/atom+xml']):
            feed_url = link.get('href')
            if feed_url:
                # Convert relative URLs to absolute
                feed_url = urljoin(base_url, feed_url)
                feeds.append(feed_url)

        # Common feed URL patterns if no feeds found in HTML
        if not feeds:
            common_paths = ['/feed', '/feed/', '/rss', '/rss/', '/atom.xml', '/.rss', '/index.xml']
            for path in common_paths:
                feeds.append(f"{base_url}{path}")

        logger.debug(f"Detected {len(feeds)} potential feed URLs")
        return feeds

    def _normalize_url(self, url: str) -> str:
        """
        Normalize URL for comparison.

        Args:
            url: URL to normalize

        Returns:
            Normalized URL (lowercase, no trailing slash, no query params)
        """
        parsed = urlparse(url)
        # Keep scheme, netloc, and path; discard query and fragment
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".lower()
        # Remove trailing slash
        return normalized.rstrip('/')

    def _title_similarity(self, title1: str, title2: str) -> float:
        """
        Calculate similarity between two titles.

        Args:
            title1: First title
            title2: Second title

        Returns:
            Similarity score between 0.0 and 1.0
        """
        if not title1 or not title2:
            return 0.0
        # Normalize: lowercase, remove extra whitespace
        t1 = re.sub(r'\s+', ' ', title1.lower()).strip()
        t2 = re.sub(r'\s+', ' ', title2.lower()).strip()
        return SequenceMatcher(None, t1, t2).ratio()

    def fetch_feed_entry(self, feed_url: str, article_url: str) -> Optional[dict]:
        """
        Fetch feed and find entry matching article URL.

        Args:
            feed_url: URL of the RSS/Atom feed
            article_url: URL of the article to find

        Returns:
            Feed entry dict or None if not found
        """
        try:
            logger.debug(f"Fetching feed: {feed_url}")
            resp = self.session.get(feed_url, timeout=self.timeout)
            resp.raise_for_status()

            feed = feedparser.parse(resp.content)

            if not feed.entries:
                logger.debug(f"No entries found in feed: {feed_url}")
                return None

            logger.debug(f"Found {len(feed.entries)} entries in feed")

            # Normalize article URL for comparison
            normalized_article_url = self._normalize_url(article_url)

            # Try to match by URL
            for entry in feed.entries:
                entry_url = entry.get('link', '')
                if entry_url:
                    normalized_entry_url = self._normalize_url(entry_url)
                    if normalized_entry_url == normalized_article_url:
                        logger.debug(f"Matched entry by exact URL: {entry.get('title', 'Untitled')}")
                        return entry

            # Fallback: match by title similarity (>80%)
            page_title = None
            # Try to extract title from article URL's HTML if we have it
            # For now, we'll skip this and just try to match by similarity
            for entry in feed.entries:
                entry_title = entry.get('title', '')
                # Use URL path as fallback title
                if not page_title:
                    page_title = article_url.split('/')[-1].replace('-', ' ').replace('_', ' ')

                similarity = self._title_similarity(entry_title, page_title)
                if similarity > 0.8:
                    logger.debug(f"Matched entry by title similarity ({similarity:.2f}): {entry_title}")
                    return entry

            logger.debug(f"No matching entry found in feed: {feed_url}")
            return None

        except requests.RequestException as e:
            logger.warning(f"Failed to fetch feed {feed_url}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error parsing feed {feed_url}: {e}")
            return None

    def _extract_content_from_entry(self, entry: dict) -> str:
        """
        Extract content from feed entry.

        Args:
            entry: Feed entry dict

        Returns:
            Extracted content as plain text
        """
        # Try different content fields in order of preference
        content = ''

        # 1. content:encoded (full content, common in WordPress)
        if 'content' in entry and len(entry['content']) > 0:
            content = entry['content'][0].get('value', '')

        # 2. summary/description
        if not content:
            content = entry.get('summary', '') or entry.get('description', '')

        # 3. Parse HTML to plain text
        if content:
            soup = BeautifulSoup(content, 'html.parser')
            # Remove script and style elements
            for element in soup(['script', 'style', 'iframe']):
                element.decompose()
            text = soup.get_text(separator='\n\n')
            # Clean up whitespace
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            content = '\n\n'.join(lines)

        return content

    def _extract_metadata_from_entry(self, entry: dict) -> dict:
        """
        Extract metadata from feed entry.

        Args:
            entry: Feed entry dict

        Returns:
            Metadata dictionary
        """
        metadata = {}

        # Title
        metadata['title'] = entry.get('title', '')

        # Authors
        authors = []
        if 'author' in entry:
            authors.append(entry['author'])
        elif 'author_detail' in entry:
            name = entry['author_detail'].get('name', '')
            if name:
                authors.append(name)
        elif 'dc_creator' in entry:  # Dublin Core creator
            authors.append(entry['dc_creator'])
        metadata['authors'] = authors

        # Publication date
        publish_date = None
        if 'published_parsed' in entry and entry['published_parsed']:
            from time import struct_time
            if isinstance(entry['published_parsed'], struct_time):
                publish_date = f"{entry['published_parsed'].tm_year}-{entry['published_parsed'].tm_mon:02d}-{entry['published_parsed'].tm_mday:02d}"
        elif 'updated_parsed' in entry and entry['updated_parsed']:
            from time import struct_time
            if isinstance(entry['updated_parsed'], struct_time):
                publish_date = f"{entry['updated_parsed'].tm_year}-{entry['updated_parsed'].tm_mon:02d}-{entry['updated_parsed'].tm_mday:02d}"
        metadata['date'] = publish_date

        # Publication name (from feed metadata, if available)
        # This would need to be passed from the feed object
        metadata['publication'] = ''

        return metadata

    def extract_from_rss(self, url: str, html: Optional[str] = None) -> Optional[ExtractionResult]:
        """
        Extract article content from RSS/Atom feed.

        Args:
            url: Article URL
            html: Optional HTML content of the article page (for feed detection)

        Returns:
            ExtractionResult or None if extraction fails
        """
        logger.info("Attempting RSS feed extraction")

        # Fetch HTML if not provided
        if not html:
            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                html = resp.text
            except requests.RequestException as e:
                logger.warning(f"Failed to fetch URL for feed detection: {e}")
                return None

        # Detect feeds
        feed_urls = self.detect_feeds(url, html)
        if not feed_urls:
            logger.info("No RSS/Atom feeds detected")
            return None

        # Try each feed URL
        for feed_url in feed_urls:
            entry = self.fetch_feed_entry(feed_url, url)
            if entry:
                # Extract content and metadata
                content = self._extract_content_from_entry(entry)
                metadata = self._extract_metadata_from_entry(entry)

                if not content:
                    logger.warning(f"Entry found but no content extracted from feed: {feed_url}")
                    continue

                # Split content into paragraphs
                paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]

                # Filter out very short paragraphs (likely navigation/boilerplate)
                paragraphs = [p for p in paragraphs if len(p) >= 20]

                if not paragraphs:
                    logger.warning("No substantial paragraphs found in feed content")
                    continue

                # Build markdown
                title = metadata.get('title', 'Untitled')
                authors = metadata.get('authors', [])
                date = metadata.get('date', '')

                markdown = f"# {title}\n\n"
                if authors:
                    markdown += f"**Authors:** {', '.join(authors)}\n\n"
                if date:
                    markdown += f"**Date:** {date}\n\n"
                markdown += "---\n\n"
                markdown += '\n\n'.join(paragraphs)

                # Calculate quality score (RSS content is usually shorter)
                word_count = sum(len(p.split()) for p in paragraphs)
                quality_score = min(1.0, (
                    0.3 * min(len(paragraphs) / 10.0, 1.0) +  # Paragraph count
                    0.4 * min(word_count / 500.0, 1.0) +       # Word count
                    0.15 * (1.0 if metadata.get('title') else 0.0) +
                    0.15 * (1.0 if authors else 0.0)
                ))

                # Apply RSS quality adjustment (×0.9)
                quality_score *= 0.9

                logger.info(f"RSS extraction successful: {len(paragraphs)} paragraphs, {word_count} words, quality {quality_score:.2f}")

                return ExtractionResult(
                    title=title,
                    authors=authors,
                    publish_date=date,
                    paragraphs=paragraphs,
                    markdown=markdown,
                    metadata=metadata,
                    engine='rss',
                    quality_score=quality_score
                )

        logger.info("No matching feed entry found in any detected feed")
        return None


if __name__ == '__main__':
    # Simple test
    import sys
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
        extractor = RSSExtractor()
        result = extractor.extract_from_rss(test_url)
        if result:
            print(f"Title: {result.title}")
            print(f"Authors: {', '.join(result.authors)}")
            print(f"Quality: {result.quality_score:.2f}")
            print(f"Paragraphs: {len(result.paragraphs)}")
        else:
            print("RSS extraction failed")
