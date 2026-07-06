"""Read Zotero WebDAV storage blobs (the zotdav fast-path source).

For each file attachment, Zotero's WebDAV file sync writes a pair into the store:
  {KEY}.zip   — a zip containing the attachment file(s) under their real names
  {KEY}.prop  — an XML sidecar written *after* the zip (Zotero's write-completion
                sentinel), holding the file mtime and MD5:
                  <properties version="1"><mtime>ms</mtime><hash>md5hex</hash></properties>

The watcher (src/zotdav_watcher.py) triggers on the `.prop`; these helpers turn a
KEY into (filename, extracted bytes, md5) without touching the Zotero API. Pure and
Linux-agnostic — no inotify here, so it is fully unit-testable anywhere.
"""

import hashlib
import zipfile
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree


class ZotdavBlobError(Exception):
    """Raised when a zotdav blob can't be read (missing zip, corrupt prop/zip)."""


def key_for(path) -> str:
    """The attachment KEY is the blob filename stem ({KEY}.prop / {KEY}.zip)."""
    return Path(path).stem


def parse_prop(prop_path) -> Dict[str, object]:
    """Parse a {KEY}.prop sidecar -> {key, mtime, md5}.

    Tolerant of namespace/attribute variation; mtime is int ms (0 if absent), md5 is
    lower-hex or None. Raises ZotdavBlobError on unreadable/!XML content.
    """
    prop_path = Path(prop_path)
    try:
        root = ElementTree.fromstring(prop_path.read_text())
    except (ElementTree.ParseError, OSError) as e:
        raise ZotdavBlobError(f"Unreadable prop {prop_path}: {e}") from e

    def _find(tag):
        # match ignoring any XML namespace on the child element
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] == tag:
                return (el.text or "").strip()
        return None

    mtime_raw = _find("mtime")
    try:
        mtime = int(mtime_raw) if mtime_raw else 0
    except ValueError:
        mtime = 0
    md5 = _find("hash")
    return {"key": key_for(prop_path), "mtime": mtime, "md5": md5.lower() if md5 else None}


def zip_for(prop_path) -> Path:
    """The {KEY}.zip paired with a {KEY}.prop (same dir, same stem)."""
    prop_path = Path(prop_path)
    return prop_path.with_suffix(".zip")


def extract_blob(zip_path, dest_dir) -> Dict[str, object]:
    """Unzip {KEY}.zip into dest_dir and describe the primary attachment file.

    Returns {key, filename, path, files, md5} where `filename`/`path` are the primary
    (largest) member — Zotero web-snapshot attachments contain several files, but a
    file attachment is a single member. `md5` is the MD5 of the primary file's bytes.
    Raises ZotdavBlobError if the zip is missing/corrupt/empty.
    """
    zip_path = Path(zip_path)
    dest_dir = Path(dest_dir)
    if not zip_path.exists():
        raise ZotdavBlobError(f"Missing zip: {zip_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            if not members:
                raise ZotdavBlobError(f"Empty zip: {zip_path}")
            zf.extractall(dest_dir)
            primary = max(members, key=lambda m: m.file_size)
    except zipfile.BadZipFile as e:
        raise ZotdavBlobError(f"Corrupt zip {zip_path}: {e}") from e

    primary_path = dest_dir / primary.filename
    return {
        "key": key_for(zip_path),
        "filename": Path(primary.filename).name,
        "path": str(primary_path),
        "files": [m.filename for m in members],
        "md5": md5_of_file(primary_path),
    }


def md5_of_file(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_pending_keys(zotdav_dir) -> List[str]:
    """KEYs with a complete pair present (both {KEY}.prop and {KEY}.zip) in the dir,
    sorted by .prop mtime (oldest first) so a startup sweep drains in arrival order."""
    zotdav_dir = Path(zotdav_dir)
    if not zotdav_dir.is_dir():
        return []
    keys = []
    for prop in zotdav_dir.glob("*.prop"):
        if prop.with_suffix(".zip").exists():
            keys.append(prop)
    keys.sort(key=lambda p: p.stat().st_mtime)
    return [key_for(p) for p in keys]
