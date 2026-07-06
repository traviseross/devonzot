"""WS1 — network DEVONthink transport: endpoint selection, remote delivery, TLS bundle.

These lock the failover contract (strict priority; None => clean skip, never a false
success) and the scp/import/cleanup sequencing, using fakes — no real Mac or network.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import devonzot_service as dz
from devonzot_service import DEVONthinkMCPInterface, MCPEndpoint, _parse_mcp_endpoints
from devonthink_mcp import _build_ca_bundle


class FakeClient:
    """Stand-in for DevonthinkMCP with just the surface copy_file_to_inbox touches."""
    def __init__(self, running=True, import_raises=False):
        self._running = running
        self.import_raises = import_raises
        self.imported = []

    def is_running(self):
        return self._running

    def database_uuid(self, name):
        return "INBOX-UUID"

    def import_file(self, path, database_uuid=None):
        if self.import_raises:
            from devonthink_mcp import DevonthinkMCPError
            raise DevonthinkMCPError("boom")
        self.imported.append(path)
        return {"uuid": "NEW-UUID"}

    def update_record(self, uuid, **kw):
        pass


def _iface(endpoints):
    iface = DEVONthinkMCPInterface.__new__(DEVONthinkMCPInterface)  # skip __init__/env
    iface.database_name = "Professional"
    iface.endpoints = endpoints
    iface._active_endpoint = None
    iface.mcp = endpoints[0].client
    iface._pending_import_uuid = None
    return iface


def _ep(label, running=True, ssh_host="Mac", import_raises=False):
    return MCPEndpoint(label=label, url=f"https://{label}:8420", ssh_host=ssh_host,
                       client=FakeClient(running=running, import_raises=import_raises))


# ---- endpoint parsing ----

def test_parse_endpoints_per_label(monkeypatch):
    monkeypatch.setenv("DEVONTHINK_MCP_ENDPOINTS", "imac,mbp")
    monkeypatch.setenv("DEVONTHINK_MCP_IMAC_URL", "https://imac:8420")
    monkeypatch.setenv("DEVONTHINK_MCP_IMAC_SSH", "iMac")
    monkeypatch.setenv("DEVONTHINK_MCP_IMAC_CACERT", "/certs/imac.pem")
    monkeypatch.setenv("DEVONTHINK_MCP_MBP_URL", "https://mbp:8420")
    monkeypatch.setenv("DEVONTHINK_MCP_MBP_SSH", "MBP")
    eps = _parse_mcp_endpoints()
    assert [e.label for e in eps] == ["imac", "mbp"]
    assert eps[0].ssh_host == "iMac" and eps[0].cacert == "/certs/imac.pem"
    assert eps[1].ssh_host == "MBP"


def test_parse_endpoints_local_fallback(monkeypatch):
    monkeypatch.delenv("DEVONTHINK_MCP_ENDPOINTS", raising=False)
    eps = _parse_mcp_endpoints()
    assert len(eps) == 1 and eps[0].label == "local" and eps[0].ssh_host is None


# ---- select_endpoint: strict priority; None => skip ----

def test_select_prefers_first_live():
    imac, mbp = _ep("imac"), _ep("mbp")
    iface = _iface([imac, mbp])
    assert iface.select_endpoint() is imac
    assert iface.mcp is imac.client  # downstream calls bind to the chosen Mac


def test_select_fails_over_when_first_down():
    imac, mbp = _ep("imac", running=False), _ep("mbp", running=True)
    iface = _iface([imac, mbp])
    assert iface.select_endpoint() is mbp
    assert iface.mcp is mbp.client


def test_select_none_when_all_down():
    iface = _iface([_ep("imac", running=False), _ep("mbp", running=False)])
    assert iface.select_endpoint() is None
    assert iface._active_endpoint is None


# ---- copy_file_to_inbox: delivery + cleanup contract ----

def test_remote_import_delivers_then_cleans_up(tmp_path, monkeypatch):
    f = tmp_path / "paper.pdf"; f.write_text("x")
    iface = _iface([_ep("imac")])
    calls = {}

    def fake_deliver(ep, p):
        calls["deliver"] = (ep.label, p)
        return "/private/tmp/devonzot/abc/paper.pdf", "/private/tmp/devonzot/abc"

    def fake_cleanup(ep, d):
        calls["cleanup"] = d

    monkeypatch.setattr(iface, "_deliver_to_endpoint", fake_deliver)
    monkeypatch.setattr(iface, "_cleanup_remote", fake_cleanup)

    ok = iface.copy_file_to_inbox(str(f), "Renamed.pdf")
    assert ok is True
    assert iface._pending_import_uuid == "NEW-UUID"
    # import used the REMOTE path, not the local one
    assert iface.mcp.imported == ["/private/tmp/devonzot/abc/paper.pdf"]
    assert calls["deliver"][0] == "imac"
    assert calls["cleanup"] == "/private/tmp/devonzot/abc"


def test_no_endpoint_is_a_clean_skip(tmp_path):
    f = tmp_path / "paper.pdf"; f.write_text("x")
    iface = _iface([_ep("imac", running=False)])
    ok = iface.copy_file_to_inbox(str(f), "Renamed.pdf")
    assert ok is False                       # caller must NOT mark processed
    assert iface._pending_import_uuid is None
    assert iface.mcp.imported == []          # never imported anywhere


def test_cleanup_runs_even_when_import_fails(tmp_path, monkeypatch):
    f = tmp_path / "paper.pdf"; f.write_text("x")
    iface = _iface([_ep("imac", import_raises=True)])
    cleaned = {}
    monkeypatch.setattr(iface, "_deliver_to_endpoint",
                        lambda ep, p: ("/private/tmp/devonzot/abc/paper.pdf", "/private/tmp/devonzot/abc"))
    monkeypatch.setattr(iface, "_cleanup_remote", lambda ep, d: cleaned.setdefault("d", d))
    ok = iface.copy_file_to_inbox(str(f), "Renamed.pdf")
    assert ok is False
    assert cleaned["d"] == "/private/tmp/devonzot/abc"   # temp not leaked on failure


def test_local_endpoint_imports_path_directly(tmp_path):
    f = tmp_path / "paper.pdf"; f.write_text("x")
    iface = _iface([_ep("local", ssh_host=None)])
    ok = iface.copy_file_to_inbox(str(f), "Renamed.pdf")
    assert ok is True
    assert iface.mcp.imported == [str(f)]    # no scp, local path imported as-is


# ---- TLS bundle ----

def test_build_ca_bundle_drops_leaf_keeps_intermediate_and_roots(tmp_path):
    leaf = "-----BEGIN CERTIFICATE-----\nLEAFDATA\n-----END CERTIFICATE-----"
    intermediate = "-----BEGIN CERTIFICATE-----\nINTERMEDIATEDATA\n-----END CERTIFICATE-----"
    full = tmp_path / "fullchain.pem"
    full.write_text(leaf + "\n" + intermediate + "\n")
    bundle_path = _build_ca_bundle(str(full))
    content = Path(bundle_path).read_text()
    assert "INTERMEDIATEDATA" in content      # intermediate supplied (server sends leaf-only)
    assert "LEAFDATA" not in content          # leaf dropped
    assert "BEGIN CERTIFICATE" in content and len(content) > 1000  # certifi roots included
