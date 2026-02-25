#!/usr/bin/env python3
"""
End-to-end pipeline for adding URLs to DEVONthink and Zotero.

Takes a URL, creates a Zotero item, extracts article content, generates
markdown with metadata, imports to DEVONthink, and creates bidirectional links.
"""

import asyncio
import logging
import os
import re
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from cleanup_service import TempFileManager
from combine_article_extracts import combine_articles
from zotero_api_client import ZoteroAPIClient
from devonzot_service import DEVONthinkInterface, FilenameGenerator
from exceptions import (
    ArticleExtractionError,
    DEVONthinkIntegrationError,
    TimeoutError,
    ZoteroAPIError
)
from rss_extractor import RSSExtractor
from wayback_extractor import WaybackExtractor

# Try to import Playwright, but don't fail if not installed
try:
    from playwright_extractor import PlaywrightExtractor, PLAYWRIGHT_AVAILABLE
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# Load environment variables
load_dotenv('/Users/travisross/DEVONzot/.env')

# Configuration from environment
ZOTERO_API_KEY = os.environ.get('ZOTERO_API_KEY')
ZOTERO_USER_ID = os.environ.get('ZOTERO_USER_ID')
TMP_DIR = Path(os.environ.get('TMP_DIR', '/Users/travisross/DEVONzot/tmp_extractions'))
EXTRACTION_TIMEOUT = int(os.environ.get('EXTRACTION_TIMEOUT', 120))
DEBUG_MODE = os.environ.get('DEBUG_MODE', 'false').lower() == 'true'

# Translation server configuration
TRANSLATION_SERVER_URL = os.environ.get('TRANSLATION_SERVER_URL')
TRANSLATION_TIMEOUT = float(os.environ.get('TRANSLATION_TIMEOUT', 30))

# Extraction fallback configuration
ENABLE_RSS_FALLBACK = os.environ.get('ENABLE_RSS_FALLBACK', 'true').lower() == 'true'
ENABLE_PLAYWRIGHT = os.environ.get('ENABLE_PLAYWRIGHT', 'false').lower() == 'true'
ENABLE_WAYBACK = os.environ.get('ENABLE_WAYBACK', 'true').lower() == 'true'
PLAYWRIGHT_TIMEOUT = int(os.environ.get('PLAYWRIGHT_TIMEOUT', 30000))
WAYBACK_TIMEOUT = int(os.environ.get('WAYBACK_TIMEOUT', 15))

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def retry_with_backoff(
    max_attempts: int = 5,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0
):
    """
    Decorator for retrying functions with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        backoff_factor: Multiplier for delay after each attempt

    Returns:
        Decorated function that retries on failure
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt == max_attempts:
                        logger.error(f"{func.__name__} failed after {max_attempts} attempts")
                        raise

                    logger.warning(f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}")
                    logger.info(f"Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                    delay = min(delay * backoff_factor, max_delay)

            # Should not reach here, but just in case
            raise last_exception

        return wrapper
    return decorator


def sanitize_short_title(title: str, length: int = 60) -> str:
    """
    Sanitize and truncate title for filename use.

    Args:
        title: Original title
        length: Maximum length

    Returns:
        Sanitized and truncated title
    """
    if not title:
        return "untitled"
    # Normalize whitespace
    s = re.sub(r'\s+', ' ', title).strip()
    # Remove problematic characters
    s = re.sub(r'[<>:"/\\|?*]', '', s)
    # Truncate at word boundary
    if len(s) > length:
        s = s[:length].rsplit(' ', 1)[0] + '...'
    return s


def author_surname_from_list(authors) -> str:
    """
    Extract surname from author list.

    Args:
        authors: List of author dicts or strings

    Returns:
        Surname of first author or empty string
    """
    if not authors:
        return ''

    first = authors[0]
    if isinstance(first, dict):
        return first.get('lastName') or first.get('last') or ''
    if isinstance(first, str):
        parts = first.split()
        return parts[-1] if parts else ''
    return ''


@retry_with_backoff(max_attempts=5, initial_delay=2.0)
def create_zotero_item_with_retry(zot: ZoteroAPIClient, url: str) -> dict:
    """
    Create Zotero item with automatic retry.

    Args:
        zot: ZoteroAPIClient instance
        url: URL to create item from

    Returns:
        Created item data

    Raises:
        ZoteroAPIError: If creation fails after all retries
    """
    created = zot.create_item_from_url(url)
    if not created:
        raise ZoteroAPIError("Failed to create Zotero item", status_code=None)
    return created


async def run_pipeline(url: str, dry_run: bool = False):
    """
    Execute the complete URL-to-DEVONthink-and-Zotero pipeline.

    Args:
        url: URL to process
        dry_run: If True, simulate pipeline without making actual changes

    Raises:
        Various exceptions for different failure modes
    """
    # Validate configuration
    if not ZOTERO_API_KEY or not ZOTERO_USER_ID:
        raise ValueError('ZOTERO_API_KEY or ZOTERO_USER_ID not set in environment')

    if dry_run:
        logger.info(f"[DRY RUN] Starting pipeline for URL: {url}")
    else:
        logger.info(f"Starting pipeline for URL: {url}")

    # Initialize clients
    zot_kwargs = {}
    if TRANSLATION_SERVER_URL:
        zot_kwargs['translation_server_url'] = TRANSLATION_SERVER_URL
    if TRANSLATION_TIMEOUT:
        zot_kwargs['translation_timeout'] = TRANSLATION_TIMEOUT
    zot = ZoteroAPIClient(ZOTERO_API_KEY, ZOTERO_USER_ID, **zot_kwargs)
    dt = DEVONthinkInterface()

    # Step 1: Create Zotero item (with translation server metadata)
    logger.info("Step 1/5: Creating Zotero item from URL")
    translated_meta = None
    try:
        if dry_run:
            logger.info(f"[DRY RUN] Would create Zotero item from: {url}")
            created = {'key': 'DRY_RUN_KEY', 'data': {'key': 'DRY_RUN_KEY', 'title': 'Dry Run Item'}}
            key = 'DRY_RUN_KEY'
        else:
            created = create_zotero_item_with_retry(zot, url)
            key = created.get('data', {}).get('key') or created.get('key')
            if not key:
                raise ZoteroAPIError("No key returned from Zotero API")
            translated_meta = created.get('_translated_metadata')
        logger.info(f"Created Zotero item with key: {key}")
    except Exception as e:
        logger.error(f"Failed to create Zotero item: {e}")
        raise ZoteroAPIError(f"Could not create Zotero item: {e}") from e

    # Use TempFileManager for automatic cleanup
    with TempFileManager(tmp_dir=TMP_DIR, debug_mode=DEBUG_MODE) as tmpfiles:
        # Step 2: Extract article with three-tier cascading fallback
        logger.info("Step 2/5: Extracting article content (trying multiple methods)")

        # Use translated metadata for filename if available, fall back to scraping
        if translated_meta:
            metadata = _metadata_from_translation(translated_meta)
            logger.info("Using translation server metadata for filename")
        else:
            metadata = _extract_basic_metadata(url)
        filename = _generate_filename(metadata)
        logger.info(f"Generated filename: {filename}")

        # Create temp file path and register for cleanup
        tmp_file = tmpfiles.register(TMP_DIR / f"{filename}.md")

        result = None
        extraction_method = "unknown"
        html = None  # Cache HTML for reuse across extractors

        # Tier 0: Standard extraction (newspaper3k + readability + trafilatura)
        try:
            logger.info(f"Trying standard extraction (timeout: {EXTRACTION_TIMEOUT}s)...")
            result = await asyncio.wait_for(
                asyncio.to_thread(combine_articles, url, str(tmp_file)),
                timeout=EXTRACTION_TIMEOUT
            )
            extraction_method = result.engine if result else "standard"

            if result and result.quality_score >= 0.1:
                logger.info(f"✓ Standard extraction succeeded (quality: {result.quality_score:.2f})")
            else:
                quality = result.quality_score if result else 0.0
                logger.warning(f"✗ Standard extraction low quality ({quality:.2f}), trying fallbacks...")
                result = None

        except asyncio.TimeoutError:
            logger.warning(f"✗ Standard extraction timed out after {EXTRACTION_TIMEOUT}s")
        except Exception as e:
            logger.warning(f"✗ Standard extraction failed: {e}")

        # Tier 1: RSS fallback
        if not result and ENABLE_RSS_FALLBACK:
            try:
                logger.info("Trying Tier 1: RSS feed extraction...")
                rss_extractor = RSSExtractor(timeout=15)
                rss_result = await asyncio.to_thread(
                    rss_extractor.extract_from_rss, url, html
                )

                if rss_result and rss_result.quality_score >= 0.1:
                    result = rss_result
                    extraction_method = "rss"
                    logger.info(f"✓ RSS extraction succeeded (quality: {result.quality_score:.2f})")

                    # Write markdown to temp file for DEVONthink import
                    tmp_file.write_text(result.markdown, encoding='utf-8')
                else:
                    quality = rss_result.quality_score if rss_result else 0.0
                    logger.warning(f"✗ RSS extraction low quality ({quality:.2f})")

            except Exception as e:
                logger.warning(f"✗ RSS extraction failed: {e}")

        # Tier 2: Playwright fallback
        if not result and ENABLE_PLAYWRIGHT and PLAYWRIGHT_AVAILABLE:
            try:
                logger.info("Trying Tier 2: Playwright browser automation...")
                playwright_extractor = PlaywrightExtractor(
                    headless=True,
                    timeout=PLAYWRIGHT_TIMEOUT
                )
                pw_result = await playwright_extractor.extract(url)

                if pw_result and pw_result.quality_score >= 0.1:
                    result = pw_result
                    extraction_method = "playwright"
                    logger.info(f"✓ Playwright extraction succeeded (quality: {result.quality_score:.2f})")

                    # Write markdown to temp file for DEVONthink import
                    tmp_file.write_text(result.markdown, encoding='utf-8')
                else:
                    quality = pw_result.quality_score if pw_result else 0.0
                    logger.warning(f"✗ Playwright extraction low quality ({quality:.2f})")

            except Exception as e:
                logger.warning(f"✗ Playwright extraction failed: {e}")
        elif not result and ENABLE_PLAYWRIGHT and not PLAYWRIGHT_AVAILABLE:
            logger.info("Playwright enabled but not installed (install: pip install playwright && playwright install chromium)")

        # Tier 3: Wayback Machine fallback
        if not result and ENABLE_WAYBACK:
            try:
                logger.info("Trying Tier 3: Internet Archive Wayback Machine...")
                wayback_extractor = WaybackExtractor(
                    timeout=WAYBACK_TIMEOUT,
                    prefer_recent=True
                )
                wb_result = await asyncio.to_thread(
                    wayback_extractor.extract_from_archive, url
                )

                if wb_result and wb_result.quality_score >= 0.1:
                    result = wb_result
                    extraction_method = "wayback"
                    archived_date = wb_result.metadata.get('archived_date', 'unknown')
                    logger.info(f"✓ Wayback extraction succeeded (archived: {archived_date}, quality: {result.quality_score:.2f})")

                    # Write markdown to temp file for DEVONthink import
                    tmp_file.write_text(result.markdown, encoding='utf-8')
                else:
                    quality = wb_result.quality_score if wb_result else 0.0
                    logger.warning(f"✗ Wayback extraction low quality ({quality:.2f})")

            except Exception as e:
                logger.warning(f"✗ Wayback extraction failed: {e}")

        # Final check: all extraction methods failed
        if not result or result.quality_score < 0.1:
            quality = result.quality_score if result else 0.0
            error_msg = (
                f"All extraction methods failed. Quality: {quality:.2f}. "
                f"Article may be paywalled, JavaScript-protected, or unavailable."
            )
            logger.error(error_msg)
            raise ArticleExtractionError(error_msg, url=url, engine=extraction_method)

        logger.info(f"Extraction complete using {extraction_method} (quality: {result.quality_score:.2f})")

        # Step 3: Copy to DEVONthink Inbox
        logger.info("Step 3/5: Copying markdown to DEVONthink Inbox")
        try:
            if dry_run:
                logger.info(f"[DRY RUN] Would copy {tmp_file} to DEVONthink Inbox as: {filename}.md")
                ok = True
            else:
                ok = dt.copy_file_to_inbox(str(tmp_file), f"{filename}.md", dry_run=dry_run)
            if not ok:
                raise DEVONthinkIntegrationError(
                    "Failed to copy file to DEVONthink Inbox",
                    operation="copy_to_inbox"
                )
            logger.info("File copied to DEVONthink Inbox")
        except Exception as e:
            logger.error(f"DEVONthink import failed: {e}")
            raise DEVONthinkIntegrationError(f"Import failed: {e}", operation="copy_to_inbox") from e

        # Step 4: Find DEVONthink item and get UUID
        logger.info("Step 4/5: Searching for imported item in DEVONthink")
        try:
            if dry_run:
                logger.info(f"[DRY RUN] Would search for: {filename}.md in DEVONthink")
                uuid = "DRY-RUN-UUID-12345678"
            else:
                uuid = await dt.find_item_by_filename_after_wait_async(f"{filename}.md", dry_run=dry_run)
            if not uuid:
                raise DEVONthinkIntegrationError(
                    "Could not find imported item in DEVONthink",
                    operation="find_uuid"
                )
            logger.info(f"Found DEVONthink UUID: {uuid}")
        except Exception as e:
            logger.error(f"Failed to find DEVONthink item: {e}")
            raise DEVONthinkIntegrationError(f"UUID lookup failed: {e}", operation="find_uuid") from e

        # Step 5: Create Zotero URL attachment
        logger.info("Step 5/5: Creating Zotero attachment linking to DEVONthink")
        try:
            attach_url = f"x-devonthink-item://{uuid}"
            batch = [{
                'parent_key': key,
                'title': filename,
                'url': attach_url
            }]

            if dry_run:
                logger.info(f"[DRY RUN] Would create Zotero attachment:")
                logger.info(f"  - Parent: {key}")
                logger.info(f"  - Title: {filename}")
                logger.info(f"  - URL: {attach_url}")
                results = [{'new_key': 'DRY_RUN_ATTACHMENT_KEY'}]
            else:
                results = zot.create_url_attachments(batch)

            if results and results[0].get('new_key'):
                new_key = results[0]['new_key']
                logger.info(f"Created Zotero attachment: {new_key}")
            else:
                raise ZoteroAPIError("Failed to create URL attachment")

        except Exception as e:
            logger.error(f"Failed to create Zotero attachment: {e}")
            raise ZoteroAPIError(f"Attachment creation failed: {e}") from e

        # Mark success for cleanup
        tmpfiles.mark_success()

    logger.info("Pipeline completed successfully!")
    logger.info(f"  Zotero item: {key}")
    logger.info(f"  DEVONthink UUID: {uuid}")
    logger.info(f"  Link: x-devonthink-item://{uuid}")


def _metadata_from_translation(translated: dict) -> dict:
    """Convert translation server metadata into the format expected by _generate_filename.

    The translation server returns Zotero-native fields (creators, blogTitle,
    publicationTitle, etc.). This maps them to the simple dict format used
    for filename generation.
    """
    # Title
    title = translated.get('title', '')

    # Authors: translation server returns [{firstName, lastName, creatorType}]
    # Filter to authors only (skip editors, translators, etc.)
    creators = translated.get('creators', [])
    authors = [c for c in creators if c.get('creatorType') == 'author'] or creators

    # Publication: try common Zotero fields in priority order
    publication = (
        translated.get('publicationTitle')
        or translated.get('blogTitle')
        or translated.get('websiteTitle')
        or translated.get('forumTitle')
        or translated.get('proceedingsTitle')
        or translated.get('bookTitle')
        or ''
    )

    # Date: translation server returns date strings like "2016-04-25"
    date = translated.get('date')

    return {
        'title': title,
        'authors': authors,
        'date': date,
        'publication': publication,
    }


def _extract_basic_metadata(url: str) -> dict:
    """
    Extract basic metadata for filename generation.

    Args:
        url: Article URL

    Returns:
        Dictionary with title, authors, date, publication
    """
    from newspaper import Article
    from bs4 import BeautifulSoup
    import requests

    metadata = {
        'title': '',
        'authors': [],
        'date': None,
        'publication': ''
    }

    try:
        # Fetch HTML with browser headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        resp = requests.get(url, timeout=10, headers=headers)
        html = resp.text

        # Extract with newspaper3k
        article = Article(url)
        article.set_html(html)
        article.parse()

        metadata['title'] = article.title or url
        metadata['authors'] = article.authors or []
        metadata['date'] = article.publish_date

        # Extract publication from meta tags
        soup = BeautifulSoup(html, 'html.parser')
        og_site = soup.find('meta', property='og:site_name')
        if og_site and og_site.get('content'):
            metadata['publication'] = og_site['content'].strip()
        else:
            tw_site = soup.find('meta', attrs={'name': 'twitter:site'})
            if tw_site and tw_site.get('content'):
                metadata['publication'] = tw_site['content'].strip().lstrip('@')

        if not metadata['publication']:
            metadata['publication'] = url.split('//')[1].split('/')[0]

    except Exception as e:
        logger.warning(f"Metadata extraction failed, using fallbacks: {e}")
        metadata['title'] = url

    return metadata


def _generate_filename(metadata: dict) -> str:
    """
    Generate filename from metadata.

    Args:
        metadata: Dictionary with title, authors, date, publication

    Returns:
        Sanitized filename (without extension)
    """
    title = sanitize_short_title(metadata.get('title', ''), length=60)
    surname = author_surname_from_list(metadata.get('authors', []))
    publication = metadata.get('publication', '')

    # Extract year from date
    year = ''
    date = metadata.get('date')
    if date:
        try:
            year = str(date.year)
        except Exception:
            m = re.search(r'(19|20)\d{2}', str(date))
            year = m.group(0) if m else ''

    # Assemble filename
    filename = f"{surname} - {title} - {publication} - {year} - Article"

    # Clean up multiple separators
    filename = re.sub(r'\s+-\s+-\s+', ' - ', filename)
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)

    # Limit length
    return filename[:140]


def main():
    """Main entry point for command-line usage."""
    if len(sys.argv) < 2:
        print('Usage: pipeline_add_url.py <url> [--dry-run]')
        print('')
        print('Options:')
        print('  --dry-run    Simulate pipeline without making actual changes')
        sys.exit(1)

    url = sys.argv[1]
    dry_run = '--dry-run' in sys.argv

    if dry_run:
        print('🔍 DRY RUN MODE - No actual changes will be made')
        print('')

    try:
        asyncio.run(run_pipeline(url, dry_run=dry_run))
        if dry_run:
            print('')
            print('✅ Dry run completed successfully')
            print('   Run without --dry-run to execute for real')
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=DEBUG_MODE)
        sys.exit(1)


if __name__ == '__main__':
    main()
