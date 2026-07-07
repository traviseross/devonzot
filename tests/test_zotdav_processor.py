"""Tests for DEVONzotService.process_zotdav_key — the server fast-path migration of a
single WebDAV blob. Mirrors the Phase 1A chain but sources the file from the blob.
"""

import sys
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import devonzot_service as dz
from devonzot_service import DEVONzotService, ServiceState


def make_blob(directory, key, filename="paper.pdf", data=b"%PDF-1.4 x"):
    with zipfile.ZipFile(Path(directory) / f"{key}.zip", "w") as zf:
        zf.writestr(filename, data)
    (Path(directory) / f"{key}.prop").write_text(
        '<properties version="1"><mtime>1</mtime><hash>abc</hash></properties>'
    )


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """A DEVONzotService with mocked Zotero/DEVONthink and a tmp zotdav dir."""
    s = DEVONzotService.__new__(DEVONzotService)
    s.state = ServiceState()
    s.zotdav_path = str(tmp_path)
    s.zotero_api = Mock()
    s.devonthink = Mock()
    s._save_state = Mock()
    s._create_devonthink_child_link = Mock(return_value=True)
    # deterministic filename (avoid building a full ZoteroItem)
    monkeypatch.setattr(dz.FilenameGenerator, "generate_filename",
                        staticmethod(lambda item: "Generated Name.pdf"))
    return s


def _stored_att(parent="PARENT1", version=7):
    return {"data": {"linkMode": "imported_file", "parentItem": parent, "version": version}}


def test_migrate_imported_file_live(svc, tmp_path):
    make_blob(tmp_path, "AAA1")
    svc.zotero_api.get_item_raw.return_value = _stored_att()
    svc.zotero_api.get_item.return_value = object()  # parent_item (opaque; filename mocked)
    svc.devonthink.copy_file_to_inbox.return_value = True
    svc.devonthink.find_item_by_filename_after_wait.return_value = "DT-UUID"
    svc.devonthink.update_item_metadata.return_value = True
    svc.zotero_api.delete_attachment.return_value = True

    r = svc.process_zotdav_key("AAA1", dry_run=False)

    assert r["result"] == "success" and r["uuid"] == "DT-UUID"
    # imported the BLOB's extracted file, not a storage path
    imported_path = svc.devonthink.copy_file_to_inbox.call_args[0][0]
    assert imported_path.endswith("paper.pdf") and "zotdav_AAA1" in imported_path
    svc._create_devonthink_child_link.assert_called_once()
    svc.zotero_api.delete_attachment.assert_called_once_with("AAA1", 7)
    assert "AAA1" in svc.state.processed_attachment_keys
    assert "PARENT1" in svc.state.processed_items


def test_dry_run_makes_no_writes(svc, tmp_path):
    make_blob(tmp_path, "AAA2")
    svc.zotero_api.get_item_raw.return_value = _stored_att()
    svc.zotero_api.get_item.return_value = object()

    r = svc.process_zotdav_key("AAA2", dry_run=True)

    assert r["result"] == "would_migrate" and r["filename"] == "Generated Name.pdf"
    svc.devonthink.copy_file_to_inbox.assert_not_called()
    svc.zotero_api.delete_attachment.assert_not_called()
    assert svc.state.processed_attachment_keys == []


def test_imported_url_is_deleted_not_imported(svc, tmp_path):
    make_blob(tmp_path, "URL1", filename="snapshot.html")
    svc.zotero_api.get_item_raw.return_value = {
        "data": {"linkMode": "imported_url", "parentItem": "P", "version": 3}}
    svc.zotero_api.delete_attachment.return_value = True

    r = svc.process_zotdav_key("URL1", dry_run=False)

    assert r["result"] == "deleted" and r["action"] == "delete_imported_url"
    svc.devonthink.copy_file_to_inbox.assert_not_called()
    svc.zotero_api.delete_attachment.assert_called_once_with("URL1", 3)
    assert "URL1" in svc.state.processed_attachment_keys


def test_already_processed_key_skipped(svc, tmp_path):
    make_blob(tmp_path, "DONE1")
    svc.state.processed_attachment_keys.append("DONE1")
    r = svc.process_zotdav_key("DONE1")
    assert r["result"] == "skipped_already_processed"
    svc.zotero_api.get_item_raw.assert_not_called()


def test_missing_blob_skipped(svc):
    r = svc.process_zotdav_key("NOPE")
    assert r["result"] == "skipped_no_blob"


def test_attachment_gone_from_zotero_skipped(svc, tmp_path):
    """A blob lingering after its Zotero item was deleted (mid-purge) is a clean skip,
    and the key is recorded so we don't refetch it every cycle."""
    make_blob(tmp_path, "GONE1")
    svc.zotero_api.get_item_raw.return_value = None
    r = svc.process_zotdav_key("GONE1", dry_run=False)
    assert r["result"] == "skipped_not_in_zotero"
    assert "GONE1" in svc.state.processed_attachment_keys


def test_no_endpoint_is_clean_skip_not_processed(svc, tmp_path):
    """If DEVONthink import returns False (no reachable endpoint), the key must NOT be
    marked processed — it retries next sweep."""
    make_blob(tmp_path, "RETRY1")
    svc.zotero_api.get_item_raw.return_value = _stored_att()
    svc.zotero_api.get_item.return_value = object()
    svc.devonthink.copy_file_to_inbox.return_value = False

    r = svc.process_zotdav_key("RETRY1", dry_run=False)
    assert r["result"] == "skipped_no_endpoint"
    assert "RETRY1" not in svc.state.processed_attachment_keys
    svc.zotero_api.delete_attachment.assert_not_called()


def test_sweep_processes_all_pending(svc, tmp_path):
    make_blob(tmp_path, "S1"); make_blob(tmp_path, "S2")
    svc.zotero_api.get_item_raw.return_value = _stored_att()
    svc.zotero_api.get_item.return_value = object()
    summary = svc.sweep_zotdav(dry_run=True)
    assert summary["total"] == 2
    assert summary["by_result"].get("would_migrate") == 2
