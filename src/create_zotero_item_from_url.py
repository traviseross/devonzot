#!/usr/bin/env python3
"""Create a Zotero item from a URL or identifier (DOI/ISBN/PMID/arXiv).

Uses the Zotero translation server for rich metadata extraction,
the same way the Zotero browser connector does.

Usage:
    python create_zotero_item_from_url.py <url_or_identifier>
    python create_zotero_item_from_url.py https://example.com/article
    python create_zotero_item_from_url.py 10.1234/example       # DOI
    python create_zotero_item_from_url.py 978-0-123456-78-9     # ISBN
"""
import json
import os
import re
import sys

from dotenv import load_dotenv
from zotero_api_client import ZoteroAPIClient

load_dotenv('/Users/travisross/DEVONzot/.env')

ZOTERO_API_KEY = os.environ.get('ZOTERO_API_KEY')
ZOTERO_USER_ID = os.environ.get('ZOTERO_USER_ID')
TRANSLATION_SERVER_URL = os.environ.get('TRANSLATION_SERVER_URL')
TRANSLATION_TIMEOUT = float(os.environ.get('TRANSLATION_TIMEOUT', 30))

if not ZOTERO_API_KEY or not ZOTERO_USER_ID:
    print('ZOTERO_API_KEY or ZOTERO_USER_ID not set in environment')
    sys.exit(1)

if len(sys.argv) < 2:
    print('Usage: create_zotero_item_from_url.py <url_or_identifier>')
    print('  Accepts URLs, DOIs, ISBNs, PMIDs, or arXiv IDs')
    sys.exit(1)

input_val = sys.argv[1]

kwargs = {}
if TRANSLATION_SERVER_URL:
    kwargs['translation_server_url'] = TRANSLATION_SERVER_URL
kwargs['translation_timeout'] = TRANSLATION_TIMEOUT

client = ZoteroAPIClient(ZOTERO_API_KEY, ZOTERO_USER_ID, **kwargs)

# Detect whether input is a URL or an identifier
is_url = input_val.startswith('http://') or input_val.startswith('https://')

if is_url:
    print(f"Creating Zotero item from URL: {input_val}")
    result = client.create_item_from_url(input_val)
else:
    print(f"Creating Zotero item from identifier: {input_val}")
    result = client.create_item_from_identifier(input_val)

if not result:
    print('No result returned from Zotero API')
    sys.exit(2)

# Show translated metadata summary
translated = result.get('_translated_metadata')
if translated:
    print(f"\n--- Translation Server Metadata ---")
    print(f"  Type:        {translated.get('itemType', '?')}")
    print(f"  Title:       {translated.get('title', '?')}")
    creators = translated.get('creators', [])
    if creators:
        names = [f"{c.get('firstName', '')} {c.get('lastName', '')}".strip() for c in creators]
        print(f"  Authors:     {', '.join(names)}")
    print(f"  Date:        {translated.get('date', '?')}")
    pub = (translated.get('publicationTitle') or translated.get('blogTitle')
           or translated.get('websiteTitle') or '')
    if pub:
        print(f"  Publication: {pub}")
    abstract = translated.get('abstractNote', '')
    if abstract:
        print(f"  Abstract:    {abstract[:120]}{'...' if len(abstract) > 120 else ''}")
    print()
else:
    print("\n(Translation server unavailable — created bare item)\n")

# Show full API response
print("--- Full API Response ---")
# Remove internal key before display
display = {k: v for k, v in result.items() if not k.startswith('_')}
print(json.dumps(display, indent=2))
