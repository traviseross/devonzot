# Zotero Web Endpoint Build Handoff

**Target server**: 192.168.1.20 (gateway)
**Service name**: zot.travisross.com/add
**Build status**: Ready for implementation
**Audience**: Backend engineers building the endpoint service (NOT iMac-specific)

This document contains everything needed to build the web endpoint from scratch. Follow it sequentially.

---

## Architecture Overview

```
Phone/Laptop/Conference
    │
    ▼
Caddy (192.168.1.20:443, TLS + forward auth)
    │
    ▼
Flask App (Docker container, port 5000)
    │
    ├─► Translation Server (Docker container, port 1969)
    │   GET /primaws/rest/pub/pnxs
    │
    ├─► Zotero API (https://api.zotero.org)
    │   Search for duplicates, create items
    │
    └─► Primo API (alliance-georgefox.primo.exlibrisgroup.com)
        Check institutional access (2-3s timeout, optional)
```

The endpoint runs on port 5000 internally. Caddy proxies and provides TLS/auth. Translation server is internal (port 1969, Docker network only).

---

## Endpoint Behavior

**GET** `/add?q=<identifier>`

Returns 201 on success, 400/500 on error. All responses are JSON.

### Success Response (201)

```json
{
  "status": "created",
  "title": "A Comprehensive Review of Modern Approaches",
  "item_key": "ABC12345",
  "item_type": "journalArticle",
  "zotero_url": "https://www.zotero.org/users/{user_id}/items/ABC12345",
  "identifier": "10.1080/17449359.2025.2588118",
  "identifier_type": "doi",
  "access": {
    "available": true,
    "source": "Taylor & Francis Journals",
    "primo_url": "https://alliance-georgefox.primo.exlibrisgroup.com/discovery/search?vid=01ALLIANCE_GFOX:GFOX&query=doi,exact,10.1080/17449359.2025.2588118"
  }
}
```

### Duplicate Response (200)

```json
{
  "status": "duplicate",
  "title": "A Comprehensive Review of Modern Approaches",
  "item_key": "XYZ67890",
  "item_type": "journalArticle",
  "zotero_url": "https://www.zotero.org/users/{user_id}/items/XYZ67890",
  "identifier": "10.1080/17449359.2025.2588118",
  "identifier_type": "doi",
  "message": "Item with this DOI already exists in your Zotero library"
}
```

### Error Response (400/500)

```json
{
  "status": "error",
  "message": "Translation server returned no results for this identifier",
  "identifier": "10.1080/invalid.doi",
  "identifier_type": "doi"
}
```

### Primo Timeout (201 with partial data)

If Primo times out or fails, item still gets created. Access field reflects the failure:

```json
{
  "status": "created",
  "title": "...",
  "item_key": "...",
  "access": {
    "available": null,
    "source": null,
    "primo_url": null
  }
}
```

---

## Directory Structure

Create this in `/opt/zotero-endpoint/` or equivalent on the gateway:

```
endpoint/
├── app.py                  # Flask application (main entry point)
├── zotero_client.py        # Zotero API client (extracted, portable)
├── primo_client.py         # Primo availability checker
├── identifier.py           # Input type detection
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container build specification
├── docker-compose.yml      # Multi-container orchestration
├── .env.example            # Environment variables template
├── .gitignore              # Version control exclusions
└── README.md               # Build and deployment instructions
```

---

## Input Type Detection (identifier.py)

The `q` parameter can be a DOI, ISBN, PMID, arXiv ID, or URL. Detect as follows:

### Regex Patterns

```python
# DOI: starts with "10." followed by dot and suffix
DOI_PATTERN = r'^10\.\S+/\S+$'

# ISBN: 10 or 13 digits with optional hyphens
ISBN_PATTERN = r'^(?:ISBN(?:-1[03])?:?\s*)?(?=[-0-9X]{10}(?:[-0-9X]{3})?(?:[-0-9X]|$))(?:97[89])?[-0-9]{1,5}?[-0-9]+[-0-9X]$'

# PMID: numeric identifier
PMID_PATTERN = r'^\d{8,}$'

# arXiv ID: YYMM.NNNNN or YYMMNNN (with optional version)
ARXIV_PATTERN = r'^\d{4}\.\d{4,5}(?:v\d+)?$|^[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?$'

# URL: http(s) protocol
URL_PATTERN = r'^https?://'
```

### Detection Logic

```python
def detect_identifier_type(q: str) -> tuple[str, str]:
    """
    Returns (identifier_type, normalized_identifier)
    Types: 'doi', 'isbn', 'pmid', 'arxiv', 'url'
    """
    q = q.strip()

    if re.match(URL_PATTERN, q):
        return ('url', q)
    if re.match(DOI_PATTERN, q):
        return ('doi', q)
    if re.match(ISBN_PATTERN, q):
        return ('isbn', q)
    if re.match(PMID_PATTERN, q):
        return ('pmid', q)
    if re.match(ARXIV_PATTERN, q):
        return ('arxiv', q)

    # Default to URL if it looks URL-like but missing protocol
    if '/' in q or '.' in q:
        return ('url', f'https://{q}')

    raise ValueError(f'Could not classify input: {q}')
```

---

## Zotero API Client (zotero_client.py)

Extract these classes/methods from `/Users/travisross/DEVONzot/src/zotero_api_client.py`:

### Classes to Keep

- `ZoteroAPIClient` — entire class

### Strip Out

- Imports: `from devonzot_service import *` (ZoteroItem, ZoteroAttachment dataclasses)
- Methods: anything DEVONthink-specific (none in the API client itself)
- Hardcoded paths: none in the API client
- The conversion helpers `_api_item_to_zotero_item()` and `_api_item_to_zotero_attachment()` reference DEVONzot dataclasses — remove or replace with dict returns

### Methods to Include

**Initialization & Helpers:**
- `__init__(api_key, user_id, api_base, api_version, rate_limit_delay, translation_server_url, translation_timeout)`
- `_rate_limit(seconds=None)`
- `_safe_request(method, url, **kwargs)` — handles rate limiting, backoff, retry-after
- `_translation_headers()` — headers for translation server (NOT Zotero auth)

**Translation Server:**
- `translate_url(url)` — POST to `/web?single=1`, returns first item dict or None
- `translate_identifier(identifier)` — POST to `/search`, returns first item dict or None

**Item Creation:**
- `create_item_from_url(url)` — full workflow: translate, create, return raw dict with `_translated_metadata`
- `create_item_from_identifier(identifier)` — full workflow: translate, create, return raw dict with `_translated_metadata`

**Item Search:**
- `_get_all_items_paginated(params)` — fetch all items matching params (needed for duplicate detection)

**Pagination Helper:**
- Used by `_get_all_items_paginated()` for handling API max of 100 items per page

### Key Implementation Details

Translation server endpoints (from investigation):

```
POST https://t0guvf0w17.execute-api.us-east-1.amazonaws.com/Prod/web?single=1
    Content-Type: text/plain
    Body: <URL>
    → Returns: [{ "itemType": "journalArticle", "title": "...", ... }]

POST https://t0guvf0w17.execute-api.us-east-1.amazonaws.com/Prod/search
    Content-Type: text/plain
    Body: <DOI|ISBN|PMID|arXiv>
    → Returns: [{ "itemType": "journalArticle", "title": "...", ... }]
```

Rate limiting: 1.0 second delay between API calls by default. Respect `Backoff` header and `Retry-After` (429/503).

---

## Primo API Client (primo_client.py)

### Endpoint

```
https://alliance-georgefox.primo.exlibrisgroup.com/primaws/rest/pub/pnxs
```

### Query Format

```
GET ?q=doi,exact,{DOI}&inst=01ALLIANCE_GFOX&vid=01ALLIANCE_GFOX:GFOX&tab=Everything&scope=MyInst_and_CI
```

Example:
```
GET https://alliance-georgefox.primo.exlibrisgroup.com/primaws/rest/pub/pnxs?q=doi,exact,10.1080/17449359.2025.2588118&inst=01ALLIANCE_GFOX&vid=01ALLIANCE_GFOX:GFOX&tab=Everything&scope=MyInst_and_CI
```

### Response Schema

```json
{
  "searchInstitutionFilterCards": [ ... ],
  "queryTerms": [ ... ],
  "docs": [
    {
      "search": {
        "fulltext": ["true"],
        "scope": ["MyInst_and_CI"]
      },
      "display": {
        "source": ["Taylor & Francis Journals"],
        "identifier": ["10.1080/17449359.2025.2588118"],
        "lds50": ["peer_reviewed"]
      },
      "delivery": {
        "availability": ["fulltext"],
        "almaOpenurl": ["https://alliance-georgefox.alma.exlibrisgroup.com/view/uresolver/01ALLIANCE_GFOX/openurl?..."]
      },
      "addata": {
        "doi": "10.1080/17449359.2025.2588118",
        "aulast": "Smith",
        "aufirst": "John"
      }
    }
  ]
}
```

### Implementation Notes

- **Timeout**: 2-3 seconds (network timeout, not request timeout)
- **Failure behavior**: If Primo fails or times out, still return 201 with `access.available: null`
- **Only called when**: A DOI is available (from input identifier detection or from translated metadata)
- **URL construction**: `https://alliance-georgefox.primo.exlibrisgroup.com/primaws/rest/pub/pnxs?q=doi,exact,{DOI}&inst=01ALLIANCE_GFOX&vid=01ALLIANCE_GFOX:GFOX&tab=Everything&scope=MyInst_and_CI`
- **Extract from response**: `docs[0].search.fulltext[0]` (true/false), `docs[0].display.source[0]` (source name)

---

## Flask Application (app.py)

### Single Route

```python
@app.route('/add', methods=['GET'])
def add_item():
    """Create a Zotero item from an identifier or URL."""
    q = request.args.get('q', '').strip()

    if not q:
        return jsonify({'status': 'error', 'message': 'Missing q parameter'}), 400

    # Flow:
    # 1. Detect identifier type
    # 2. Search Zotero for duplicate (DOI only)
    # 3. Resolve metadata via translation server
    # 4. Create item in Zotero
    # 5. Query Primo for access (if DOI available)
    # 6. Return JSON response
```

### Main Flow Logic

```
Input: q=<identifier or URL>
  │
  ├─ Detect type (DOI/ISBN/PMID/arXiv/URL)
  │
  ├─ IF DOI: Search Zotero for q?=doi,exact,{DOI}
  │   IF found: Return 200 with status='duplicate'
  │
  ├─ Resolve metadata:
  │   IF URL: client.create_item_from_url(q)
  │   ELSE: client.create_item_from_identifier(q)
  │   IF no result: Return 400 error
  │
  ├─ Extract DOI from:
  │   - Original input (if DOI)
  │   - Translated metadata (from response['data']['DOI'])
  │
  ├─ IF DOI available: Query Primo (2-3s timeout, fail gracefully)
  │
  └─ Return 201 with full response
```

### Environment Variables

```bash
# Zotero
ZOTERO_API_KEY=<your_api_key>
ZOTERO_USER_ID=<your_user_id>

# Translation server (internal Docker, port 1969)
TRANSLATION_SERVER_URL=http://zot-translation:1969

# Primo
PRIMO_BASE_URL=https://alliance-georgefox.primo.exlibrisgroup.com
PRIMO_INST=01ALLIANCE_GFOX
PRIMO_VID=01ALLIANCE_GFOX:GFOX

# Flask
FLASK_ENV=production
FLASK_DEBUG=false
```

---

## Docker Setup

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app.py zotero_client.py primo_client.py identifier.py .

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:5000/health')"

# Run with gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "30", "app:app"]
```

### docker-compose.yml

```yaml
version: '3.8'

services:
  zot-endpoint:
    build: .
    ports:
      - "5000:5000"
    environment:
      ZOTERO_API_KEY: ${ZOTERO_API_KEY}
      ZOTERO_USER_ID: ${ZOTERO_USER_ID}
      TRANSLATION_SERVER_URL: http://zot-translation:1969
      PRIMO_BASE_URL: https://alliance-georgefox.primo.exlibrisgroup.com
      PRIMO_INST: 01ALLIANCE_GFOX
      PRIMO_VID: 01ALLIANCE_GFOX:GFOX
    depends_on:
      zot-translation:
        condition: service_healthy
    networks:
      - zotero-network
    restart: unless-stopped

  zot-translation:
    image: zotero/translation-server:latest
    ports:
      - "1969:1969"
    networks:
      - zotero-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:1969/"]
      interval: 30s
      timeout: 3s
      retries: 3
    restart: unless-stopped

networks:
  zotero-network:
    driver: bridge
```

### .env.example

```bash
# Zotero credentials (from your account)
ZOTERO_API_KEY=your_api_key_here
ZOTERO_USER_ID=your_user_id_here

# Translation server (Docker internal)
TRANSLATION_SERVER_URL=http://zot-translation:1969

# Primo discovery
PRIMO_BASE_URL=https://alliance-georgefox.primo.exlibrisgroup.com
PRIMO_INST=01ALLIANCE_GFOX
PRIMO_VID=01ALLIANCE_GFOX:GFOX

# Flask
FLASK_ENV=production
FLASK_DEBUG=false
```

### requirements.txt

```
flask==2.3.3
gunicorn==21.2.0
requests==2.31.0
python-dotenv==1.0.0
```

---

## Caddy Configuration Snippet

Add this to your Caddy config (reverse_proxy to Docker container):

```caddy
zot.travisross.com {
    # Forward auth (assumes existing OAuth guard)
    forward_auth localhost:9091 {
        uri /auth
        copy_headers Remote-User Remote-Groups
    }

    # Reverse proxy to Flask endpoint
    reverse_proxy localhost:5000 {
        header_uri /add /add
        timeout 10s
    }
}
```

This assumes:
- Caddy is already running on the gateway
- OAuth guard is on localhost:9091
- Docker is mapping port 5000 from the container to the host

If using Docker Compose on the same server, use the container name and internal Docker network instead:

```caddy
zot.travisross.com {
    forward_auth localhost:9091 {
        uri /auth
        copy_headers Remote-User Remote-Groups
    }

    reverse_proxy http://zot-endpoint:5000 {
        timeout 10s
    }
}
```

---

## Build & Deployment

### On the Gateway Server

1. **Clone the repository** (or copy files):
   ```bash
   mkdir -p /opt/zotero-endpoint
   cd /opt/zotero-endpoint
   ```

2. **Create `.env` from `.env.example`**:
   ```bash
   cp .env.example .env
   # Edit .env with your Zotero API key and user ID
   ```

3. **Build and start containers**:
   ```bash
   docker compose build
   docker compose up -d
   ```

4. **Verify services**:
   ```bash
   docker compose ps
   curl http://localhost:5000/health
   curl http://localhost:1969/
   ```

5. **Test the endpoint**:
   ```bash
   curl "http://localhost:5000/add?q=10.1080/17449359.2025.2588118"
   ```

6. **Update Caddy config** and reload

### Health Check Endpoints

- **Flask**: `GET /health` → 200 OK
- **Translation server**: `GET /` → 200 OK (translators check)

---

## Testing Checklist

- [ ] **Identifier detection**: Test DOI, ISBN, PMID, arXiv, URL inputs
- [ ] **Translation server**: Verify `/search` and `/web?single=1` work
- [ ] **Item creation**: Submit a real DOI, verify item appears in Zotero
- [ ] **Duplicate detection**: Submit same DOI twice, verify second returns `status: duplicate`
- [ ] **Primo integration**: Submit a DOI with institutional access, verify `access.available: true`
- [ ] **Primo timeout**: Verify item still creates if Primo is slow/unreachable
- [ ] **Error handling**: Submit garbage input, verify graceful 400 response
- [ ] **Docker build**: `docker compose up` succeeds, containers stay running
- [ ] **Caddy routing**: `https://zot.travisross.com/add?q=<DOI>` works from external machine
- [ ] **iOS Shortcut**: Build test shortcut that sends a DOI and parses the JSON response

---

## Monitoring & Logs

```bash
# View logs
docker compose logs -f zot-endpoint
docker compose logs -f zot-translation

# Check container health
docker compose ps

# Test translation server directly
curl -X POST http://localhost:1969/search \
  -H "Content-Type: text/plain" \
  -d "10.1080/17449359.2025.2588118"

# Test Zotero API
curl -H "Authorization: Bearer YOUR_API_KEY" \
  "https://api.zotero.org/users/YOUR_USER_ID/items?q=doi,exact,10.1080/17449359.2025.2588118"
```

---

## Notes for Builders

### Rate Limiting

The Zotero API enforces rate limits. The client includes backoff logic:
- 1 second delay between requests by default
- Respects `Retry-After` header (429/503 responses)
- Exponential backoff up to 60 seconds

### Translation Server Reliability

The translation server (Docker image `zotero/translation-server:latest`) is self-hosted to avoid external dependencies. It uses CrossRef for metadata resolution and falls back gracefully on publisher pages (which are often blocked by Cloudflare).

### Primo Graceful Degradation

If Primo is unavailable, the endpoint still creates the item and returns `access: null`. This is intentional — the primary goal is item creation; access information is supplementary.

### Duplicate Detection

Currently checks only by DOI. If you need to check by other identifiers (ISBN, PMID), add those to the `_get_all_items_paginated()` query.

---

## What's NOT Included

- **PDF attachment**: Translation server returns no PDF URLs. Use Zotero's built-in "Find Available PDF" or manual attachment on the iMac.
- **EZproxy integration**: Campus IP auto-auth only works for in-browser requests. Zotero still faces Cloudflare blocks. Not useful for automated requests.
- **Custom PDF resolver**: Could be added later via Zotero Desktop's `findPDFs.resolvers` config.
- **Browser connector**: Requires a real browser. This endpoint is headless HTTP only.

---

## Contact & References

- **Zotero API v3 docs**: https://www.zotero.org/support/dev/web_api/v3/start
- **Translation server**: https://github.com/zotero/translation-server
- **Primo REST API**: https://developers.exlibrisgroup.com/primo/integrations/rest-api/
- **George Fox Primo instance**: https://alliance-georgefox.primo.exlibrisgroup.com

---

**Document version**: 1.0
**Last updated**: 2026-02-24
**Status**: Ready for implementation
