"""
Shared pytest fixtures for DEVONzot test suite.

Provides mocks for:
- ZoteroAPIClient (API interactions)
- DEVONthinkInterface (AppleScript automation)
- Article extraction results (successful and low-quality)
- Environment configuration
- Sample API responses
"""

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


@pytest.fixture
def mock_zotero_client():
    """Mock ZoteroAPIClient with common methods."""
    client = Mock()
    client.create_item_from_url = Mock(return_value={
        'key': 'TEST123',
        'data': {'title': 'Test Article', 'itemType': 'journalArticle'},
        '_translated_metadata': None,
    })
    client.create_url_attachments = Mock(return_value=[{'key': 'ATTACH1'}])
    client.get_item = Mock(return_value=None)
    client.get_item_raw = Mock(return_value={'key': 'TEST123', 'data': {}})
    client.get_items_needing_sync = Mock(return_value=[])
    client.get_stored_attachments = Mock(return_value=[])
    client.get_zotfile_symlinks = Mock(return_value=[])
    client.update_item_url = Mock(return_value=True)
    client.invalidate_caches = Mock()
    client.last_library_version = 100
    client._rate_limit = Mock()
    client._safe_request = Mock()
    return client


@pytest.fixture
def mock_devonthink_interface():
    """Mock DEVONthinkInterface without AppleScript."""
    dt = Mock()
    dt.copy_file_to_inbox = Mock(return_value=True)
    dt.find_item_by_filename_after_wait_async = AsyncMock(
        return_value="MOCK-UUID-12345"
    )
    dt.update_item_metadata = Mock(return_value=True)
    return dt


@pytest.fixture
def mock_extraction_success():
    """Mock successful extraction result."""
    from article_extraction import ExtractionResult
    return ExtractionResult(
        title="Test Article",
        authors=["John Smith"],
        publish_date="2024-01-15",
        markdown="# Test Article\n\nContent here.",
        paragraphs=["Paragraph 1", "Paragraph 2", "Paragraph 3"],
        quality_score=0.85,
        engine="standard",
        metadata={"title": "Test Article", "authors": ["John Smith"]}
    )


@pytest.fixture
def mock_extraction_low_quality():
    """Mock extraction with low quality score."""
    from article_extraction import ExtractionResult
    return ExtractionResult(
        title="Minimal",
        authors=[],
        publish_date=None,
        markdown="# Minimal\n\nShort content.",
        paragraphs=["Short content."],
        quality_score=0.05,  # Below threshold
        engine="standard",
        metadata={}
    )


@pytest.fixture
def temp_extraction_dir(tmp_path, monkeypatch):
    """Temporary directory for extraction files with env config."""
    extraction_dir = tmp_path / "extractions"
    extraction_dir.mkdir()
    monkeypatch.setenv('TMP_DIR', str(extraction_dir))
    return extraction_dir


@pytest.fixture
def mock_env_config(monkeypatch):
    """Set test-safe environment configuration."""
    monkeypatch.setenv('ZOTERO_API_KEY', 'fake-api-key-for-testing')
    monkeypatch.setenv('ZOTERO_USER_ID', 'fake-user-id')
    monkeypatch.setenv('EXTRACTION_TIMEOUT', '120')
    monkeypatch.setenv('ENABLE_RSS_FALLBACK', 'true')
    monkeypatch.setenv('ENABLE_PLAYWRIGHT', 'false')
    monkeypatch.setenv('ENABLE_WAYBACK', 'true')
    monkeypatch.setenv('DEBUG_MODE', 'false')


@pytest.fixture
def sample_zotero_response_success():
    """Sample successful Zotero API response."""
    return {
        'successful': {
            '0': {
                'key': 'ABC123XYZ',
                'version': 1234,
                'library': {'type': 'user', 'id': 12345}
            }
        },
        'unchanged': {},
        'failed': {}
    }


@pytest.fixture
def sample_zotero_item_metadata():
    """Sample Zotero item with full metadata."""
    return {
        'key': 'ABC123XYZ',
        'version': 1234,
        'data': {
            'key': 'ABC123XYZ',
            'version': 1234,
            'itemType': 'journalArticle',
            'title': 'Machine Learning in Modern Healthcare',
            'creators': [
                {'creatorType': 'author', 'firstName': 'John', 'lastName': 'Smith'},
                {'creatorType': 'author', 'firstName': 'Jane', 'lastName': 'Doe'}
            ],
            'date': '2024-01-15',
            'publicationTitle': 'Nature Medicine',
            'url': 'https://example.com/article',
            'abstractNote': 'This article discusses...',
        }
    }
