#!/usr/bin/env python3
"""One-time DRY-RUN dedup backfill for DEVONzot.

Finds content-duplicate records ALREADY sitting in DEVONthink — including files added
before Zotero, under arbitrary/unrelated names — using DEVONthink's native content-hash
duplicate detection (`get_record_duplicates`). This is REPORT-ONLY: it never trashes,
merges, moves, or stamps anything.

Scope caveat: only databases currently OPEN in DEVONthink are visible over MCP (right
now that is typically just "Global Inbox"). Open the other target databases
(Professional / Articles / Books / Research) in DEVONthink for a full-library scan.

Usage:
    python src/dedup_backfill.py [--database NAME] [--limit N] [--out PATH]
"""

import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import time
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from devonthink_mcp import DevonthinkMCP, DevonthinkMCPError

logger = logging.getLogger("dedup_backfill")

# Throttle between MCP calls. A prior un-throttled scan overloaded DEVONthink hard
# enough to wedge the app; a small pace keeps the scan gentle. Override with
# DEDUP_BACKFILL_THROTTLE (seconds) or --throttle.
THROTTLE = float(os.environ.get("DEDUP_BACKFILL_THROTTLE", "0.2"))


def _call(fn, *args, retries=4, **kwargs):
    """Call an MCP method, retrying transient transport errors, and pace afterward so
    the scan never floods the DEVONthink HTTP server."""
    for attempt in range(retries):
        try:
            result = fn(*args, **kwargs)
            if THROTTLE:
                time.sleep(THROTTLE)
            return result
        except DevonthinkMCPError as e:
            if attempt == retries - 1:
                raise
            logger.warning(f"MCP transient error ({e}); retry {attempt + 1}/{retries - 1}")
            time.sleep(2 + 2 * attempt)

PAGE = 500          # search_records page size
DUP_BATCH = 500     # get_record_duplicates batch size (MCP max)
GROUP_TYPES = {"group"}
# Enumerate documents via a single fast server-side query. `kind:any` matches every
# record that has a document kind (i.e. all content records — the dedup targets),
# and is ~10x faster/more reliable than an additionDate range scan (which times out
# under load). Groups/tags are excluded, which is what we want. Report notes the count.
ALL_QUERY = "kind:any"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "dedup_backfill_report.md"


# ── pure helpers (unit-tested) ────────────────────────────────────

def zotero_key_from_comment(comment):
    """Extract the Zotero key from a record comment whose first line is
    'Zotero Key: XXX' (the format DEVONzot stamps). None if not present."""
    if not comment:
        return None
    first = comment.splitlines()[0].strip() if comment else ""
    if first.startswith("Zotero Key:"):
        key = first.split(":", 1)[1].strip()
        return key or None
    return None


def build_clusters(dupmap, records_by_uuid):
    """Union-find duplicate clusters from a {uuid: [dup record dicts]} map.

    Mutates records_by_uuid to include any dup records not seen during enumeration.
    Returns a list of sets of uuids, each of size >= 2.
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:      # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for uuid, dups in dupmap.items():
        find(uuid)
        for d in dups:
            du = d.get("uuid")
            if du:
                records_by_uuid.setdefault(du, d)
                union(uuid, du)

    clusters = {}
    for u in list(parent):
        clusters.setdefault(find(u), set()).add(u)
    return [c for c in clusters.values() if len(c) >= 2]


def choose_survivor(members):
    """members: list of record dicts (uuid, name, additionDate, zotero_key).
    Prefer a Zotero-linked member; among ties, the oldest additionDate."""
    def key(m):
        return (0 if m.get("zotero_key") else 1, m.get("additionDate") or "")
    return sorted(members, key=key)[0]


def render_markdown(clusters, scanned, generated_at):
    lines = [
        "# DEVONzot dedup backfill — DRY RUN (no changes made)",
        "",
        f"Generated: {generated_at}",
        "",
        "## Scope",
        f"- Databases scanned (only OPEN databases are visible via MCP): "
        f"{', '.join(f'{n} ({c} records)' for n, c in scanned) or 'none'}",
        "- To scan the whole library, open Professional / Articles / Books / Research "
        "in DEVONthink and re-run.",
        "",
        f"## Duplicate clusters found: {len(clusters)}",
        f"- Redundant records that WOULD be trashed: "
        f"{sum(len(c['members']) - 1 for c in clusters)}",
        f"- Clusters touching a Zotero-linked record: "
        f"{sum(1 for c in clusters if any(m.get('zotero_key') for m in c['members']))}",
        "",
    ]
    for i, c in enumerate(clusters, 1):
        surv = c["survivor"]
        lines.append(f"### Cluster {i} — {len(c['members'])} copies")
        lines.append(f"- **KEEP** `{surv['uuid']}` — {surv.get('name','?')!r}"
                     f"{' [Zotero:'+surv['zotero_key']+']' if surv.get('zotero_key') else ''}"
                     f" (added {surv.get('additionDate','?')})")
        for m in c["members"]:
            if m["uuid"] == surv["uuid"]:
                continue
            lines.append(f"- would trash `{m['uuid']}` — {m.get('name','?')!r}"
                         f"{' [Zotero:'+m['zotero_key']+']' if m.get('zotero_key') else ''}"
                         f" (added {m.get('additionDate','?')})")
        lines.append("")
    return "\n".join(lines)


# ── MCP-driven enumeration ────────────────────────────────────────

def enumerate_records(mcp, db_uuid):
    """Yield document (non-group) records in a database via server-side search
    (paginated). Far faster than recursively walking nested groups, and it can't
    double-count a record reached through multiple group paths."""
    offset, total = 0, None
    while True:
        res = _call(mcp._tool, "search_records",
                    {"query": ALL_QUERY, "database_uuid": db_uuid,
                     "limit": PAGE, "offset": offset})
        items = res.get("results", []) if isinstance(res, dict) else (res or [])
        if total is None and isinstance(res, dict):
            total = res.get("total")
        if not items:
            break
        for it in items:
            if it.get("type") not in GROUP_TYPES and it.get("uuid"):
                yield it
        offset += len(items)
        if (total is not None and offset >= total) or len(items) < PAGE:
            break


def batch_duplicates(mcp, uuids):
    """Return {uuid: [dup record dicts]} using batched get_record_duplicates."""
    out = {}
    for i in range(0, len(uuids), DUP_BATCH):
        chunk = uuids[i:i + DUP_BATCH]
        try:
            res = _call(mcp._tool, "get_record_duplicates", {"uuids": chunk})
        except Exception as e:
            logger.warning(f"batch duplicates failed for chunk @{i}: {e}")
            continue
        for entry in (res.get("results", []) if isinstance(res, dict) else []):
            out[entry.get("uuid")] = entry.get("duplicates") or []
    return out


def run_backfill(mcp, only_database=None, out_path=DEFAULT_OUT):
    dbs = mcp.get_databases()
    dbs = dbs if isinstance(dbs, list) else []
    if only_database:
        dbs = [d for d in dbs if d.get("name") == only_database]
    if not dbs:
        logger.warning("No open databases to scan (only OPEN databases are visible).")
        return None

    records_by_uuid = {}
    scanned = []
    for db in dbs:
        recs = list(enumerate_records(mcp, db.get("uuid")))
        for r in recs:
            records_by_uuid[r["uuid"]] = r
        scanned.append((db.get("name"), len(recs)))
        logger.info(f"enumerated {len(recs)} records in {db.get('name')!r}")

    dupmap = batch_duplicates(mcp, list(records_by_uuid.keys()))
    cluster_sets = build_clusters(dupmap, records_by_uuid)

    clusters = []
    for cs in cluster_sets:
        members = []
        for u in cs:
            rec = dict(records_by_uuid.get(u, {"uuid": u}))
            try:
                props = _call(mcp.get_record_properties, u)
                rec.setdefault("name", props.get("name"))
                rec.setdefault("additionDate", props.get("additionDate"))
                rec["zotero_key"] = zotero_key_from_comment(props.get("comment"))
            except Exception:
                rec["zotero_key"] = None
            members.append(rec)
        clusters.append({"members": members, "survivor": choose_survivor(members)})

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    md = render_markdown(clusters, scanned, generated_at)
    Path(out_path).write_text(md)
    Path(out_path).with_suffix(".json").write_text(json.dumps(
        {"generated_at": generated_at, "scanned": scanned, "clusters": clusters}, indent=2))

    redundant = sum(len(c["members"]) - 1 for c in clusters)
    logger.info(f"DONE (dry run): {len(clusters)} duplicate clusters, "
                f"{redundant} redundant records. Report -> {out_path}")
    return clusters


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="DEVONzot dedup backfill (DRY RUN, report-only)")
    ap.add_argument("--database", help="Scan only this database (default: all open)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Report output path (.md)")
    ap.add_argument("--throttle", type=float, default=None,
                    help="Seconds to pace between MCP calls (default 0.2; raise if DEVONthink is busy)")
    args = ap.parse_args()
    if args.throttle is not None:
        globals()["THROTTLE"] = args.throttle
    # Shorter per-call timeout so a transiently-loaded DEVONthink fails fast and retries
    # rather than hanging ~90s per call.
    mcp = DevonthinkMCP(timeout=30)
    if not mcp.is_running():
        logger.error("DEVONthink MCP not reachable on :8420 — is the HTTP server enabled?")
        sys.exit(1)
    run_backfill(mcp, only_database=args.database, out_path=args.out)


if __name__ == "__main__":
    main()
