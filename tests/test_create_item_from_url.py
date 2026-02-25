"""
Unit tests for ZoteroAPIClient.create_item_from_url() method.

Tests the core Zotero API integration for creating items from URLs,
including rate limiting, error handling, translation server integration,
and metadata inference.
"""

import pytest
from unittest.mock import Mock, patch
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from zotero_api_client import ZoteroAPIClient
from exceptions import ZoteroAPIError


class TestCreateItemFromURL:
    """Test suite for create_item_from_url() method."""

    @patch.object(ZoteroAPIClient, 'translate_url', return_value=None)
    @patch.object(ZoteroAPIClient, '_safe_request')
    @patch.object(ZoteroAPIClient, 'get_item_raw')
    @patch.object(ZoteroAPIClient, '_rate_limit')
    def test_create_item_from_url_success(
        self, mock_rate_limit, mock_get_item_raw, mock_safe_request,
        mock_translate
    ):
        """Test successful Zotero item creation from URL (no translation)."""
        # Arrange
        mock_safe_request.return_value = Mock(
            status_code=200,
            json=lambda: {'successful': {'0': {'key': 'ABC123'}}}
        )
        mock_get_item_raw.return_value = {
            'key': 'ABC123',
            'data': {
                'title': 'Test Article',
                'itemType': 'journalArticle',
                'url': 'https://example.com/article'
            }
        }

        client = ZoteroAPIClient('fake-key', 'fake-id')

        # Act
        result = client.create_item_from_url('https://example.com/article')

        # Assert
        assert result is not None
        assert result['key'] == 'ABC123'
        assert 'data' in result
        assert result['data']['title'] == 'Test Article'
        assert result['_translated_metadata'] is None
        mock_rate_limit.assert_called_once()
        mock_get_item_raw.assert_called_once_with('ABC123')

    @patch.object(ZoteroAPIClient, 'translate_url')
    @patch.object(ZoteroAPIClient, '_safe_request')
    @patch.object(ZoteroAPIClient, 'get_item_raw')
    @patch.object(ZoteroAPIClient, '_rate_limit')
    def test_create_item_from_url_with_translation(
        self, mock_rate_limit, mock_get_item_raw, mock_safe_request,
        mock_translate
    ):
        """Test item creation with translation server metadata."""
        # Arrange - translation server returns rich metadata
        translated = {
            'key': '3ZYNTNIK',
            'version': 0,
            'itemType': 'blogPost',
            'creators': [{'firstName': 'Jonathan', 'lastName': 'Merritt', 'creatorType': 'author'}],
            'title': 'The Spirituality of Snoopy',
            'blogTitle': 'The Atlantic',
            'date': '2016-04-25',
            'url': 'https://example.com/article',
            'abstractNote': 'How the faith of Charles Schulz shaped his work.',
            'language': 'en',
        }
        mock_translate.return_value = translated

        mock_safe_request.return_value = Mock(
            status_code=200,
            json=lambda: {'successful': {'0': {'key': 'ABC123'}}}
        )
        mock_get_item_raw.return_value = {
            'key': 'ABC123',
            'data': {
                'title': 'The Spirituality of Snoopy',
                'itemType': 'blogPost',
            }
        }

        client = ZoteroAPIClient('fake-key', 'fake-id')

        # Act
        result = client.create_item_from_url('https://example.com/article')

        # Assert
        assert result is not None
        assert result['_translated_metadata'] is not None
        assert result['_translated_metadata']['itemType'] == 'blogPost'
        assert result['_translated_metadata']['title'] == 'The Spirituality of Snoopy'

        # Verify the POST payload uses translated data (not bare journalArticle)
        call_args = mock_safe_request.call_args
        payload = call_args[1]['json']
        assert payload[0]['itemType'] == 'blogPost'
        assert payload[0]['title'] == 'The Spirituality of Snoopy'
        # key and version should be stripped
        assert 'key' not in payload[0]
        assert 'version' not in payload[0]

    @patch.object(ZoteroAPIClient, 'translate_url', return_value=None)
    @patch.object(ZoteroAPIClient, '_safe_request')
    @patch.object(ZoteroAPIClient, '_rate_limit')
    def test_create_item_from_url_returns_none_on_failure(
        self, mock_rate_limit, mock_safe_request, mock_translate
    ):
        """Test graceful failure when API returns error."""
        # Arrange
        mock_safe_request.return_value = Mock(
            status_code=500,
            json=lambda: {'error': 'Internal Server Error'}
        )

        client = ZoteroAPIClient('fake-key', 'fake-id')

        # Act
        result = client.create_item_from_url('https://example.com/article')

        # Assert
        assert result is None
        mock_rate_limit.assert_called_once()

    @patch.object(ZoteroAPIClient, 'translate_url', return_value=None)
    @patch.object(ZoteroAPIClient, '_safe_request')
    @patch.object(ZoteroAPIClient, '_rate_limit')
    def test_create_item_from_url_malformed_response(
        self, mock_rate_limit, mock_safe_request, mock_translate
    ):
        """Test handling of malformed API response."""
        # Arrange - Response missing 'successful' key
        mock_safe_request.return_value = Mock(
            status_code=200,
            json=lambda: {'data': {'key': 'ABC123'}}  # Wrong structure
        )

        client = ZoteroAPIClient('fake-key', 'fake-id')

        # Act
        result = client.create_item_from_url('https://example.com/article')

        # Assert
        assert result is None  # Should handle gracefully
        mock_rate_limit.assert_called_once()

    @patch.object(ZoteroAPIClient, 'translate_url', return_value=None)
    @patch.object(ZoteroAPIClient, '_safe_request')
    @patch.object(ZoteroAPIClient, '_rate_limit')
    def test_create_item_from_url_no_key_in_response(
        self, mock_rate_limit, mock_safe_request, mock_translate
    ):
        """Test handling of response with empty successful dict."""
        # Arrange - successful dict is empty
        mock_safe_request.return_value = Mock(
            status_code=200,
            json=lambda: {'successful': {}, 'unchanged': {}, 'failed': {}}
        )

        client = ZoteroAPIClient('fake-key', 'fake-id')

        # Act
        result = client.create_item_from_url('https://example.com/article')

        # Assert
        assert result is None
        mock_rate_limit.assert_called_once()

    @patch.object(ZoteroAPIClient, 'translate_url', return_value=None)
    @patch('time.sleep')
    @patch.object(ZoteroAPIClient, '_safe_request')
    def test_create_item_from_url_rate_limiting(
        self, mock_safe_request, mock_sleep, mock_translate
    ):
        """Test that rate limiting is called before API request."""
        # Arrange
        mock_safe_request.return_value = Mock(
            status_code=200,
            json=lambda: {'successful': {'0': {'key': 'ABC123'}}}
        )

        # Create client without mocking _rate_limit to test actual behavior
        client = ZoteroAPIClient('fake-key', 'fake-id')

        # Act
        with patch.object(client, 'get_item_raw', return_value={'key': 'ABC123', 'data': {}}):
            result = client.create_item_from_url('https://example.com/article')

        # Assert
        assert result is not None
        # Should have called time.sleep via _rate_limit
        assert mock_sleep.called

    @patch.object(ZoteroAPIClient, 'translate_url', return_value=None)
    @patch.object(ZoteroAPIClient, '_safe_request')
    @patch.object(ZoteroAPIClient, '_rate_limit')
    def test_create_item_from_url_constructs_correct_payload(
        self, mock_rate_limit, mock_safe_request, mock_translate
    ):
        """Test that API request payload is constructed correctly (fallback path)."""
        # Arrange
        mock_safe_request.return_value = Mock(
            status_code=200,
            json=lambda: {'successful': {'0': {'key': 'ABC123'}}}
        )

        client = ZoteroAPIClient('fake-key', 'fake-id')
        test_url = 'https://example.com/article'

        # Act
        with patch.object(client, 'get_item_raw', return_value={'key': 'ABC123', 'data': {}}):
            result = client.create_item_from_url(test_url)

        # Assert
        assert mock_safe_request.called
        call_args = mock_safe_request.call_args

        # Verify HTTP method and endpoint
        assert call_args[0][0] == 'POST'
        assert 'fake-id' in call_args[0][1]  # user_id in URL
        assert '/items' in call_args[0][1]

        # Verify payload structure (bare fallback since translate_url returned None)
        payload = call_args[1]['json']
        assert isinstance(payload, list)
        assert len(payload) == 1
        assert payload[0]['itemType'] == 'journalArticle'
        assert payload[0]['url'] == test_url

    @patch.object(ZoteroAPIClient, 'translate_url', return_value=None)
    @patch.object(ZoteroAPIClient, '_safe_request')
    @patch.object(ZoteroAPIClient, '_rate_limit')
    def test_create_item_from_url_fetchback_failure_returns_creation_response(
        self, mock_rate_limit, mock_safe_request, mock_translate
    ):
        """Test that item key is still returned when get_item_raw fails post-creation."""
        # Arrange - creation succeeds but fetch-back fails
        mock_safe_request.return_value = Mock(
            status_code=200,
            json=lambda: {'successful': {'0': {'key': 'ABC123', 'version': 42}}}
        )

        client = ZoteroAPIClient('fake-key', 'fake-id')

        # Act
        with patch.object(client, 'get_item_raw', return_value=None):
            result = client.create_item_from_url('https://example.com/article')

        # Assert - should still return the creation response, not None
        assert result is not None
        assert result['key'] == 'ABC123'
        assert result['_translated_metadata'] is None


class TestTranslateURL:
    """Test suite for translate_url() method."""

    @patch('zotero_api_client.requests.post')
    def test_translate_url_success(self, mock_post):
        """Test successful URL translation."""
        mock_post.return_value = Mock(
            status_code=200,
            json=lambda: [{
                'itemType': 'blogPost',
                'title': 'Test Article',
                'creators': [{'firstName': 'John', 'lastName': 'Doe', 'creatorType': 'author'}],
            }]
        )

        client = ZoteroAPIClient('fake-key', 'fake-id')
        result = client.translate_url('https://example.com/article')

        assert result is not None
        assert result['itemType'] == 'blogPost'
        assert result['title'] == 'Test Article'

    @patch('zotero_api_client.requests.post')
    def test_translate_url_timeout(self, mock_post):
        """Test graceful handling of translation server timeout."""
        import requests
        mock_post.side_effect = requests.Timeout()

        client = ZoteroAPIClient('fake-key', 'fake-id')
        result = client.translate_url('https://example.com/article')

        assert result is None

    @patch('zotero_api_client.requests.post')
    def test_translate_url_server_error(self, mock_post):
        """Test graceful handling of translation server error."""
        mock_post.return_value = Mock(status_code=500)

        client = ZoteroAPIClient('fake-key', 'fake-id')
        result = client.translate_url('https://example.com/article')

        assert result is None

    @patch('zotero_api_client.requests.post')
    def test_translate_url_empty_response(self, mock_post):
        """Test handling of empty translation result."""
        mock_post.return_value = Mock(status_code=200, json=lambda: [])

        client = ZoteroAPIClient('fake-key', 'fake-id')
        result = client.translate_url('https://example.com/article')

        assert result is None


class TestTranslateIdentifier:
    """Test suite for translate_identifier() method."""

    @patch('zotero_api_client.requests.post')
    def test_translate_identifier_doi(self, mock_post):
        """Test DOI lookup via translation server."""
        mock_post.return_value = Mock(
            status_code=200,
            json=lambda: [{
                'itemType': 'journalArticle',
                'title': 'A Study of Things',
                'DOI': '10.1234/example',
            }]
        )

        client = ZoteroAPIClient('fake-key', 'fake-id')
        result = client.translate_identifier('10.1234/example')

        assert result is not None
        assert result['DOI'] == '10.1234/example'

    @patch('zotero_api_client.requests.post')
    def test_translate_identifier_failure(self, mock_post):
        """Test graceful handling of identifier lookup failure."""
        mock_post.return_value = Mock(status_code=501)

        client = ZoteroAPIClient('fake-key', 'fake-id')
        result = client.translate_identifier('invalid-identifier')

        assert result is None


class TestCreateItemFromIdentifier:
    """Test suite for create_item_from_identifier() method."""

    @patch.object(ZoteroAPIClient, 'translate_identifier')
    @patch.object(ZoteroAPIClient, '_safe_request')
    @patch.object(ZoteroAPIClient, 'get_item_raw')
    @patch.object(ZoteroAPIClient, '_rate_limit')
    def test_create_item_from_identifier_success(
        self, mock_rate_limit, mock_get_item_raw, mock_safe_request,
        mock_translate
    ):
        """Test successful item creation from DOI."""
        translated = {
            'key': 'TEMP',
            'version': 0,
            'itemType': 'journalArticle',
            'title': 'A Study',
            'DOI': '10.1234/example',
        }
        mock_translate.return_value = translated
        mock_safe_request.return_value = Mock(
            status_code=200,
            json=lambda: {'successful': {'0': {'key': 'XYZ789'}}}
        )
        mock_get_item_raw.return_value = {
            'key': 'XYZ789',
            'data': {'title': 'A Study', 'itemType': 'journalArticle'}
        }

        client = ZoteroAPIClient('fake-key', 'fake-id')
        result = client.create_item_from_identifier('10.1234/example')

        assert result is not None
        assert result['key'] == 'XYZ789'
        assert result['_translated_metadata']['DOI'] == '10.1234/example'

    @patch.object(ZoteroAPIClient, 'translate_identifier', return_value=None)
    def test_create_item_from_identifier_translation_fails(self, mock_translate):
        """Test that identifier creation fails when translation fails (no fallback)."""
        client = ZoteroAPIClient('fake-key', 'fake-id')
        result = client.create_item_from_identifier('bad-identifier')

        assert result is None
