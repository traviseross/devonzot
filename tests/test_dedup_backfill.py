"""Offline tests for the dry-run dedup backfill (src/dedup_backfill.py).

Pure logic (clustering, survivor pick, comment parse, report) + an end-to-end
run_backfill with a mocked MCP. No network, report written to tmp_path.
"""

import sys
import json
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import dedup_backfill as bf

bf.THROTTLE = 0  # no inter-call pacing in unit tests


# ── zotero_key_from_comment ───────────────────────────────────────

def test_zotero_key_parse():
    assert bf.zotero_key_from_comment("Zotero Key: ABC123\nAuthors: X") == "ABC123"
    assert bf.zotero_key_from_comment("Zotero Key:   DEF \n") == "DEF"
    assert bf.zotero_key_from_comment("no key here") is None
    assert bf.zotero_key_from_comment("") is None
    assert bf.zotero_key_from_comment(None) is None


# ── build_clusters (union-find) ───────────────────────────────────

def test_clusters_transitive_union():
    # A~B and B~C should collapse into one cluster {A,B,C}
    dupmap = {
        "A": [{"uuid": "B"}],
        "B": [{"uuid": "A"}, {"uuid": "C"}],
        "C": [{"uuid": "B"}],
        "D": [],                      # singleton -> excluded
    }
    recs = {}
    clusters = bf.build_clusters(dupmap, recs)
    assert len(clusters) == 1
    assert clusters[0] == {"A", "B", "C"}


def test_clusters_two_separate_pairs():
    dupmap = {"A": [{"uuid": "B"}], "B": [{"uuid": "A"}],
              "X": [{"uuid": "Y"}], "Y": [{"uuid": "X"}]}
    clusters = bf.build_clusters(dupmap, {})
    assert sorted(sorted(c) for c in clusters) == [["A", "B"], ["X", "Y"]]


def test_clusters_absorb_unenumerated_dup_record():
    # A dup can reference a record we never enumerated; it must be added to records.
    recs = {}
    bf.build_clusters({"A": [{"uuid": "Z", "name": "zed"}]}, recs)
    assert recs["Z"]["name"] == "zed"


# ── choose_survivor ───────────────────────────────────────────────

def test_survivor_prefers_zotero_linked():
    members = [
        {"uuid": "old", "additionDate": "2020", "zotero_key": None},
        {"uuid": "linked", "additionDate": "2025", "zotero_key": "K"},
    ]
    assert bf.choose_survivor(members)["uuid"] == "linked"


def test_survivor_oldest_when_no_zotero_link():
    members = [
        {"uuid": "new", "additionDate": "2025", "zotero_key": None},
        {"uuid": "old", "additionDate": "2020", "zotero_key": None},
    ]
    assert bf.choose_survivor(members)["uuid"] == "old"


# ── render_markdown ───────────────────────────────────────────────

def test_render_markdown_lists_keep_and_trash():
    clusters = [{
        "members": [
            {"uuid": "keep", "name": "A", "additionDate": "2020", "zotero_key": "K"},
            {"uuid": "dupe", "name": "B", "additionDate": "2025", "zotero_key": None},
        ],
        "survivor": {"uuid": "keep", "name": "A", "additionDate": "2020", "zotero_key": "K"},
    }]
    md = bf.render_markdown(clusters, [("Global Inbox", 3068)], "2026-07-02T00:00:00Z")
    assert "DRY RUN" in md
    assert "**KEEP** `keep`" in md
    assert "would trash `dupe`" in md
    assert "Global Inbox (3068 records)" in md


# ── end-to-end run_backfill with a mocked MCP ─────────────────────

def test_run_backfill_writes_report(tmp_path):
    mcp = Mock()
    mcp.get_databases.return_value = [{"name": "Global Inbox", "uuid": "ROOT", "rootUUID": "ROOT"}]

    # one search page: two duplicates (A,B) + one unique (C) + a group (skipped)
    def search(args):
        if args["offset"] == 0:
            return {"results": [
                {"uuid": "A", "type": "pdf", "name": "paper", "additionDate": "2020"},
                {"uuid": "B", "type": "pdf", "name": "PAPER-copy", "additionDate": "2025"},
                {"uuid": "C", "type": "markdown", "name": "note", "additionDate": "2021"},
                {"uuid": "G", "type": "group", "name": "a group", "additionDate": "2019"},
            ], "total": 4}
        return {"results": [], "total": 4}

    def dispatch(name, args):
        if name == "search_records":
            return search(args)
        if name == "get_record_duplicates":
            return {"results": [
                {"uuid": "A", "duplicates": [{"uuid": "B"}]},
                {"uuid": "B", "duplicates": [{"uuid": "A"}]},
                {"uuid": "C", "duplicates": []},
            ]}
        return {}
    mcp._tool.side_effect = dispatch
    mcp.get_record_properties.side_effect = lambda u: {
        "A": {"name": "paper", "additionDate": "2020", "comment": "Zotero Key: ZK1"},
        "B": {"name": "PAPER-copy", "additionDate": "2025", "comment": ""},
    }[u]

    out = tmp_path / "report.md"
    clusters = bf.run_backfill(mcp, out_path=out)

    assert len(clusters) == 1
    members = {m["uuid"] for m in clusters[0]["members"]}
    assert members == {"A", "B"}
    assert clusters[0]["survivor"]["uuid"] == "A"          # Zotero-linked wins
    assert out.exists() and "would trash `B`" in out.read_text()
    data = json.loads(out.with_suffix(".json").read_text())
    assert data["scanned"] == [["Global Inbox", 3]]
