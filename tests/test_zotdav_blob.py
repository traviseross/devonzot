"""Tests for the zotdav blob reader (parse .prop, unzip .zip) against synthetic pairs.

No real Zotero library data — pairs are synthesized to Zotero's WebDAV format.
"""

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from zotdav_blob import (
    parse_prop, extract_blob, list_pending_keys, key_for, zip_for,
    md5_of_file, ZotdavBlobError,
)


def make_pair(directory, key, filename="paper.pdf", data=b"%PDF-1.4 fake\n", mtime_ms=1719000000000):
    """Write a synthetic {KEY}.zip + {KEY}.prop pair; return the file's md5."""
    directory = Path(directory)
    zpath = directory / f"{key}.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(filename, data)
    md5 = hashlib.md5(data).hexdigest()
    (directory / f"{key}.prop").write_text(
        f'<properties version="1"><mtime>{mtime_ms}</mtime><hash>{md5}</hash></properties>'
    )
    return md5


def test_parse_prop(tmp_path):
    md5 = make_pair(tmp_path, "ABCD1234", mtime_ms=1719000000000)
    got = parse_prop(tmp_path / "ABCD1234.prop")
    assert got == {"key": "ABCD1234", "mtime": 1719000000000, "md5": md5}


def test_parse_prop_with_namespace_and_missing_fields(tmp_path):
    (tmp_path / "K.prop").write_text(
        '<properties xmlns="http://x/"><hash>D41D8CD98F00B204E9800998ECF8427E</hash></properties>'
    )
    got = parse_prop(tmp_path / "K.prop")
    assert got["key"] == "K" and got["mtime"] == 0
    assert got["md5"] == "d41d8cd98f00b204e9800998ecf8427e"  # lower-cased


def test_parse_prop_corrupt_raises(tmp_path):
    (tmp_path / "K.prop").write_text("not xml <<<")
    with pytest.raises(ZotdavBlobError):
        parse_prop(tmp_path / "K.prop")


def test_extract_blob(tmp_path):
    data = b"hello world pdf bytes"
    md5 = make_pair(tmp_path, "EFGH5678", filename="My Paper.pdf", data=data)
    dest = tmp_path / "out"
    got = extract_blob(tmp_path / "EFGH5678.zip", dest)
    assert got["key"] == "EFGH5678"
    assert got["filename"] == "My Paper.pdf"
    assert Path(got["path"]).read_bytes() == data
    assert got["md5"] == md5
    assert got["files"] == ["My Paper.pdf"]


def test_extract_blob_picks_largest_member(tmp_path):
    zpath = tmp_path / "WEB1.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("index.html", b"<html>small</html>")
        zf.writestr("main.pdf", b"x" * 5000)  # largest -> primary
    got = extract_blob(zpath, tmp_path / "out")
    assert got["filename"] == "main.pdf"
    assert set(got["files"]) == {"index.html", "main.pdf"}


def test_extract_blob_missing_zip_raises(tmp_path):
    with pytest.raises(ZotdavBlobError):
        extract_blob(tmp_path / "nope.zip", tmp_path / "out")


def test_extract_blob_corrupt_zip_raises(tmp_path):
    bad = tmp_path / "BAD.zip"; bad.write_bytes(b"not a zip")
    with pytest.raises(ZotdavBlobError):
        extract_blob(bad, tmp_path / "out")


def test_list_pending_keys_only_complete_pairs_sorted_by_mtime(tmp_path):
    make_pair(tmp_path, "NEWER", mtime_ms=2000000000000)
    make_pair(tmp_path, "OLDER", mtime_ms=1000000000000)
    # bump filesystem mtimes to match arrival order (list sorts by fs mtime)
    import os
    os.utime(tmp_path / "OLDER.prop", (1_000_000_000, 1_000_000_000))
    os.utime(tmp_path / "NEWER.prop", (2_000_000_000, 2_000_000_000))
    # an orphan .prop with no paired .zip must be ignored
    (tmp_path / "ORPHAN.prop").write_text("<properties/>")
    assert list_pending_keys(tmp_path) == ["OLDER", "NEWER"]


def test_list_pending_keys_missing_dir_is_empty(tmp_path):
    assert list_pending_keys(tmp_path / "does-not-exist") == []


def test_helpers(tmp_path):
    assert key_for("/x/ABCD1234.prop") == "ABCD1234"
    assert zip_for(tmp_path / "K.prop") == tmp_path / "K.zip"
    f = tmp_path / "f.bin"; f.write_bytes(b"abc")
    assert md5_of_file(f) == hashlib.md5(b"abc").hexdigest()
