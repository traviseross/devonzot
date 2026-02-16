#!/usr/bin/env python3
"""
Diagnostic tool to investigate Zotero attachment detection issues.
Analyzes the Zotero database to identify undetected PDFs and edge cases.

Usage:
    python src/diagnose_attachments.py
    python src/diagnose_attachments.py --json-only  # Skip console output
"""

import sqlite3
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime

# Configuration
ZOTERO_DB_PATH = "/Users/travisross/Zotero/zotero.sqlite"
ZOTERO_STORAGE_PATH = "/Users/travisross/Zotero/storage"
OUTPUT_FILE = "storage_diagnostics_report.json"

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
    linkmode_0_stored: int = 0
    linkmode_1_linked: int = 0
    linkmode_2_weblink: int = 0
    linkmode_3_relative: int = 0
    linkmode_other: int = 0

    # By parent relationship
    has_parent: int = 0
    no_parent_orphaned: int = 0
    parent_not_found: int = 0

    # File existence
    file_exists: int = 0
    file_missing: int = 0
    path_unresolvable: int = 0

    # Content type
    pdf_content_type: int = 0
    html_content_type: int = 0
    other_content_type: int = 0
    null_content_type: int = 0

    # Current detection
    detected_by_current_query: int = 0
    not_detected_by_current_query: int = 0

@dataclass
class PathExample:
    """Example attachment with path"""
    item_id: int
    path: str
    link_mode: int
    content_type: Optional[str]
    parent_item_id: Optional[int]
    file_exists: bool
    detected: bool

def connect_to_zotero_db():
    """Connect to Zotero database read-only"""
    conn = sqlite3.connect(f"file:{ZOTERO_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn

def categorize_path(path: Optional[str]) -> Dict[str, bool]:
    """Categorize a path by format"""
    if not path:
        return {'null_or_empty': True}

    categories = {
        'with_storage_prefix': path.startswith('storage:'),
        'http_url': path.startswith('http://') or path.startswith('https://'),
        'absolute_file_path': path.startswith('/') or (len(path) > 1 and path[1] == ':'),  # Unix or Windows
        'without_storage_prefix': False,
        'with_forward_slash': False
    }

    # Check for storage key without prefix
    if not categories['with_storage_prefix'] and not categories['http_url'] and not categories['absolute_file_path']:
        # Pattern: 8-char uppercase alphanumeric followed by : or /
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

    # Format: storage:KEY:filename.pdf
    if path.startswith("storage:"):
        parts = path.split(":")
        if len(parts) >= 3:
            key = parts[1]
            filename = ":".join(parts[2:])
            return storage_base / key / filename

    # Format: KEY:filename.pdf (no prefix)
    elif ":" in path and not path.startswith("/"):
        parts = path.split(":", 1)
        if len(parts) == 2 and re.match(r'^[A-Z0-9]{8}$', parts[0]):
            key, filename = parts
            return storage_base / key / filename

    # Format: KEY/filename.pdf (forward slash)
    elif "/" in path and not path.startswith("/"):
        parts = path.split("/", 1)
        if len(parts) == 2 and re.match(r'^[A-Z0-9]{8}$', parts[0]):
            key, filename = parts
            return storage_base / key / filename

    # Absolute path
    elif path.startswith("/"):
        return Path(path)

    return None

def current_detection_query(conn) -> set:
    """Run the current detection query to see what it catches"""
    query = """
        SELECT ia.itemID
        FROM itemAttachments ia
        WHERE ia.linkMode = 0
        AND ia.path IS NOT NULL
        AND ia.path LIKE 'storage:%'
    """

    cursor = conn.execute(query)
    return set(row['itemID'] for row in cursor.fetchall())

def analyze_attachments(conn) -> tuple[AttachmentStats, List[PathExample], Dict[str, List[str]]]:
    """Comprehensive attachment analysis"""
    stats = AttachmentStats()
    examples = []
    path_patterns = defaultdict(list)

    # Get all attachments
    query = """
        SELECT
            ia.itemID,
            ia.parentItemID,
            ia.linkMode,
            ia.contentType,
            ia.path,
            ia.storageHash
        FROM itemAttachments ia
    """

    cursor = conn.execute(query)
    detected_items = current_detection_query(conn)

    for row in cursor.fetchall():
        stats.total_attachments += 1
        item_id = row['itemID']
        path = row['path']
        link_mode = row['linkMode']
        content_type = row['contentType']
        parent_item_id = row['parentItemID']

        # Categorize path
        path_cats = categorize_path(path)
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
            stats.linkmode_0_stored += 1
        elif link_mode == 1:
            stats.linkmode_1_linked += 1
        elif link_mode == 2:
            stats.linkmode_2_weblink += 1
        elif link_mode == 3:
            stats.linkmode_3_relative += 1
        else:
            stats.linkmode_other += 1

        # Parent relationship
        if parent_item_id:
            stats.has_parent += 1
            # Check if parent exists
            parent_check = conn.execute(
                "SELECT itemID FROM items WHERE itemID = ?",
                (parent_item_id,)
            ).fetchone()
            if not parent_check:
                stats.parent_not_found += 1
        else:
            stats.no_parent_orphaned += 1

        # File existence
        resolved_path = resolve_storage_path(path)
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

        # Detection
        detected = item_id in detected_items
        if detected:
            stats.detected_by_current_query += 1
        else:
            stats.not_detected_by_current_query += 1

        # Collect examples
        if len(examples) < 50:  # Limit examples
            examples.append(PathExample(
                item_id=item_id,
                path=path or "",
                link_mode=link_mode,
                content_type=content_type,
                parent_item_id=parent_item_id,
                file_exists=file_exists,
                detected=detected
            ))

        # Collect path patterns
        if path:
            # Extract pattern (first 20 chars or until first variable part)
            pattern = path[:20]
            if item_id not in detected_items and link_mode == 0:
                path_patterns['undetected'].append(path)

    return stats, examples, dict(path_patterns)

def analyze_orphaned_files() -> Dict[str, Any]:
    """Find files in storage directory without database records"""
    storage_path = Path(ZOTERO_STORAGE_PATH)

    if not storage_path.exists():
        return {'error': f'Storage path does not exist: {storage_path}'}

    orphaned_files = []
    total_storage_keys = 0

    for storage_dir in storage_path.iterdir():
        if storage_dir.is_dir() and re.match(r'^[A-Z0-9]{8}$', storage_dir.name):
            total_storage_keys += 1
            # Check if this storage key is in database
            # (This is a simplified check - full check would query database)
            files_in_dir = list(storage_dir.glob('*'))
            if len(files_in_dir) > 0:
                orphaned_files.append({
                    'storage_key': storage_dir.name,
                    'files': [f.name for f in files_in_dir[:5]]  # First 5 files
                })

            if len(orphaned_files) >= 10:  # Limit to first 10 for performance
                break

    return {
        'total_storage_directories': total_storage_keys,
        'sample_directories': orphaned_files[:10]
    }

def generate_report(stats: AttachmentStats, examples: List[PathExample],
                   path_patterns: Dict[str, List[str]], orphaned_files: Dict[str, Any]) -> Dict[str, Any]:
    """Generate comprehensive diagnostic report"""
    report = {
        'timestamp': datetime.now().isoformat(),
        'database_path': ZOTERO_DB_PATH,
        'storage_path': ZOTERO_STORAGE_PATH,

        'summary': {
            'total_attachments': stats.total_attachments,
            'detected_by_current_query': stats.detected_by_current_query,
            'not_detected': stats.not_detected_by_current_query,
            'detection_rate': f"{(stats.detected_by_current_query / stats.total_attachments * 100):.1f}%" if stats.total_attachments > 0 else "0%"
        },

        'path_formats': {
            'with_storage_prefix': stats.with_storage_prefix,
            'without_storage_prefix': stats.without_storage_prefix,
            'with_forward_slash': stats.with_forward_slash,
            'null_or_empty': stats.null_or_empty_path,
            'http_url': stats.http_url_path,
            'absolute_file_path': stats.absolute_file_path
        },

        'link_modes': {
            'linkMode_0_stored': stats.linkmode_0_stored,
            'linkMode_1_linked': stats.linkmode_1_linked,
            'linkMode_2_weblink': stats.linkmode_2_weblink,
            'linkMode_3_relative': stats.linkmode_3_relative,
            'linkMode_other': stats.linkmode_other
        },

        'parent_relationships': {
            'has_parent': stats.has_parent,
            'no_parent_orphaned': stats.no_parent_orphaned,
            'parent_not_found': stats.parent_not_found
        },

        'file_existence': {
            'file_exists': stats.file_exists,
            'file_missing': stats.file_missing,
            'path_unresolvable': stats.path_unresolvable
        },

        'content_types': {
            'pdf': stats.pdf_content_type,
            'html': stats.html_content_type,
            'other': stats.other_content_type,
            'null': stats.null_content_type
        },

        'examples': {
            'detected_samples': [asdict(ex) for ex in examples if ex.detected][:10],
            'not_detected_samples': [asdict(ex) for ex in examples if not ex.detected][:10]
        },

        'undetected_path_patterns': path_patterns.get('undetected', [])[:20],

        'orphaned_files_on_disk': orphaned_files
    }

    return report

def print_summary(report: Dict[str, Any]):
    """Print human-readable summary to console"""
    print("\n" + "="*70)
    print("📊 ZOTERO ATTACHMENT DETECTION DIAGNOSTIC REPORT")
    print("="*70)

    summary = report['summary']
    print(f"\n🔍 DETECTION SUMMARY:")
    print(f"  Total attachments in database: {summary['total_attachments']:,}")
    print(f"  Detected by current query:     {summary['detected_by_current_query']:,}")
    print(f"  NOT detected:                  {summary['not_detected']:,}")
    print(f"  Detection rate:                {summary['detection_rate']}")

    print(f"\n📁 PATH FORMATS:")
    path_formats = report['path_formats']
    for key, value in path_formats.items():
        if value > 0:
            print(f"  {key.replace('_', ' ').title():30s}: {value:,}")

    print(f"\n🔗 LINK MODES:")
    link_modes = report['link_modes']
    for key, value in link_modes.items():
        if value > 0:
            print(f"  {key.replace('_', ' '):30s}: {value:,}")

    print(f"\n👥 PARENT RELATIONSHIPS:")
    parents = report['parent_relationships']
    for key, value in parents.items():
        if value > 0:
            print(f"  {key.replace('_', ' ').title():30s}: {value:,}")

    print(f"\n💾 FILE EXISTENCE:")
    files = report['file_existence']
    for key, value in files.items():
        if value > 0:
            status = "✅" if key == 'file_exists' else "❌"
            print(f"  {status} {key.replace('_', ' ').title():28s}: {value:,}")

    print(f"\n📄 CONTENT TYPES:")
    content_types = report['content_types']
    for key, value in content_types.items():
        if value > 0:
            print(f"  {key.upper():30s}: {value:,}")

    # Show examples of undetected items
    not_detected = report['examples']['not_detected_samples']
    if not_detected:
        print(f"\n⚠️  EXAMPLES OF UNDETECTED ATTACHMENTS:")
        for ex in not_detected[:5]:
            print(f"\n  Item ID: {ex['item_id']}")
            print(f"    Path: {ex['path'][:70]}{'...' if len(ex['path']) > 70 else ''}")
            print(f"    LinkMode: {ex['link_mode']}")
            print(f"    ContentType: {ex['content_type'] or 'NULL'}")
            print(f"    Parent: {ex['parent_item_id'] or 'ORPHANED'}")
            print(f"    File exists: {'✅' if ex['file_exists'] else '❌'}")

    # Show undetected path patterns
    patterns = report.get('undetected_path_patterns', [])
    if patterns:
        print(f"\n🔍 SAMPLE UNDETECTED PATH PATTERNS:")
        for path in patterns[:10]:
            print(f"  {path}")

    print(f"\n{'='*70}")
    print(f"📁 Report saved to: {OUTPUT_FILE}")
    print(f"{'='*70}\n")

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Diagnose Zotero attachment detection issues")
    parser.add_argument('--json-only', action='store_true', help='Output JSON only, skip console summary')
    args = parser.parse_args()

    try:
        print("Connecting to Zotero database...")
        conn = connect_to_zotero_db()

        print("Analyzing attachments...")
        stats, examples, path_patterns = analyze_attachments(conn)

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

        conn.close()

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0

if __name__ == "__main__":
    exit(main())
