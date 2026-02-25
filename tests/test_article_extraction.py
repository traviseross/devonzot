"""
Unit tests for article extraction module.

Tests HTMLFetcher, MetadataAggregator, and ArticleExtractor classes.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from article_extraction import (
    HTMLFetcher,
    MetadataAggregator,
    ArticleExtractor,
    ExtractionResult
)
from exceptions import NetworkError, ArticleExtractionError


class TestHTMLFetcher:
    """Test HTMLFetcher class."""

    def test_init_default_params(self):
        """Test initialization with default parameters."""
        fetcher = HTMLFetcher()
        assert fetcher.timeout == 10
        assert fetcher.rotate_user_agents is True

    def test_init_custom_params(self):
        """Test initialization with custom parameters."""
        fetcher = HTMLFetcher(timeout=30, rotate_user_agents=False)
        assert fetcher.timeout == 30
        assert fetcher.rotate_user_agents is False

    @patch('article_extraction.requests.Session.get')
    def test_fetch_success(self, mock_get):
        """Test successful HTML fetch."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '<html><body>Test content</body></html>'
        mock_get.return_value = mock_response

        fetcher = HTMLFetcher()
        html = fetcher.fetch('https://example.com')

        assert html == '<html><body>Test content</body></html>'
        assert mock_get.called

    @patch('article_extraction.requests.Session.get')
    def test_fetch_retry_on_failure(self, mock_get):
        """Test fetch retries on transient failures."""
        import requests

        # First two attempts fail with requests exceptions, third succeeds
        mock_response_success = Mock()
        mock_response_success.status_code = 200
        mock_response_success.text = '<html>Success</html>'
        mock_response_success.raise_for_status = Mock()  # No exception

        # Use requests.exceptions for proper exception types
        mock_get.side_effect = [
            requests.exceptions.Timeout("Timeout"),
            requests.exceptions.ConnectionError("Connection failed"),
            mock_response_success
        ]

        fetcher = HTMLFetcher()
        html = fetcher.fetch('https://example.com', max_retries=3)

        assert html == '<html>Success</html>'
        assert mock_get.call_count == 3

    @patch('article_extraction.requests.Session.get')
    def test_fetch_raises_network_error_after_retries(self, mock_get):
        """Test fetch raises NetworkError after all retries fail."""
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError("Network error")

        fetcher = HTMLFetcher()
        with pytest.raises(NetworkError):
            fetcher.fetch('https://example.com', max_retries=3)

        assert mock_get.call_count == 3


class TestMetadataAggregator:
    """Test MetadataAggregator class."""

    def test_join_meta_simple(self):
        """Test simple metadata merging."""
        primary = {'title': 'Test Title', 'author': 'John Doe'}
        secondary = {'author': 'Jane Doe', 'date': '2026-01-01'}

        result = MetadataAggregator.join_meta(primary, secondary)

        assert result['title'] == 'Test Title'
        assert result['author'] == 'John Doe'  # Primary takes precedence
        assert result['date'] == '2026-01-01'   # Filled from secondary

    def test_join_meta_empty_primary_values(self):
        """Test metadata merging when primary has empty values."""
        primary = {'title': '', 'author': None}
        secondary = {'title': 'Secondary Title', 'author': 'Secondary Author'}

        result = MetadataAggregator.join_meta(primary, secondary)

        assert result['title'] == 'Secondary Title'
        assert result['author'] == 'Secondary Author'

    def test_join_meta_multiple_sources(self):
        """Test merging from multiple metadata sources."""
        primary = {'title': 'Primary'}
        secondary = {'author': 'Secondary Author'}
        tertiary = {'date': '2026-01-01', 'author': 'Tertiary Author'}

        result = MetadataAggregator.join_meta(primary, secondary, tertiary)

        assert result['title'] == 'Primary'
        assert result['author'] == 'Secondary Author'  # Secondary fills before tertiary
        assert result['date'] == '2026-01-01'

    def test_clean_authors_valid_names(self):
        """Test cleaning valid author names."""
        authors = ['John Doe', 'Jane Smith-Johnson', "Patrick O'Brien"]

        result = MetadataAggregator.clean_authors(authors)

        assert len(result) == 3
        assert 'John Doe' in result
        assert 'Jane Smith-Johnson' in result
        assert "Patrick O'Brien" in result

    def test_clean_authors_removes_invalid(self):
        """Test cleaning removes invalid author patterns."""
        authors = [
            'John Doe',           # Valid
            'wp-block-author',    # Invalid (HTML artifact)
            'AB',                 # Invalid (too short)
            'font-size: 12px',    # Invalid (CSS)
            'Jane Smith'          # Valid
        ]

        result = MetadataAggregator.clean_authors(authors)

        assert len(result) == 2
        assert 'John Doe' in result
        assert 'Jane Smith' in result

    def test_clean_authors_removes_duplicates(self):
        """Test cleaning removes duplicate authors."""
        authors = ['John Doe', 'Jane Smith', 'John Doe', 'Jane Smith']

        result = MetadataAggregator.clean_authors(authors)

        assert len(result) == 2
        assert result == ['John Doe', 'Jane Smith']  # Order preserved

    def test_clean_authors_handles_string_input(self):
        """Test cleaning handles string input (converts to list)."""
        authors = 'John Doe'

        result = MetadataAggregator.clean_authors(authors)

        assert result == ['John Doe']

    def test_extract_publication_name_from_og_tag(self):
        """Test extracting publication from Open Graph meta tag."""
        html = '''
        <html>
        <head>
            <meta property="og:site_name" content="The New York Times" />
        </head>
        </html>
        '''

        result = MetadataAggregator.extract_publication_name(html, 'https://nytimes.com/article')

        assert result == 'The New York Times'

    def test_extract_publication_name_from_twitter_tag(self):
        """Test extracting publication from Twitter meta tag."""
        html = '''
        <html>
        <head>
            <meta name="twitter:site" content="@nytimes" />
        </head>
        </html>
        '''

        result = MetadataAggregator.extract_publication_name(html, 'https://nytimes.com/article')

        assert result == 'nytimes'  # @ stripped

    def test_extract_publication_name_fallback_to_domain(self):
        """Test extracting publication falls back to domain."""
        html = '<html><head></head></html>'

        result = MetadataAggregator.extract_publication_name(html, 'https://example.com/article/123')

        assert result == 'example.com'


class TestArticleExtractor:
    """Test ArticleExtractor class."""

    @pytest.fixture
    def mock_html_fetcher(self):
        """Create a mock HTMLFetcher."""
        fetcher = Mock(spec=HTMLFetcher)
        fetcher.fetch.return_value = '<html><body>Test HTML</body></html>'
        return fetcher

    def test_init_default_fetcher(self):
        """Test initialization creates default fetcher."""
        extractor = ArticleExtractor()
        assert extractor.html_fetcher is not None
        assert isinstance(extractor.html_fetcher, HTMLFetcher)

    def test_init_custom_fetcher(self, mock_html_fetcher):
        """Test initialization with custom fetcher."""
        extractor = ArticleExtractor(html_fetcher=mock_html_fetcher)
        assert extractor.html_fetcher is mock_html_fetcher

    @patch('article_extraction.Article')
    @patch('article_extraction.Document')
    @patch('article_extraction.trafilatura.extract')
    def test_extract_with_all_engines(self, mock_trafilatura, mock_readability, mock_newspaper, mock_html_fetcher):
        """Test extraction with all engines working."""
        # Mock newspaper3k
        mock_article = Mock()
        mock_article.title = 'Test Article'
        mock_article.authors = ['John Doe']
        mock_article.publish_date = None
        mock_article.text = 'Test paragraph 1\nTest paragraph 2'
        mock_newspaper.return_value = mock_article

        # Mock readability
        mock_doc = Mock()
        mock_doc.title.return_value = 'Test Article'
        mock_doc.summary.return_value = '<p>Test paragraph 1</p><p>Test paragraph 2</p>'
        mock_readability.return_value = mock_doc

        # Mock trafilatura
        mock_trafilatura.return_value = '{"title": "Test Article", "date": "2026-01-01"}'

        extractor = ArticleExtractor(html_fetcher=mock_html_fetcher)
        result = extractor.extract('https://example.com', html='<html>Test</html>')

        assert isinstance(result, ExtractionResult)
        assert result.title == 'Test Article'
        assert result.engine == 'newspaper3k+readability+trafilatura'
        assert result.quality_score > 0

    def test_clean_paragraphs_removes_newsletter_signup(self):
        """Test paragraph cleaning removes newsletter signups."""
        extractor = ArticleExtractor()
        paragraphs = [
            'This is a valid paragraph.',
            'Sign up for our newsletter!',
            'Another valid paragraph.',
            'Subscribe to our editorial note.'
        ]

        result = extractor._clean_paragraphs(paragraphs)

        assert len(result) == 2
        assert 'This is a valid paragraph.' in result
        assert 'Another valid paragraph.' in result

    def test_clean_paragraphs_removes_short_content(self):
        """Test paragraph cleaning removes very short paragraphs."""
        extractor = ArticleExtractor()
        paragraphs = [
            'This is a valid paragraph with enough content.',
            'Short',  # Too short
            'OK',     # Too short
            'Another valid paragraph here.'
        ]

        result = extractor._clean_paragraphs(paragraphs)

        assert len(result) == 2

    def test_calculate_quality_score(self):
        """Test quality score calculation."""
        extractor = ArticleExtractor()

        # High quality: many paragraphs, lots of words, complete metadata
        paragraphs = ['Word ' * 100 for _ in range(20)]  # 20 paragraphs, 2000 words
        metadata = {'title': 'Test', 'authors': ['Author'], 'date': '2026-01-01'}

        score = extractor._calculate_quality_score(paragraphs, metadata)

        assert score >= 0.8  # Should be high quality
        assert score <= 1.0

    def test_calculate_quality_score_low(self):
        """Test quality score for low-quality extraction."""
        extractor = ArticleExtractor()

        # Low quality: few paragraphs, few words, incomplete metadata
        paragraphs = ['Short paragraph.']
        metadata = {'title': 'Test'}

        score = extractor._calculate_quality_score(paragraphs, metadata)

        assert score < 0.5  # Should be low quality


class TestExtractionResult:
    """Test ExtractionResult dataclass."""

    def test_extraction_result_creation(self):
        """Test creating an ExtractionResult."""
        result = ExtractionResult(
            title='Test Title',
            authors=['Author 1', 'Author 2'],
            publish_date='2026-01-01',
            paragraphs=['Para 1', 'Para 2'],
            markdown='# Test\n\nPara 1\n\nPara 2',
            metadata={'key': 'value'},
            engine='test-engine',
            quality_score=0.85
        )

        assert result.title == 'Test Title'
        assert len(result.authors) == 2
        assert result.quality_score == 0.85
        assert result.engine == 'test-engine'

    def test_extraction_result_defaults(self):
        """Test ExtractionResult with default values."""
        result = ExtractionResult(title='Test')

        assert result.authors == []
        assert result.publish_date is None
        assert result.paragraphs == []
        assert result.markdown == ''
        assert result.metadata == {}
        assert result.engine == 'unknown'
        assert result.quality_score == 0.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
