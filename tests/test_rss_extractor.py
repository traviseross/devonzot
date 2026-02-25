"""
Unit tests for RSS extractor module.

Tests RSSExtractor class for feed detection, article matching, and content extraction.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from rss_extractor import RSSExtractor
from article_extraction import ExtractionResult


class TestRSSExtractor:
    """Test RSSExtractor class."""

    def test_init_default_params(self):
        """Test initialization with default parameters."""
        extractor = RSSExtractor()
        assert extractor.timeout == 15

    def test_init_custom_timeout(self):
        """Test initialization with custom timeout."""
        extractor = RSSExtractor(timeout=30)
        assert extractor.timeout == 30

    def test_detect_feeds_from_link_tags(self):
        """Test feed detection from HTML link tags."""
        html = '''
        <html>
        <head>
            <link rel="alternate" type="application/rss+xml" href="/feed" />
            <link rel="alternate" type="application/atom+xml" href="/atom.xml" />
        </head>
        </html>
        '''
        extractor = RSSExtractor()
        feeds = extractor.detect_feeds('https://example.com/article', html)

        assert len(feeds) >= 2
        assert any('/feed' in feed for feed in feeds)
        assert any('/atom.xml' in feed for feed in feeds)

    def test_detect_feeds_fallback_to_common_paths(self):
        """Test feed detection falls back to common paths when no link tags."""
        html = '<html><head></head><body>No feed links</body></html>'
        extractor = RSSExtractor()
        feeds = extractor.detect_feeds('https://example.com/article', html)

        # Should return common feed path suggestions
        assert len(feeds) > 0
        assert any('/feed' in feed for feed in feeds)
        assert any('/rss' in feed for feed in feeds)

    def test_normalize_url(self):
        """Test URL normalization for comparison."""
        extractor = RSSExtractor()

        # Test trailing slash removal
        assert extractor._normalize_url('https://example.com/article/') == 'https://example.com/article'

        # Test query parameter removal
        assert extractor._normalize_url('https://example.com/article?utm_source=x') == 'https://example.com/article'

        # Test lowercase conversion
        assert extractor._normalize_url('https://Example.COM/Article') == 'https://example.com/article'

    def test_title_similarity_identical(self):
        """Test title similarity with identical titles."""
        extractor = RSSExtractor()
        similarity = extractor._title_similarity('Test Article', 'Test Article')
        assert similarity == 1.0

    def test_title_similarity_similar(self):
        """Test title similarity with similar titles."""
        extractor = RSSExtractor()
        similarity = extractor._title_similarity(
            'The Quick Brown Fox',
            'The Quick Brown Fox Jumps'
        )
        assert similarity > 0.7  # High similarity

    def test_title_similarity_different(self):
        """Test title similarity with different titles."""
        extractor = RSSExtractor()
        similarity = extractor._title_similarity(
            'Completely Different Title',
            'Another Unrelated Article'
        )
        assert similarity < 0.5  # Low similarity

    def test_title_similarity_empty(self):
        """Test title similarity with empty strings."""
        extractor = RSSExtractor()
        assert extractor._title_similarity('', 'Test') == 0.0
        assert extractor._title_similarity('Test', '') == 0.0
        assert extractor._title_similarity('', '') == 0.0

    @patch('rss_extractor.feedparser.parse')
    @patch('rss_extractor.requests.Session.get')
    def test_fetch_feed_entry_exact_url_match(self, mock_get, mock_parse):
        """Test fetching feed entry with exact URL match."""
        # Mock HTTP response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'<rss>...</rss>'
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Mock feedparser response
        mock_feed = Mock()
        mock_feed.entries = [
            {'link': 'https://example.com/article', 'title': 'Test Article'},
            {'link': 'https://example.com/other', 'title': 'Other Article'}
        ]
        mock_parse.return_value = mock_feed

        extractor = RSSExtractor()
        entry = extractor.fetch_feed_entry(
            'https://example.com/feed',
            'https://example.com/article'
        )

        assert entry is not None
        assert entry['title'] == 'Test Article'

    @patch('rss_extractor.feedparser.parse')
    @patch('rss_extractor.requests.Session.get')
    def test_fetch_feed_entry_normalized_url_match(self, mock_get, mock_parse):
        """Test fetching feed entry with normalized URL match."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'<rss>...</rss>'
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        mock_feed = Mock()
        mock_feed.entries = [
            {'link': 'https://example.com/article/', 'title': 'Test Article'}
        ]
        mock_parse.return_value = mock_feed

        extractor = RSSExtractor()
        entry = extractor.fetch_feed_entry(
            'https://example.com/feed',
            'https://example.com/article?utm_source=x'  # Different but normalized matches
        )

        assert entry is not None
        assert entry['title'] == 'Test Article'

    @patch('rss_extractor.feedparser.parse')
    @patch('rss_extractor.requests.Session.get')
    def test_fetch_feed_entry_no_match(self, mock_get, mock_parse):
        """Test fetching feed entry with no matching entry."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'<rss>...</rss>'
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        mock_feed = Mock()
        mock_feed.entries = [
            {'link': 'https://example.com/other', 'title': 'Different Article'}
        ]
        mock_parse.return_value = mock_feed

        extractor = RSSExtractor()
        entry = extractor.fetch_feed_entry(
            'https://example.com/feed',
            'https://example.com/nonexistent'
        )

        assert entry is None

    def test_extract_content_from_entry_with_content_field(self):
        """Test content extraction from entry with content field."""
        entry = {
            'content': [{'value': '<p>Paragraph 1</p><p>Paragraph 2</p>'}],
            'summary': 'Summary text'
        }

        extractor = RSSExtractor()
        content = extractor._extract_content_from_entry(entry)

        assert 'Paragraph 1' in content
        assert 'Paragraph 2' in content
        assert '<p>' not in content  # HTML stripped

    def test_extract_content_from_entry_with_summary(self):
        """Test content extraction falls back to summary."""
        entry = {
            'summary': '<p>Summary paragraph</p>'
        }

        extractor = RSSExtractor()
        content = extractor._extract_content_from_entry(entry)

        assert 'Summary paragraph' in content
        assert '<p>' not in content  # HTML stripped

    def test_extract_metadata_from_entry(self):
        """Test metadata extraction from feed entry."""
        import time
        from time import struct_time

        # Create a struct_time for 2024-01-15
        test_time = struct_time((2024, 1, 15, 12, 0, 0, 0, 15, 0))

        entry = {
            'title': 'Test Article Title',
            'author': 'John Doe',
            'published_parsed': test_time
        }

        extractor = RSSExtractor()
        metadata = extractor._extract_metadata_from_entry(entry)

        assert metadata['title'] == 'Test Article Title'
        assert 'John Doe' in metadata['authors']
        assert metadata['date'] == '2024-01-15'

    @patch('rss_extractor.RSSExtractor.detect_feeds')
    @patch('rss_extractor.RSSExtractor.fetch_feed_entry')
    def test_extract_from_rss_success(self, mock_fetch_entry, mock_detect_feeds):
        """Test successful RSS extraction."""
        import time
        from time import struct_time

        # Mock feed detection
        mock_detect_feeds.return_value = ['https://example.com/feed']

        # Mock feed entry
        test_time = struct_time((2024, 1, 15, 12, 0, 0, 0, 15, 0))
        mock_entry = {
            'title': 'Test Article',
            'author': 'Jane Smith',
            'published_parsed': test_time,
            'content': [{'value': '<p>' + ('Test content word ' * 100) + '</p>'}]  # Lots of content
        }
        mock_fetch_entry.return_value = mock_entry

        extractor = RSSExtractor()
        result = extractor.extract_from_rss('https://example.com/article', html='<html></html>')

        assert result is not None
        assert isinstance(result, ExtractionResult)
        assert result.title == 'Test Article'
        assert 'Jane Smith' in result.authors
        assert result.engine == 'rss'
        assert result.quality_score > 0
        assert result.quality_score <= 1.0

    @patch('rss_extractor.RSSExtractor.detect_feeds')
    def test_extract_from_rss_no_feeds_detected(self, mock_detect_feeds):
        """Test RSS extraction when no feeds are detected."""
        mock_detect_feeds.return_value = []

        extractor = RSSExtractor()
        result = extractor.extract_from_rss('https://example.com/article', html='<html></html>')

        assert result is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
