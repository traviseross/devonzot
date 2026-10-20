#!/usr/bin/env python3
"""One-time retrofit: add a Palimpsest link to every EPUB already migrated to DEVONthink.

Going forward, newly-migrated EPUBs get both an x-devonthink-item:// and an
x-palimpsest-item:// linked_url child automatically (see devonzot_service
_create_devonthink_child_link). This script backfills the EPUBs that were migrated
*before* that convention existed: it finds every EPUB record in DEVONthink, matches it
to the Zotero item that already carries its x-devonthink-item:// link, and — if that
item has no Palimpsest sibling yet — adds one with the SAME UUID.

Join key: the DEVONthink record UUID, which appears verbatim in the Zotero item's
x-devonthink-item://<UUID> child. The Palimpsest link reuses that UUID byte-for-byte
(case-sensitive). Existing DEVONthink links are never touched.

Idempotent: a parent that already has an x-palimpsest-item:// link is skipped, so the
script is safe to re-run (e.g. after a rate-limit interruption).

Usage:
    python src/backfill_palimpsest_links.py            # DRY RUN (default) — reports, writes nothing
    python src/backfill_palimpsest_links.py --execute   # actually create the links
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")
sys.path.insert(0, str(_ROOT / "src"))

from devonthink_mcp import DevonthinkMCP  # noqa: E402
from zotero_api_client import ZoteroAPIClient  # noqa: E402
# Single source of truth for schemes + the Palimpsest child title.
from devonzot_service import (  # noqa: E402
    DEVONTHINK_LINK_SCHEME,
    PALIMPSEST_LINK_SCHEME,
    PALIMPSEST_LINK_TITLE,
)


def _results(resp):
    return resp.get("results", []) if isinstance(resp, dict) else (resp or [])


def _epub_uuids(dt: DevonthinkMCP) -> dict:
    """UUID -> record name for every EPUB record across all open databases.

    Replicants/duplicates can repeat a UUID (same file in multiple groups); dict-keying
    on UUID collapses those to one entry, which is what we want (one link per Zotero item).
    """
    out = {}
    for r in _results(dt.search_records("extension:epub", limit=1000)):
        u = r.get("uuid")
        if u:
            out.setdefault(u, r.get("name", "?"))
    return out


def _zotero_link_index(zot: ZoteroAPIClient):
    """Scan all linked_url children once. Return:

    dt_links: list of (parent_key, uuid) carrying x-devonthink-item:// links
    pal_pairs: set of (parent_key, uuid) that already carry x-palimpsest-item:// links
    """
    dt_links, pal_pairs = [], set()
    dt_pre, pal_pre = f"{DEVONTHINK_LINK_SCHEME}://", f"{PALIMPSEST_LINK_SCHEME}://"
    for a in zot._get_all_attachments_cached():
        d = a.get("data", {})
        if d.get("linkMode") != "linked_url":
            continue
        url = d.get("url", "") or ""
        parent = d.get("parentItem")
        if url.startswith(dt_pre):
            dt_links.append((parent, url[len(dt_pre):].strip()))
        elif url.startswith(pal_pre):
            pal_pairs.add((parent, url[len(pal_pre):].strip()))
    return dt_links, pal_pairs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="Actually create the Palimpsest links (default is a dry run).")
    args = ap.parse_args()
    dry_run = not args.execute

    dt = DevonthinkMCP(
        url=os.environ["DEVONTHINK_MCP_IMAC_URL"],
        token=os.environ.get("DEVONTHINK_MCP_IMAC_TOKEN", ""),
        cacert=os.environ.get("DEVONTHINK_MCP_IMAC_CACERT"),
    )
    zot = ZoteroAPIClient(
        api_key=os.environ["ZOTERO_API_KEY"],
        user_id=os.environ["ZOTERO_USER_ID"],
    )

    epubs = _epub_uuids(dt)
    print(f"DEVONthink EPUB records: {len(epubs)} unique UUID(s)")

    dt_links, pal_pairs = _zotero_link_index(zot)
    epub_dt = [(p, u) for (p, u) in dt_links if u in epubs]
    to_add = [(p, u) for (p, u) in epub_dt if (p, u) not in pal_pairs]
    already = len(epub_dt) - len(to_add)

    print(f"Zotero items x-devonthink-item linked to an EPUB: {len(epub_dt)}")
    print(f"  already have a Palimpsest link: {already}")
    print(f"  to add: {len(to_add)}")
    linked = {u for (_, u) in dt_links}
    print(f"EPUBs in DEVONthink with no Zotero link (not retrofittable here): "
          f"{len([u for u in epubs if u not in linked])}")

    if not to_add:
        print("\nNothing to do.")
        return 0

    mode = "DRY RUN — would create" if dry_run else "Creating"
    print(f"\n{mode} {len(to_add)} Palimpsest link(s):")
    created = failed = 0
    for parent, uuid in to_add:
        label = epubs.get(uuid, "?")[:60]
        if dry_run:
            print(f"  [dry] {parent}  x-palimpsest-item://{uuid}  {label}")
            continue
        res = zot.create_url_attachments([{
            "parent_key": parent,
            "title": PALIMPSEST_LINK_TITLE,
            "url": f"{PALIMPSEST_LINK_SCHEME}://{uuid}",
        }])
        if res and res[0].get("new_key"):
            created += 1
            print(f"  ✅ {parent}  {res[0]['new_key']}  {label}")
        else:
            failed += 1
            print(f"  ❌ {parent}  FAILED  {label}")

    if not dry_run:
        print(f"\nDone: {created} created, {failed} failed.")
    else:
        print("\n(dry run — no writes. Re-run with --execute to apply.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
