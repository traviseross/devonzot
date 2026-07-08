"""Tests for the server-safe reconcile backstop (DEVONzotService.reconcile) and the
DEVONZOT_PROFILE=server guard that keeps it from deleting Mac-only ZotFile records.

reconcile is the low-frequency mop-up behind the always-on zotdav watcher. It must:
  * push metadata edits of already-migrated parents to DEVONthink,
  * NEVER migrate/cloud-download a changed stored (linkMode=0) blob (the watcher owns it),
  * delete imported_url (linkMode=1) snapshots,
  * prune local state for deleted Zotero items,
  * and — the unsafe-behavior #2 regression — NEVER delete a linkMode=2 item that is
    merely unreachable-on-server and absent from DEVONthink.
"""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import devonzot_service as dz
from devonzot_service import DEVONzotService, ServiceState, ZoteroAttachment


@pytest.fixture
def svc():
    """A DEVONzotService with Zotero/DEVONthink mocked, ready for reconcile()."""
    s = DEVONzotService.__new__(DEVONzotService)
    # Non-None baseline so tests exercise the incremental path (None triggers baseline-init).
    s.state = ServiceState(last_library_version=100)
    # reconcile reloads state at start and merges at save; return our in-memory state so
    # the mocked disk == the test state (concurrent-merge is covered by its own test).
    s._load_state = Mock(side_effect=lambda: s.state)
    s.zotero_api = Mock()
    s.zotero_api.invalidate_caches = Mock()
    s.zotero_api.last_library_version = 200
    s.zotero_api.get_deleted_since = Mock(return_value={"items": []})
    s.devonthink = Mock()
    s.devonthink.update_item_metadata = Mock(return_value=True)
    s._save_state = Mock()
    return s


def _att_item(key, link_mode, parent="P1", version=3):
    return {"data": {"itemType": "attachment", "linkMode": link_mode,
                     "key": key, "parentItem": parent, "version": version}}


async def test_changed_parent_pushes_metadata_to_devonthink(svc):
    """A changed, already-migrated parent -> update_item_metadata called with the UUID
    extracted from its x-devonthink-item:// link. No files, no deletes."""
    svc.state.processed_items = ["PARENT1"]
    svc.zotero_api.get_changed_item_versions.return_value = {"PARENT1": 150}
    svc.zotero_api.get_items_by_keys.return_value = [
        {"data": {"itemType": "journalArticle", "key": "PARENT1"}}]
    # targeted children fetch (one request) returns the parent's DEVONthink-link child
    svc.zotero_api.get_item_children = Mock(return_value=[
        {"data": {"linkMode": "linked_url", "url": "x-devonthink-item://ABC-UUID-1"}}])
    fake_parent = object()
    svc.zotero_api.get_item = Mock(return_value=fake_parent)

    summary = await svc.reconcile(dry_run=False)

    svc.devonthink.update_item_metadata.assert_called_once_with(
        "ABC-UUID-1", fake_parent, False)
    assert summary["metadata_updated"] == 1
    svc.zotero_api.delete_attachment.assert_not_called()


async def test_changed_stored_attachment_is_skipped_not_downloaded(svc):
    """A changed stored (imported_file/linkMode=0) attachment is owned by the watcher.
    Reconcile must SKIP it — no import, no cloud download, no delete."""
    svc.zotero_api.get_changed_item_versions.return_value = {"ATT1": 150}
    svc.zotero_api.get_items_by_keys.return_value = [_att_item("ATT1", "imported_file")]

    summary = await svc.reconcile(dry_run=False)

    assert summary["stored_skipped"] == 1
    svc.devonthink.update_item_metadata.assert_not_called()
    svc.zotero_api.delete_attachment.assert_not_called()
    # never marked processed here — leave it for the watcher's re-sweep
    assert "ATT1" not in svc.state.processed_attachment_keys


async def test_imported_url_is_deleted(svc):
    """imported_url (linkMode=1) snapshots are deleted (Phase 0 behavior)."""
    svc.zotero_api.get_changed_item_versions.return_value = {"URL1": 150}
    svc.zotero_api.get_items_by_keys.return_value = [
        _att_item("URL1", "imported_url", version=9)]
    svc.zotero_api.delete_attachment.return_value = True

    summary = await svc.reconcile(dry_run=False)

    svc.zotero_api.delete_attachment.assert_called_once_with("URL1", 9)
    assert summary["imported_url_deleted"] == 1
    assert "URL1" in svc.state.processed_attachment_keys


async def test_deleted_key_pruned_from_state(svc):
    """A deleted Zotero item prunes both processed_items and processed_attachment_keys."""
    svc.state.processed_items = ["DEL1"]
    svc.state.processed_attachment_keys = ["DEL1"]
    svc.zotero_api.get_changed_item_versions.return_value = {}
    svc.zotero_api.get_deleted_since.return_value = {"items": ["DEL1"]}

    summary = await svc.reconcile(dry_run=False)

    assert "DEL1" not in svc.state.processed_items
    assert "DEL1" not in svc.state.processed_attachment_keys
    assert summary["deleted_pruned"] == 1


async def test_dry_run_makes_no_writes(svc):
    """--reconcile --dry-run must not delete or persist state."""
    svc.zotero_api.get_changed_item_versions.return_value = {"URL1": 150}
    svc.zotero_api.get_items_by_keys.return_value = [_att_item("URL1", "imported_url")]

    summary = await svc.reconcile(dry_run=True)

    svc.zotero_api.delete_attachment.assert_not_called()
    svc._save_state.assert_not_called()
    assert summary["imported_url_deleted"] == 1  # would-delete counted


async def test_first_run_initializes_baseline_without_full_scan(svc):
    """With no baseline (last_library_version=None, the server's real state), reconcile must
    NOT diff from 0 (a 16k full scan + mass historical delete). It seeds the current version
    and processes nothing this run."""
    svc.state.last_library_version = None
    svc.zotero_api.last_library_version = 68010

    summary = await svc.reconcile(dry_run=False)

    assert summary.get("baseline_initialized") == 68010
    assert summary["changed"] == 0
    # never fetched changed items or deletions — no scan happened
    svc.zotero_api.get_items_by_keys.assert_not_called()
    svc.zotero_api.delete_attachment.assert_not_called()
    assert svc.state.last_library_version == 68010


async def test_save_merges_with_concurrent_watcher_writes(svc):
    """The watcher may append a processed_attachment_key while reconcile runs. The merged
    save must preserve that key (union), not clobber it with reconcile's stale copy."""
    svc.state.processed_attachment_keys = ["OLD1"]
    svc.zotero_api.get_changed_item_versions.return_value = {"URL1": 150}
    svc.zotero_api.get_items_by_keys.return_value = [_att_item("URL1", "imported_url", version=9)]
    svc.zotero_api.delete_attachment.return_value = True

    # simulate the watcher having added WATCH1 on disk since reconcile started: the base
    # reload (used only by _save_state_merged) carries a key our in-memory copy lacks.
    def loader():
        base = ServiceState(last_library_version=100)
        base.processed_attachment_keys = ["OLD1", "WATCH1"]  # watcher's concurrent addition
        return base
    calls = {"n": 0}
    def load_side_effect():
        # first call (reconcile start) returns the live test state; the save-time reload
        # returns the "disk" copy that includes the watcher's addition.
        calls["n"] += 1
        return svc.state if calls["n"] == 1 else loader()
    svc._load_state = Mock(side_effect=load_side_effect)

    await svc.reconcile(dry_run=False)

    keys = set(svc.state.processed_attachment_keys)
    assert "WATCH1" in keys   # watcher's concurrent write survived
    assert "URL1" in keys      # reconcile's own addition present
    assert "OLD1" in keys


# --- unsafe-behavior #2 regression: the DEVONZOT_PROFILE=server guard ---

@pytest.fixture
def zfsvc(monkeypatch):
    """A service wired to exercise _process_single_zotfile_attachment directly."""
    s = DEVONzotService.__new__(DEVONzotService)
    s.state = ServiceState()
    s.zotero_api = Mock()
    s.zotero_api.get_item.return_value = object()  # parent_item (opaque)
    s.devonthink = Mock()
    s.dedup = Mock()
    s.dedup.find_or_adopt_by_content.return_value = (None, False, None)  # not in DEVONthink
    s._get_zotero_filename = Mock(return_value="orig.pdf")
    monkeypatch.setattr(dz.FilenameGenerator, "generate_filename",
                        staticmethod(lambda item: "Name.pdf"))
    return s


def _zotfile_att():
    return ZoteroAttachment(
        key="ZF1", parent_key="P1", link_mode=2, content_type="application/pdf",
        path="/Users/travisross/ZotFile Import/mac-only.pdf",  # unreachable on server
        storage_hash=None)


def test_zotfile_not_in_devonthink_is_not_deleted_on_server(zfsvc, monkeypatch):
    """Server profile: a linkMode=2 file that's unreachable AND not in DEVONthink must be
    SKIPPED, never deleted — its bytes live on a Mac we can't see."""
    monkeypatch.setattr(dz, "DEVONZOT_PROFILE", "server")

    result = zfsvc._process_single_zotfile_attachment(_zotfile_att(), dry_run=False)

    assert result["result"] == "skipped_file_missing"
    zfsvc.zotero_api.delete_attachment.assert_not_called()
    zfsvc.zotero_api.get_item_raw.assert_not_called()


def test_zotfile_not_in_devonthink_is_deleted_on_mac(zfsvc, monkeypatch):
    """Contrast: on the mac profile the same case IS treated as a broken attachment and
    deleted — proving the guard (not some other skip) is what protects the server."""
    monkeypatch.setattr(dz, "DEVONZOT_PROFILE", "mac")
    zfsvc.zotero_api.get_item_raw.return_value = {"data": {"version": 4}}

    result = zfsvc._process_single_zotfile_attachment(_zotfile_att(), dry_run=False)

    assert result["result"] == "cleaned_broken"
    zfsvc.zotero_api.delete_attachment.assert_called_once_with("ZF1", 4)
