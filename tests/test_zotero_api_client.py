"""
Tests for the shared ZoteroAPIClient module.
Verifies API JSON → dataclass conversion, pagination, filtering, and update logic.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    """Create a ZoteroAPIClient with mocked rate limiting."""
    from zotero_api_client import ZoteroAPIClient
    client = ZoteroAPIClient(
        api_key='fake-key',
        user_id='12345',
        api_base='https://api.zotero.org',
        rate_limit_delay=0,  # No delays in tests
    )
    client._rate_limit = Mock()  # Skip rate limiting
    return client


@pytest.fixture
def sample_api_item():
    """Sample Zotero API item response."""
    return {
        'key': 'ABC12345',
        'version': 42,
        'data': {
            'key': 'ABC12345',
            'version': 42,
            'itemType': 'journalArticle',
            'title': 'Machine Learning in Modern Healthcare',
            'creators': [
                {'creatorType': 'author', 'firstName': 'John', 'lastName': 'Smith'},
                {'creatorType': 'author', 'firstName': 'Jane', 'lastName': 'Doe'},
            ],
            'date': '2024-01-15',
            'publicationTitle': 'Nature Medicine',
            'DOI': '10.1234/test',
            'url': 'https://example.com/article',
            'abstractNote': 'This article discusses ML in healthcare.',
            'tags': [{'tag': 'machine-learning'}, {'tag': 'healthcare'}],
            'collections': ['COL1KEY8'],
            'dateAdded': '2024-01-10T12:00:00Z',
            'dateModified': '2024-01-15T14:30:00Z',
        },
    }


@pytest.fixture
def sample_api_attachment_imported():
    """Sample imported_file attachment from API."""
    return {
        'key': 'ATT12345',
        'version': 10,
        'data': {
            'key': 'ATT12345',
            'version': 10,
            'itemType': 'attachment',
            'linkMode': 'imported_file',
            'contentType': 'application/pdf',
            'path': 'storage:ATT12345:paper.pdf',
            'md5': 'abc123hash',
            'parentItem': 'ABC12345',
        },
    }


@pytest.fixture
def sample_api_attachment_linked():
    """Sample linked_file attachment from API."""
    return {
        'key': 'LNK12345',
        'version': 5,
        'data': {
            'key': 'LNK12345',
            'version': 5,
            'itemType': 'attachment',
            'linkMode': 'linked_file',
            'contentType': 'application/pdf',
            'path': '/Users/test/ZotFile Import/paper.pdf',
            'md5': None,
            'parentItem': 'ABC12345',
        },
    }


@pytest.fixture
def sample_api_attachment_url():
    """Sample linked_url attachment from API."""
    return {
        'key': 'URL12345',
        'version': 3,
        'data': {
            'key': 'URL12345',
            'version': 3,
            'itemType': 'attachment',
            'linkMode': 'linked_url',
            'contentType': 'text/html',
            'path': '',
            'md5': None,
            'parentItem': 'ABC12345',
        },
    }


# ── Conversion tests ─────────────────────────────────────────────

class TestAPIItemConversion:

    def test_converts_item_to_zotero_item(self, api_client, sample_api_item):
        """API JSON → ZoteroItem with all fields populated."""
        # Mock collection name lookup
        api_client._collection_name_cache = {'COL1KEY8': 'My Collection'}

        item = api_client._api_item_to_zotero_item(sample_api_item)

        assert item.key == 'ABC12345'
        assert item.title == 'Machine Learning in Modern Healthcare'
        assert item.item_type == 'journalArticle'
        assert item.year == 2024
        assert item.doi == '10.1234/test'
        assert item.publication == 'Nature Medicine'
        assert item.url == 'https://example.com/article'
        assert item.abstract == 'This article discusses ML in healthcare.'
        assert len(item.creators) == 2
        assert item.creators[0]['lastName'] == 'Smith'
        assert item.tags == ['machine-learning', 'healthcare']
        assert item.collections == ['My Collection']
        assert item.version == 42

    def test_parses_year_from_various_date_formats(self, api_client):
        """Year extraction works with different date formats."""
        api_client._collection_name_cache = {}

        for date_str, expected_year in [
            ('2024-01-15', 2024),
            ('January 2023', 2023),
            ('2019', 2019),
            ('no year here', None),
            ('', None),
            (None, None),
        ]:
            api_data = {'data': {
                'key': 'X', 'itemType': 'book', 'title': 'T',
                'date': date_str, 'creators': [], 'tags': [],
                'collections': [], 'dateAdded': '', 'dateModified': '',
            }}
            item = api_client._api_item_to_zotero_item(api_data)
            assert item.year == expected_year, f"Failed for date: {date_str}"

    def test_converts_imported_file_attachment(self, api_client, sample_api_attachment_imported):
        """imported_file → linkMode 0."""
        att = api_client._api_item_to_zotero_attachment(sample_api_attachment_imported)

        assert att.key == 'ATT12345'
        assert att.parent_key == 'ABC12345'
        assert att.link_mode == 0
        assert att.content_type == 'application/pdf'
        assert att.path == 'storage:ATT12345:paper.pdf'
        assert att.storage_hash == 'abc123hash'
        assert att.version == 10

    def test_converts_linked_file_attachment(self, api_client, sample_api_attachment_linked):
        """linked_file → linkMode 2."""
        att = api_client._api_item_to_zotero_attachment(sample_api_attachment_linked)

        assert att.key == 'LNK12345'
        assert att.link_mode == 2
        assert att.parent_key == 'ABC12345'

    def test_converts_linked_url_attachment(self, api_client, sample_api_attachment_url):
        """linked_url → linkMode 3."""
        att = api_client._api_item_to_zotero_attachment(sample_api_attachment_url)

        assert att.key == 'URL12345'
        assert att.link_mode == 3

    def test_unknown_linkmode_returns_negative_one(self, api_client):
        """Unknown linkMode string maps to -1."""
        api_data = {'data': {
            'key': 'X', 'linkMode': 'unknown_mode',
            'contentType': '', 'path': '', 'md5': None, 'parentItem': None,
        }}
        att = api_client._api_item_to_zotero_attachment(api_data)
        assert att.link_mode == -1


# ── Filtering tests ──────────────────────────────────────────────

class TestAttachmentFiltering:

    def test_get_stored_attachments_filters_correctly(
        self, api_client,
        sample_api_attachment_imported, sample_api_attachment_linked, sample_api_attachment_url,
    ):
        """Only imported_file/imported_url with storage: path returned."""
        api_client._attachment_cache = [
            sample_api_attachment_imported,
            sample_api_attachment_linked,
            sample_api_attachment_url,
        ]

        result = api_client.get_stored_attachments()

        assert len(result) == 1
        assert result[0].key == 'ATT12345'

    def test_get_zotfile_symlinks_filters_correctly(
        self, api_client,
        sample_api_attachment_imported, sample_api_attachment_linked, sample_api_attachment_url,
    ):
        """Only linked_file attachments returned."""
        api_client._attachment_cache = [
            sample_api_attachment_imported,
            sample_api_attachment_linked,
            sample_api_attachment_url,
        ]

        result = api_client.get_zotfile_symlinks()

        assert len(result) == 1
        assert result[0].key == 'LNK12345'

    def test_get_items_needing_sync_excludes_notes_and_attachments(self, api_client):
        """Notes and attachments are filtered out."""
        api_client._collection_name_cache = {}

        items = [
            {'data': {'key': 'A', 'itemType': 'journalArticle', 'title': 'Paper',
                      'url': '', 'creators': [], 'tags': [], 'collections': [],
                      'dateAdded': '', 'dateModified': ''}},
            {'data': {'key': 'B', 'itemType': 'note', 'title': 'Note',
                      'url': '', 'creators': [], 'tags': [], 'collections': [],
                      'dateAdded': '', 'dateModified': ''}},
            {'data': {'key': 'C', 'itemType': 'attachment', 'title': 'Attach',
                      'url': '', 'creators': [], 'tags': [], 'collections': [],
                      'dateAdded': '', 'dateModified': ''}},
        ]

        api_client._get_all_items_paginated = Mock(return_value=items)
        result = api_client.get_items_needing_sync()

        assert len(result) == 1
        assert result[0].key == 'A'

    def test_get_items_needing_sync_excludes_already_linked(self, api_client):
        """Items with x-devonthink-item:// URLs are excluded."""
        api_client._collection_name_cache = {}

        items = [
            {'data': {'key': 'A', 'itemType': 'book', 'title': 'Unlinked',
                      'url': 'https://example.com', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
            {'data': {'key': 'B', 'itemType': 'book', 'title': 'Linked',
                      'url': 'x-devonthink-item://UUID-HERE', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
        ]

        api_client._get_all_items_paginated = Mock(return_value=items)
        result = api_client.get_items_needing_sync()

        assert len(result) == 1
        assert result[0].key == 'A'


# ── Pagination tests ─────────────────────────────────────────────

class TestPagination:

    def test_single_page(self, api_client):
        """Single page of results fetched correctly."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{'data': {'key': 'A'}}, {'data': {'key': 'B'}}]
        mock_response.headers = {'Total-Results': '2', 'Last-Modified-Version': '50'}

        api_client._safe_request = Mock(return_value=mock_response)

        result = api_client._get_all_items_paginated({})

        assert len(result) == 2
        assert api_client.last_library_version == 50

    def test_multiple_pages(self, api_client):
        """Pagination fetches all pages."""
        page1 = Mock()
        page1.status_code = 200
        page1.json.return_value = [{'data': {'key': f'K{i}'}} for i in range(100)]
        page1.headers = {'Total-Results': '150', 'Last-Modified-Version': '60'}

        page2 = Mock()
        page2.status_code = 200
        page2.json.return_value = [{'data': {'key': f'K{i}'}} for i in range(100, 150)]
        page2.headers = {'Total-Results': '150', 'Last-Modified-Version': '60'}

        api_client._safe_request = Mock(side_effect=[page1, page2])

        result = api_client._get_all_items_paginated({})

        assert len(result) == 150
        assert api_client._safe_request.call_count == 2


# ── Update URL tests ─────────────────────────────────────────────

class TestUpdateItemUrl:

    def test_dry_run_skips_api_call(self, api_client):
        """Dry run logs but does not call API."""
        result = api_client.update_item_url('KEY123', 'uuid-abc', dry_run=True)

        assert result is True
        api_client._rate_limit.assert_not_called()

    def test_successful_update(self, api_client):
        """PATCH succeeds with correct version header."""
        get_response = Mock()
        get_response.status_code = 200
        get_response.json.return_value = {'version': 42}

        patch_response = Mock()
        patch_response.status_code = 204

        api_client._safe_request = Mock(side_effect=[get_response, patch_response])

        result = api_client.update_item_url('KEY123', 'uuid-abc')

        assert result is True
        # Verify PATCH was called with correct version header
        patch_call = api_client._safe_request.call_args_list[1]
        assert patch_call[1]['headers']['If-Unmodified-Since-Version'] == '42'
        assert patch_call[1]['json'] == {'url': 'x-devonthink-item://uuid-abc'}

    def test_version_conflict_returns_false(self, api_client):
        """412 Precondition Failed returns False."""
        get_response = Mock()
        get_response.status_code = 200
        get_response.json.return_value = {'version': 42}

        patch_response = Mock()
        patch_response.status_code = 412

        api_client._safe_request = Mock(side_effect=[get_response, patch_response])

        result = api_client.update_item_url('KEY123', 'uuid-abc')

        assert result is False

    def test_fetch_failure_returns_false(self, api_client):
        """If initial GET fails, returns False without patching."""
        get_response = Mock()
        get_response.status_code = 404

        api_client._safe_request = Mock(return_value=get_response)

        result = api_client.update_item_url('KEY123', 'uuid-abc')

        assert result is False
        assert api_client._safe_request.call_count == 1  # Only GET, no PATCH


# ── Cache tests ──────────────────────────────────────────────────

class TestCaching:

    def test_attachment_cache_reused(self, api_client, sample_api_attachment_imported):
        """Second call to get_stored_attachments uses cache."""
        api_client._attachment_cache = [sample_api_attachment_imported]

        result1 = api_client.get_stored_attachments()
        result2 = api_client.get_stored_attachments()

        assert len(result1) == 1
        assert len(result2) == 1

    def test_invalidate_clears_cache(self, api_client):
        """invalidate_caches clears attachment and collection caches."""
        api_client._attachment_cache = [{'data': {}}]
        api_client._collection_name_cache = {'A': 'B'}

        api_client.invalidate_caches()

        assert api_client._attachment_cache is None
        assert api_client._collection_name_cache is None


# ── Imported URL Attachments tests ────────────────────────────────────

class TestGetImportedUrlAttachments:
    """Test get_imported_url_attachments() filters only linkMode=1 items."""

    @pytest.fixture
    def sample_api_attachment_imported_url(self):
        """Sample imported_url attachment (linkMode=1)."""
        return {
            'key': 'IMPURL123',
            'version': 8,
            'data': {
                'key': 'IMPURL123',
                'version': 8,
                'itemType': 'attachment',
                'linkMode': 'imported_url',
                'contentType': 'text/html',
                'path': 'storage:IMPURL123:snapshot.html',
                'md5': 'snapshot_hash',
                'parentItem': 'ABC12345',
            },
        }

    def test_returns_only_imported_url_items(
        self, api_client,
        sample_api_attachment_imported,
        sample_api_attachment_linked,
        sample_api_attachment_url,
        sample_api_attachment_imported_url,
    ):
        """Returns only imported_url (linkMode=1) attachments."""
        api_client._attachment_cache = [
            sample_api_attachment_imported,      # linkMode=0 (imported_file)
            sample_api_attachment_imported_url,  # linkMode=1 (imported_url)
            sample_api_attachment_linked,        # linkMode=2 (linked_file)
            sample_api_attachment_url,           # linkMode=3 (linked_url)
        ]

        result = api_client.get_imported_url_attachments()

        assert len(result) == 1
        assert result[0].key == 'IMPURL123'
        assert result[0].link_mode == 1

    def test_excludes_imported_file_attachments(self, api_client, sample_api_attachment_imported):
        """Excludes imported_file (linkMode=0) items."""
        api_client._attachment_cache = [sample_api_attachment_imported]

        result = api_client.get_imported_url_attachments()

        assert len(result) == 0

    def test_excludes_linked_file_attachments(self, api_client, sample_api_attachment_linked):
        """Excludes linked_file (linkMode=2) items."""
        api_client._attachment_cache = [sample_api_attachment_linked]

        result = api_client.get_imported_url_attachments()

        assert len(result) == 0

    def test_excludes_linked_url_attachments(self, api_client, sample_api_attachment_url):
        """Excludes linked_url (linkMode=3) items."""
        api_client._attachment_cache = [sample_api_attachment_url]

        result = api_client.get_imported_url_attachments()

        assert len(result) == 0

    def test_returns_empty_list_when_none_found(self, api_client):
        """Returns empty list when no imported_url attachments exist."""
        api_client._attachment_cache = []

        result = api_client.get_imported_url_attachments()

        assert result == []

    def test_handles_multiple_imported_url_items(self, api_client):
        """Returns all imported_url attachments when multiple exist."""
        items = [
            {
                'key': f'IMPURL{i}',
                'version': i,
                'data': {
                    'key': f'IMPURL{i}',
                    'version': i,
                    'itemType': 'attachment',
                    'linkMode': 'imported_url',
                    'contentType': 'text/html',
                    'path': f'storage:IMPURL{i}:snapshot.html',
                    'md5': f'hash{i}',
                    'parentItem': 'ABC12345',
                },
            }
            for i in range(5)
        ]
        api_client._attachment_cache = items

        result = api_client.get_imported_url_attachments()

        assert len(result) == 5
        assert all(att.link_mode == 1 for att in result)


# ── Stored Attachments (Narrow) tests ──────────────────────────────────

class TestGetStoredAttachmentsNarrow:
    """Test that get_stored_attachments() returns only imported_file items."""

    def test_returns_only_imported_file_items(
        self, api_client,
        sample_api_attachment_imported,
        sample_api_attachment_linked,
        sample_api_attachment_url,
    ):
        """Returns only imported_file (linkMode=0) attachments."""
        api_client._attachment_cache = [
            sample_api_attachment_imported,  # linkMode=0
            sample_api_attachment_linked,    # linkMode=2
            sample_api_attachment_url,       # linkMode=3
        ]

        result = api_client.get_stored_attachments()

        assert len(result) == 1
        assert result[0].key == 'ATT12345'
        assert result[0].link_mode == 0

    def test_excludes_imported_url_items(self, api_client):
        """Excludes imported_url (linkMode=1) items."""
        imported_url_att = {
            'key': 'IMPURL123',
            'version': 8,
            'data': {
                'key': 'IMPURL123',
                'version': 8,
                'itemType': 'attachment',
                'linkMode': 'imported_url',
                'contentType': 'text/html',
                'path': 'storage:IMPURL123:snapshot.html',
                'md5': 'snapshot_hash',
                'parentItem': 'ABC12345',
            },
        }
        api_client._attachment_cache = [imported_url_att]

        result = api_client.get_stored_attachments()

        assert len(result) == 0

    def test_handles_filename_field_without_path(self, api_client):
        """Returns imported_file items with filename field but no path field."""
        att_with_filename = {
            'key': 'ATT_FILENAME',
            'version': 15,
            'data': {
                'key': 'ATT_FILENAME',
                'version': 15,
                'itemType': 'attachment',
                'linkMode': 'imported_file',
                'contentType': 'application/pdf',
                'path': '',  # Empty path
                'filename': 'paper.pdf',  # Has filename instead
                'md5': 'file_hash',
                'parentItem': 'ABC12345',
            },
        }
        api_client._attachment_cache = [att_with_filename]

        result = api_client.get_stored_attachments()

        assert len(result) == 1
        assert result[0].key == 'ATT_FILENAME'
        assert result[0].filename == 'paper.pdf'

    def test_handles_storage_path_with_filename(self, api_client, sample_api_attachment_imported):
        """Returns imported_file items with storage: path (ignores filename)."""
        api_client._attachment_cache = [sample_api_attachment_imported]

        result = api_client.get_stored_attachments()

        assert len(result) == 1
        assert result[0].path == 'storage:ATT12345:paper.pdf'

    def test_excludes_imported_file_with_no_path_or_filename(self, api_client):
        """Excludes imported_file items with neither path nor filename."""
        att_empty = {
            'key': 'ATT_EMPTY',
            'version': 5,
            'data': {
                'key': 'ATT_EMPTY',
                'version': 5,
                'itemType': 'attachment',
                'linkMode': 'imported_file',
                'contentType': 'application/pdf',
                'path': '',
                'filename': '',  # Empty filename
                'md5': None,
                'parentItem': 'ABC12345',
            },
        }
        api_client._attachment_cache = [att_empty]

        result = api_client.get_stored_attachments()

        assert len(result) == 0

    def test_returns_empty_list_when_none_found(self, api_client):
        """Returns empty list when no imported_file attachments exist."""
        api_client._attachment_cache = []

        result = api_client.get_stored_attachments()

        assert result == []


# ── Batch Delete tests ────────────────────────────────────────────────

class TestBatchDelete:
    """Test delete_items_batch() batch deletion with version handling."""

    def test_dry_run_returns_count_without_api_call(self, api_client):
        """Dry run returns count of items to delete without making API call."""
        item_keys = ['KEY1', 'KEY2', 'KEY3']

        result = api_client.delete_items_batch(item_keys, library_version=100, dry_run=True)

        assert result['deleted'] == 0
        assert result['would_delete'] == 3
        assert result['failed'] == []
        assert result['version_conflict'] is False
        api_client._rate_limit.assert_not_called()

    def test_successful_batch_delete_under_50_items(self, api_client):
        """Successfully deletes batch of <50 items with 204 response."""
        item_keys = ['KEY1', 'KEY2', 'KEY3']
        
        response = Mock()
        response.status_code = 204
        response.headers = {'Last-Modified-Version': '105'}
        
        api_client._safe_request = Mock(return_value=response)

        result = api_client.delete_items_batch(item_keys, library_version=100)

        assert result['deleted'] == 3
        assert result['failed'] == []
        assert result['version_conflict'] is False
        assert api_client.last_library_version == 105
        api_client._safe_request.assert_called_once()

    def test_multiple_batches_for_more_than_50_items(self, api_client):
        """Splits deletion into multiple batches for >50 items."""
        item_keys = [f'KEY{i}' for i in range(125)]  # 125 items = 3 batches
        
        response1 = Mock()
        response1.status_code = 204
        response1.headers = {'Last-Modified-Version': '101'}
        
        response2 = Mock()
        response2.status_code = 204
        response2.headers = {'Last-Modified-Version': '102'}
        
        response3 = Mock()
        response3.status_code = 204
        response3.headers = {'Last-Modified-Version': '103'}
        
        api_client._safe_request = Mock(side_effect=[response1, response2, response3])

        result = api_client.delete_items_batch(item_keys, library_version=100)

        assert result['deleted'] == 125
        assert result['failed'] == []
        assert result['version_conflict'] is False
        assert api_client.last_library_version == 103
        assert api_client._safe_request.call_count == 3

    def test_version_conflict_stops_processing(self, api_client):
        """412 version conflict stops processing and marks failed items."""
        item_keys = [f'KEY{i}' for i in range(75)]  # 75 items = 2 batches
        
        response1 = Mock()
        response1.status_code = 204
        response1.headers = {'Last-Modified-Version': '101'}
        
        response2 = Mock()  # Second batch gets 412
        response2.status_code = 412
        
        api_client._safe_request = Mock(side_effect=[response1, response2])

        result = api_client.delete_items_batch(item_keys, library_version=100)

        assert result['deleted'] == 50  # First batch succeeded
        assert len(result['failed']) == 25  # Second batch failed
        assert result['version_conflict'] is True
        assert api_client._safe_request.call_count == 2

    def test_empty_list_returns_zero_results(self, api_client):
        """Empty item list returns zero deleted items."""
        api_client._safe_request = Mock()
        
        result = api_client.delete_items_batch([], library_version=100)

        assert result['deleted'] == 0
        assert result['failed'] == []
        assert result['version_conflict'] is False
        api_client._safe_request.assert_not_called()

    def test_exactly_50_items_single_batch(self, api_client):
        """Exactly 50 items processed in single batch."""
        item_keys = [f'KEY{i}' for i in range(50)]
        
        response = Mock()
        response.status_code = 204
        response.headers = {'Last-Modified-Version': '101'}
        
        api_client._safe_request = Mock(return_value=response)

        result = api_client.delete_items_batch(item_keys, library_version=100)

        assert result['deleted'] == 50
        assert api_client._safe_request.call_count == 1

    def test_api_error_marks_batch_as_failed(self, api_client):
        """API error (non-204, non-412) marks batch items as failed."""
        item_keys = ['KEY1', 'KEY2', 'KEY3']
        
        response = Mock()
        response.status_code = 500
        
        api_client._safe_request = Mock(return_value=response)

        result = api_client.delete_items_batch(item_keys, library_version=100)

        assert result['deleted'] == 0
        assert result['failed'] == item_keys
        assert result['version_conflict'] is False

    def test_none_response_marks_batch_as_failed(self, api_client):
        """None response (network failure) marks batch as failed."""
        item_keys = ['KEY1', 'KEY2']
        
        api_client._safe_request = Mock(return_value=None)

        result = api_client.delete_items_batch(item_keys, library_version=100)

        assert result['deleted'] == 0
        assert result['failed'] == item_keys
        assert result['version_conflict'] is False


# ── Delete Attachment tests ───────────────────────────────────────────

class TestDeleteAttachmentFixed:
    """Test delete_attachment() returns correct status for each HTTP code."""

    def test_returns_true_on_204_success(self, api_client):
        """Returns True when attachment deleted (204 No Content)."""
        response = Mock()
        response.status_code = 204
        
        api_client._safe_request = Mock(return_value=response)

        result = api_client.delete_attachment('ATT123', version=42)

        assert result is True
        api_client._safe_request.assert_called_once()

    def test_returns_false_on_412_version_conflict(self, api_client):
        """Returns False on version conflict (412 Precondition Failed)."""
        response = Mock()
        response.status_code = 412
        
        api_client._safe_request = Mock(return_value=response)

        result = api_client.delete_attachment('ATT123', version=42)

        assert result is False

    def test_returns_false_on_404_not_found(self, api_client):
        """Returns False when attachment not found (404)."""
        response = Mock()
        response.status_code = 404
        
        api_client._safe_request = Mock(return_value=response)

        result = api_client.delete_attachment('ATT_NOTFOUND', version=42)

        assert result is False

    def test_returns_false_on_none_response_network_failure(self, api_client):
        """Returns False on None response (network failure)."""
        api_client._safe_request = Mock(return_value=None)

        result = api_client.delete_attachment('ATT123', version=42)

        assert result is False

    def test_sends_correct_version_header(self, api_client):
        """Sends correct If-Unmodified-Since-Version header."""
        response = Mock()
        response.status_code = 204
        
        api_client._safe_request = Mock(return_value=response)

        api_client.delete_attachment('ATT123', version=99)

        call_args = api_client._safe_request.call_args
        headers = call_args[1]['headers']
        assert headers['If-Unmodified-Since-Version'] == '99'

    def test_constructs_correct_url(self, api_client):
        """Constructs correct deletion URL."""
        response = Mock()
        response.status_code = 204
        
        api_client._safe_request = Mock(return_value=response)

        api_client.delete_attachment('ATT_KEY_123', version=42)

        call_args = api_client._safe_request.call_args
        url = call_args[0][1]
        assert 'ATT_KEY_123' in url
        assert '/items/ATT_KEY_123' in url

    def test_returns_false_on_500_server_error(self, api_client):
        """Returns False on server error (500)."""
        response = Mock()
        response.status_code = 500
        
        api_client._safe_request = Mock(return_value=response)

        result = api_client.delete_attachment('ATT123', version=42)

        assert result is False


# ── Get Items Needing Sync tests ──────────────────────────────────────

class TestGetItemsNeedingSync:
    """Test get_items_needing_sync() skips processed items."""

    def test_skips_items_in_processed_set(self, api_client):
        """Skips items that are in processed_items set."""
        api_client._collection_name_cache = {}

        items = [
            {'data': {'key': 'A', 'itemType': 'book', 'title': 'Book A',
                      'url': 'https://a.com', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
            {'data': {'key': 'B', 'itemType': 'book', 'title': 'Book B',
                      'url': 'https://b.com', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
            {'data': {'key': 'C', 'itemType': 'book', 'title': 'Book C',
                      'url': 'https://c.com', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
        ]

        api_client._get_all_items_paginated = Mock(return_value=items)
        result = api_client.get_items_needing_sync(processed_items=['B', 'C'])

        assert len(result) == 1
        assert result[0].key == 'A'

    def test_processes_empty_processed_items(self, api_client):
        """Returns all non-attachment items when processed_items is empty."""
        api_client._collection_name_cache = {}

        items = [
            {'data': {'key': 'A', 'itemType': 'book', 'title': 'Book A',
                      'url': 'https://a.com', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
            {'data': {'key': 'B', 'itemType': 'book', 'title': 'Book B',
                      'url': 'https://b.com', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
        ]

        api_client._get_all_items_paginated = Mock(return_value=items)
        result = api_client.get_items_needing_sync(processed_items=[])

        assert len(result) == 2

    def test_processes_none_processed_items(self, api_client):
        """Treats None processed_items as empty list."""
        api_client._collection_name_cache = {}

        items = [
            {'data': {'key': 'A', 'itemType': 'book', 'title': 'Book A',
                      'url': 'https://a.com', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
        ]

        api_client._get_all_items_paginated = Mock(return_value=items)
        result = api_client.get_items_needing_sync(processed_items=None)

        assert len(result) == 1
        assert result[0].key == 'A'

    def test_still_skips_legacy_devonthink_urls(self, api_client):
        """Still skips items with legacy x-devonthink-item:// parent URLs."""
        api_client._collection_name_cache = {}

        items = [
            {'data': {'key': 'A', 'itemType': 'book', 'title': 'Unlinked',
                      'url': 'https://example.com', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
            {'data': {'key': 'B', 'itemType': 'book', 'title': 'Legacy Linked',
                      'url': 'x-devonthink-item://UUID-OLD', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
            {'data': {'key': 'C', 'itemType': 'book', 'title': 'Regular URL',
                      'url': 'https://another.com', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
        ]

        api_client._get_all_items_paginated = Mock(return_value=items)
        result = api_client.get_items_needing_sync(processed_items=['X'])  # X is not in items

        assert len(result) == 2
        assert result[0].key == 'A'
        assert result[1].key == 'C'

    def test_combined_processed_and_legacy_filtering(self, api_client):
        """Combines processed_items filtering and legacy URL skipping."""
        api_client._collection_name_cache = {}

        items = [
            {'data': {'key': 'A', 'itemType': 'book', 'title': 'Book A',
                      'url': 'https://a.com', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
            {'data': {'key': 'B', 'itemType': 'book', 'title': 'Book B',
                      'url': 'x-devonthink-item://UUID', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
            {'data': {'key': 'C', 'itemType': 'book', 'title': 'Book C',
                      'url': 'https://c.com', 'creators': [], 'tags': [],
                      'collections': [], 'dateAdded': '', 'dateModified': ''}},
        ]

        api_client._get_all_items_paginated = Mock(return_value=items)
        result = api_client.get_items_needing_sync(processed_items=['C'])

        assert len(result) == 1
        assert result[0].key == 'A'

    def test_since_version_passed_to_pagination(self, api_client):
        """Passes since_version parameter to _get_all_items_paginated."""
        api_client._collection_name_cache = {}
        api_client._get_all_items_paginated = Mock(return_value=[])

        api_client.get_items_needing_sync(since_version=50, processed_items=[])

        call_args = api_client._get_all_items_paginated.call_args
        assert call_args[0][0]['since'] == 50
