#!/usr/bin/env python3
"""
Playwright browser automation extraction module.

Provides PlaywrightExtractor class for extracting article content from
JavaScript-heavy websites using headless browser automation. Optional
dependency - gracefully handles case where Playwright is not installed.
"""

import asyncio
import logging
from typing import Optional

# Try to import Playwright, but don't fail if not installed
try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightTimeout = Exception  # Placeholder

from article_extraction import ArticleExtractor, ExtractionResult


logger = logging.getLogger(__name__)


class PlaywrightExtractor:
    """Extract article content using Playwright browser automation."""

    def __init__(self, headless: bool = True, timeout: int = 30000):
        """
        Initialize Playwright extractor.

        Args:
            headless: Run browser in headless mode
            timeout: Page load timeout in milliseconds

        Raises:
            ImportError: If Playwright is not installed
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is not installed. Install with: "
                "pip install playwright && playwright install chromium"
            )

        self.headless = headless
        self.timeout = timeout

    async def _wait_for_content(self, page) -> bool:
        """
        Wait for article content to load on page.

        Args:
            page: Playwright Page object

        Returns:
            True if content found, False otherwise
        """
        # Common article content selectors
        content_selectors = [
            'article',
            'main',
            '[role="main"]',
            '.post-content',
            '.article-content',
            '.entry-content',
            '#content',
            '.content'
        ]

        try:
            # Wait for at least one content selector to appear
            for selector in content_selectors:
                try:
                    await page.wait_for_selector(selector, timeout=5000, state='attached')
                    logger.debug(f"Found content selector: {selector}")
                    return True
                except PlaywrightTimeout:
                    continue

            # If no specific content selector found, check if page has loaded
            logger.debug("No specific content selector found, assuming page loaded")
            return True

        except Exception as e:
            logger.warning(f"Error waiting for content: {e}")
            return False

    async def extract(self, url: str) -> Optional[ExtractionResult]:
        """
        Extract article content using Playwright browser automation.

        Args:
            url: URL to extract from

        Returns:
            ExtractionResult or None if extraction fails
        """
        if not PLAYWRIGHT_AVAILABLE:
            logger.warning("Playwright not available, skipping browser extraction")
            return None

        logger.info("Attempting Playwright browser extraction")

        try:
            async with async_playwright() as p:
                # Launch browser
                logger.debug(f"Launching Chromium (headless={self.headless})")
                browser = await p.chromium.launch(headless=self.headless)

                try:
                    # Create new page
                    page = await browser.new_page()

                    # Set realistic viewport
                    await page.set_viewport_size({"width": 1280, "height": 720})

                    # Navigate to URL
                    logger.debug(f"Navigating to {url}")
                    try:
                        response = await page.goto(url, timeout=self.timeout, wait_until='domcontentloaded')

                        if not response:
                            logger.warning("No response from page.goto")
                            return None

                        if not response.ok:
                            logger.warning(f"Page returned status {response.status}")
                            # Continue anyway, content might still be accessible

                    except PlaywrightTimeout:
                        logger.warning(f"Page load timed out after {self.timeout}ms")
                        return None

                    # Wait for content to load
                    content_loaded = await self._wait_for_content(page)
                    if not content_loaded:
                        logger.warning("Content did not load within timeout")

                    # Additional wait for dynamic content (e.g., API calls)
                    try:
                        await page.wait_for_load_state('networkidle', timeout=5000)
                        logger.debug("Network idle state reached")
                    except PlaywrightTimeout:
                        logger.debug("Network idle timeout, proceeding anyway")

                    # Get rendered HTML
                    html = await page.content()

                    if not html or len(html) < 1000:
                        logger.warning(f"Retrieved HTML is too short ({len(html)} bytes)")
                        return None

                    logger.debug(f"Retrieved {len(html)} bytes of rendered HTML")

                finally:
                    await browser.close()

                # Use existing ArticleExtractor to parse the rendered HTML
                logger.debug("Parsing rendered HTML with ArticleExtractor")
                extractor = ArticleExtractor()
                result = extractor.extract(url, html=html)

                if result:
                    # Apply Playwright quality adjustment (×0.95)
                    result.quality_score *= 0.95
                    result.engine = 'playwright'
                    logger.info(f"Playwright extraction successful: quality {result.quality_score:.2f}")
                else:
                    logger.warning("ArticleExtractor failed to parse rendered HTML")

                return result

        except ImportError as e:
            logger.error(f"Playwright import error: {e}")
            return None

        except Exception as e:
            logger.error(f"Playwright extraction failed: {e}", exc_info=True)
            return None


async def main():
    """Simple test for command-line usage."""
    import sys
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
        try:
            extractor = PlaywrightExtractor()
            result = await extractor.extract(test_url)
            if result:
                print(f"Title: {result.title}")
                print(f"Authors: {', '.join(result.authors)}")
                print(f"Quality: {result.quality_score:.2f}")
                print(f"Paragraphs: {len(result.paragraphs)}")
            else:
                print("Playwright extraction failed")
        except ImportError as e:
            print(f"Error: {e}")
    else:
        print("Usage: python playwright_extractor.py <url>")


if __name__ == '__main__':
    asyncio.run(main())
