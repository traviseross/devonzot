#!/usr/bin/env python3
"""
Diagnostic tool to investigate Zotero attachment detection issues.
Fetches attachment data from the Zotero Web API and analyzes it.

Usage:
    python src/diagnose_attachments.py
    python src/diagnose_attachments.py --json-only  # Skip console output
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from dotenv import load_dotenv
from zotero_api_client import ZoteroAPIClient, LINK_MODE_MAP

# Configuration
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

ZOTERO_STORAGE_PATH = "/Users/travisross/Zotero/storage"
OUTPUT_FILE = "storage_diagnostics_report.json"

# Reverse mapping for display: int -> string
LINK_MODE_NAMES = {v: k for k, v in LINK_MODE_MAP.items()}


@dataclass
class AttachmentStats:
    """Statistics about attachment detection"""
    total_attachments: int = 0

    # By path format
    with_storage_prefix: int = 0
    without_storage_prefix: int = 0
    with_forward_slash: int = 0
    null_or_empty_path: int = 0
    http_url_path: int = 0
    absolute_file_path: int = 0

    # By linkMode
    linkmode_0_imported_file: int = 0
    linkmode_1_imported_url: int = 0
    linkmode_2_linked_file: int = 0
    linkmode_3_linked_url: int = 0
    linkmode_other: int = 0

    # By parent relationship
    has_parent: int = 0
    no_parent_orphaned: int = 0

    # File existence (local storage check)
    file_exists: int = 0
    file_missing: int = 0
    path_unresolvable: int = 0

    # Content type
    pdf_content_type: int = 0
    html_content_type: int = 0
    other_content_type: int = 0
    null_content_type: int = 0

    # Would be detected by service (imported_file/imported_url with storage: path)
    detected_by_service: int = 0
    not_detected_by_service: int = 0


@dataclass
class PathExample:
    """Example attachment with path"""
    key: str
    path: str
    link_mode: int
    link_mode_name: str
    content_type: Optional[str]
    parent_key: Optional[str]
    file_exists: bool
    detected: bool


def categorize_path(path: Optional[str]) -> Dict[str, bool]:
    """Categorize a path by format"""
    if not path:
        return {'null_or_empty': True}

    categories = {
        'with_storage_prefix': path.startswith('storage:'),
        'http_url': path.startswith('http://') or path.startswith('https://'),
        'absolute_file_path': path.startswith('/') or (len(path) > 1 and path[1] == ':'),
        'without_storage_prefix': False,
        'with_forward_slash': False,
    }

    if not categories['with_storage_prefix'] and not categories['http_url'] and not categories['absolute_file_path']:
        if re.match(r'^[A-Z0-9]{8}[:/]', path):
            if ':' in path:
                categories['without_storage_prefix'] = True
            elif '/' in path:
                categories['with_forward_slash'] = True

    return categories


def resolve_storage_path(path: Optional[str]) -> Optional[Path]:
    """Try to resolve a path to actual file location"""
    if not path:
        return None

    storage_base = Path(ZOTERO_STORAGE_PATH)

    if path.startswith("storage:"):
        parts = path.split(":")
        if len(parts) >= 3:
            key = parts[1]
            filename = ":".join(parts[2:])
            return storage_base / key / filename

    elif ":" in path and not path.startswith("/"):
        parts = path.split(":", 1)
        if len(parts) == 2 and re.match(r'^[A-Z0-9]{8}$', parts[0]):
            key, filename = parts
            return storage_base / key / filename

    elif "/" in path and not path.startswith("/"):
        parts = path.split("/", 1)
        if len(parts) == 2 and re.match(r'^[A-Z0-9]{8}$', parts[0]):
            key, filename = parts
            return storage_base / key / filename

    elif path.startswith("/"):
        return Path(path)

    return None


def analyze_attachments(client: ZoteroAPIClient) -> tuple:
    """Comprehensive attachment analysis via the Zotero API"""
    stats = AttachmentStats()
    examples = []
    path_patterns = defaultdict(list)

    print("Fetching all attachments from Zotero API...")
    all_attachments = client._get_all_items_paginated({'itemType': 'attachment'})
    print(f"Fetched {len(all_attachments)} attachments")

    for api_item in all_attachments:
        data = api_item.get('data', {})
        stats.total_attachments += 1

        key = data.get('key', '')
        path = data.get('path', '') or ''
        link_mode_str = data.get('linkMode', '')
        link_mode = LINK_MODE_MAP.get(link_mode_str, -1)
        content_type = data.get('contentType', '')
        parent_key = data.get('parentItem')

        # Categorize path
        path_cats = categorize_path(path if path else None)
        if path_cats.get('null_or_empty'):
            stats.null_or_empty_path += 1
        if path_cats.get('with_storage_prefix'):
            stats.with_storage_prefix += 1
        if path_cats.get('without_storage_prefix'):
            stats.without_storage_prefix += 1
        if path_cats.get('with_forward_slash'):
            stats.with_forward_slash += 1
        if path_cats.get('http_url'):
            stats.http_url_path += 1
        if path_cats.get('absolute_file_path'):
            stats.absolute_file_path += 1

        # LinkMode
        if link_mode == 0:
            stats.linkmode_0_imported_file += 1
        elif link_mode == 1:
            stats.linkmode_1_imported_url += 1
        elif link_mode == 2:
            stats.linkmode_2_linked_file += 1
        elif link_mode == 3:
            stats.linkmode_3_linked_url += 1
        else:
            stats.linkmode_other += 1

        # Parent relationship
        if parent_key:
            stats.has_parent += 1
        else:
            stats.no_parent_orphaned += 1

        # File existence (local check)
        resolved_path = resolve_storage_path(path if path else None)
        file_exists = False
        if resolved_path:
            file_exists = resolved_path.exists()
            if file_exists:
                stats.file_exists += 1
            else:
                stats.file_missing += 1
        else:
            stats.path_unresolvable += 1

        # Content type
        if content_type:
            if 'pdf' in content_type.lower():
                stats.pdf_content_type += 1
            elif 'html' in content_type.lower():
                stats.html_content_type += 1
            else:
                stats.other_content_type += 1
        else:
            stats.null_content_type += 1

        # Would be detected by the service (imported_file/imported_url with storage: path)
        detected = (link_mode_str in ('imported_file', 'imported_url')
                    and path and path.startswith('storage:'))
        if detected:
            stats.detected_by_service += 1
        else:
            stats.not_detected_by_service += 1

        # Collect examples
        if len(examples) < 50:
            examples.append(PathExample(
                key=key,
                path=path,
                link_mode=link_mode,
                link_mode_name=link_mode_str,
                content_type=content_type or None,
                parent_key=parent_key,
                file_exists=file_exists,
                detected=detected,
            ))

        # Collect path patterns for undetected imported files
        if path and not detected and link_mode == 0:
            path_patterns['undetected'].append(path)

    return stats, examples, dict(path_patterns)


def analyze_orphaned_files() -> Dict[str, Any]:
    """Find files in storage directory (filesystem check, no DB needed)"""
    storage_path = Path(ZOTERO_STORAGE_PATH)

    if not storage_path.exists():
        return {'error': f'Storage path does not exist: {storage_path}'}

    orphaned_files = []
    total_storage_keys = 0

    for storage_dir in storage_path.iterdir():
        if storage_dir.is_dir() and re.match(r'^[A-Z0-9]{8}$', storage_dir.name):
            total_storage_keys += 1
            files_in_dir = list(storage_dir.glob('*'))
            if len(files_in_dir) > 0:
                orphaned_files.append({
                    'storage_key': storage_dir.name,
                    'files': [f.name for f in files_in_dir[:5]],
                })

            if len(orphaned_files) >= 10:
                break

    return {
        'total_storage_directories': total_storage_keys,
        'sample_directories': orphaned_files[:10],
    }


def generate_report(stats: AttachmentStats, examples: List[PathExample],
                    path_patterns: Dict[str, List[str]], orphaned_files: Dict[str, Any]) -> Dict[str, Any]:
    """Generate comprehensive diagnostic report"""
    report = {
        'timestamp': datetime.now().isoformat(),
        'source': 'Zotero Web API',
        'storage_path': ZOTERO_STORAGE_PATH,

        'summary': {
            'total_attachments': stats.total_attachments,
            'detected_by_service': stats.detected_by_service,
            'not_detected': stats.not_detected_by_service,
            'detection_rate': f"{(stats.detected_by_service / stats.total_attachments * 100):.1f}%" if stats.total_attachments > 0 else "0%",
        },

        'path_formats': {
            'with_storage_prefix': stats.with_storage_prefix,
            'without_storage_prefix': stats.without_storage_prefix,
            'with_forward_slash': stats.with_forward_slash,
            'null_or_empty': stats.null_or_empty_path,
            'http_url': stats.http_url_path,
            'absolute_file_path': stats.absolute_file_path,
        },

        'link_modes': {
            'imported_file (0)': stats.linkmode_0_imported_file,
            'imported_url (1)': stats.linkmode_1_imported_url,
            'linked_file (2)': stats.linkmode_2_linked_file,
            'linked_url (3)': stats.linkmode_3_linked_url,
            'other': stats.linkmode_other,
        },

        'parent_relationships': {
            'has_parent': stats.has_parent,
            'no_parent_orphaned': stats.no_parent_orphaned,
        },

        'file_existence': {
            'file_exists': stats.file_exists,
            'file_missing': stats.file_missing,
            'path_unresolvable': stats.path_unresolvable,
        },

        'content_types': {
            'pdf': stats.pdf_content_type,
            'html': stats.html_content_type,
            'other': stats.other_content_type,
            'null': stats.null_content_type,
        },

        'examples': {
            'detected_samples': [asdict(ex) for ex in examples if ex.detected][:10],
            'not_detected_samples': [asdict(ex) for ex in examples if not ex.detected][:10],
        },

        'undetected_path_patterns': path_patterns.get('undetected', [])[:20],

        'orphaned_files_on_disk': orphaned_files,
    }

    return report


def print_summary(report: Dict[str, Any]):
    """Print human-readable summary to console"""
    print("\n" + "=" * 70)
    print("ZOTERO ATTACHMENT DETECTION DIAGNOSTIC REPORT")
    print("=" * 70)

    summary = report['summary']
    print(f"\nDETECTION SUMMARY:")
    print(f"  Total attachments:             {summary['total_attachments']:,}")
    print(f"  Detected by service:           {summary['detected_by_service']:,}")
    print(f"  NOT detected:                  {summary['not_detected']:,}")
    print(f"  Detection rate:                {summary['detection_rate']}")

    print(f"\nPATH FORMATS:")
    path_formats = report['path_formats']
    for key, value in path_formats.items():
        if value > 0:
            print(f"  {key.replace('_', ' ').title():30s}: {value:,}")

    print(f"\nLINK MODES:")
    link_modes = report['link_modes']
    for key, value in link_modes.items():
        if value > 0:
            print(f"  {key:30s}: {value:,}")

    print(f"\nPARENT RELATIONSHIPS:")
    parents = report['parent_relationships']
    for key, value in parents.items():
        if value > 0:
            print(f"  {key.replace('_', ' ').title():30s}: {value:,}")

    print(f"\nFILE EXISTENCE:")
    files = report['file_existence']
    for key, value in files.items():
        if value > 0:
            print(f"  {key.replace('_', ' ').title():30s}: {value:,}")

    print(f"\nCONTENT TYPES:")
    content_types = report['content_types']
    for key, value in content_types.items():
        if value > 0:
            print(f"  {key.upper():30s}: {value:,}")

    # Show examples of undetected items
    not_detected = report['examples']['not_detected_samples']
    if not_detected:
        print(f"\nEXAMPLES OF UNDETECTED ATTACHMENTS:")
        for ex in not_detected[:5]:
            print(f"\n  Key: {ex['key']}")
            print(f"    Path: {ex['path'][:70]}{'...' if len(ex['path']) > 70 else ''}")
            print(f"    LinkMode: {ex['link_mode_name']} ({ex['link_mode']})")
            print(f"    ContentType: {ex['content_type'] or 'NULL'}")
            print(f"    Parent: {ex['parent_key'] or 'ORPHANED'}")
            print(f"    File exists: {'yes' if ex['file_exists'] else 'no'}")

    # Show undetected path patterns
    patterns = report.get('undetected_path_patterns', [])
    if patterns:
        print(f"\nSAMPLE UNDETECTED PATH PATTERNS:")
        for path in patterns[:10]:
            print(f"  {path}")

    print(f"\n{'=' * 70}")
    print(f"Report saved to: {OUTPUT_FILE}")
    print(f"{'=' * 70}\n")


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Diagnose Zotero attachment detection issues")
    parser.add_argument('--json-only', action='store_true', help='Output JSON only, skip console summary')
    args = parser.parse_args()

    try:
        print("Connecting to Zotero Web API...")
        client = ZoteroAPIClient(
            api_key=os.environ["ZOTERO_API_KEY"],
            user_id=os.environ["ZOTERO_USER_ID"],
            api_base=os.environ.get("ZOTERO_API_BASE", "https://api.zotero.org"),
            api_version=os.environ.get("API_VERSION", "3"),
            rate_limit_delay=float(os.environ.get("RATE_LIMIT_DELAY", 1.0)),
        )

        print("Analyzing attachments...")
        stats, examples, path_patterns = analyze_attachments(client)

        print("Checking for orphaned files on disk...")
        orphaned_files = analyze_orphaned_files()

        print("Generating report...")
        report = generate_report(stats, examples, path_patterns, orphaned_files)

        # Save JSON report
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(report, f, indent=2)

        # Print summary unless json-only
        if not args.json_only:
            print_summary(report)
        else:
            print(f"Report saved to: {OUTPUT_FILE}")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
