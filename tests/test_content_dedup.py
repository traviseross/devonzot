"""Offline unit tests for the content-hash dedup gate (src/content_dedup.py).

Mocks DevonthinkMCP; the local index is always isolated to tmp_path so tests never
touch real state (mirrors the STATE_FILE isolation lesson in test_phase0_deletion).
"""

import sys
import json
import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import content_dedup
from content_dedup import ContentDedup, sha256_of_file, SHA_FIELD, SHA_SEARCH


@pytest.fixture
def idx(tmp_path):
    return tmp_path / "content_sha_index.json"


@pytest.fixture
def mcp():
    m = Mock()
    m.get_record_properties.return_value = {}
    m.search_records.return_value = {"results": [], "total": 0}
    m.get_record_duplicates.return_value = []
    return m


def _file(tmp_path, name, data=b"hello dedup"):
    p = tmp_path / name
    p.write_bytes(data)
    return p, hashlib.sha256(data).hexdigest()


# ── sha256_of_file ────────────────────────────────────────────────

def test_sha256_known_and_missing(tmp_path):
    p, expected = _file(tmp_path, "a.md")
    assert sha256_of_file(p) == expected
    assert sha256_of_file(tmp_path / "nope.md") is None


def test_sha256_unreadable(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise PermissionError("denied")
    monkeypatch.setattr("builtins.open", boom)
    assert sha256_of_file(tmp_path / "x") is None


# ── pre-import: find_or_adopt_by_content ──────────────────────────

def test_mode_off_uses_name_search_only(tmp_path, idx, mcp):
    name_cb = Mock(return_value="UUID-NAME")
    d = ContentDedup(mcp, idx, find_by_name=name_cb, mode="off")
    p, _ = _file(tmp_path, "f.md")
    uuid, adopted, sha = d.find_or_adopt_by_content(str(p), "gen", "zot.md")
    assert (uuid, adopted, sha) == ("UUID-NAME", True, None)
    name_cb.assert_called_once()
    mcp.search_records.assert_not_called()  # no hashing/search when off


def test_index_hit_adopts_without_import(tmp_path, idx, mcp):
    p, sha = _file(tmp_path, "f.md")
    idx.write_text(json.dumps({sha: "UUID-IDX"}))
    mcp.get_record_properties.return_value = {"uuid": "UUID-IDX"}
    d = ContentDedup(mcp, idx, find_by_name=Mock(), mode="live")
    uuid, adopted, got = d.find_or_adopt_by_content(str(p), "gen", "zot.md")
    assert (uuid, adopted, got) == ("UUID-IDX", True, sha)
    mcp.search_records.assert_not_called()


def test_stale_index_entry_pruned_then_falls_through(tmp_path, idx, mcp):
    p, sha = _file(tmp_path, "f.md")
    idx.write_text(json.dumps({sha: "GONE"}))
    mcp.get_record_properties.return_value = {}          # record no longer exists
    mcp.search_records.return_value = {"results": []}
    d = ContentDedup(mcp, idx, find_by_name=Mock(return_value=None), mode="live")
    uuid, adopted, _ = d.find_or_adopt_by_content(str(p), "gen", "zot.md")
    assert (uuid, adopted) == (None, False)
    assert sha not in json.loads(idx.read_text())        # pruned


def test_metadata_search_hit_adopts_and_indexes(tmp_path, idx, mcp):
    p, sha = _file(tmp_path, "f.md")
    mcp.search_records.return_value = {"results": [{"uuid": "UUID-META"}]}
    d = ContentDedup(mcp, idx, find_by_name=Mock(), mode="live")
    uuid, adopted, _ = d.find_or_adopt_by_content(str(p), "gen", "zot.md")
    assert (uuid, adopted) == ("UUID-META", True)
    mcp.search_records.assert_called_once_with(f"{SHA_SEARCH}:{sha}", limit=1)
    assert json.loads(idx.read_text())[sha] == "UUID-META"   # cached locally


def test_name_fallback_hit_stamps_sha(tmp_path, idx, mcp):
    p, sha = _file(tmp_path, "f.md")
    name_cb = Mock(return_value="UUID-NAME")
    d = ContentDedup(mcp, idx, find_by_name=name_cb, mode="live")
    uuid, adopted, _ = d.find_or_adopt_by_content(str(p), "gen", "zot.md")
    assert (uuid, adopted) == ("UUID-NAME", True)
    mcp.set_record_custom_metadata.assert_called_once_with(
        "UUID-NAME", {SHA_FIELD: sha}, mode="merge")


def test_miss_returns_none_with_sha(tmp_path, idx, mcp):
    p, sha = _file(tmp_path, "f.md")
    d = ContentDedup(mcp, idx, find_by_name=Mock(return_value=None), mode="live")
    uuid, adopted, got = d.find_or_adopt_by_content(str(p), "gen", "zot.md")
    assert (uuid, adopted, got) == (None, False, sha)


# ── shadow (observe-only) mode ────────────────────────────────────

def test_shadow_returns_legacy_result_and_mutates_nothing(tmp_path, idx, mcp):
    """Content match exists, but shadow must return the legacy name result and never
    stamp/adopt — the migration flow stays byte-identical to today."""
    p, sha = _file(tmp_path, "f.md")
    idx.write_text(json.dumps({sha: "UUID-CONTENT"}))
    mcp.get_record_properties.return_value = {"uuid": "UUID-CONTENT"}
    name_cb = Mock(return_value=None)   # name search misses (the interesting case)
    d = ContentDedup(mcp, idx, find_by_name=name_cb, mode="shadow")
    uuid, adopted, got = d.find_or_adopt_by_content(str(p), "gen", "zot.md")
    assert (uuid, adopted, got) == (None, False, sha)      # legacy (name) result
    name_cb.assert_called_once()
    mcp.set_record_custom_metadata.assert_not_called()     # zero mutation


# ── post-import: reconcile_after_import ───────────────────────────

def test_reconcile_no_duplicate_stamps_new(idx, mcp):
    d = ContentDedup(mcp, idx, mode="live")
    survivor = d.reconcile_after_import("NEW", "abc123")
    assert survivor == "NEW"
    mcp.set_record_custom_metadata.assert_called_once_with(
        "NEW", {SHA_FIELD: "abc123"}, mode="merge")
    mcp.trash_record.assert_not_called()


def test_reconcile_duplicate_adopts_oldest_and_trashes_new(idx, mcp):
    mcp.get_record_duplicates.return_value = [
        {"uuid": "OLD", "additionDate": "2024-01-01T00:00:00"},
        {"uuid": "MID", "additionDate": "2025-01-01T00:00:00"},
    ]
    d = ContentDedup(mcp, idx, mode="live")
    survivor = d.reconcile_after_import("NEW", "abc123")
    assert survivor == "OLD"                               # oldest wins
    mcp.trash_record.assert_called_once_with("NEW")
    mcp.set_record_custom_metadata.assert_called_once_with(
        "OLD", {SHA_FIELD: "abc123"}, mode="merge")


def test_reconcile_excludes_self_from_dups(idx, mcp):
    mcp.get_record_duplicates.return_value = [{"uuid": "NEW"}]  # only self
    d = ContentDedup(mcp, idx, mode="live")
    survivor = d.reconcile_after_import("NEW", "abc123")
    assert survivor == "NEW"
    mcp.trash_record.assert_not_called()


def test_reconcile_trash_failure_keeps_new(idx, mcp):
    mcp.get_record_duplicates.return_value = [{"uuid": "OLD"}]
    mcp.trash_record.side_effect = RuntimeError("locked")
    d = ContentDedup(mcp, idx, mode="live")
    survivor = d.reconcile_after_import("NEW", "abc123")
    assert survivor == "NEW"                                # no dangling link


def test_reconcile_dry_run_no_writes(idx, mcp):
    mcp.get_record_duplicates.return_value = [{"uuid": "OLD"}]
    d = ContentDedup(mcp, idx, mode="live")
    survivor = d.reconcile_after_import("NEW", "abc123", dry_run=True)
    assert survivor == "NEW"
    mcp.trash_record.assert_not_called()
    mcp.set_record_custom_metadata.assert_not_called()


def test_reconcile_mode_off_is_noop(idx, mcp):
    d = ContentDedup(mcp, idx, mode="off")
    assert d.reconcile_after_import("NEW", "abc123") == "NEW"
    mcp.get_record_duplicates.assert_not_called()


def test_reconcile_shadow_observes_without_trashing(idx, mcp):
    mcp.get_record_duplicates.return_value = [{"uuid": "OLD"}]
    d = ContentDedup(mcp, idx, mode="shadow")
    survivor = d.reconcile_after_import("NEW", "abc123")
    assert survivor == "NEW"                                # keeps new; observes only
    mcp.get_record_duplicates.assert_called_once()          # did look
    mcp.trash_record.assert_not_called()
    mcp.set_record_custom_metadata.assert_not_called()


def test_dry_run_find_does_not_stamp(tmp_path, idx, mcp):
    p, sha = _file(tmp_path, "f.md")
    name_cb = Mock(return_value="UUID-NAME")
    d = ContentDedup(mcp, idx, find_by_name=name_cb, mode="live")
    d.find_or_adopt_by_content(str(p), "gen", "zot.md", dry_run=True)
    mcp.set_record_custom_metadata.assert_not_called()     # dry-run stamps nothing
    assert not idx.exists()
