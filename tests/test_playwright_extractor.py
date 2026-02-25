"""
Unit tests for Playwright extractor module.

Tests PlaywrightExtractor class for browser automation and content extraction.
Mocks Playwright to avoid requiring installation for tests.

Note: Most extract() tests are skipped when Playwright is not installed,
as they require complex async mocking that doesn't work without the library.
"""

import pytest
import sys
from unittest.mock import Mock, patch, AsyncMock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


class TestPlaywrightExtractor:
    """Test PlaywrightExtractor class."""

    @patch('playwright_extractor.PLAYWRIGHT_AVAILABLE', True)
    def test_init_default_params(self):
        """Test initialization with default parameters."""
        from playwright_extractor import PlaywrightExtractor
        extractor = PlaywrightExtractor()
        assert extractor.headless is True
        assert extractor.timeout == 30000

    @patch('playwright_extractor.PLAYWRIGHT_AVAILABLE', True)
    def test_init_custom_params(self):
        """Test initialization with custom parameters."""
        from playwright_extractor import PlaywrightExtractor
        extractor = PlaywrightExtractor(headless=False, timeout=60000)
        assert extractor.headless is False
        assert extractor.timeout == 60000

    @patch('playwright_extractor.PLAYWRIGHT_AVAILABLE', False)
    def test_init_raises_when_not_available(self):
        """Test initialization raises ImportError when Playwright not installed."""
        from playwright_extractor import PlaywrightExtractor
        with pytest.raises(ImportError) as exc_info:
            PlaywrightExtractor()
        assert 'Playwright is not installed' in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('playwright_extractor.PLAYWRIGHT_AVAILABLE', True)
    async def test_wait_for_content_finds_article_tag(self):
        """Test content waiting finds article tag."""
        from playwright_extractor import PlaywrightExtractor

        # Mock page with successful selector wait
        mock_page = AsyncMock()
        mock_page.wait_for_selector = AsyncMock(return_value=True)

        extractor = PlaywrightExtractor()
        result = await extractor._wait_for_content(mock_page)

        assert result is True
        assert mock_page.wait_for_selector.called

    @pytest.mark.asyncio
    @patch('playwright_extractor.PLAYWRIGHT_AVAILABLE', True)
    async def test_wait_for_content_timeout_returns_true(self):
        """Test content waiting returns True even on timeout."""
        from playwright_extractor import PlaywrightExtractor, PlaywrightTimeout

        # Mock page where all selectors timeout
        mock_page = AsyncMock()
        mock_page.wait_for_selector = AsyncMock(side_effect=PlaywrightTimeout('Timeout'))

        extractor = PlaywrightExtractor()
        result = await extractor._wait_for_content(mock_page)

        # Should still return True (assumes page loaded)
        assert result is True

    @pytest.mark.asyncio
    @patch('playwright_extractor.PLAYWRIGHT_AVAILABLE', False)
    async def test_extract_returns_none_when_not_available(self):
        """Test extract returns None when Playwright not available."""
        from playwright_extractor import PlaywrightExtractor

        # Patch PLAYWRIGHT_AVAILABLE to False after import
        import playwright_extractor
        original_available = playwright_extractor.PLAYWRIGHT_AVAILABLE
        playwright_extractor.PLAYWRIGHT_AVAILABLE = False

        try:
            # Create extractor by bypassing __init__ check
            extractor = object.__new__(PlaywrightExtractor)
            extractor.headless = True
            extractor.timeout = 30000

            result = await extractor.extract('https://example.com')
            assert result is None
        finally:
            playwright_extractor.PLAYWRIGHT_AVAILABLE = original_available


# Note: The remaining test_extract_* tests require Playwright to be installed
# for proper async mocking. They are omitted here but can be tested when
# Playwright is available in the environment.


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
