# scan-import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a launchd Python service at `~/scan-import/` that watches an SMB-mounted folder for new OCR'd PDFs and imports them into DEVONthink 4 with Ollama-generated human-readable names and DT4's built-in AI classification.

**Architecture:** FSEvents watcher (watchdog) detects new PDFs in `/Volumes/Media/scanning/pdf_out/`; pipeline calls DT4's MCP server at `localhost:8420` to import, read OCR text, rename, classify, and file; on success the source PDF is archived to a monthly subfolder on the same SMB share.

**Tech Stack:** Python 3.13, watchdog (FSEvents), requests, python-dotenv, Ollama (`llama3.2:3b`), DEVONthink 4 MCP (localhost:8420, bearer-token auth).

---

## File Map

| File | Responsibility |
|---|---|
| `devonthink_mcp.py` | DT4 MCP client — copy of DEVONzot's, plus `get_record_text` and `classify_record` |
| `rename.py` | `build_filename`, `ollama_label`, `ollama_label_fallback` |
| `files.py` | `wait_for_stable`, `archive_file`, `fail_file` |
| `pipeline.py` | `process_pdf` — orchestrates the full import pipeline |
| `scan_import_service.py` | FSEvents handler + `main()` + config loading |
| `config/.env.example` | Documented config template |
| `config/.env` | Live secrets (gitignored) |
| `com.traviseross.scan-import.plist` | launchd agent definition |
| `requirements.txt` | Runtime + test dependencies |
| `pytest.ini` | Test config |
| `tests/conftest.py` | Shared fixtures |
| `tests/test_devonthink_mcp.py` | Tests for the two new MCP methods |
| `tests/test_rename.py` | Tests for rename utilities |
| `tests/test_files.py` | Tests for file movement utilities |
| `tests/test_pipeline.py` | Tests for `process_pdf` orchestration |

---

## Task 1: Project Scaffold

**Files:**
- Create: `~/scan-import/` (entire directory tree)

- [ ] **Step 1: Create the service directory and copy the MCP client**

```bash
mkdir -p ~/scan-import/config ~/scan-import/tests
cp ~/DEVONzot/src/devonthink_mcp.py ~/scan-import/devonthink_mcp.py
touch ~/scan-import/tests/__init__.py
```

- [ ] **Step 2: Write `requirements.txt`**

```
watchdog>=4.0.0
requests>=2.31.0
python-dotenv>=1.0.0
pytest>=8.0.0
```

- [ ] **Step 3: Write `pytest.ini`**

```ini
[pytest]
testpaths = tests
```

- [ ] **Step 4: Write `.gitignore`**

```
config/.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 5: Create the virtualenv and install dependencies**

```bash
cd ~/scan-import
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Expected: `Successfully installed watchdog-... requests-... python-dotenv-... pytest-...`

- [ ] **Step 6: Write `tests/conftest.py` (empty for now — fixtures added per task)**

```python
import pytest
```

- [ ] **Step 7: Verify pytest runs with zero tests**

```bash
cd ~/scan-import
.venv/bin/pytest -v
```

Expected: `no tests ran`

- [ ] **Step 8: Init git and commit**

```bash
cd ~/scan-import
git init
git add .
git commit -m "chore: scaffold scan-import service"
```

---

## Task 2: Extend `devonthink_mcp.py` — `get_record_text` + `classify_record`

**Files:**
- Modify: `~/scan-import/devonthink_mcp.py` (add two methods near the end of the class)
- Create: `~/scan-import/tests/test_devonthink_mcp.py`

`get_record_text` returns raw OCR text (plain string, not JSON). `classify_record` returns a ranked list of group dicts: `[{"uuid": "…", "databaseUUID": "…", "name": "Bills", "score": 0.87}, …]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_devonthink_mcp.py`:

```python
import json
from unittest.mock import MagicMock
import pytest
from devonthink_mcp import DevonthinkMCP


@pytest.fixture
def mcp():
    client = DevonthinkMCP(url="http://localhost:8420", token="test-token")
    client._initialized = True  # skip init handshake in tests
    return client


def _make_response(text: str, is_error: bool = False):
    return {
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        }
    }


def test_get_record_text_returns_string(mcp):
    mcp._post = MagicMock(return_value=_make_response("T-Mobile bill for March 2026."))
    result = mcp.get_record_text("some-uuid")
    assert result == "T-Mobile bill for March 2026."


def test_get_record_text_empty_response_returns_empty_string(mcp):
    mcp._post = MagicMock(return_value={"result": {"content": [], "isError": False}})
    result = mcp.get_record_text("some-uuid")
    assert result == ""


def test_classify_record_returns_list(mcp):
    payload = [
        {"uuid": "AAA", "databaseUUID": "DDD", "name": "Bills", "score": 0.87},
        {"uuid": "BBB", "databaseUUID": "DDD", "name": "Other", "score": 0.42},
    ]
    mcp._post = MagicMock(return_value=_make_response(json.dumps(payload)))
    result = mcp.classify_record("some-uuid")
    assert len(result) == 2
    assert result[0]["uuid"] == "AAA"
    assert result[0]["score"] == 0.87


def test_classify_record_empty_list_returns_empty(mcp):
    mcp._post = MagicMock(return_value=_make_response("[]"))
    result = mcp.classify_record("some-uuid")
    assert result == []
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd ~/scan-import
.venv/bin/pytest tests/test_devonthink_mcp.py -v
```

Expected: 4 failures — `AttributeError: 'DevonthinkMCP' object has no attribute 'get_record_text'`

- [ ] **Step 3: Add `get_record_text` and `classify_record` to `devonthink_mcp.py`**

Add these two methods to the `DevonthinkMCP` class, after `trash_record`:

```python
def get_record_text(self, uuid: str) -> str:
    """Return the plain-text content of a record (OCR text for PDFs)."""
    result = self._tool("get_record_text", {"uuid": uuid})
    if isinstance(result, str):
        return result
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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
.venv/bin/pytest tests/test_devonthink_mcp.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add devonthink_mcp.py tests/test_devonthink_mcp.py
git commit -m "feat: add get_record_text and classify_record to MCP client"
```

---

## Task 3: `rename.py`

**Files:**
- Create: `~/scan-import/rename.py`
- Create: `~/scan-import/tests/test_rename.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rename.py`:

```python
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from rename import build_filename, ollama_label, ollama_label_fallback

FIXED_MTIME = datetime(2026, 3, 15, 12, 0, 0).timestamp()
FIXED_DATE = "2026-03-15"


def test_build_filename_combines_date_and_label():
    assert build_filename("T-Mobile Bill", FIXED_MTIME) == f"{FIXED_DATE} T-Mobile Bill"


def test_build_filename_strips_illegal_chars():
    result = build_filename('Invoice: "Acme/Corp"', FIXED_MTIME)
    for ch in r'\/:*?"<>|':
        assert ch not in result


def test_build_filename_strips_surrounding_whitespace():
    result = build_filename("  T-Mobile Bill  ", FIXED_MTIME)
    assert result == f"{FIXED_DATE} T-Mobile Bill"


def test_ollama_label_returns_stripped_response():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "  T-Mobile Bill  "}
    mock_resp.raise_for_status = MagicMock()
    with patch("rename.requests.post", return_value=mock_resp):
        result = ollama_label("T-Mobile total home internet...", "http://localhost:11434", "llama3.2:3b")
    assert result == "T-Mobile Bill"


def test_ollama_label_sends_model_and_keepalive():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "Bill"}
    mock_resp.raise_for_status = MagicMock()
    with patch("rename.requests.post", return_value=mock_resp) as mock_post:
        ollama_label("some text", "http://localhost:11434", "llama3.2:3b")
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["model"] == "llama3.2:3b"
    assert kwargs["json"]["options"]["keep_alive"] == 0
    assert kwargs["json"]["stream"] is False


def test_ollama_label_fallback_returns_first_meaningful_line():
    text = "\n\nT-Mobile\nTotal Home Internet Statement\nAccount: 555-1234"
    assert ollama_label_fallback(text) == "T-Mobile"


def test_ollama_label_fallback_skips_lines_shorter_than_4_chars():
    text = "OK\nHi\nProvidenceHealthStatement"
    assert ollama_label_fallback(text) == "ProvidenceHealthStatement"


def test_ollama_label_fallback_truncates_long_lines():
    result = ollama_label_fallback("A" * 100)
    assert len(result) <= 60


def test_ollama_label_fallback_strips_illegal_chars():
    result = ollama_label_fallback('Invoice: "Acme/Corp"')
    for ch in r'\/:*?"<>|':
        assert ch not in result


def test_ollama_label_fallback_returns_default_when_no_text():
    assert ollama_label_fallback("") == "Scanned Document"
    assert ollama_label_fallback("  \n\n  ") == "Scanned Document"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
.venv/bin/pytest tests/test_rename.py -v
```

Expected: `ModuleNotFoundError: No module named 'rename'`

- [ ] **Step 3: Create `rename.py`**

```python
import re

import requests


def build_filename(label: str, mtime: float) -> str:
    from datetime import datetime
    date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    label = re.sub(r'[\\/:*?"<>|]', "", label).strip()
    return f"{date} {label}"


def ollama_label(text: str, url: str, model: str) -> str:
    """Call Ollama to generate a short human-readable label from OCR text."""
    prompt = (
        "Given this scanned document text, output ONLY a short human-readable label "
        "(2-5 words) suitable for a filename. No extension, no path, no punctuation. "
        "Examples: T-Mobile Bill, Providence Invoice, Driver License\n\n"
        f"Text:\n{text[:400]}"
    )
    resp = requests.post(
        f"{url.rstrip('/')}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"keep_alive": 0},
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def ollama_label_fallback(text: str) -> str:
    """Return the first meaningful line of OCR text as a label."""
    for line in text.splitlines():
        line = re.sub(r'[\\/:*?"<>|]', "", line).strip()
        if len(line) >= 4:
            return line[:60]
    return "Scanned Document"
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
.venv/bin/pytest tests/test_rename.py -v
```

Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add rename.py tests/test_rename.py
git commit -m "feat: add rename utilities (build_filename, ollama_label, fallback)"
```

---

## Task 4: `files.py`

**Files:**
- Create: `~/scan-import/files.py`
- Create: `~/scan-import/tests/test_files.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_files.py`:

```python
from datetime import datetime
from pathlib import Path

import pytest

from files import archive_file, fail_file, wait_for_stable


def test_wait_for_stable_returns_true_for_static_file(tmp_path):
    f = tmp_path / "test.pdf"
    f.write_bytes(b"hello")
    assert wait_for_stable(f, polls=3, interval=0.0) is True


def test_wait_for_stable_returns_false_for_missing_file(tmp_path):
    f = tmp_path / "missing.pdf"
    assert wait_for_stable(f, polls=2, interval=0.0) is False


def test_archive_file_moves_file(tmp_path):
    src = tmp_path / "scan.pdf"
    src.write_bytes(b"pdf content")
    expected_month = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m")
    archive_base = tmp_path / "archive"

    dest = archive_file(src, archive_base)

    assert not src.exists()
    assert dest.exists()
    assert dest == archive_base / expected_month / "scan.pdf"


def test_archive_file_creates_month_directory(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"content")
    archive_base = tmp_path / "archive"

    archive_file(src, archive_base)

    month_dirs = list(archive_base.iterdir())
    assert len(month_dirs) == 1
    assert month_dirs[0].is_dir()


def test_archive_file_preserves_filename(tmp_path):
    src = tmp_path / "my special scan.pdf"
    src.write_bytes(b"pdf")
    dest = archive_file(src, tmp_path / "archive")
    assert dest.name == "my special scan.pdf"


def test_fail_file_moves_file(tmp_path):
    src = tmp_path / "bad.pdf"
    src.write_bytes(b"pdf")
    failed_dir = tmp_path / "failed"

    fail_file(src, failed_dir, "MCP connection refused")

    assert not src.exists()
    assert (failed_dir / "bad.pdf").exists()


def test_fail_file_writes_log_sidecar(tmp_path):
    src = tmp_path / "bad.pdf"
    src.write_bytes(b"pdf")

    fail_file(src, tmp_path / "failed", "import_file: timeout")

    log = tmp_path / "failed" / "bad.log"
    assert log.exists()
    content = log.read_text()
    assert "import_file: timeout" in content


def test_fail_file_creates_failed_directory(tmp_path):
    src = tmp_path / "bad.pdf"
    src.write_bytes(b"pdf")
    failed_dir = tmp_path / "failed"
    assert not failed_dir.exists()

    fail_file(src, failed_dir, "error")

    assert failed_dir.exists()
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
.venv/bin/pytest tests/test_files.py -v
```

Expected: `ModuleNotFoundError: No module named 'files'`

- [ ] **Step 3: Create `files.py`**

```python
import shutil
import time
from datetime import datetime
from pathlib import Path


def wait_for_stable(path: Path, polls: int = 3, interval: float = 0.5) -> bool:
    """Return True once the file size stops changing across `polls` checks."""
    last_size = -1
    for _ in range(polls):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last_size:
            return True
        last_size = size
        time.sleep(interval)
    return True


def archive_file(src: Path, archive_base: Path) -> Path:
    """Move src into archive_base/YYYY-MM/src.name. Returns the destination path."""
    month = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m")
    dest_dir = archive_base / month
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.move(str(src), str(dest))
    return dest


def fail_file(src: Path, failed_dir: Path, error: str) -> None:
    """Move src to failed_dir and write a .log sidecar with the error message."""
    failed_dir.mkdir(parents=True, exist_ok=True)
    dest = failed_dir / src.name
    shutil.move(str(src), str(dest))
    log = dest.with_suffix(".log")
    log.write_text(f"{datetime.now().isoformat()}\n{error}\n")
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
.venv/bin/pytest tests/test_files.py -v
```

Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add files.py tests/test_files.py
git commit -m "feat: add file utilities (wait_for_stable, archive_file, fail_file)"
```

---

## Task 5: `pipeline.py`

**Files:**
- Create: `~/scan-import/pipeline.py`
- Create: `~/scan-import/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devonthink_mcp import DevonthinkMCPError
from pipeline import process_pdf


@pytest.fixture
def cfg(tmp_path):
    return {
        "archive_folder": str(tmp_path / "archive"),
        "failed_folder": str(tmp_path / "failed"),
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3.2:3b",
        "classify_min_score": "0.5",
    }


@pytest.fixture
def pdf_file(tmp_path):
    f = tmp_path / "scan_2026-06-08_120000.pdf"
    f.write_bytes(b"fake pdf content")
    return f


@pytest.fixture
def mock_mcp():
    mcp = MagicMock()
    mcp.import_file.return_value = {"uuid": "TEST-UUID-1234"}
    mcp.get_record_text.return_value = "T-Mobile Total Home Internet\nStatement March 2026"
    mcp.classify_record.return_value = [
        {"uuid": "GROUP-UUID", "databaseUUID": "DB-UUID", "name": "Bills", "score": 0.87}
    ]
    return mcp


def test_happy_path_archives_source_file(pdf_file, mock_mcp, cfg):
    with patch("pipeline.ollama_label", return_value="T-Mobile Bill"):
        process_pdf(pdf_file, mock_mcp, cfg)

    assert not pdf_file.exists()
    archived = list(Path(cfg["archive_folder"]).rglob("*.pdf"))
    assert len(archived) == 1


def test_happy_path_renames_record(pdf_file, mock_mcp, cfg):
    with patch("pipeline.ollama_label", return_value="T-Mobile Bill"):
        process_pdf(pdf_file, mock_mcp, cfg)

    mock_mcp.update_record.assert_called_once()
    name_arg = mock_mcp.update_record.call_args[1]["name"]
    assert "T-Mobile Bill" in name_arg


def test_happy_path_moves_to_classified_group(pdf_file, mock_mcp, cfg):
    with patch("pipeline.ollama_label", return_value="T-Mobile Bill"):
        process_pdf(pdf_file, mock_mcp, cfg)

    mock_mcp.move_record.assert_called_once_with(
        "TEST-UUID-1234", destination="GROUP-UUID", database_uuid="DB-UUID"
    )


def test_ollama_failure_falls_back_to_first_ocr_line(pdf_file, mock_mcp, cfg):
    with patch("pipeline.ollama_label", side_effect=Exception("connection refused")):
        process_pdf(pdf_file, mock_mcp, cfg)

    mock_mcp.update_record.assert_called_once()
    name_arg = mock_mcp.update_record.call_args[1]["name"]
    assert "T-Mobile" in name_arg
    assert not pdf_file.exists()  # still archived on success


def test_low_classify_score_leaves_record_in_inbox(pdf_file, mock_mcp, cfg):
    mock_mcp.classify_record.return_value = [
        {"uuid": "GRP", "databaseUUID": "DB", "name": "Bills", "score": 0.2}
    ]
    with patch("pipeline.ollama_label", return_value="Some Doc"):
        process_pdf(pdf_file, mock_mcp, cfg)

    mock_mcp.move_record.assert_not_called()
    assert not pdf_file.exists()  # still archived — not a failure


def test_no_classify_suggestions_leaves_record_in_inbox(pdf_file, mock_mcp, cfg):
    mock_mcp.classify_record.return_value = []
    with patch("pipeline.ollama_label", return_value="Some Doc"):
        process_pdf(pdf_file, mock_mcp, cfg)

    mock_mcp.move_record.assert_not_called()
    assert not pdf_file.exists()


def test_mcp_import_error_moves_to_failed(pdf_file, mock_mcp, cfg):
    mock_mcp.import_file.side_effect = DevonthinkMCPError("connection refused")

    process_pdf(pdf_file, mock_mcp, cfg)

    assert not pdf_file.exists()
    failed_pdfs = list(Path(cfg["failed_folder"]).glob("*.pdf"))
    failed_logs = list(Path(cfg["failed_folder"]).glob("*.log"))
    assert len(failed_pdfs) == 1
    assert len(failed_logs) == 1


def test_classify_error_does_not_fail_pipeline(pdf_file, mock_mcp, cfg):
    mock_mcp.classify_record.side_effect = Exception("DT4 busy")
    with patch("pipeline.ollama_label", return_value="Some Doc"):
        process_pdf(pdf_file, mock_mcp, cfg)

    assert not pdf_file.exists()  # archived — classify error is non-fatal
    mock_mcp.move_record.assert_not_called()
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
.venv/bin/pytest tests/test_pipeline.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline'`

- [ ] **Step 3: Create `pipeline.py`**

```python
import logging
from pathlib import Path

from devonthink_mcp import DevonthinkMCP, DevonthinkMCPError
from files import archive_file, fail_file, wait_for_stable
from rename import build_filename, ollama_label, ollama_label_fallback

logger = logging.getLogger(__name__)

_MIN_SCORE_DEFAULT = 0.5


def process_pdf(path: Path, mcp: DevonthinkMCP, cfg: dict) -> None:
    """Full import pipeline: import → rename → classify → archive.

    On MCP failure: moves source to failed/ with a .log sidecar.
    On classify failure: leaves the record in Global Inbox (non-fatal).
    """
    failed_dir = Path(cfg["failed_folder"])
    try:
        _run(path, mcp, cfg)
        archive_file(path, Path(cfg["archive_folder"]))
    except DevonthinkMCPError as exc:
        logger.error("MCP error processing %s: %s", path.name, exc)
        fail_file(path, failed_dir, str(exc))
    except Exception as exc:
        logger.error("Unexpected error processing %s: %s", path.name, exc, exc_info=True)
        fail_file(path, failed_dir, str(exc))


def _run(path: Path, mcp: DevonthinkMCP, cfg: dict) -> None:
    wait_for_stable(path)

    rec = mcp.import_file(str(path))
    uuid = rec["uuid"]
    logger.info("Imported %s → %s", path.name, uuid)

    text = mcp.get_record_text(uuid) or ""

    try:
        label = ollama_label(text, cfg["ollama_url"], cfg["ollama_model"])
        if not label:
            raise ValueError("empty response")
    except Exception as exc:
        logger.warning("Ollama unavailable (%s) — using fallback rename", exc)
        label = ollama_label_fallback(text)

    new_name = build_filename(label, path.stat().st_mtime)
    mcp.update_record(uuid, name=new_name)
    logger.info("Renamed to %s", new_name)

    min_score = float(cfg.get("classify_min_score", _MIN_SCORE_DEFAULT))
    try:
        suggestions = mcp.classify_record(uuid) or []
        if suggestions and suggestions[0].get("score", 0) >= min_score:
            top = suggestions[0]
            mcp.move_record(uuid, destination=top["uuid"], database_uuid=top["databaseUUID"])
            logger.info("Filed to %s / %s", top.get("databaseName", "?"), top["name"])
        else:
            logger.info("No confident suggestion (score < %.2f) — left in Global Inbox", min_score)
    except Exception as exc:
        logger.warning("classify_record failed (%s) — left in Global Inbox", exc)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
.venv/bin/pytest tests/test_pipeline.py -v
```

Expected: 8 passed

- [ ] **Step 5: Run the full suite to make sure nothing regressed**

```bash
.venv/bin/pytest -v
```

Expected: 21 passed (4 + 9 + 8 + 8 + 8 from prior tasks... adjust if counts differ — the point is 0 failures)

- [ ] **Step 6: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: add process_pdf pipeline (import, rename, classify, archive)"
```

---

## Task 6: `scan_import_service.py`

**Files:**
- Create: `~/scan-import/scan_import_service.py`

No unit tests for the watcher itself (FSEvents requires a running OS event loop; behaviour is verified in Task 8's smoke test). One test covers the startup mount-wait guard.

- [ ] **Step 1: Write the startup guard test**

Add to `tests/test_pipeline.py` (append at the bottom — it tests service-level behaviour):

```python
# -- startup guard --

def test_wait_for_mount_returns_true_when_folder_exists(tmp_path):
    from scan_import_service import wait_for_mount
    assert wait_for_mount(tmp_path, timeout=1) is True


def test_wait_for_mount_returns_false_when_folder_absent(tmp_path):
    from scan_import_service import wait_for_mount
    absent = tmp_path / "not_here"
    assert wait_for_mount(absent, timeout=0) is False
```

- [ ] **Step 2: Run them — verify they fail**

```bash
.venv/bin/pytest tests/test_pipeline.py::test_wait_for_mount_returns_true_when_folder_exists tests/test_pipeline.py::test_wait_for_mount_returns_false_when_folder_absent -v
```

Expected: `ModuleNotFoundError: No module named 'scan_import_service'`

- [ ] **Step 3: Create `scan_import_service.py`**

```python
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from devonthink_mcp import DevonthinkMCP
from pipeline import process_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

_ENV_PATH = Path(__file__).parent / "config" / ".env"


def load_config() -> dict:
    load_dotenv(_ENV_PATH)
    return {
        "watch_folder": os.environ.get("WATCH_FOLDER", "/Volumes/Media/scanning/pdf_out"),
        "archive_folder": os.environ.get(
            "ARCHIVE_FOLDER", "/Volumes/Media/scanning/pdf_out/archive"
        ),
        "failed_folder": os.environ.get(
            "FAILED_FOLDER", "/Volumes/Media/scanning/pdf_out/failed"
        ),
        "ollama_url": os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        "ollama_model": os.environ.get("OLLAMA_MODEL", "llama3.2:3b"),
        "classify_min_score": os.environ.get("CLASSIFY_MIN_SCORE", "0.5"),
    }


class PDFHandler(FileSystemEventHandler):
    def __init__(self, mcp: DevonthinkMCP, cfg: dict):
        self.mcp = mcp
        self.cfg = cfg

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".pdf":
            return
        # Ignore files dropped into archive/ or failed/ subdirectories
        if any(p.name in ("archive", "failed") for p in path.parents):
            return
        logger.info("Detected new PDF: %s", path.name)
        process_pdf(path, self.mcp, self.cfg)


def wait_for_mount(watch_folder: Path, timeout: int = 60) -> bool:
    """Block until watch_folder exists or timeout expires. Returns True on success."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if watch_folder.exists():
            return True
        logger.info("Waiting for %s ...", watch_folder)
        time.sleep(5)
    return watch_folder.exists()


def main() -> None:
    cfg = load_config()
    watch_folder = Path(cfg["watch_folder"])

    mcp = DevonthinkMCP()

    if not mcp.is_running():
        logger.error("DEVONthink is not running — exiting (launchd will restart)")
        sys.exit(1)

    if not wait_for_mount(watch_folder):
        logger.error("Watch folder unavailable after 60 s: %s", watch_folder)
        sys.exit(1)

    logger.info("scan-import started, watching %s", watch_folder)
    handler = PDFHandler(mcp, cfg)
    observer = Observer()
    observer.schedule(handler, str(watch_folder), recursive=False)
    observer.start()

    try:
        while observer.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()

    logger.info("scan-import stopped")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the startup guard tests — verify they pass**

```bash
.venv/bin/pytest tests/test_pipeline.py -v -k "mount"
```

Expected: 2 passed

- [ ] **Step 5: Run the full suite**

```bash
.venv/bin/pytest -v
```

Expected: 0 failures

- [ ] **Step 6: Commit**

```bash
git add scan_import_service.py tests/test_pipeline.py
git commit -m "feat: add FSEvents watcher service and main() entrypoint"
```

---

## Task 7: Config Files and launchd Plist

**Files:**
- Create: `~/scan-import/config/.env.example`
- Create: `~/scan-import/com.traviseross.scan-import.plist`

- [ ] **Step 1: Create `config/.env.example`**

```bash
# DEVONthink 4 MCP bearer token
# Find at: DEVONthink > Settings > AI > MCP > Bearer Token
DEVONTHINK_MCP_TOKEN=your_token_here

# Paths (defaults shown — only set if different)
# WATCH_FOLDER=/Volumes/Media/scanning/pdf_out
# ARCHIVE_FOLDER=/Volumes/Media/scanning/pdf_out/archive
# FAILED_FOLDER=/Volumes/Media/scanning/pdf_out/failed

# Ollama
# OLLAMA_URL=http://localhost:11434
# OLLAMA_MODEL=llama3.2:3b

# DT4 classify AI: minimum score (0–1) to auto-file. Below this, record stays in Global Inbox.
# CLASSIFY_MIN_SCORE=0.5
```

- [ ] **Step 2: Copy `.env.example` to `.env` and fill in the MCP token**

Find the token at: **DEVONthink > Settings > AI > MCP > Bearer Token**

```bash
cp ~/scan-import/config/.env.example ~/scan-import/config/.env
# edit ~/scan-import/config/.env and paste the token
```

- [ ] **Step 3: Create `com.traviseross.scan-import.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.traviseross.scan-import</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/travisross/scan-import/.venv/bin/python</string>
        <string>/Users/travisross/scan-import/scan_import_service.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/travisross/scan-import</string>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/travisross/Library/Logs/scan-import.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/travisross/Library/Logs/scan-import.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

- [ ] **Step 4: Commit**

```bash
git add config/.env.example com.traviseross.scan-import.plist
git commit -m "chore: add .env.example and launchd plist"
```

---

## Task 8: Install and Smoke Test

**Files:** none (operational steps only)

- [ ] **Step 1: Install Ollama and pull the model (if not already done)**

```bash
brew install ollama
ollama pull llama3.2:3b
```

Expected: model downloads and is listed in `ollama list`

- [ ] **Step 2: Verify the SMB share is mounted**

```bash
ls /Volumes/Media/scanning/pdf_out
```

Expected: directory listing (may be empty)

- [ ] **Step 3: Do a dry run of the service to check startup**

```bash
cd ~/scan-import
DEVONTHINK_MCP_TOKEN=$(grep DEVONTHINK_MCP_TOKEN config/.env | cut -d= -f2) \
  .venv/bin/python scan_import_service.py
```

Expected log output:
```
... scan_import_service INFO scan-import started, watching /Volumes/Media/scanning/pdf_out
```

Press Ctrl-C to stop.

- [ ] **Step 4: Copy a test PDF into the watch folder**

If a real scan isn't available, create a minimal PDF:

```bash
# On the server — or copy any existing PDF into the folder
ssh zvra.traviseross.com "cp /some/existing/file.pdf /media/external/scanning/pdf_out/test_scan.pdf"
```

Then restart the service and watch the log:

```bash
cd ~/scan-import
.venv/bin/python scan_import_service.py &
tail -f ~/Library/Logs/scan-import.log
```

Expected sequence in log:
```
Detected new PDF: test_scan.pdf
Imported test_scan.pdf → <UUID>
Renamed to 2026-06-08 <Label>
Filed to <Database> / <Group>   ← or "left in Global Inbox" if library is sparse
```

- [ ] **Step 5: Verify in DEVONthink**

Open DEVONthink 4. Check Global Inbox (or the classified group). Confirm:
- Record exists with the renamed filename (not `test_scan.pdf`)
- Content is the OCR'd PDF

- [ ] **Step 6: Verify the source was archived**

```bash
ls /Volumes/Media/scanning/pdf_out/archive/
```

Expected: a `YYYY-MM/` subdirectory containing `test_scan.pdf`

- [ ] **Step 7: Install the launchd agent**

```bash
cp ~/scan-import/com.traviseross.scan-import.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.traviseross.scan-import.plist
launchctl list | grep scan-import
```

Expected: the service PID appears in the list (non-zero = running)

- [ ] **Step 8: Final commit**

```bash
cd ~/scan-import
git add .
git commit -m "chore: mark smoke test complete"
```
