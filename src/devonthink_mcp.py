"""DEVONthink 4 MCP client.

Talks to DEVONthink's built-in MCP server (JSON-RPC 2.0 over HTTP) using a bearer
token. This replaces the legacy AppleScript control path: `import_file` imports a
file and returns its record UUID in a single call, eliminating the fragile
copy-to-Inbox -> wait -> title-search dance and the macOS Automation (TCC)
permission dependency.

Config (env / .env):
    DEVONTHINK_MCP_URL    default "http://localhost:8420"
    DEVONTHINK_MCP_TOKEN  bearer token from DEVONthink > Settings > AI > MCP

Sync client (uses `requests`, matching ZoteroAPIClient's style). The MCP server
runs inside DEVONthink on the same Mac, so calls are local and fast.
"""

import os
import re
import ssl
import json
import socket
import hashlib
import logging
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import certifi
import requests

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:8420"
PROTOCOL_VERSION = "2025-03-26"

_CERT_RE = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)

# id-ad-caIssuers (OID 1.3.6.1.5.5.7.48.2), DER-encoded as it sits inside an
# AuthorityInfoAccess AccessDescription — the marker for "where to fetch my issuer".
_CA_ISSUERS_OID = bytes.fromhex("06082b06010505073002")


def _aia_ca_issuers_url(der):
    """Pull the CA-Issuers (AIA) URL out of a DER certificate without a crypto lib.

    Each AccessDescription in the AuthorityInfoAccess extension is the access-method
    OID followed by the access-location GeneralName; for CA-Issuers that location is a
    [6] uniformResourceIdentifier (tag 0x86) holding the HTTP URL of the issuing
    intermediate. We scan for the caIssuers OID and read the short-form IA5String that
    follows. Returns None if absent (or a long-form length we don't need to handle —
    LE's AIA URLs are ~25 bytes).
    """
    i = der.find(_CA_ISSUERS_OID)
    if i < 0:
        return None
    j = i + len(_CA_ISSUERS_OID)
    if j + 1 >= len(der) or der[j] != 0x86:      # expect [6] URI, short-form length
        return None
    length = der[j + 1]
    if length & 0x80 or j + 2 + length > len(der):
        return None
    return der[j + 2 : j + 2 + length].decode("ascii", "replace")


def _fetch_leaf_intermediate(url, timeout=8):
    """Follow the served leaf's own AIA pointer to fetch its issuing intermediate, live.

    DEVONthink 4 serves its leaf certificate only, and Let's Encrypt rotates which
    intermediate signs each renewal — so any *stored* intermediate eventually goes
    stale (this is the whole failure mode). Instead we peek at whatever leaf the
    endpoint is serving right now (an unverified read — we inspect the cert, we don't
    trust the channel), follow that leaf's AIA URL to fetch its intermediate straight
    from the CA, and cache it keyed by URL. Intermediates are stable per URL, so a
    cache hit is never stale; a rotation changes the URL and misses. Returns PEM text,
    or None on any failure (caller falls back to certifi + the pinned chain).
    """
    p = urlparse(url)
    host, port = p.hostname, (p.port or 443)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE                # we only need to READ the leaf
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    aia = _aia_ca_issuers_url(der)
    if not aia:
        return None
    cache = (Path(tempfile.gettempdir())
             / f"devonzot_le_int_{hashlib.sha256(aia.encode()).hexdigest()[:16]}.pem")
    if cache.exists():
        return cache.read_text()
    with urlopen(aia, timeout=timeout) as r:       # AIA host comes from the cert itself
        pem = ssl.DER_cert_to_PEM_cert(r.read())
    cache.write_text(pem)
    return pem


def _build_ca_bundle(cacert_path, url=None, timeout=8):
    """Return a path to a verify bundle for a leaf-only TLS endpoint.

    DEVONthink's MCP server presents its leaf certificate only (no intermediate),
    so `requests` can't build a chain from the handshake alone ('unable to get
    issuer certificate'). We trust the permanent ISRG roots (via certifi) and
    supply the missing intermediate ourselves. The authoritative source is the
    *live* leaf's own AIA pointer (see _fetch_leaf_intermediate): whatever cert the
    Mac is serving — freshly renewed or months stale, signed by any LE intermediate
    — we follow the leaf to its issuer and fetch it, so there is nothing stored to
    go stale. The intermediate(s) baked into the pinned fullchain are folded in too,
    as a fallback for when the endpoint isn't reachable at build time. The output
    file is named by a content hash so we don't churn or leak temp files.
    """
    intermediates = []
    if cacert_path and Path(cacert_path).exists():
        certs = _CERT_RE.findall(Path(cacert_path).read_text())
        intermediates = certs[1:] if len(certs) > 1 else certs  # drop the leaf
    if url and url.lower().startswith("https"):
        try:
            live = _fetch_leaf_intermediate(url, timeout)
            if live and live.strip() not in intermediates:
                intermediates.append(live.strip())
        except Exception as e:
            logger.debug("live AIA intermediate fetch failed for %s: %s", url, e)
    bundle = certifi.contents().rstrip() + "\n" + "\n".join(intermediates) + "\n"
    digest = hashlib.sha256(bundle.encode()).hexdigest()[:16]
    out = Path(tempfile.gettempdir()) / f"devonzot_ca_{digest}.pem"
    if not out.exists():
        out.write_text(bundle)
    return str(out)


class DevonthinkMCPError(Exception):
    """Raised when an MCP call fails (transport error or tool/RPC error)."""


class DevonthinkMCP:
    def __init__(self, url=None, token=None, timeout=60, cacert=None):
        self.url = url or os.environ.get("DEVONTHINK_MCP_URL", DEFAULT_URL)
        self.token = token or os.environ.get("DEVONTHINK_MCP_TOKEN", "")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"
        # TLS: a remote LAN endpoint delivers a fullchain.pem (leaf-only handshake);
        # build a proper verify bundle from it. Local http/localhost needs nothing.
        cacert = cacert or os.environ.get("DEVONTHINK_MCP_CACERT")
        if cacert or self.url.lower().startswith("https"):
            self.session.verify = _build_ca_bundle(cacert, self.url)
        self._id = 0
        self._session_id = None
        self._initialized = False
        self._db_uuid_cache = None

    # ---- low-level JSON-RPC ----

    def _post(self, payload):
        headers = {}
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        try:
            resp = self.session.post(
                self.url, data=json.dumps(payload), headers=headers, timeout=self.timeout
            )
        except requests.RequestException as e:
            raise DevonthinkMCPError(f"MCP transport error: {e}") from e
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        return self._parse(resp.text)

    @staticmethod
    def _parse(text):
        # DEVONthink replies with plain JSON, but tolerate SSE framing too.
        data_lines = [l[5:].strip() for l in text.splitlines() if l.startswith("data:")]
        if data_lines:
            text = data_lines[-1]
        text = (text or "").strip()
        if not text:
            return None
        return json.loads(text)

    def _next_id(self):
        self._id += 1
        return self._id

    def _ensure_init(self):
        if self._initialized:
            return
        self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "devonzot", "version": "1.0"},
            },
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True

    def _tool(self, name, arguments=None):
        """Call a tool; return its parsed content (dict/list if JSON, else text)."""
        self._ensure_init()
        d = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        if d and "error" in d:
            raise DevonthinkMCPError(f"{name}: {d['error'].get('message', d['error'])}")
        result = (d or {}).get("result", {})
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        text = "\n".join(texts).strip()
        if result.get("isError"):
            raise DevonthinkMCPError(f"{name} failed: {text or result}")
        if not text:
            return result
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text

    # ---- high-level API ----

    def is_running(self):
        """True if DEVONthink is reachable (the MCP server runs inside the app)."""
        try:
            self._tool("is_running")
            return True
        except DevonthinkMCPError as e:
            logger.debug(f"DEVONthink not reachable via MCP: {e}")
            return False

    def get_databases(self):
        result = self._tool("get_databases")
        return result if isinstance(result, list) else result.get("databases", result)

    def _databases_by_name(self):
        if self._db_uuid_cache is None:
            self._db_uuid_cache = {db.get("name"): db for db in self.get_databases()}
        return self._db_uuid_cache

    def database_uuid(self, name):
        """Resolve a database name to its UUID (cached)."""
        db = self._databases_by_name().get(name)
        return db.get("uuid") if db else None

    def database_root_uuid(self, name):
        """Resolve a database name to its root group UUID (a move destination)."""
        db = self._databases_by_name().get(name)
        return db.get("rootUUID") if db else None

    def import_file(self, path, database_uuid=None, destination=None, mode=None):
        """Import a file into DEVONthink. Returns the new record dict (incl. 'uuid')."""
        args = {"path": path}
        if database_uuid:
            args["database_uuid"] = database_uuid
        if destination:
            args["destination"] = destination
        if mode:
            args["mode"] = mode
        return self._tool("import_file", args)

    def update_record(self, uuid, **fields):
        """Set name/comment/url/tags/etc. on a record in one call."""
        args = {"uuid": uuid}
        args.update({k: v for k, v in fields.items() if v is not None})
        return self._tool("update_record", args)

    def set_record_tags(self, uuid, tags, mode=None):
        args = {"uuid": uuid, "tags": tags}
        if mode:
            args["mode"] = mode
        return self._tool("set_record_tags", args)

    def lookup_records(self, **criteria):
        """Find existing records by name/filename/path/url/comment (dedup guard)."""
        return self._tool("lookup_records", {k: v for k, v in criteria.items() if v is not None})

    def search_records(self, query, database_uuid=None, limit=None, group_uuid=None):
        """Full DEVONthink search (query syntax, e.g. 'name:foo name:bar')."""
        args = {"query": query}
        if database_uuid:
            args["database_uuid"] = database_uuid
        if limit:
            args["limit"] = limit
        if group_uuid:
            args["group_uuid"] = group_uuid
        return self._tool("search_records", args)

    def get_record_properties(self, uuid):
        return self._tool("get_record_properties", {"uuid": uuid})

    def move_record(self, uuid, destination, database_uuid=None):
        """Move record(s) to a destination group (UUID or location path)."""
        args = {"destination": destination}
        if isinstance(uuid, (list, tuple)):
            args["uuids"] = list(uuid)
        else:
            args["uuid"] = uuid
        if database_uuid:
            args["database_uuid"] = database_uuid
        return self._tool("move_record", args)

    def trash_record(self, uuids):
        if isinstance(uuids, str):
            uuids = [uuids]
        return self._tool("trash_record", {"uuids": uuids})

    def extract_record_content(self, uuid: str, max_chars: int = 4000) -> str:
        """Extract textual content from a record for AI consumption.

        Works for PDFs (including scanned/OCR'd), HTML, RTF, Markdown.
        Returns pages joined by newlines, truncated to max_chars.
        Use this instead of get_record_text for PDFs — get_record_text only
        works for plain-text records (RTF, Markdown, plain text).
        """
        result = self._tool("extract_record_content", {"uuid": uuid})
        if isinstance(result, list):
            parts = [p.get("text", "") for p in result if isinstance(p, dict)]
            return "\n".join(parts)[:max_chars]
        if isinstance(result, str):
            return result[:max_chars]
        return ""

    def classify_record(self, uuid: str) -> list:
        """Suggest destination groups via DT4's built-in AI, ordered by score.

        Each suggestion: {"uuid": "…", "databaseUUID": "…", "name": "…", "score": 0.87}
        Returns [] when DT4 has no suggestion (sparse library for this doc type).
        """
        result = self._tool("classify_record", {"uuid": uuid})
        if isinstance(result, list):
            return result
        return []

    # ---- content-dedup support (see src/content_dedup.py) ----

    def get_record_duplicates(self, uuid: str) -> list:
        """DEVONthink's content-hash duplicates of a record.

        Returns a list of the *other* records DEVONthink considers byte-identical
        (excludes the queried record itself); [] if none. Note: records excluded
        from AI are filtered out of this result by the MCP server.
        """
        result = self._tool("get_record_duplicates", {"uuid": uuid})
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("results", [])
        return []

    def set_record_custom_metadata(self, uuid: str, metadata: dict, mode: str = "merge"):
        """Set custom metadata fields. mode='merge' overlays without dropping other
        keys. Field identifiers must be alphanumeric (no underscores) — DEVONthink
        auto-creates an unknown-but-valid field on first set.
        """
        return self._tool(
            "set_record_custom_metadata",
            {"uuid": uuid, "metadata": metadata, "mode": mode},
        )

    def get_record_custom_metadata(self, uuid: str) -> dict:
        """Return a record's custom metadata as a {identifier: value} dict."""
        result = self._tool("get_record_custom_metadata", {"uuid": uuid})
        return result if isinstance(result, dict) else {}
