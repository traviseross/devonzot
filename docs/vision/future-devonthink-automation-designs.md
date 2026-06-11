# Future DEVONthink Automation Designs

Design notes for an autonomous LLM- and "See Also & Classify"-powered service
to identify, rename, tag, and otherwise process existing documents in
DEVONthink, plus a human-in-the-loop Claude ↔ DEVONthink MCP workflow.

## Context

- DEVONthink 4 on iMac (always-on) and MBP
- `src/devonthink_mcp.py` already in DEVONzot with a working MCP client
- Ollama running locally (`llama3.2:3b`)
- DEVONthink's built-in "See Also & Classify" AI for local, private operations
- Multiple databases with years of poorly-named documents: photorec dumps,
  scanner timestamp names, and old HDD recoveries never renamed after triage

---

## Two Distinct Modes

**Autonomous batch service** — unattended, processes the backlog and ongoing
ingestion. Speed and scale matter; individual errors are acceptable as long as
they're recoverable.

**Human-in-the-loop** — Claude Code + MCP as an interactive conversation about
your documents. You're the final authority; Claude is the analyst and actor.
Best for edge cases, complex docs, and anything worth reviewing before
committing.

These aren't competing designs — they're tiers of the same system.

---

## Tool Assignments

Each tool has a distinct job it's suited for:

**DEVONthink's See Also & Classify** → *where* should this live? It knows your
library, not just the document. A tax notice near other tax notices → files to
Personal/Tax. It can't generate names but gives filing context that an LLM has
no way to derive. Use it to pick the destination group; let the group path also
confirm tagging ("Insurance/State Farm" → `State Farm` tag validated).

**Ollama (llama3.2:3b)** → *what is this called?* Entity, type, date extraction
from OCR text. Fast, free, local. Already proven in scan-import. Keep
expectations calibrated: good at "State Farm - Bill - March 2026", not suited
for subtle analysis.

**Claude via MCP** → *what is this, really?* Ambiguous documents, cross-document
reasoning ("these 3 docs appear to be the same policy across years"), synthesis,
and interactive review. Not cost-effective per document in the batch path —
reserve for the human-in-loop tier and for documents the autonomous service
flags as uncertain.

---

## Autonomous Service Design

### Target discovery

Query by name pattern, not by content:
- `name:Document*` — scanner defaults
- `name:Scanned*` — scanner defaults
- `name:Image*` — photorec / camera rolls
- ISO date-only names (`2022-03-01`, etc.)
- Photorec patterns (`f*` with numeric suffix, etc.)

Optionally: tag absence (`tag:` field empty) as a secondary filter for
unprocessed documents.

### Skip guard

If a document has a non-generic name **and** tags already set, leave it alone.
The batch processor must be conservative by default. A `--all` flag for more
aggressive targeting is explicit opt-in.

### Pipeline per document

1. **Discover** — search_records by name pattern → build ordered queue
2. **Extract** — `extract_record_content`; check length (very short = poor OCR
   or image-only → flag, don't process)
3. **Name** — Ollama extracts `{issuing_entity}`, `{document_type}`,
   `{issue_date}`; `validate_date` bounds the date to ≤ today; fallback chain
   mirrors scan-import (`ollama_label_fallback` → mtime month/year)
4. **Classify** — `classify_record` suggests a destination group; confidence
   threshold gates auto-filing vs. flagging
5. **Tag** — `set_record_tags` with entity, document type, year at minimum
6. **Commit or queue** — above-threshold: `update_record` + `move_record`
   immediately; below-threshold: move to a DT4 "Review" group for the human tier
7. **Undo log** — append JSON record per change: `{uuid, before_name,
   after_name, before_tags, after_tags, before_group, after_group, timestamp}`
   so any change can be reversed

### Known-entities list

`config/known_entities.txt` (one per line, `#` for comments) is loaded at
startup and passed to Ollama as a soft constraint. The retitler's output is the
primary data source for building this list — run in dry-run mode first,
review the proposed entity names, curate the list, then apply.

---

## Human-in-the-Loop Design

### Option A: Claude Code skill (`/dt-review`)

Claude queries the DT4 "Review" group, pulls N documents, shows proposed
renames and filing targets, and you approve/reject/edit per document before
Claude commits. Batch review session in the terminal. Works today because the
DEVONthink MCP is already configured in Claude Code.

### Option B: Ambient smart conversation

Paste a DT4 link (`x-devonthink-item://UUID`) or UUID directly into a Claude
Code session. Claude calls `extract_record_content`, reasons about the document,
proposes a name + tags + destination, you respond with approval or correction.
No skill infrastructure needed. Good for one-off docs and spot-checking the
photorec dump in small batches.

---

## Recommended Implementation Sequence

1. **`retitle_records.py` with dry-run mode** — batch retitler targeting generic
   name patterns. Dry run prints proposed changes to stdout; apply mode commits
   with undo log. Validates rename logic against real data, safe to run
   repeatedly. This is the immediate value.

2. **Known-entities list** — built from retitler dry-run output and the
   scan-import log. Shared by both the batch service and the interactive path.

3. **`/dt-review` skill** — once rename logic is tuned from real data, wrap it
   in a Claude Code skill for the uncertain/flagged cases and ongoing use.

4. **Autonomous ongoing service** — only if the manual batch proves reliable
   enough to trust unattended. May not be necessary if the human-in-loop skill
   is fast enough for the volume.

---

## Open Questions

- **Confidence threshold**: what score from `classify_record` is high enough to
  auto-file vs. send to Review group? Needs calibration against real data.
- **OCR quality floor**: what minimum text length / density indicates usable OCR
  vs. flagging for manual review?
- **Scope beyond scans**: photorec dumps may include images, audio, and other
  non-text files — pipeline needs a content-type gate early.
- **Tag schema**: settle on a consistent tag vocabulary before the first real
  run so tags don't fragment (e.g., "State Farm" vs. "StateFarm" vs.
  "state-farm").
