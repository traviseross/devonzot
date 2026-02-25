"""
Tests for DEVONzot Phase 0 deletion and helper methods.

This test module covers:
- `delete_imported_url_attachments()` — deletes linkMode=1 attachments
- `retry_pending_deletes()` — retries failed item deletions
- `_get_zotero_filename()` — extracts original filename
- `_find_or_adopt_in_devonthink()` — searches DEVONthink and renames if needed
- `_parent_has_devonthink_link()` — checks for x-devonthink-item:// children
- `_create_devonthink_child_link()` — creates linked_url DEVONthink links
- `rename_item()` on DEVONthinkInterface — renames items by UUID
- ServiceState pending_deletes initialization

Mocks all external dependencies (API, DEVONthink, filesystem).
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, call
from pathlib import Path
from dataclasses import dataclass
import sys
import json

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from devonzot_service import (
    DEVONzotService,
    DEVONthinkInterface,
    ServiceState,
    ZoteroAttachment,
    ZoteroItem,
)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_env(monkeypatch):
    """Mock environment variables for service initialization."""
    monkeypatch.setenv('ZOTERO_API_KEY', 'test-api-key-12345')
    monkeypatch.setenv('ZOTERO_USER_ID', 'test-user-id-67890')
    monkeypatch.setenv('ZOTERO_API_BASE', 'https://api.zotero.org')
    monkeypatch.setenv('API_VERSION', '3')
    monkeypatch.setenv('RATE_LIMIT_DELAY', '0.1')


@pytest.fixture
def mock_zotero_client():
    """Mock ZoteroAPIClient for service."""
    client = Mock()
    client.api_key = 'test-api-key-12345'
    client.user_id = 'test-user-id-67890'
    client.last_library_version = 100
    
    # Common return values
    client.get_imported_url_attachments = Mock(return_value=[])
    client.get_stored_attachments = Mock(return_value=[])
    client.get_zotfile_symlinks = Mock(return_value=[])
    client.get_item = Mock(return_value=None)
    client.get_item_raw = Mock(return_value=None)
    client.delete_items_batch = Mock(return_value={'deleted': 0, 'failed': []})
    client.delete_attachment = Mock(return_value=True)
    client.create_url_attachments = Mock(return_value=[])
    client._get_all_attachments_cached = Mock(return_value=[])
    
    return client


@pytest.fixture
def mock_devonthink():
    """Mock DEVONthinkInterface."""
    dt = Mock(spec=DEVONthinkInterface)
    dt.database_name = "Professional"
    dt.execute_script = Mock(return_value="SUCCESS")
    dt.is_devonthink_running = Mock(return_value=True)
    dt.copy_file_to_inbox = Mock(return_value=True)
    dt.find_item_by_filename_after_wait = Mock(return_value="test-uuid-12345")
    dt.update_item_metadata = Mock(return_value=True)
    dt.rename_item = Mock(return_value=True)
    dt._search_database_for_filename = Mock(return_value=None)
    
    return dt


@pytest.fixture
def service_with_mocks(mock_env, mock_zotero_client, mock_devonthink, tmp_path, monkeypatch):
    """Create DEVONzotService with mocked dependencies."""
    # Mock the state file path
    state_file = tmp_path / "service_state.json"
    
    with patch('devonzot_service.ZoteroAPIClient') as MockZoteroAPI, \
         patch('devonzot_service.DEVONthinkInterface') as MockDEVONthink, \
         patch('devonzot_service.STATE_FILE', state_file), \
         patch('devonzot_service.DEVONZOT_PATH', tmp_path), \
         patch('devonzot_service.ConflictDetector'):
        
        MockZoteroAPI.return_value = mock_zotero_client
        MockDEVONthink.return_value = mock_devonthink
        
        service = DEVONzotService()
        service.zotero_api = mock_zotero_client
        service.devonthink = mock_devonthink
        
        return service


@pytest.fixture
def sample_zotero_item():
    """Sample ZoteroItem for testing."""
    return ZoteroItem(
        key='ITEM123',
        title='Test Paper on Machine Learning',
        creators=[
            {'firstName': 'John', 'lastName': 'Smith', 'creatorType': 'author'},
            {'firstName': 'Jane', 'lastName': 'Doe', 'creatorType': 'author'},
        ],
        item_type='journalArticle',
        publication='Nature',
        date='2024-01-15',
        year=2024,
        doi='10.1234/test',
        url='https://example.com/article',
        abstract='A test abstract',
        tags=['machine-learning', 'test'],
        collections=['COL123'],
        date_added='2024-01-10T12:00:00Z',
        date_modified='2024-01-15T14:30:00Z',
    )


@pytest.fixture
def sample_attachment_imported_url():
    """Sample imported_url attachment (linkMode=1)."""
    return ZoteroAttachment(
        key='ATT_URL_001',
        parent_key='ITEM123',
        link_mode=1,
        content_type='text/html',
        path='storage:ATT_URL_001:snapshot.html',
        storage_hash='abc123hash',
        version=5,
        filename='snapshot.html',
        url=None,
    )


@pytest.fixture
def sample_attachment_stored():
    """Sample stored (imported_file) attachment (linkMode=0)."""
    return ZoteroAttachment(
        key='ATT_STORED_001',
        parent_key='ITEM123',
        link_mode=0,
        content_type='application/pdf',
        path='storage:ATT_STORED_001:document.pdf',
        storage_hash='pdf123hash',
        version=3,
        filename='document.pdf',
        url=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST: TestGetZoteroFilename
# ─────────────────────────────────────────────────────────────────────────────


class TestGetZoteroFilename:
    """Test _get_zotero_filename() method."""
    
    def test_returns_filename_if_present(self, service_with_mocks):
        """Returns filename field if it exists."""
        attachment = ZoteroAttachment(
            key='ATT001',
            parent_key='ITEM001',
            link_mode=1,
            content_type='text/html',
            path='storage:ATT001:file.pdf',
            storage_hash='hash',
            filename='custom_name.html',
        )
        
        result = service_with_mocks._get_zotero_filename(attachment)
        assert result == 'custom_name.html'
    
    def test_extracts_name_from_path_if_no_filename(self, service_with_mocks):
        """Extracts filename from path when filename field is None."""
        attachment = ZoteroAttachment(
            key='ATT001',
            parent_key='ITEM001',
            link_mode=2,
            content_type='application/pdf',
            path='/Users/test/ZotFile Import/my_paper.pdf',
            storage_hash='hash',
            filename=None,
        )
        
        result = service_with_mocks._get_zotero_filename(attachment)
        assert result == 'my_paper.pdf'
    
    def test_returns_none_if_both_missing(self, service_with_mocks):
        """Returns None when both filename and path are None."""
        attachment = ZoteroAttachment(
            key='ATT001',
            parent_key='ITEM001',
            link_mode=1,
            content_type='text/html',
            path=None,
            storage_hash='hash',
            filename=None,
        )
        
        result = service_with_mocks._get_zotero_filename(attachment)
        assert result is None
    
    def test_extracts_name_from_storage_path(self, service_with_mocks):
        """Extracts filename from storage: paths."""
        attachment = ZoteroAttachment(
            key='ATT001',
            parent_key='ITEM001',
            link_mode=0,
            content_type='application/pdf',
            path='storage:ATT001:paper.pdf',
            storage_hash='hash',
            filename=None,
        )
        
        result = service_with_mocks._get_zotero_filename(attachment)
        # Path.name on storage: paths returns the full string (not a real filesystem path)
        assert result == 'storage:ATT001:paper.pdf'


# ─────────────────────────────────────────────────────────────────────────────
# TEST: TestFindOrAdoptInDevonthink
# ─────────────────────────────────────────────────────────────────────────────


class TestFindOrAdoptInDevonthink:
    """Test _find_or_adopt_in_devonthink() method."""
    
    def test_returns_uuid_when_found_by_generated_name(self, service_with_mocks, mock_devonthink):
        """Returns UUID when item found by generated name in first search."""
        mock_devonthink._search_database_for_filename = Mock(side_effect=[
            'found-uuid-12345',  # First call (generated name in Global Inbox)
        ])
        
        result = service_with_mocks._find_or_adopt_in_devonthink(
            generated_name='Smith, John - Test Paper - 2024 - Journal Article',
            zotero_filename='old_snapshot.html',
            dry_run=False
        )
        
        assert result == 'found-uuid-12345'
        # Should search for generated name
        assert mock_devonthink._search_database_for_filename.call_count >= 1
    
    def test_returns_uuid_and_renames_when_found_by_zotero_name(self, service_with_mocks, mock_devonthink):
        """Returns UUID and renames when found by Zotero name (second search)."""
        # First search for generated name returns None in all databases
        # Second search for Zotero name returns UUID
        mock_devonthink._search_database_for_filename = Mock(side_effect=[
            None,  # Generated name - Global Inbox
            None,  # Generated name - Professional
            None,  # Generated name - Articles
            None,  # Generated name - Books
            None,  # Generated name - Research
            None,  # Zotero name - Global Inbox
            'zotero-found-uuid',  # Zotero name - Professional
        ])
        
        result = service_with_mocks._find_or_adopt_in_devonthink(
            generated_name='Generated - Name - 2024',
            zotero_filename='old_snapshot.pdf',
            dry_run=False
        )
        
        assert result == 'zotero-found-uuid'
        # Should have called rename_item
        mock_devonthink.rename_item.assert_called_once()
    
    def test_returns_none_when_not_found_by_either_name(self, service_with_mocks, mock_devonthink):
        """Returns None when not found by either name."""
        mock_devonthink._search_database_for_filename = Mock(return_value=None)
        
        result = service_with_mocks._find_or_adopt_in_devonthink(
            generated_name='Generated - Name - 2024',
            zotero_filename='old_name.pdf',
            dry_run=False
        )
        
        assert result is None
        mock_devonthink.rename_item.assert_not_called()
    
    def test_skips_zotero_name_search_if_same_as_generated(self, service_with_mocks, mock_devonthink):
        """Skips Zotero name search if same as generated name."""
        mock_devonthink._search_database_for_filename = Mock(return_value=None)
        
        result = service_with_mocks._find_or_adopt_in_devonthink(
            generated_name='Same - Name',
            zotero_filename='Same - Name',
            dry_run=False
        )
        
        assert result is None
        # Should only search for generated name, not zotero name
        call_count = mock_devonthink._search_database_for_filename.call_count
        # 5 databases for generated name, 0 for zotero name
        assert call_count == 5
    
    def test_dry_run_mode(self, service_with_mocks, mock_devonthink):
        """Operates in dry run mode without side effects."""
        mock_devonthink._search_database_for_filename = Mock(return_value=None)
        
        result = service_with_mocks._find_or_adopt_in_devonthink(
            generated_name='Test - Name',
            zotero_filename='old.pdf',
            dry_run=True
        )
        
        # In dry run, still searches but returns None if not found
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# TEST: TestParentHasDevonthinkLink
# ─────────────────────────────────────────────────────────────────────────────


class TestParentHasDevonthinkLink:
    """Test _parent_has_devonthink_link() method."""
    
    def test_returns_true_when_linked_url_child_exists(self, service_with_mocks, mock_zotero_client):
        """Returns True when a linked_url child with x-devonthink-item:// exists."""
        mock_zotero_client._get_all_attachments_cached = Mock(return_value=[
            {
                'key': 'ATT001',
                'data': {
                    'key': 'ATT001',
                    'itemType': 'attachment',
                    'linkMode': 'linked_url',
                    'parentItem': 'ITEM123',
                    'url': 'x-devonthink-item://some-uuid-here',
                    'title': 'DEVONthink Link',
                }
            }
        ])
        
        result = service_with_mocks._parent_has_devonthink_link('ITEM123')
        assert result is True
    
    def test_returns_false_when_no_such_child_exists(self, service_with_mocks, mock_zotero_client):
        """Returns False when no linked_url child with x-devonthink-item:// exists."""
        mock_zotero_client._get_all_attachments_cached = Mock(return_value=[
            {
                'key': 'ATT001',
                'data': {
                    'key': 'ATT001',
                    'itemType': 'attachment',
                    'linkMode': 'imported_file',
                    'parentItem': 'ITEM123',
                    'path': 'storage:ATT001:file.pdf',
                }
            }
        ])
        
        result = service_with_mocks._parent_has_devonthink_link('ITEM123')
        assert result is False
    
    def test_returns_false_when_child_has_different_url(self, service_with_mocks, mock_zotero_client):
        """Returns False when child has different URL format."""
        mock_zotero_client._get_all_attachments_cached = Mock(return_value=[
            {
                'key': 'ATT001',
                'data': {
                    'key': 'ATT001',
                    'itemType': 'attachment',
                    'linkMode': 'linked_url',
                    'parentItem': 'ITEM123',
                    'url': 'https://example.com',
                    'title': 'Regular Web Link',
                }
            }
        ])
        
        result = service_with_mocks._parent_has_devonthink_link('ITEM123')
        assert result is False
    
    def test_returns_false_for_empty_attachments(self, service_with_mocks, mock_zotero_client):
        """Returns False when no attachments exist."""
        mock_zotero_client._get_all_attachments_cached = Mock(return_value=[])
        
        result = service_with_mocks._parent_has_devonthink_link('ITEM123')
        assert result is False
    
    def test_ignores_different_parent_items(self, service_with_mocks, mock_zotero_client):
        """Ignores x-devonthink-item:// links for other parent items."""
        mock_zotero_client._get_all_attachments_cached = Mock(return_value=[
            {
                'key': 'ATT001',
                'data': {
                    'key': 'ATT001',
                    'itemType': 'attachment',
                    'linkMode': 'linked_url',
                    'parentItem': 'OTHER_ITEM',
                    'url': 'x-devonthink-item://some-uuid',
                }
            }
        ])
        
        result = service_with_mocks._parent_has_devonthink_link('ITEM123')
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# TEST: TestCreateDevonthinkChildLink
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateDevonthinkChildLink:
    """Test _create_devonthink_child_link() method."""
    
    def test_skips_if_parent_already_has_link(self, service_with_mocks, mock_zotero_client):
        """Skips creation if parent already has DEVONthink link."""
        mock_zotero_client._get_all_attachments_cached = Mock(return_value=[
            {
                'key': 'EXISTING_LINK',
                'data': {
                    'linkMode': 'linked_url',
                    'parentItem': 'ITEM123',
                    'url': 'x-devonthink-item://existing-uuid',
                }
            }
        ])
        
        result = service_with_mocks._create_devonthink_child_link(
            parent_key='ITEM123',
            dt_uuid='new-uuid',
            dry_run=False
        )
        
        assert result is True
        # Should not create new attachment
        service_with_mocks.zotero_api.create_url_attachments.assert_not_called()
    
    def test_creates_child_when_no_duplicate(self, service_with_mocks, mock_zotero_client):
        """Creates child attachment when no duplicate exists."""
        mock_zotero_client._get_all_attachments_cached = Mock(return_value=[])
        mock_zotero_client.create_url_attachments = Mock(return_value=[
            {'new_key': 'NEW_ATTACH_001'}
        ])
        
        result = service_with_mocks._create_devonthink_child_link(
            parent_key='ITEM123',
            dt_uuid='new-uuid-12345',
            dry_run=False
        )
        
        assert result is True
        mock_zotero_client.create_url_attachments.assert_called_once()
        
        # Check the attachment data passed to create
        call_args = mock_zotero_client.create_url_attachments.call_args
        attachment_data = call_args[0][0]
        assert attachment_data[0]['parent_key'] == 'ITEM123'
        assert 'x-devonthink-item://new-uuid-12345' in attachment_data[0]['url']
    
    def test_handles_dry_run(self, service_with_mocks, mock_zotero_client):
        """Handles dry run mode without creating attachment."""
        mock_zotero_client._get_all_attachments_cached = Mock(return_value=[])
        
        result = service_with_mocks._create_devonthink_child_link(
            parent_key='ITEM123',
            dt_uuid='new-uuid',
            dry_run=True
        )
        
        assert result is True
        # Should not create attachment in dry run
        mock_zotero_client.create_url_attachments.assert_not_called()
    
    def test_returns_false_on_api_failure(self, service_with_mocks, mock_zotero_client):
        """Returns False when API call fails."""
        mock_zotero_client._get_all_attachments_cached = Mock(return_value=[])
        mock_zotero_client.create_url_attachments = Mock(return_value=[])  # Empty response
        
        result = service_with_mocks._create_devonthink_child_link(
            parent_key='ITEM123',
            dt_uuid='new-uuid',
            dry_run=False
        )
        
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# TEST: TestDeleteImportedUrlAttachments
# ─────────────────────────────────────────────────────────────────────────────


class TestDeleteImportedUrlAttachments:
    """Test delete_imported_url_attachments() method."""
    
    def test_files_deleted_from_disk(self, service_with_mocks, mock_zotero_client, tmp_path):
        """Deletes files from disk when they exist."""
        # Create test files
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        file1 = storage_dir / "snapshot1.html"
        file2 = storage_dir / "snapshot2.html"
        file1.write_text("<html>content1</html>")
        file2.write_text("<html>content2</html>")
        
        # Create attachments pointing to these files
        attachments = [
            ZoteroAttachment(
                key='ATT_URL_001',
                parent_key='ITEM1',
                link_mode=1,
                content_type='text/html',
                path=str(file1),
                storage_hash='hash1',
                version=1,
            ),
            ZoteroAttachment(
                key='ATT_URL_002',
                parent_key='ITEM2',
                link_mode=1,
                content_type='text/html',
                path=str(file2),
                storage_hash='hash2',
                version=1,
            ),
        ]
        
        mock_zotero_client.get_imported_url_attachments = Mock(return_value=attachments)
        mock_zotero_client.delete_items_batch = Mock(return_value={'deleted': 2, 'failed': []})
        
        with patch('devonzot_service.ZOTERO_STORAGE_PATH', str(storage_dir)):
            result = service_with_mocks.delete_imported_url_attachments(dry_run=False)
        
        assert result['files_deleted'] == 2
        assert not file1.exists()
        assert not file2.exists()
    
    def test_batch_api_delete_called_with_correct_keys(self, service_with_mocks, mock_zotero_client, tmp_path):
        """Batch API delete called with correct attachment keys."""
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        file1 = storage_dir / "file1.html"
        file1.write_text("<html></html>")
        
        attachments = [
            ZoteroAttachment(
                key='KEY_ATT_001',
                parent_key='ITEM1',
                link_mode=1,
                content_type='text/html',
                path=str(file1),
                storage_hash='hash',
                version=5,
            ),
        ]
        
        mock_zotero_client.get_imported_url_attachments = Mock(return_value=attachments)
        mock_zotero_client.delete_items_batch = Mock(return_value={'deleted': 1, 'failed': []})
        
        with patch('devonzot_service.ZOTERO_STORAGE_PATH', str(storage_dir)):
            result = service_with_mocks.delete_imported_url_attachments(dry_run=False)
        
        # Verify delete_items_batch was called with correct keys
        mock_zotero_client.delete_items_batch.assert_called_once()
        call_args = mock_zotero_client.delete_items_batch.call_args
        assert 'KEY_ATT_001' in call_args[0][0]
    
    def test_dry_run_doesnt_delete(self, service_with_mocks, mock_zotero_client, tmp_path):
        """Dry run doesn't delete files."""
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        file1 = storage_dir / "snapshot.html"
        file1.write_text("<html></html>")
        
        attachments = [
            ZoteroAttachment(
                key='ATT_URL_001',
                parent_key='ITEM1',
                link_mode=1,
                content_type='text/html',
                path=str(file1),
                storage_hash='hash',
                version=1,
            ),
        ]
        
        mock_zotero_client.get_imported_url_attachments = Mock(return_value=attachments)
        mock_zotero_client.delete_items_batch = Mock(return_value={'deleted': 0, 'would_delete': 2, 'failed': []})

        with patch('devonzot_service.ZOTERO_STORAGE_PATH', str(storage_dir)):
            result = service_with_mocks.delete_imported_url_attachments(dry_run=True)

        # Files should still exist in dry run
        assert file1.exists()
        # Action counter stays at 0; would_delete_files tracks what would happen
        assert result['files_deleted'] == 0
        assert result['would_delete_files'] == 1
    
    def test_handles_missing_files_gracefully(self, service_with_mocks, mock_zotero_client):
        """Handles missing files gracefully."""
        attachments = [
            ZoteroAttachment(
                key='ATT_URL_001',
                parent_key='ITEM1',
                link_mode=1,
                content_type='text/html',
                path='/nonexistent/path/file.html',
                storage_hash='hash',
                version=1,
            ),
        ]
        
        mock_zotero_client.get_imported_url_attachments = Mock(return_value=attachments)
        mock_zotero_client.delete_items_batch = Mock(return_value={'deleted': 0, 'failed': []})
        
        result = service_with_mocks.delete_imported_url_attachments(dry_run=False)
        
        assert result['files_missing'] == 1
        assert result['files_deleted'] == 0
    
    def test_returns_correct_summary(self, service_with_mocks, mock_zotero_client, tmp_path):
        """Returns correct summary of deleted items."""
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        
        attachments = [
            ZoteroAttachment(
                key='KEY_001',
                parent_key='ITEM1',
                link_mode=1,
                content_type='text/html',
                path='/missing/file.html',
                storage_hash='hash',
                version=1,
            ),
        ]
        
        mock_zotero_client.get_imported_url_attachments = Mock(return_value=attachments)
        mock_zotero_client.delete_items_batch = Mock(return_value={'deleted': 0, 'failed': ['KEY_001']})
        
        result = service_with_mocks.delete_imported_url_attachments(dry_run=False)
        
        assert result['total_found'] == 1
        assert result['files_missing'] == 1
        assert result['items_failed'] == 1


# ─────────────────────────────────────────────────────────────────────────────
# TEST: TestRetryPendingDeletes
# ─────────────────────────────────────────────────────────────────────────────


class TestRetryPendingDeletes:
    """Test retry_pending_deletes() method."""
    
    def test_successfully_retries_and_clears_pending_items(self, service_with_mocks, mock_zotero_client):
        """Successfully retries and clears pending items from list."""
        # Set up pending deletes in service state
        service_with_mocks.state.pending_deletes = [
            {'key': 'KEY_001', 'version': 5},
            {'key': 'KEY_002', 'version': 3},
        ]
        
        # Mock the API to return that items exist
        mock_zotero_client.get_item_raw = Mock(return_value={'data': {'version': 5}})
        mock_zotero_client.delete_attachment = Mock(return_value=True)
        
        result = service_with_mocks.retry_pending_deletes(dry_run=False)
        
        assert result['retried'] == 2
        assert result['deleted'] == 2
        assert result['failed'] == 0
        # Pending deletes should be cleared
        assert service_with_mocks.state.pending_deletes == []
    
    def test_handles_already_deleted_items(self, service_with_mocks, mock_zotero_client):
        """Handles items that no longer exist (already deleted)."""
        service_with_mocks.state.pending_deletes = [
            {'key': 'KEY_001', 'version': 5},
            {'key': 'KEY_002', 'version': 3},
        ]
        
        # First item returns None (already deleted), second returns data
        mock_zotero_client.get_item_raw = Mock(side_effect=[None, {'data': {'version': 3}}])
        mock_zotero_client.delete_attachment = Mock(return_value=True)
        
        result = service_with_mocks.retry_pending_deletes(dry_run=False)
        
        assert result['retried'] == 2
        assert result['deleted'] == 2  # Both counted as deleted
        assert result['failed'] == 0
    
    def test_keeps_failed_items_in_pending_list(self, service_with_mocks, mock_zotero_client):
        """Keeps failed items in pending list for retry later."""
        service_with_mocks.state.pending_deletes = [
            {'key': 'KEY_001', 'version': 5},
            {'key': 'KEY_002', 'version': 3},
        ]
        
        mock_zotero_client.get_item_raw = Mock(return_value={'data': {'version': 5}})
        # First delete succeeds, second fails
        mock_zotero_client.delete_attachment = Mock(side_effect=[True, False])
        
        result = service_with_mocks.retry_pending_deletes(dry_run=False)
        
        assert result['retried'] == 2
        assert result['deleted'] == 1
        assert result['failed'] == 1
        # Failed item should remain in pending list
        assert len(service_with_mocks.state.pending_deletes) == 1
        assert service_with_mocks.state.pending_deletes[0]['key'] == 'KEY_002'
    
    def test_dry_run_mode(self, service_with_mocks, mock_zotero_client):
        """Operates in dry run mode without actual deletion."""
        service_with_mocks.state.pending_deletes = [
            {'key': 'KEY_001', 'version': 5},
        ]
        
        result = service_with_mocks.retry_pending_deletes(dry_run=True)

        assert result['retried'] == 1
        assert result['deleted'] == 0
        assert result['would_delete'] == 1
        # Pending list should NOT be modified in dry run
        assert len(service_with_mocks.state.pending_deletes) == 1
        mock_zotero_client.get_item_raw.assert_not_called()
    
    def test_returns_empty_result_when_no_pending_deletes(self, service_with_mocks, mock_zotero_client):
        """Returns empty result when no pending deletes exist."""
        service_with_mocks.state.pending_deletes = []
        
        result = service_with_mocks.retry_pending_deletes(dry_run=False)
        
        assert result['retried'] == 0
        assert result['deleted'] == 0
        assert result['failed'] == 0


# ─────────────────────────────────────────────────────────────────────────────
# TEST: TestDEVONthinkInterfaceRenameItem
# ─────────────────────────────────────────────────────────────────────────────


class TestDEVONthinkRenameItem:
    """Test rename_item() on DEVONthinkInterface."""
    
    def test_renames_item_successfully(self):
        """Successfully renames an item by UUID."""
        with patch('devonzot_service.subprocess.run') as mock_run:
            # First call is is_devonthink_running (returns "true"), then rename call
            mock_run.side_effect = [
                Mock(returncode=0, stdout="true", stderr=""),    # is_devonthink_running
                Mock(returncode=0, stdout="SUCCESS", stderr=""),  # rename_item
            ]
            
            dt = DEVONthinkInterface(database_name="Professional")
            result = dt.rename_item(
                uuid='test-uuid-12345',
                new_name='New Item Name',
                dry_run=False
            )
            
            assert result is True
            assert mock_run.call_count == 2
    
    def test_handles_dry_run(self):
        """Handles dry run mode without executing script."""
        dt = DEVONthinkInterface(database_name="Professional")
        result = dt.rename_item(
            uuid='test-uuid',
            new_name='New Name',
            dry_run=True
        )
        
        assert result is True
    
    def test_returns_false_on_error_response(self):
        """Returns False when AppleScript returns error."""
        with patch('devonzot_service.subprocess.run') as mock_run:
            # First call is is_devonthink_running (returns "true"), then rename call fails
            mock_run.side_effect = [
                Mock(returncode=0, stdout="true", stderr=""),    # is_devonthink_running
                Mock(returncode=1, stdout="", stderr="ERROR: Item not found"),  # rename_item fails
            ]
            
            dt = DEVONthinkInterface(database_name="Professional")
            result = dt.rename_item(
                uuid='invalid-uuid',
                new_name='New Name',
                dry_run=False
            )
            
            assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# TEST: TestServiceStatePendingDeletes
# ─────────────────────────────────────────────────────────────────────────────


class TestServiceStatePendingDeletes:
    """Test ServiceState pending_deletes initialization and behavior."""
    
    def test_initializes_empty_pending_deletes(self):
        """Initializes pending_deletes as empty list."""
        state = ServiceState()
        
        assert state.pending_deletes == []
        assert isinstance(state.pending_deletes, list)
    
    def test_initializes_with_provided_pending_deletes(self):
        """Initializes with provided pending_deletes."""
        pending = [
            {'key': 'KEY_001', 'version': 5},
            {'key': 'KEY_002', 'version': 3},
        ]
        state = ServiceState(pending_deletes=pending)
        
        assert state.pending_deletes == pending
        assert len(state.pending_deletes) == 2
    
    def test_preserves_other_fields_during_init(self):
        """Preserves other fields during initialization."""
        pending = [{'key': 'KEY_001', 'version': 5}]
        state = ServiceState(
            last_sync='2024-01-15T12:00:00Z',
            restart_count=2,
            pending_deletes=pending
        )
        
        assert state.last_sync == '2024-01-15T12:00:00Z'
        assert state.restart_count == 2
        assert state.pending_deletes == pending
    
    def test_can_append_to_pending_deletes(self):
        """Can append new items to pending_deletes."""
        state = ServiceState()
        
        state.pending_deletes.append({'key': 'KEY_001', 'version': 5})
        
        assert len(state.pending_deletes) == 1
        assert state.pending_deletes[0]['key'] == 'KEY_001'
    
    def test_json_serialization_of_pending_deletes(self):
        """Serializes pending_deletes to JSON correctly."""
        state = ServiceState(
            pending_deletes=[
                {'key': 'KEY_001', 'version': 5},
                {'key': 'KEY_002', 'version': 3},
            ]
        )
        
        # Simulate saving and loading from JSON
        from dataclasses import asdict
        state_dict = asdict(state)
        
        assert 'pending_deletes' in state_dict
        assert len(state_dict['pending_deletes']) == 2
        
        # Reload from dict
        reloaded = ServiceState(**state_dict)
        assert reloaded.pending_deletes == state.pending_deletes


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION-STYLE TESTS
# ─────────────────────────────────────────────────────────────────────────────


class TestPhase0IntegrationScenarios:
    """Integration-style tests for Phase 0 deletion workflows."""
    
    def test_complete_imported_url_deletion_workflow(self, service_with_mocks, mock_zotero_client, tmp_path):
        """Complete workflow: find imported_url attachments, delete files, delete items."""
        # Create test files
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        file1 = storage_dir / "snapshot1.html"
        file1.write_text("<html></html>")
        
        attachments = [
            ZoteroAttachment(
                key='URL_ATT_001',
                parent_key='ITEM1',
                link_mode=1,
                content_type='text/html',
                path=str(file1),
                storage_hash='hash',
                version=1,
            ),
        ]
        
        mock_zotero_client.get_imported_url_attachments = Mock(return_value=attachments)
        mock_zotero_client.delete_items_batch = Mock(return_value={'deleted': 1, 'failed': []})
        
        with patch('devonzot_service.ZOTERO_STORAGE_PATH', str(storage_dir)):
            result = service_with_mocks.delete_imported_url_attachments(dry_run=False)
        
        assert result['total_found'] == 1
        assert result['files_deleted'] == 1
        assert result['items_deleted'] == 1
        assert not file1.exists()
    
    def test_workflow_with_failed_deletes_and_retry(self, service_with_mocks, mock_zotero_client, tmp_path):
        """Workflow: retry handles pending deletes from prior failures."""
        # Manually add a failed item to pending_deletes (simulating prior failed deletion)
        service_with_mocks.state.pending_deletes = [
            {'key': 'URL_ATT_001', 'version': 1},
        ]
        
        # Now retry
        mock_zotero_client.get_item_raw = Mock(return_value={'data': {'version': 1}})
        mock_zotero_client.delete_attachment = Mock(return_value=True)
        
        result = service_with_mocks.retry_pending_deletes(dry_run=False)
        assert result['deleted'] == 1
        assert result['retried'] == 1
        assert service_with_mocks.state.pending_deletes == []
