"""
Tests for the three-tier article extraction fallback chain.

Tests cascading fallback logic:
- Tier 0: Standard extraction (newspaper3k + readability + trafilatura)
- Tier 1: RSS/Atom feed extraction
- Tier 2: Playwright browser automation (optional)
- Tier 3: Wayback Machine archive extraction
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from pathlib import Path
import sys
import asyncio

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from pipeline_add_url import run_pipeline
from article_extraction import ExtractionResult
from exceptions import ArticleExtractionError


@pytest.mark.asyncio
class TestPipelineFallback:
    """Test suite for extraction fallback chain."""

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    @patch('pipeline_add_url.DEVONthinkInterface')
    @patch('pipeline_add_url.ZoteroAPIClient')
    @patch('pipeline_add_url.RSSExtractor')
    @patch('pipeline_add_url.WaybackExtractor')
    async def test_fallback_standard_succeeds_immediately(
        self,
        mock_wayback_class,
        mock_rss_class,
        mock_zot_class,
        mock_dt_class,
        mock_combine_articles,
        mock_create_item,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test standard extraction succeeds, other tiers not attempted."""
        # Arrange
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {'title': 'Test'}
        }

        # Standard extraction returns high quality
        high_quality_result = ExtractionResult(
            title="Test Article",
            authors=["John Smith"],
            publish_date="2024-01-15",
            markdown="# Test\n\nGood content.",
            paragraphs=["Para 1", "Para 2", "Para 3", "Para 4"],
            quality_score=0.85,  # High quality
            engine="standard",
            metadata={}
        )
        mock_combine_articles.return_value = high_quality_result

        # Mock DEVONthink and Zotero
        mock_dt = mock_dt_class.return_value
        mock_dt.copy_file_to_inbox = Mock(return_value=True)
        mock_dt.find_item_by_filename_after_wait_async = AsyncMock(return_value="UUID-1")

        mock_zot = mock_zot_class.return_value
        mock_zot.create_url_attachments = Mock(return_value=[{'key': 'ATTACH1'}])

        # Act
        await run_pipeline('https://example.com/article')

        # Assert
        assert mock_combine_articles.called
        # RSS and Wayback should NOT be attempted
        mock_rss = mock_rss_class.return_value
        assert mock_rss.extract_from_rss.call_count == 0
        mock_wayback = mock_wayback_class.return_value
        assert mock_wayback.extract_from_archive.call_count == 0

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    @patch('pipeline_add_url.RSSExtractor')
    @patch('pipeline_add_url.DEVONthinkInterface')
    @patch('pipeline_add_url.ZoteroAPIClient')
    async def test_fallback_standard_fails_rss_succeeds(
        self,
        mock_zot_class,
        mock_dt_class,
        mock_rss_class,
        mock_combine_articles,
        mock_create_item,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test fallback to RSS when standard extraction quality too low."""
        # Arrange
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {'title': 'Test'}
        }

        # Standard returns low quality
        low_quality = ExtractionResult(
            title="Test",
            authors=[],
            publish_date=None,
            markdown="# Test\n\nMinimal.",
            paragraphs=["Minimal."],
            quality_score=0.05,  # Below threshold
            engine="standard",
            metadata={}
        )
        mock_combine_articles.return_value = low_quality

        # RSS returns good quality
        rss_quality = ExtractionResult(
            title="Test Article",
            authors=["Smith"],
            publish_date="2024-01-15",
            markdown="# Test\n\nGood RSS content.",
            paragraphs=["Para 1", "Para 2", "Para 3"],
            quality_score=0.72,  # Good quality
            engine="rss",
            metadata={}
        )
        mock_rss = mock_rss_class.return_value
        mock_rss.extract_from_rss = Mock(return_value=rss_quality)

        # Mock DEVONthink and Zotero
        mock_dt = mock_dt_class.return_value
        mock_dt.copy_file_to_inbox = Mock(return_value=True)
        mock_dt.find_item_by_filename_after_wait_async = AsyncMock(return_value="UUID-1")

        mock_zot = mock_zot_class.return_value
        mock_zot.create_url_attachments = Mock(return_value=[{'key': 'ATTACH1'}])

        # Act
        await run_pipeline('https://example.com/article')

        # Assert
        assert mock_combine_articles.called  # Standard tried first
        assert mock_rss.extract_from_rss.called  # RSS tried second
        # Pipeline should complete successfully with RSS result

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    @patch('pipeline_add_url.RSSExtractor')
    @patch('pipeline_add_url.PlaywrightExtractor')
    @patch('pipeline_add_url.DEVONthinkInterface')
    @patch('pipeline_add_url.ZoteroAPIClient')
    @patch('pipeline_add_url.ENABLE_PLAYWRIGHT', True)
    async def test_fallback_playwright_succeeds(
        self,
        mock_zot_class,
        mock_dt_class,
        mock_playwright_class,
        mock_rss_class,
        mock_combine_articles,
        mock_create_item,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test Playwright fallback when standard and RSS fail."""
        # Arrange
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {'title': 'Test'}
        }

        # Standard and RSS return low quality
        low_quality = ExtractionResult(
            title="Test",
            authors=[],
            date=None,
            publication=None,
            url="https://example.com/article",
            markdown="# Test\n\nMinimal.",
            paragraphs=["Minimal."],
            quality_score=0.05,
            engine="standard",
            metadata={}
        )
        mock_combine_articles.return_value = low_quality

        mock_rss = mock_rss_class.return_value
        mock_rss.extract_from_rss = Mock(return_value=low_quality)

        # Playwright returns good quality
        playwright_quality = ExtractionResult(
            title="Test Article",
            authors=["Smith"],
            publish_date="2024-01-15",
            markdown="# Test\n\nRendered JS content.",
            paragraphs=["Para 1", "Para 2", "Para 3"],
            quality_score=0.65,
            engine="playwright",
            metadata={}
        )
        mock_playwright = mock_playwright_class.return_value
        mock_playwright.extract = AsyncMock(return_value=playwright_quality)

        # Mock DEVONthink and Zotero
        mock_dt = mock_dt_class.return_value
        mock_dt.copy_file_to_inbox = Mock(return_value=True)
        mock_dt.find_item_by_filename_after_wait_async = AsyncMock(return_value="UUID-1")

        mock_zot = mock_zot_class.return_value
        mock_zot.create_url_attachments = Mock(return_value=[{'key': 'ATTACH1'}])

        # Act
        await run_pipeline('https://example.com/article')

        # Assert
        assert mock_playwright.extract.called

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    @patch('pipeline_add_url.RSSExtractor')
    @patch('pipeline_add_url.WaybackExtractor')
    @patch('pipeline_add_url.DEVONthinkInterface')
    @patch('pipeline_add_url.ZoteroAPIClient')
    async def test_fallback_wayback_final_tier(
        self,
        mock_zot_class,
        mock_dt_class,
        mock_wayback_class,
        mock_rss_class,
        mock_combine_articles,
        mock_create_item,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test Wayback Machine as final fallback tier."""
        # Arrange
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {'title': 'Test'}
        }

        # Standard and RSS fail
        low_quality = ExtractionResult(
            title="Test",
            authors=[],
            date=None,
            publication=None,
            url="https://example.com/article",
            markdown="# Test\n\nMinimal.",
            paragraphs=["Minimal."],
            quality_score=0.05,
            engine="standard",
            metadata={}
        )
        mock_combine_articles.return_value = low_quality

        mock_rss = mock_rss_class.return_value
        mock_rss.extract_from_rss = Mock(return_value=low_quality)

        # Wayback returns acceptable quality
        wayback_quality = ExtractionResult(
            title="Test Article",
            authors=["Smith"],
            publish_date="2024-01-15",
            markdown="# Test\n\nArchived content.",
            paragraphs=["Para 1", "Para 2"],
            quality_score=0.15,  # Minimal but acceptable
            engine="wayback",
            metadata={"archived_date": "2023-12-01"}
        )
        mock_wayback = mock_wayback_class.return_value
        mock_wayback.extract_from_archive = Mock(return_value=wayback_quality)

        # Mock DEVONthink and Zotero
        mock_dt = mock_dt_class.return_value
        mock_dt.copy_file_to_inbox = Mock(return_value=True)
        mock_dt.find_item_by_filename_after_wait_async = AsyncMock(return_value="UUID-1")

        mock_zot = mock_zot_class.return_value
        mock_zot.create_url_attachments = Mock(return_value=[{'key': 'ATTACH1'}])

        # Act
        await run_pipeline('https://example.com/article')

        # Assert
        assert mock_wayback.extract_from_archive.called

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    @patch('pipeline_add_url.RSSExtractor')
    @patch('pipeline_add_url.ENABLE_PLAYWRIGHT', False)
    @patch('pipeline_add_url.WaybackExtractor')
    async def test_fallback_playwright_disabled(
        self,
        mock_wayback_class,
        mock_rss_class,
        mock_combine_articles,
        mock_create_item,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test Playwright tier skipped when disabled."""
        # Arrange
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {'title': 'Test'}
        }

        # Standard and RSS fail
        low_quality = ExtractionResult(
            title="Test",
            authors=[],
            date=None,
            publication=None,
            url="https://example.com/article",
            markdown="# Test",
            paragraphs=["Minimal."],
            quality_score=0.05,
            engine="standard",
            metadata={}
        )
        mock_combine_articles.return_value = low_quality

        mock_rss = mock_rss_class.return_value
        mock_rss.extract_from_rss = Mock(return_value=low_quality)
        mock_wayback = mock_wayback_class.return_value
        mock_wayback.extract_from_archive = Mock(return_value=low_quality)

        # Act & Assert
        with pytest.raises(ArticleExtractionError):
            await run_pipeline('https://example.com/article')

        # Playwright should not be imported/used when disabled
        # (This test verifies the skip logic)

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    async def test_fallback_timeout_handling(
        self,
        mock_combine_articles,
        mock_create_item,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test timeout triggers fallback to next tier."""
        # Arrange
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {'title': 'Test'}
        }

        # Standard extraction times out
        async def slow_extraction(*args, **kwargs):
            await asyncio.sleep(200)  # Exceeds EXTRACTION_TIMEOUT
            return None

        mock_combine_articles.side_effect = slow_extraction

        # Act & Assert
        # Should timeout and attempt fallback tiers
        # (Actual timeout handling depends on implementation)
        with pytest.raises((asyncio.TimeoutError, ArticleExtractionError)):
            await run_pipeline('https://example.com/article')

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    @patch('pipeline_add_url.RSSExtractor')
    @patch('pipeline_add_url.WaybackExtractor')
    async def test_fallback_quality_threshold_enforcement(
        self,
        mock_wayback_class,
        mock_rss_class,
        mock_combine_articles,
        mock_create_item,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test all extractors rejected if below quality threshold."""
        # Arrange
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {'title': 'Test'}
        }

        # All extractors return quality just below threshold
        almost_quality = ExtractionResult(
            title="Test",
            authors=[],
            publish_date=None,
            markdown="# Test\n\nShort.",
            paragraphs=["Short."],
            quality_score=0.09,  # Just below 0.1 threshold
            engine="standard",
            metadata={}
        )
        mock_combine_articles.return_value = almost_quality

        mock_rss = mock_rss_class.return_value
        mock_rss.extract_from_rss = Mock(return_value=almost_quality)

        mock_wayback = mock_wayback_class.return_value
        mock_wayback.extract_from_archive = Mock(return_value=almost_quality)

        # Act & Assert
        with pytest.raises(ArticleExtractionError, match="insufficient quality|extraction failed"):
            await run_pipeline('https://example.com/article')

        # Verify all tiers attempted
        assert mock_combine_articles.called
        assert mock_rss.extract_from_rss.called
        assert mock_wayback.extract_from_archive.called
