"""
Unit tests for Wayback Machine extractor module.

Tests WaybackExtractor class for Internet Archive snapshot retrieval and extraction.
"""

import pytest
from unittest.mock import Mock, patch
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from wayback_extractor import WaybackExtractor
from article_extraction import ExtractionResult


class TestWaybackExtractor:
    """Test WaybackExtractor class."""

    def test_init_default_params(self):
        """Test initialization with default parameters."""
        extractor = WaybackExtractor()
        assert extractor.timeout == 15
        assert extractor.prefer_recent is True

    def test_init_custom_params(self):
        """Test initialization with custom parameters."""
        extractor = WaybackExtractor(timeout=30, prefer_recent=False)
        assert extractor.timeout == 30
        assert extractor.prefer_recent is False

    @patch('wayback_extractor.requests.Session.get')
    def test_get_latest_snapshot_success(self, mock_get):
        """Test successful snapshot retrieval from Wayback API."""
        # Mock Wayback API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'archived_snapshots': {
                'closest': {
                    'available': True,
                    'url': 'https://web.archive.org/web/20240115120000/https://example.com/article',
                    'timestamp': '20240115120000',
                    'status': '200'
                }
            }
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        extractor = WaybackExtractor()
        snapshot = extractor.get_latest_snapshot('https://example.com/article')

        assert snapshot is not None
        assert 'url' in snapshot
        assert 'timestamp' in snapshot
        assert snapshot['timestamp'] == '20240115120000'
        assert 'web.archive.org' in snapshot['url']

    @patch('wayback_extractor.requests.Session.get')
    def test_get_latest_snapshot_not_available(self, mock_get):
        """Test snapshot retrieval when no snapshots available."""
        # Mock Wayback API response with no snapshots
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'archived_snapshots': {}
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        extractor = WaybackExtractor()
        snapshot = extractor.get_latest_snapshot('https://example.com/new-article')

        assert snapshot is None

    @patch('wayback_extractor.requests.Session.get')
    def test_get_latest_snapshot_api_error(self, mock_get):
        """Test snapshot retrieval handles API errors."""
        import requests
        mock_get.side_effect = requests.RequestException('API error')

        extractor = WaybackExtractor()
        snapshot = extractor.get_latest_snapshot('https://example.com/article')

        assert snapshot is None

    def test_parse_timestamp_valid(self):
        """Test parsing valid Wayback timestamp."""
        extractor = WaybackExtractor()
        date = extractor._parse_timestamp('20240115120000')
        assert date == '2024-01-15'

    def test_parse_timestamp_short(self):
        """Test parsing short timestamp (date only)."""
        extractor = WaybackExtractor()
        date = extractor._parse_timestamp('20240115')
        assert date == '2024-01-15'

    def test_parse_timestamp_invalid(self):
        """Test parsing invalid timestamp."""
        extractor = WaybackExtractor()
        date = extractor._parse_timestamp('invalid')
        assert date is None

    @patch('wayback_extractor.WaybackExtractor.get_latest_snapshot')
    @patch('wayback_extractor.requests.Session.get')
    @patch('wayback_extractor.ArticleExtractor')
    def test_extract_from_archive_success(self, mock_article_extractor_class, mock_get, mock_get_snapshot):
        """Test successful extraction from archived snapshot."""
        # Mock snapshot retrieval
        mock_get_snapshot.return_value = {
            'url': 'https://web.archive.org/web/20240115120000/https://example.com/article',
            'timestamp': '20240115120000',
            'status': '200'
        }

        # Mock archived HTML fetch
        mock_html_response = Mock()
        mock_html_response.status_code = 200
        mock_html_response.text = '<html><body>' + 'x' * 2000 + '</body></html>'
        mock_html_response.raise_for_status = Mock()
        mock_get.return_value = mock_html_response

        # Mock ArticleExtractor
        mock_extraction_result = ExtractionResult(
            title='Archived Article',
            authors=['Jane Doe'],
            quality_score=0.8,
            engine='wayback'
        )
        mock_extractor_instance = Mock()
        mock_extractor_instance.extract.return_value = mock_extraction_result
        mock_article_extractor_class.return_value = mock_extractor_instance

        extractor = WaybackExtractor()
        result = extractor.extract_from_archive('https://example.com/article')

        assert result is not None
        assert result.title == 'Archived Article'
        assert result.engine == 'wayback'
        # Quality should be adjusted (×0.85)
        assert result.quality_score == pytest.approx(0.8 * 0.85)
        # Should have archived_date in metadata
        assert 'archived_date' in result.metadata
        assert result.metadata['archived_date'] == '2024-01-15'

    @patch('wayback_extractor.WaybackExtractor.get_latest_snapshot')
    def test_extract_from_archive_no_snapshot(self, mock_get_snapshot):
        """Test extraction when no snapshot available."""
        mock_get_snapshot.return_value = None

        extractor = WaybackExtractor()
        result = extractor.extract_from_archive('https://example.com/article')

        assert result is None

    @patch('wayback_extractor.WaybackExtractor.get_latest_snapshot')
    @patch('wayback_extractor.requests.Session.get')
    def test_extract_from_archive_fetch_fails(self, mock_get, mock_get_snapshot):
        """Test extraction when archived HTML fetch fails."""
        import requests

        mock_get_snapshot.return_value = {
            'url': 'https://web.archive.org/web/20240115120000/https://example.com/article',
            'timestamp': '20240115120000',
            'status': '200'
        }

        # Mock HTML fetch failure
        mock_get.side_effect = requests.RequestException('Fetch failed')

        extractor = WaybackExtractor()
        result = extractor.extract_from_archive('https://example.com/article')

        assert result is None

    @patch('wayback_extractor.WaybackExtractor.get_latest_snapshot')
    @patch('wayback_extractor.requests.Session.get')
    @patch('wayback_extractor.ArticleExtractor')
    def test_extract_from_archive_extractor_fails(self, mock_article_extractor_class, mock_get, mock_get_snapshot):
        """Test extraction when ArticleExtractor fails to parse."""
        mock_get_snapshot.return_value = {
            'url': 'https://web.archive.org/web/20240115120000/https://example.com/article',
            'timestamp': '20240115120000',
            'status': '200'
        }

        mock_html_response = Mock()
        mock_html_response.status_code = 200
        mock_html_response.text = '<html><body>' + 'x' * 2000 + '</body></html>'
        mock_html_response.raise_for_status = Mock()
        mock_get.return_value = mock_html_response

        # Mock ArticleExtractor to return None (parsing failed)
        mock_extractor_instance = Mock()
        mock_extractor_instance.extract.return_value = None
        mock_article_extractor_class.return_value = mock_extractor_instance

        extractor = WaybackExtractor()
        result = extractor.extract_from_archive('https://example.com/article')

        assert result is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
