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
import json
import logging
import requests

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:8420"
PROTOCOL_VERSION = "2025-03-26"


class DevonthinkMCPError(Exception):
    """Raised when an MCP call fails (transport error or tool/RPC error)."""


class DevonthinkMCP:
    def __init__(self, url=None, token=None, timeout=60):
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
