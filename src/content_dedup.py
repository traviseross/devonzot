"""Content-hash dedup gate for DEVONzot.

Two reinforcing halves, both gated by env DEDUP_GATE_ENABLED (default off):

  find_or_adopt_by_content()  — PRE-import. SHA-256 the local file, then look for an
      existing DEVONthink record with the same content: local index -> DEVONthink
      custom-metadata search -> name-based fallback. A hit means "adopt this record,
      don't import a duplicate".

  reconcile_after_import()    — POST-import safety net. Ask DEVONthink
      get_record_duplicates on the freshly imported record; if a pre-existing
      byte-identical record exists (e.g. added before Zotero under an arbitrary
      name — the case name search can't catch), adopt the original and trash the
      new import.

Both halves stamp `contentsha256` custom metadata on the surviving record and update
a local {sha: uuid} index, so the index self-heals lazily.

Spike-verified tokens (2026-07-02): the custom-metadata identifier must be alphanumeric
(`content_sha256` with an underscore is REJECTED); DEVONthink auto-creates `contentsha256`
on first set. Search by value uses the `md` storage prefix: `mdcontentsha256:<sha>`.
get_record_duplicates is content-hash based and returns the *other* duplicate records.
Both search and get_record_duplicates omit AI-excluded records — the local index is the
only catch for those, so it is always maintained.
"""

import os
import json
import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CHUNK = 1024 * 1024                 # 1 MiB streaming reads (large PDFs)
SHA_FIELD = "contentsha256"        # set_record_custom_metadata key (alphanumeric only)
SHA_SEARCH = "mdcontentsha256"     # search_records token (md storage prefix)

MODES = ("off", "shadow", "live")


def gate_mode() -> str:
    """DEDUP_GATE_MODE: 'off' (default), 'shadow' (observe+log, no mutation), 'live'."""
    m = os.environ.get("DEDUP_GATE_MODE", "off").strip().lower()
    return m if m in MODES else "off"


def sha256_of_file(path) -> "str | None":
    """Streaming SHA-256 of a file's bytes; None if missing/unreadable (gate no-ops)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK), b""):
                h.update(chunk)
    except (OSError, ValueError) as e:
        logger.warning(f"content-dedup: could not hash {path}: {e}")
        return None
    return h.hexdigest()


class ContentDedup:
    """Content-identity gate shared by all import sites.

    `find_by_name` is a callback to the existing name-search adopter
    (DEVONzotService._find_or_adopt_in_devonthink) with signature
    (generated_name, zotero_filename, dry_run) -> uuid | None. It is always used as
    the final fallback so behavior with the flag OFF is identical to today.
    """

    def __init__(self, mcp, index_path, find_by_name=None, mode=None):
        self.mcp = mcp
        self.index_path = Path(index_path)
        self._find_by_name = find_by_name
        self.mode = gate_mode() if mode is None else mode
        self._index = self._load_index()

    def _acts(self, dry_run=False) -> bool:
        """True only when we should actually mutate (adopt/trash/stamp)."""
        return self.mode == "live" and not dry_run

    # ---- local {sha: uuid} index ----

    def _load_index(self) -> dict:
        try:
            with open(self.index_path) as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_index(self):
        try:
            tmp = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
            with open(tmp, "w") as f:
                json.dump(self._index, f, indent=2)
            os.replace(tmp, self.index_path)
        except OSError as e:
            logger.warning(f"content-dedup: could not save index: {e}")

    def _record_exists(self, uuid) -> bool:
        try:
            props = self.mcp.get_record_properties(uuid)
            return bool(props) and props.get("uuid") == uuid
        except Exception:
            return False

    def _stamp(self, uuid, sha, dry_run=False):
        """Stamp contentsha256 on a record and record it in the local index."""
        if not (uuid and sha):
            return
        if dry_run:
            logger.info(f"[DRY RUN] content-dedup: would stamp {sha[:12]}… on {uuid}")
            return
        try:
            self.mcp.set_record_custom_metadata(uuid, {SHA_FIELD: sha}, mode="merge")
        except Exception as e:
            logger.warning(f"content-dedup: stamp failed for {uuid}: {e}")
        self._index[sha] = uuid
        self._save_index()

    # ---- pre-import gate ----

    def find_or_adopt_by_content(self, local_path, generated_name,
                                 zotero_filename, dry_run=False):
        """Return (uuid, adopted, sha).

        (uuid, True, sha)  -> adopt this existing record; caller must NOT import.
        (None, False, sha) -> proceed to import, then call reconcile_after_import(sha).
        `sha` is None when the mode is off or the file couldn't be hashed.

        mode=off    -> name search only (byte-identical to legacy).
        mode=shadow -> compute the content decision, LOG it vs the name decision, but
                       RETURN the legacy name result and change nothing (observe-only).
        mode=live   -> the real gate (content match adopts; name hit stamps).
        """
        # off: behave exactly as today — name search only, no hashing.
        if self.mode == "off":
            uuid = self._find_by_name(generated_name, zotero_filename, dry_run) \
                if self._find_by_name else None
            return (uuid, uuid is not None, None)

        sha = sha256_of_file(local_path) if local_path else None
        content_uuid = self._content_lookup(sha) if sha else None

        if not self._acts(dry_run):
            # shadow / dry-run: reproduce legacy behavior exactly (name search, incl. its
            # normal rename side-effect — that happens today regardless of the gate) and
            # only ADD an observation. No stamp, no trash, no adopt-substitution.
            name_uuid = self._find_by_name(generated_name, zotero_filename, dry_run) \
                if self._find_by_name else None
            if content_uuid and content_uuid != name_uuid:
                logger.info(f"DEDUP-OBSERVE pre-import: WOULD adopt {content_uuid} by content "
                            f"{(sha or '')[:12]}… (name search -> {name_uuid or 'miss'}) "
                            f"file={generated_name!r}")
            elif sha:
                logger.info(f"DEDUP-OBSERVE pre-import: no content match "
                            f"{(sha or '')[:12]}… (name search -> {name_uuid or 'miss'})")
            return (name_uuid, name_uuid is not None, sha)

        # live — content lookup wins; only run the name search (which renames) on a miss.
        if content_uuid:
            self._index[sha] = content_uuid   # cache locally (no redundant DT write)
            self._save_index()
            logger.info(f"content-dedup: content match {(sha or '')[:12]}… -> "
                        f"{content_uuid} (adopt)")
            return (content_uuid, True, sha)
        name_uuid = self._find_by_name(generated_name, zotero_filename, dry_run) \
            if self._find_by_name else None
        if name_uuid:
            self._stamp(name_uuid, sha, dry_run)   # seed the index on a name hit
            return (name_uuid, True, sha)
        return (None, False, sha)

    def _content_lookup(self, sha):
        """Return an existing record uuid for this sha (local index -> DT search), or None."""
        # 1. local index (also the only path that sees AI-excluded records)
        uuid = self._index.get(sha)
        if uuid and self._record_exists(uuid):
            return uuid
        if uuid:  # stale entry — record gone
            self._index.pop(sha, None)
            self._save_index()
        # 2. DEVONthink content-metadata search
        try:
            res = self.mcp.search_records(f"{SHA_SEARCH}:{sha}", limit=1)
            results = res.get("results", []) if isinstance(res, dict) \
                else (res if isinstance(res, list) else [])
            if results and results[0].get("uuid"):
                return results[0]["uuid"]
        except Exception as e:
            logger.warning(f"content-dedup: metadata search failed: {e}")
        return None

    # ---- post-import safety net ----

    def reconcile_after_import(self, new_uuid, sha, dry_run=False):
        """After importing `new_uuid`, check DEVONthink's native content duplicates.

        mode=off    -> no-op, returns new_uuid.
        mode=shadow -> LOG whether a pre-existing duplicate exists (would adopt/trash),
                       returns new_uuid, mutates nothing.
        mode=live   -> adopt the oldest pre-existing duplicate, trash new_uuid, stamp
                       the survivor; returns the survivor uuid.
        """
        if self.mode == "off" or not new_uuid:
            return new_uuid

        try:
            dups = self.mcp.get_record_duplicates(new_uuid)
        except Exception as e:
            logger.warning(f"content-dedup: get_record_duplicates failed for "
                           f"{new_uuid}: {e}")
            dups = []
        pre_existing = [d for d in dups
                        if d.get("uuid") and d.get("uuid") != new_uuid]

        oldest = sorted(
            pre_existing,
            key=lambda d: d.get("additionDate") or d.get("creationDate") or "",
        )[0] if pre_existing else None

        if not self._acts(dry_run):
            # shadow / dry-run: observe only.
            if oldest:
                logger.info(f"DEDUP-OBSERVE post-import: new import {new_uuid} duplicates "
                            f"{len(pre_existing)} existing record(s); WOULD adopt "
                            f"{oldest.get('uuid')} ({oldest.get('name','?')!r}) and trash "
                            f"{new_uuid}")
            return new_uuid

        # live
        if oldest:
            orig_uuid = oldest.get("uuid")
            logger.info(f"content-dedup: content duplicate — adopting {orig_uuid}, "
                        f"trashing new import {new_uuid}")
            try:
                self.mcp.trash_record(new_uuid)
            except Exception as e:
                logger.warning(f"content-dedup: trash of {new_uuid} failed "
                               f"(keeping it to avoid a dangling link): {e}")
                return new_uuid
            self._stamp(orig_uuid, sha, dry_run)
            return orig_uuid

        # no duplicate: stamp the new record so future imports short-circuit
        self._stamp(new_uuid, sha, dry_run)
        return new_uuid
