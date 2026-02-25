"""
Integration tests for the full 5-step pipeline.

Tests end-to-end workflow from URL to Zotero item creation,
article extraction, DEVONthink import, and bidirectional linking.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from pathlib import Path
import sys
import asyncio

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from pipeline_add_url import run_pipeline
from exceptions import ZoteroAPIError, ArticleExtractionError, DEVONthinkIntegrationError


@pytest.mark.asyncio
@pytest.mark.integration
class TestPipelineIntegration:
    """Test suite for full pipeline integration."""

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    @patch('pipeline_add_url.DEVONthinkInterface')
    @patch('pipeline_add_url.ZoteroAPIClient')
    async def test_full_pipeline_success_standard_extraction(
        self,
        mock_zot_class,
        mock_dt_class,
        mock_combine_articles,
        mock_create_item,
        mock_extraction_success,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test complete pipeline with successful standard extraction."""
        # Arrange - Mock all 5 steps
        # Step 1: Zotero item creation
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {
                'title': 'Test Article',
                'creators': [{'lastName': 'Smith'}],
                'date': '2024-01-15'
            }
        }

        # Step 2: Standard extraction
        mock_combine_articles.return_value = mock_extraction_success

        # Step 3: DEVONthink copy
        mock_dt = mock_dt_class.return_value
        mock_dt.copy_file_to_inbox = Mock(return_value=True)

        # Step 4: UUID lookup
        mock_dt.find_item_by_filename_after_wait_async = AsyncMock(
            return_value="UUID-12345"
        )

        # Step 5: Create attachment
        mock_zot = mock_zot_class.return_value
        mock_zot.create_url_attachments = Mock(
            return_value=[{'key': 'ATTACH1', 'data': {}}]
        )

        # Act
        await run_pipeline('https://example.com/article')

        # Assert - Verify all steps called
        assert mock_create_item.called
        assert mock_combine_articles.called
        assert mock_dt.copy_file_to_inbox.called
        assert mock_dt.find_item_by_filename_after_wait_async.called
        assert mock_zot.create_url_attachments.called

        # Verify attachment URL format
        call_args = mock_zot.create_url_attachments.call_args[0][0]
        assert len(call_args) == 1
        assert 'x-devonthink-item://UUID-12345' in call_args[0]['url']
        assert call_args[0]['parent_key'] == 'TEST123'

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    async def test_full_pipeline_zotero_creation_fails(
        self,
        mock_create_item,
        mock_env_config
    ):
        """Test pipeline exits early when Zotero creation fails."""
        # Arrange
        mock_create_item.side_effect = ZoteroAPIError("API Error", status_code=500)

        # Act & Assert
        with pytest.raises(ZoteroAPIError):
            await run_pipeline('https://example.com/article')

        # Verify only Step 1 was attempted
        assert mock_create_item.called

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    @patch('pipeline_add_url.RSSExtractor')
    @patch('pipeline_add_url.WaybackExtractor')
    async def test_full_pipeline_extraction_fails_all_tiers(
        self,
        mock_wayback_class,
        mock_rss_class,
        mock_combine_articles,
        mock_create_item,
        mock_extraction_low_quality,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test pipeline fails when all extraction tiers return low quality."""
        # Arrange
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {'title': 'Test'}
        }

        # All extractors return low quality
        mock_combine_articles.return_value = mock_extraction_low_quality

        mock_rss = mock_rss_class.return_value
        mock_rss.extract_from_rss = Mock(return_value=mock_extraction_low_quality)

        mock_wayback = mock_wayback_class.return_value
        mock_wayback.extract_from_archive = Mock(return_value=mock_extraction_low_quality)

        # Act & Assert
        with pytest.raises(ArticleExtractionError):
            await run_pipeline('https://example.com/article')

        # Verify all extraction methods attempted
        assert mock_combine_articles.called
        assert mock_rss.extract_from_rss.called
        assert mock_wayback.extract_from_archive.called

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    @patch('pipeline_add_url.DEVONthinkInterface')
    async def test_full_pipeline_devonthink_copy_fails(
        self,
        mock_dt_class,
        mock_combine_articles,
        mock_create_item,
        mock_extraction_success,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test pipeline fails when DEVONthink copy fails."""
        # Arrange
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {'title': 'Test'}
        }
        mock_combine_articles.return_value = mock_extraction_success

        # Step 3 fails
        mock_dt = mock_dt_class.return_value
        mock_dt.copy_file_to_inbox = Mock(return_value=False)

        # Act & Assert
        with pytest.raises(DEVONthinkIntegrationError):
            await run_pipeline('https://example.com/article')

        # Verify Step 4 never called
        assert mock_dt.find_item_by_filename_after_wait_async.call_count == 0

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    @patch('pipeline_add_url.DEVONthinkInterface')
    async def test_full_pipeline_uuid_lookup_fails(
        self,
        mock_dt_class,
        mock_combine_articles,
        mock_create_item,
        mock_extraction_success,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test pipeline fails when UUID lookup returns None."""
        # Arrange
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {'title': 'Test'}
        }
        mock_combine_articles.return_value = mock_extraction_success

        mock_dt = mock_dt_class.return_value
        mock_dt.copy_file_to_inbox = Mock(return_value=True)
        # Step 4 fails
        mock_dt.find_item_by_filename_after_wait_async = AsyncMock(return_value=None)

        # Act & Assert
        with pytest.raises(DEVONthinkIntegrationError):
            await run_pipeline('https://example.com/article')

    @patch('pipeline_add_url.create_zotero_item_with_retry')
    @patch('pipeline_add_url.combine_articles')
    @patch('pipeline_add_url.DEVONthinkInterface')
    @patch('pipeline_add_url.ZoteroAPIClient')
    async def test_full_pipeline_dry_run_mode(
        self,
        mock_zot_class,
        mock_dt_class,
        mock_combine_articles,
        mock_create_item,
        mock_extraction_success,
        temp_extraction_dir,
        mock_env_config
    ):
        """Test pipeline in dry-run mode doesn't make actual changes."""
        # Arrange - In dry-run, mocks aren't called
        mock_create_item.return_value = {
            'key': 'TEST123',
            'data': {'title': 'Test'}
        }
        mock_combine_articles.return_value = mock_extraction_success

        # Act
        await run_pipeline('https://example.com/article', dry_run=True)

        # Assert - In dry-run mode, actual API calls are skipped
        # create_item_from_url should NOT be called in dry-run
        assert mock_create_item.call_count == 0
