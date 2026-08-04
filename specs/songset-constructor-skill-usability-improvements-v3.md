# Songset Constructor Skill — Usability Improvements v3

**Date:** 2026-08-03
**Skill location:** `lab/skills/songset-constructor/` (canonical; sole target of this spec)
**Spec type:** Documentation + minor code enhancement
**Supersedes:** `specs/songset-constructor-skill-usability-improvements-v1.md`, `specs/songset-constructor-skill-usability-improvements-v2.md`
**Audience:** Fresh implementing agent — this spec is self-contained; v1/v2 do not need to be open alongside.

## Goal

Fix six usability gaps discovered during real-world use of the songset-constructor skill. All six caused the agent to fail, produce corrupted artifacts, or require manual database introspection to proceed. The fixes are primarily **documentation** (`SKILL.md` + `README.md`), with one small **code enhancement** (add `--album-series` to `semantic_search.py`) and a **docstring correction** in `sow-admin songset create`.

This v3 spec incorporates v1 + v2 review feedback and adds:

- **Explicit `[REPLACE]` / `[INSERT]` / `[APPEND]` / `[PRESERVE]` markers** on every SKILL.md edit, so the implementer never accidentally deletes existing valuable content (theme-fusion weights in Step 3, CFD ≤ 6 rationale in Step 4, etc.).
- **Canonical-path policy** stated explicitly: `lab/skills/songset-constructor/` is the only target. The `.agents/skills/songset-constructor/` mirror is **out of scope** for this spec.
- **A quoted-heredoc convention** (`<<'EOF'`) for every multi-line inline Python invocation, which is more robust than multi-line `python -c "..."` strings in agent terminal environments.
- **An explicit `_theme_vocab_search → _keyword_search` fallback threading diff** for Issue 5, so the `--album-series` filter is not silently lost when no themes match.
- **Defensive trim at Step 12** in addition to the new plan-time constraint at Step 5.

## Scope Clarifications

- **Canonical path:** `lab/skills/songset-constructor/` is the only location modified by this spec. OpenCode's session-side `.agents/skills/songset-constructor/` reference is treated as an externally-managed mirror — out of scope here. If a future task syncs the two, it is a separate spec.
- **5-song cap UX:** The plan-time constraint is added at Step 5 (H0 row + planning guidelines). Step 12 keeps a defensive trim in case a malformed proposal slips through.
- **Step 12 default:** Step 12 (persist songset) remains **optional**. The agent only invokes `sow-admin songset create` when the user explicitly requests persistence.
- **Pipeline robustness:** Out of scope. No `pipefail`, per-step smoke tests, or stream-contract tutorials are added in this v3.
- **Machine-readable series discovery:** Out of scope. `sow-admin catalog list --albums --sort series` remains a Rich-table output; the agent parses the table. No new flags are added.

## Background — Root Cause Analysis

### Issue 1: Terminal lacked `python` — zsh couldn't find it

The agent's shell environment had no `python` on `PATH` (only `python3` via `uv run`). When the agent ran `python scripts/fetch_pool.py ... > /tmp/pool_raw.json`, zsh failed to find `python`, the redirect created an **empty** `/tmp/pool_raw.json`, and downstream `enrich_pool.py` failed with a JSON decode error on the empty file.

**Root cause:** SKILL.md:29-31 documents the `uv run --project ops/admin-cli --extra admin --extra constructor python ...` invocation, but the agent sometimes strips the `uv run` prefix when constructing shell commands, assuming a bare `python` exists. The SKILL.md does not explicitly warn that **bare `python` is not on PATH** and that omitting `uv run` will silently produce empty output files due to the redirect. In addition, the existing Step 3/4/6/10 examples show bare script paths, and every script's Usage docstring shows `python <script>.py`, reinforcing the wrong pattern.

**Relevant files:**
- `lab/skills/songset-constructor/SKILL.md:27-34` (Script Invocation section) and Step 3/4/6/10 examples
- `lab/skills/songset-constructor/scripts/*.py` (Usage docstrings)
- `lab/skills/songset-constructor/scripts/preflight.sh:77,90` (already uses `uv run` internally)

### Issue 2: Transitions JSON wrapper mismatch

`build_transitions.py:70` outputs `{"transitions": [...], "pool": [...]}` — a JSON **object** with two keys. But `score_songset.py:67-71` expects a top-level object with `items`, `pool`, `transitions`, `config` keys.

The mismatch: when piping `build_transitions.py` output directly into `score_songset.py`, the agent must **unwrap** the `{"transitions": [...], "pool": [...]}` object and re-wrap it into the score_songset input format `{"items": [...], "pool": [...], "transitions": [...], "config": {...}}`. SKILL.md:77-82 shows the pipe `enrich_pool.py | build_transitions.py` but never documents that build_transitions output is a **wrapper object**, not a bare array. The agent tried to use the transitions list directly from build_transitions output, causing a shape mismatch.

**Root cause:** SKILL.md Step 4 (line 77-82) describes the transition matrix output fields but does not document the **output JSON shape** (`{"transitions": [...], "pool": [...]}`). SKILL.md Step 6 (line 128-129) shows the score_songset input shape but doesn't explain how to bridge from build_transitions output to score_songset input.

**Relevant files:**
- `lab/skills/songset-constructor/scripts/build_transitions.py:70` — outputs `{"transitions": [...], "pool": [...]}`
- `lab/skills/songset-constructor/scripts/score_songset.py:9-19,64-71` — expects `{"items": [...], "pool": [...], "transitions": [...], "config": {...}}`
- `lab/skills/songset-constructor/SKILL.md:75-82` (Step 4), `125-135` (Step 6)

### Issue 3: `album_series` discovery — no table listing exposed

The agent needed to find valid `album_series` values to pass to `fetch_pool.py --album-series`. The admin DB module (`ReadOnlyClient`) doesn't expose a `list_album_series()` method. The agent had to inspect `information_schema.columns` to discover that the `songs` table uses `id` (not `song_id`), that the user table is named `user` (not `users`), and then query `SELECT DISTINCT album_series FROM songs` directly.

**Root cause:** SKILL.md Step 2 (line 49-57) documents `--album-series "敬拜讚美 (1)"` as a filter but never tells the agent **how to discover available album series values**. The Admin CLI already has `sow-admin catalog list --albums` (`catalog.py:717-720, 766-793`) which lists album names + series + song counts, but this is not documented in the skill.

**Relevant files:**
- `ops/admin-cli/src/stream_of_worship/admin/commands/catalog.py:691-793` — `catalog list --albums` command
- `lab/skills/songset-constructor/SKILL.md:47-57` (Step 2 — Fetch Catalog Pool)

### Issue 4: Song resolution format — slug IDs failed with `songset create`

The agent extracted `song_id` values (e.g., `wo_de_ye_su_4c27d159`) from the enriched pool and passed them to `sow-admin songset create`. The command failed with "No song found" because the agent used the **catalog slug ID** format.

However, examining `_songset_create_helpers.py:15,53-57`, the `resolve_song_token()` function **does** accept slug IDs — the regex `^[a-z0-9_]+_[0-9a-f]{8}$` matches `wo_de_ye_su_4c27d159`. The `get_song(token)` lookup at line 54 should work. The actual failure was that the agent passed **truncated or malformed** slug IDs, or passed the `recording_hash_prefix` (a 12-char hex like `a1b2c3d4e5f6`) instead of the `song_id` field.

**Root cause:** SKILL.md does not document the `sow-admin songset create` command at all. The agent had no guidance on which field from the SongCandidate object to use (`song_id` vs `recording_hash_prefix`), what format `song_id` has (`{slug}_{8-hex-chars}`), or that `songset create` accepts both slug IDs and fuzzy title search.

**Relevant files:**
- `ops/admin-cli/src/stream_of_worship/admin/commands/_songset_create_helpers.py:15,18-96` — `resolve_song_token()` with slug ID regex
- `ops/admin-cli/src/stream_of_worship/admin/services/catalog_edit.py:65-77` — `compute_song_id()` generates `{slug}_{8-char-sha256-hex}`
- `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py:650-895` — `songset create` command
- `lab/skills/songset-constructor/SKILL.md` — no Step 12 for persistence

### Issue 5: Semantic search can't limit to album series

`semantic_search.py` has no `--album-series` flag. The SQL queries (lines 152-168, 215-235) scan the entire `songs` table with no `album_series` filter. When the agent needs to find songs within a specific album series (e.g., only "敬拜讚美 (1)"), it must run semantic_search, get results, then manually intersect with a fetch_pool result set — fragile and slow.

**Root cause:** `semantic_search.py` was designed for global catalog search and never had an album-series filter. `fetch_pool.py:31-35` has `--album-series` (repeatable), but `semantic_search.py:29-46` does not.

**Fallback threading hazard:** `_theme_vocab_search` (line 209) falls back to `_keyword_search` when no themes match. The `album_series` parameter **must** be threaded through this fallback call as well, or the filter is silently lost on the fallback path.

**Relevant files:**
- `lab/skills/songset-constructor/scripts/semantic_search.py:28-46` (argparse), `122-188` (`_pgvector_search`), `191-256` (`_theme_vocab_search`), `73-99` (`_keyword_search`)
- `lab/skills/songset-constructor/scripts/fetch_pool.py:31-35` (has `--album-series`)
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/db.py:35` — `POOL_QUERY` filter pattern
- `lab/skills/songset-constructor/SKILL.md:119` (documents `semantic_search.py` without album-series)

### Issue 6: Need to document `sow-admin songset create` with song_id

The agent needs to persist the top-ranked proposal to the database as a songset. SKILL.md has no Step 12 for persistence. The agent doesn't know:
- That `sow-admin songset create` accepts slug IDs like `wo_de_ye_su_4c27d159` (not `song_XXXX`-style IDs — the docstring example at `songset.py:716-717` uses `song_0123` which is a **placeholder**, not the real format)
- That ambiguous titles should be avoided by passing the `song_id` field from SongCandidate directly
- That `--user` or `SOW_DEFAULT_USER` env var is required
- That `--dry-run` can validate resolution without persisting
- That `--yes` skips the confirmation prompt (needed for non-interactive agent runs)

**Root cause:** No documentation in SKILL.md or README.md about the persistence step, plus misleading placeholder IDs in the command's own help text.

**Relevant files:**
- `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py:650-895` and `715-724` (docstring examples)
- `ops/admin-cli/src/stream_of_worship/admin/commands/_songset_create_helpers.py:18-96`
- `lab/skills/songset-constructor/SKILL.md` — missing Step 12

## Conventions Used in This Spec

### Edit markers

Every SKILL.md / README.md / source-file edit carries one of the following markers. The implementer applies edits using these markers as the authority — anything not explicitly marked stays untouched.

- **`[REPLACE lines X-Y]`** — Completely remove the current lines X through Y and substitute the new content shown.
- **`[INSERT AFTER line N]`** — Add the new content immediately after line N. Existing content above and below is preserved.
- **`[INSERT BEFORE line N]`** — Add the new content immediately before line N.
- **`[APPEND to section "<name>"]`** — Add the new content at the end of the named section.
- **`[PRESERVE lines X-Y]`** — Explicit note that lines X-Y must remain unchanged. Used inside a `[REPLACE]` block to clarify which surrounding lines stay.

> **Note on line numbers:** Line numbers refer to the file's *current* state before any edits from this spec are applied. If multiple edits target the same file, apply them top-to-bottom; later markers continue to reference the original numbering unless explicitly described as relative to an earlier marker.

### Quoted heredoc for inline Python

All multi-line `python` invocations in this spec use the **quoted heredoc** form:

```bash
uv run --project ops/admin-cli --extra admin --extra constructor python <<'EOF'
import json, sys
# ... code ...
EOF
```

The quoted sentinel (`<<'EOF'` instead of `<<EOF`) prevents shell variable/backtick/`$` expansion inside the Python source. This is more robust than multi-line `python -c "..."` strings, particularly in agent terminal environments where escaping can be lossy.

## Design Decisions

1. **Issue 1 (python not on PATH):**
   - Add a prominent warning box in SKILL.md's Script Invocation section that bare `python` is not available; all scripts MUST be run via `uv run --project ops/admin-cli --extra admin --extra constructor python <script>.py`.
   - Document the available extra groups (`admin`, `constructor`) and what they provide.
   - Add a troubleshooting note about empty output files from failed `python` invocations.
   - **Update all SKILL.md Step examples** (Steps 3, 4, 6, 10) to use the full `uv run` prefix.
   - **Update Usage docstrings in every skill script** (`fetch_pool.py`, `enrich_pool.py`, `build_transitions.py`, `score_songset.py`, `semantic_search.py`, `write_report.py`, `get_lyrics.py`) to show the full `uv run` invocation instead of bare `python <script>.py`.

2. **Issue 2 (transitions JSON wrapper):**
   - Add explicit "Output Shape" documentation to SKILL.md Step 4 showing the `{"transitions": [...], "pool": [...]}` wrapper.
   - Add a "Bridging to score_songset.py" subsection in Step 6 showing how to extract `transitions` and `pool` from build_transitions output and combine them with `items` and `config` into the score_songset input.
   - Provide the bridge as a **quoted-heredoc** Python snippet that writes `/tmp/score_input.json`.

3. **Issue 3 (album_series discovery):**
   - Add a "Discovering Album Series" subsection to SKILL.md Step 2 documenting `sow-admin catalog list --albums --sort series` as the canonical way to find available album series values.
   - Show example output format and note that `--sort series` groups by series number.

4. **Issue 4 (song resolution format):**
   - Document in the new Step 12 that `song_id` from SongCandidate (format: `{slug}_{8-hex-chars}`, e.g., `wo_de_ye_su_4c27d159`) is the correct token to pass to `sow-admin songset create`.
   - Warn that `recording_hash_prefix` (12-char hex) is NOT a valid songset create token.
   - Warn that the docstring example `song_0123` is a placeholder — real IDs are slug-based.

5. **Issue 5 (semantic search album-series):**
   - Add `--album-series` (repeatable) flag to `semantic_search.py`.
   - Modify `_pgvector_search` and `_theme_vocab_search` SQL queries to add `AND (cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s))` clause, mirroring `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/db.py:35` (`POOL_QUERY`).
   - Add post-filter to `_keyword_search` using **exact string equality** against `song.album_series`. Note: `song.album_series` may be `None`; the `None not in album_series` Python check correctly filters out NULL-series songs, matching the SQL behaviour (`ANY` never matches NULL).
   - **Thread `album_series` through `_theme_vocab_search`'s fallback call to `_keyword_search`** (currently line 209). Without this, the filter is silently lost when no themes match.
   - Normalize `args.album_series` from `None` to `[]` in `main()` before passing to search functions.
   - Document the new flag in SKILL.md Step 5's optional tools section.

6. **Issue 6 (document songset create):**
   - Add Step 12 — "Persist Songset to DB (Optional)" to SKILL.md.
   - Show how to extract `song_id` values from the top proposal's items, construct the `sow-admin songset create` command with `--user`, `--yes`, and optionally `--dry-run` first.
   - Include the `SOW_DEFAULT_USER` env var shortcut and the `--name` flag for custom naming.
   - Note the 5-song / 25-minute limits enforced by `songset create` and that proposals with >5 songs must be trimmed (see also Step 5 cap).
   - Correct the misleading `song_0123` placeholders in `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py` docstring examples to use real slug-style IDs.

7. **Step 5 plan-time cap (new in v3):**
   - Add a hard-cap note in SKILL.md Step 5 planning guidelines: "Hard cap: `count ≤ 5` (enforced by `SONGSET_MAX_SONGS`; exceeding this fails at `songset create` time, not earlier). Never draft a proposal with more than 5 songs."
   - Step 12 keeps a defensive trim instruction in case a malformed proposal slips through.

## Files to Change

### 1. `lab/skills/songset-constructor/SKILL.md` (documentation — primary)

#### 1a. Script Invocation section

**`[REPLACE lines 27-34]`** with:

````markdown
## Script Invocation

> **CRITICAL:** Bare `python` is NOT on PATH in this environment. All scripts
> MUST be run via `uv run`. Omitting `uv run` will silently produce empty
> output files (the shell redirect creates the file before `python` fails,
> leaving a 0-byte file that breaks downstream JSON parsing).

All Python scripts run via:
```bash
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/<script>.py [OPTIONS]
```

**Extra groups required:**
- `--extra admin` — psycopg3, typer, rich, boto3, miniaudio, numpy (DB access, R2, config)
- `--extra constructor` — langgraph, pydantic, rapidfuzz, numpy (theme fusion, beam search, scoring)

Both are required for all skill scripts. The `preflight.sh` script already uses
`uv run` internally — invoke it with `bash lab/skills/songset-constructor/scripts/preflight.sh`
(no `uv run` prefix needed for the shell script itself).

**Troubleshooting empty output files:** If a downstream script fails with
`json.JSONDecodeError` on a file that should contain JSON, check that the
upstream command was invoked with `uv run` and that the file is not 0 bytes.

All scripts accept JSON via stdin or `--input <file>` and output JSON to stdout
(diagnostics to stderr).
````

#### 1b. Step 2 — Fetch Catalog Pool

**`[INSERT AFTER line 48]`** (the `### Step 2 — Fetch Catalog Pool` heading) — insert the new "Discovering Album Series" subsection immediately after the heading, **before** the existing "Run `scripts/fetch_pool.py`..." paragraph at line 49:

````markdown
**Discovering available album series:**
Before using `--album-series`, find valid values via the Admin CLI:
```bash
uv run --project ops/admin-cli --extra admin sow-admin catalog list --albums --sort series
```
This prints a table of album names, album series, and song counts. Use the
exact `album_series` string (e.g., `敬拜讚美 (1)`, `HYMN`, `CPW`) as the
`--album-series` argument. The `--albums` flag shows aggregated counts;
omit it to list individual songs.
````

**`[PRESERVE lines 49-57]`** — the existing introduction, flag list, and cache-staleness paragraphs after the heading remain unchanged.

#### 1c. Step 3 — Enrich Pool

**`[REPLACE lines 61-64]`** (the existing intro sentence + bare-paths code block) with:

````markdown
Pipe the raw pool JSON into `scripts/enrich_pool.py`:
```bash
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/fetch_pool.py \
    --pool-limit 500 --prefer-fresh \
    | uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/enrich_pool.py --season christmas
```
````

**`[PRESERVE lines 65-73]`** — the theme-fusion weights paragraph (line 66), the enrichment-summary review bullets (lines 68-71), and the early-stop check (line 73) all remain unchanged.

#### 1d. Step 4 — Build Transition Matrix

**`[REPLACE lines 77-80]`** (the existing intro sentence + bare-paths code block) with:

````markdown
Pipe the enriched pool into `scripts/build_transitions.py`:
```bash
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/enrich_pool.py ... \
    | uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/build_transitions.py
```

**Output shape:** `build_transitions.py` outputs a JSON **object** (not a bare array):
```json
{
  "transitions": [...],
  "pool": [...]
}
```
The `pool` array is the enriched pool with `fan_out` and `is_dead_end` fields
updated. The `transitions` array contains TransitionCandidate objects keyed
by `(from_hash_prefix, to_hash_prefix)`.

**Important:** This wrapper object cannot be piped directly into
`score_songset.py`. See Step 6 for bridging instructions.
````

**`[PRESERVE line 82]`** — the existing CFD ≤ 6 rationale + transition-fields paragraph remains unchanged.

#### 1e. Step 5 — Plan Songset(s)

**`[INSERT AFTER line 113]`** (the bullet `- Ensure phase doesn't drop by more than 1 between adjacent songs (H7)`) — insert as a new bullet between line 113 and line 114:

```markdown
- **Hard cap: `count ≤ 5`** (enforced by `SONGSET_MAX_SONGS`; exceeding this fails at `songset create` time, not earlier). Never draft a proposal with more than 5 songs.
```

**`[PRESERVE lines 84-113, 115-123]`** — the templates table, H0-H8 hard-constraints table, and remaining planning/optional-tool guidance remain unchanged.

#### 1f. Step 5 optional tools — semantic_search `--album-series`

**`[REPLACE line 119]`** (the existing single-line bullet for `semantic_search.py`) with:

````markdown
- Use `scripts/semantic_search.py --query "感恩" --limit 20` to find songs matching a specific theme you need to fill a template slot
  - Add `--album-series "敬拜讚美 (1)"` (repeatable) to restrict search to specific album series, mirroring `fetch_pool.py`'s filter
````

#### 1g. Step 6 — Score and Validate

**`[INSERT AFTER line 126]`** (the `### Step 6 — Score and Validate` heading) — insert the new "Bridging from build_transitions.py output to score_songset.py input" subsection immediately after the heading, **before** the existing "Submit your draft songset..." paragraph at line 127:

````markdown
**Bridging from `build_transitions.py` output to `score_songset.py` input:**

`build_transitions.py` outputs `{"transitions": [...], "pool": [...]}`.
`score_songset.py` expects `{"items": [...], "pool": [...], "transitions": [...], "config": {...}}`.

You must merge the transitions+pool from build_transitions with your draft
items and config. Example using a quoted heredoc (preferred over multi-line
`python -c "..."` strings because the quoted sentinel `<<'EOF'` suppresses
shell interpolation):

```bash
# Save build_transitions output to a file
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/enrich_pool.py ... \
    | uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/build_transitions.py \
    > /tmp/transitions_pool.json

# Build the score_songset input by merging with your draft items.
uv run --project ops/admin-cli --extra admin --extra constructor python <<'EOF'
import json

tp = json.load(open('/tmp/transitions_pool.json'))
payload = {
    'items': [
        {'position': 1, 'recording_hash_prefix': 'a1b2c3d4e5f6', 'key_shift_semitones': 0},
        # ... more draft items in position order ...
    ],
    'pool': tp['pool'],
    'transitions': tp['transitions'],
    'config': {'count': 4, 'intimate': False, 'relax_h1': True},
}
with open('/tmp/score_input.json', 'w') as f:
    json.dump(payload, f, ensure_ascii=False)
EOF

# Score the draft
uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/score_songset.py \
    --input /tmp/score_input.json
```
````

**`[REPLACE lines 127-130]`** (the existing "Submit your draft songset..." sentence + bare `echo | python score_songset.py` example) with:

````markdown
Submit your draft songset to `scripts/score_songset.py`:
```bash
echo '{"items": [...], "pool": [...], "transitions": [...], "config": {"count": 4, "intimate": false}}' \
    | uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/score_songset.py
```
````

**`[PRESERVE lines 132-145]`** — the "The script returns" bullets and "Score interpretation" table remain unchanged.

#### 1h. Step 10 — Write Report

**`[REPLACE lines 189-192]`** (the existing intro + bare-paths code block) with:

````markdown
Run `scripts/write_report.py` with the final proposals, pool, transitions, and config:
```bash
echo '{"proposals": [...], "pool": [...], "config": {...}, "transitions": [...], "summary": "..."}' \
    | uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/write_report.py \
        --output-dir output/songset_constructor/<timestamp>/
```
````

**`[PRESERVE lines 194-198]`** — the report-contents bullet list remains unchanged.

#### 1i. New Step 12 — Persist Songset to DB (Optional)

**`[APPEND to section "Workflow"]`** after the existing Step 11 content:

````markdown
### Step 12 — Persist Songset to DB (Optional)

If the user wants to persist the top-ranked proposal as a songset in the
database, use the Admin CLI `songset create` command.

**Song ID format:** The `song_id` field in SongCandidate objects follows the
format `{slug}_{8-char-hex}` (e.g., `wo_de_ye_su_4c27d159`). This is the
correct token to pass to `songset create`. Do NOT pass
`recording_hash_prefix` (a 12-char hex like `a1b2c3d4e5f6`) — it is not a
valid song ID.

> **Note:** The `songset create` docstring example previously used `song_0123`
> as a placeholder. Real song IDs are slug-based (e.g., `wo_de_ye_su_4c27d159`),
> not numeric `song_XXXX` IDs.

**Defensive trim:** Step 5 already constrains `count ≤ 5` at plan time. As a
belt-and-suspenders check, verify the top proposal has ≤ 5 items before
calling `songset create`; if it does not, keep items by `position` ascending
and truncate to 5.

**Extract song_ids from the top proposal:**
The top proposal's `items` list contains `song_id` fields in position order.
Extract them and pass to `songset create`:

```bash
# Set the user email (or pass --user each time)
export SOW_DEFAULT_USER=alice@example.com

# Extract song_ids from the top proposal (assumes /tmp/top_proposal.json).
# Quoted heredoc suppresses shell interpolation inside the Python source.
SONG_IDS=$(uv run --project ops/admin-cli --extra admin --extra constructor python <<'EOF'
import json
p = json.load(open('/tmp/top_proposal.json'))
print(' '.join(item['song_id'] for item in sorted(p['items'], key=lambda x: x['position'])))
EOF
)

# Defensive trim: keep first 5 if oversize slipped through
SONG_IDS=$(echo "$SONG_IDS" | awk '{for(i=1;i<=5 && i<=NF;i++) printf "%s%s", $i, (i<5 && i<NF ? OFS : ORS)}')

# Dry-run first to validate resolution
uv run --project ops/admin-cli --extra admin sow-admin songset create \
    $SONG_IDS --dry-run --yes

# Persist for real
uv run --project ops/admin-cli --extra admin sow-admin songset create \
    $SONG_IDS --name "Sunday_Worship_Set_1" --yes
```

**Flags:**
- `--user <email>` / `-u` — Owner email (or set `SOW_DEFAULT_USER` env var)
- `--name <name>` / `-n` — Custom songset name (auto-generated from titles if omitted)
- `--yes` / `-y` — Skip confirmation prompt (required for non-interactive agent runs)
- `--dry-run` — Resolve + validate but skip DB writes (recommended first)
- `--description <text>` / `-d` — Optional description

**Constraints enforced by songset create:**
- Max 5 songs (`SONGSET_MAX_SONGS`)
- Max 25 minutes total recording duration (`SONGSET_MAX_DURATION_SECONDS=1500`)
- Latest active recording selected automatically (latest-active-wins rule)

**Avoiding ambiguous title matches:** If you pass a title instead of a song_id
and multiple songs match, the command errors in `--yes` mode. Always use the
`song_id` field from SongCandidate for deterministic resolution.
````

### 2. `lab/skills/songset-constructor/README.md` (documentation — secondary)

**`[APPEND to file]`** — add the following new sections after the existing content:

````markdown
## Script Reference

| Script | Input | Output |
|--------|-------|--------|
| `preflight.sh` | none | exit code 0/1 + diagnostic text to stdout |
| `fetch_pool.py` | CLI flags | JSON array of SongCandidate (stdout) |
| `enrich_pool.py` | JSON array of SongCandidate (stdin/`--input`) | JSON array of enriched SongCandidate (stdout) |
| `build_transitions.py` | JSON array of enriched SongCandidate (stdin/`--input`) | JSON object `{"transitions": [...], "pool": [...]}` (stdout) |
| `score_songset.py` | JSON object `{"items": [...], "pool": [...], "transitions": [...], "config": {...}}` (stdin/`--input`) | JSON object `{"score": {...}, "validation": {...}, "proposal": {...}}` (stdout) |
| `semantic_search.py` | CLI flags (`--query`, `--album-series` repeatable, `--limit`, etc.) | JSON array of song dicts (stdout) |
| `get_lyrics.py` | CLI flags (`--hash-prefix` or `--song-id`) | LRC/raw lyrics text (stdout) |
| `write_report.py` | JSON object `{"proposals": [...], "pool": [...], "config": {...}, "transitions": [...], "summary": "..."}` (stdin) | file path to `proposal_report.md` (stdout) |

## Pipeline Data Flow

```
fetch_pool.py → [raw pool array]
    ↓
enrich_pool.py → [enriched pool array]
    ↓
build_transitions.py → {"transitions": [...], "pool": [...]}
    ↓
(score_songset.py needs items + pool + transitions + config merged into one object)
    ↓
write_report.py → proposal_report.md
```

## Persisting a Songset

See SKILL.md Step 12 for using `sow-admin songset create` to persist the
top-ranked proposal. Use the `song_id` field (format: `{slug}_{8-hex}`,
e.g., `wo_de_ye_su_4c27d159`) from SongCandidate objects — not
`recording_hash_prefix`.
````

### 3. `lab/skills/songset-constructor/scripts/semantic_search.py` (code enhancement)

Add `--album-series` flag (repeatable) and filter SQL queries.

#### 3a. argparse — `[INSERT AFTER line 45]`

Insert the new argument before `args = parser.parse_args()` at line 46:

```python
    parser.add_argument(
        "--album-series",
        action="append",
        default=None,
        help='Filter by album series (e.g., "敬拜讚美 (1)"). Repeatable.',
    )
```

#### 3b. main() normalization — `[INSERT AFTER line 46]` (after `args = parser.parse_args()`)

```python
    album_series = args.album_series or []
```

Update the dispatch calls in lines 59-66 to pass `album_series` through:

**`[REPLACE lines 59-66]`** with:

```python
        if args.mode == "keyword" or (args.mode == "auto" and args.field):
            results = _keyword_search(read_client, args.query, args.field or "all", args.limit, album_series)
        elif args.mode == "semantic":
            results = _semantic_search(read_client, args.query, args.limit, album_series)
        else:  # auto
            results = _semantic_search(read_client, args.query, args.limit, album_series)
            if not results:
                results = _keyword_search(read_client, args.query, "all", args.limit, album_series)
```

#### 3c. `_pgvector_search` — `[REPLACE lines 122-128]` (function signature) and `[REPLACE lines 152-168]` (SQL + execute tuple)

New signature:
```python
def _pgvector_search(
    read_client: ReadOnlyClient,
    query: str,
    limit: int,
    api_key: str,
    base_url: str,
    album_series: list[str],
) -> list[dict]:
```

New SQL + execute:
```python
    cursor.execute(
        """
        SELECT s.id, s.title, s.title_pinyin, s.album_name, s.album_series,
               s.musical_key, r.hash_prefix, r.tempo_bpm, r.musical_mode,
               1 - (se.embedding <=> %s::vector) AS score
        FROM songs s
        JOIN recordings r ON s.id = r.song_id
        LEFT JOIN song_embedding se ON se.song_id = s.id
        WHERE se.embedding IS NOT NULL
          AND r.visibility_status IN ('published', 'review')
          AND r.deleted_at IS NULL
          AND s.deleted_at IS NULL
          AND (cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s))
        ORDER BY se.embedding <=> %s::vector
        LIMIT %s
        """,
        (str(embedding), album_series, album_series, str(embedding), limit),
    )
```

#### 3d. `_theme_vocab_search` — `[REPLACE lines 191-195]` (signature) and `[REPLACE lines 215-235]` (SQL + execute tuple) and `[REPLACE line 209]` (fallback threading)

New signature:
```python
def _theme_vocab_search(
    read_client: ReadOnlyClient,
    query: str,
    limit: int,
    album_series: list[str],
) -> list[dict]:
```

New SQL + execute:
```python
    cursor.execute(
        """
        SELECT s.id, s.title, s.title_pinyin, s.album_name, s.album_series,
               s.musical_key, r.hash_prefix, r.tempo_bpm, r.musical_mode,
               MAX(1 - (se.embedding <=> ta.embedding)) AS score
        FROM songs s
        JOIN recordings r ON s.id = r.song_id
        LEFT JOIN song_embedding se ON se.song_id = s.id
        CROSS JOIN theme_anchors ta
        WHERE ta.theme = ANY(%s)
          AND se.embedding IS NOT NULL
          AND r.visibility_status IN ('published', 'review')
          AND r.deleted_at IS NULL
          AND s.deleted_at IS NULL
          AND (cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s))
        GROUP BY s.id, s.title, s.title_pinyin, s.album_name, s.album_series,
                 s.musical_key, r.hash_prefix, r.tempo_bpm, r.musical_mode
        ORDER BY score DESC
        LIMIT %s
        """,
        (matched_themes, album_series, album_series, limit),
    )
```

**Fallback threading** — replace the existing recursive `_keyword_search` fallback (currently line 209):
```python
    if not matched_themes:
        # Fall back to keyword search
        return _keyword_search(read_client, query, "all", limit, album_series)
```
This is the critical change that prevents silent loss of the `--album-series` filter when no themes match.

#### 3e. `_keyword_search` — `[REPLACE lines 73-99]` (signature + body)

```python
def _keyword_search(
    read_client: ReadOnlyClient,
    query: str,
    field: str,
    limit: int,
    album_series: list[str],
) -> list[dict]:
    """Keyword search using ReadOnlyClient.search_songs (ILIKE)."""
    songs = read_client.search_songs(query, field=field, limit=limit)
    results = []
    for song in songs:
        if album_series and song.album_series not in album_series:
            continue
        recording = read_client.get_recording_by_song_id(song.id)
        results.append(
            {
                "song_id": song.id,
                "title": song.title,
                "title_pinyin": song.title_pinyin,
                "recording_hash_prefix": recording.hash_prefix if recording else None,
                "tempo_bpm": recording.tempo_bpm if recording else None,
                "musical_key": recording.musical_key if recording else song.musical_key,
                "musical_mode": recording.musical_mode if recording else None,
                "album_name": song.album_name,
                "album_series": song.album_series,
                "score": 1.0,
                "match_type": "keyword",
            }
        )
    return results
```

#### 3f. `_semantic_search` — update signature and dispatch

Update the existing `_semantic_search` (line 102) to accept `album_series: list[str]` and thread it into both `_pgvector_search(...)` (line 114) and `_theme_vocab_search(...)` (line 119):

```python
def _semantic_search(
    read_client: ReadOnlyClient,
    query: str,
    limit: int,
    album_series: list[str],
) -> list[dict]:
    """Semantic search using pgvector or theme-vocab fallback."""
    import os

    api_key = os.environ.get("SOW_EMBEDDING_API_KEY")
    base_url = os.environ.get("SOW_EMBEDDING_BASE_URL")

    if api_key and base_url:
        results = _pgvector_search(read_client, query, limit, api_key, base_url, album_series)
        if results:
            return results

    return _theme_vocab_search(read_client, query, limit, album_series)
```

### 4. Script Usage docstrings (all skill scripts)

Update the `Usage:` block in each of the following files to use the full `uv run` invocation:

- `lab/skills/songset-constructor/scripts/fetch_pool.py`
- `lab/skills/songset-constructor/scripts/enrich_pool.py`
- `lab/skills/songset-constructor/scripts/build_transitions.py`
- `lab/skills/songset-constructor/scripts/score_songset.py`
- `lab/skills/songset-constructor/scripts/semantic_search.py`
- `lab/skills/songset-constructor/scripts/write_report.py`
- `lab/skills/songset-constructor/scripts/get_lyrics.py`

Example replacement for `fetch_pool.py`:
```python
"""
Usage:
    uv run --project ops/admin-cli --extra admin --extra constructor python fetch_pool.py [--pool-limit 500] [--album-series "敬拜讚美 (1)"] [--no-cache]
"""
```

Apply the analogous pattern to each script's docstring. The full `uv run` prefix is the single source of truth.

### 5. `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py` (docstring correction)

**`[REPLACE lines 715-724]`** (the `Examples:` block in `create_songset`'s docstring) with:

```python
    Examples:
      sow-admin songset create --user alice@example.com \\
          wo_de_ye_su_4c27d159 "信實偉大" en_dian_zhi_lu_a1b2c3d4 "恩典之路"

      sow-admin songset create -u bob@example.com -n "Sunday_Set_1" \\
          wo_de_ye_su_4c27d159 en_dian_zhi_lu_a1b2c3d4 shi_jie_de_jie_zhi_d4e5f6a7 --yes

      # Use env var for batch:
      export SOW_DEFAULT_USER=alice@example.com
      sow-admin songset create wo_de_ye_su_4c27d159 en_dian_zhi_lu_a1b2c3d4 -y
```

The `song_0123`-style placeholders are misleading — real song IDs are slug-based
(`{slug}_{8-char-hex}`) as produced by `compute_song_id()` in
`ops/admin-cli/src/stream_of_worship/admin/services/catalog_edit.py:65-77`.

## Verification

After implementation, verify each item below. Steps that reproduce a bug from the **pre-change** state will fail; re-run them after the change is applied.

1. **Issue 1:**
   - Run `bash lab/skills/songset-constructor/scripts/preflight.sh` — should exit 0 (or exit 0 with WARN if cache is available and DB is unreachable).
   - Run `fetch_pool.py` with the full `uv run` prefix and a redirect to a file — the file should be non-empty JSON.
   - `grep -nE "^\s*python\s" lab/skills/songset-constructor/SKILL.md lab/skills/songset-constructor/scripts/*.py` — no matches for bare `python <script>.py` invocations in Usage docstrings or SKILL.md examples (the only acceptable `python` token is inside the full `uv run ... python ...` form).

2. **Issue 2:** Pipe `build_transitions.py` output through:
   ```bash
   uv run --project ops/admin-cli --extra admin --extra constructor python <<'EOF'
   import json, sys
   d = json.load(sys.stdin)
   print(type(d).__name__, list(d.keys()))
   EOF
   ```
   — should print `dict ['transitions', 'pool']`.

3. **Issue 3:** Run `uv run --project ops/admin-cli --extra admin sow-admin catalog list --albums --sort series` — should print a table of album series with song counts.

4. **Issue 4:** Extract a `song_id` from an enriched pool JSON and pass it to `uv run --project ops/admin-cli --extra admin sow-admin songset create --dry-run --yes <song_id>` — should resolve successfully (assuming `SOW_DEFAULT_USER` is set or `--user` is passed).

5. **Issue 5:** Run:
   ```bash
   uv run --project ops/admin-cli --extra admin --extra constructor python lab/skills/songset-constructor/scripts/semantic_search.py \
       --query "感恩" --album-series "敬拜讚美 (1)" --limit 5
   ```
   — every result's `album_series` field should equal `敬拜讚美 (1)`. Then force the theme-vocab fallback (e.g. by unsetting `SOW_EMBEDDING_API_KEY`) and re-run; the filter must still apply. Finally, force the keyword fallback path (e.g. `--query "asdfnotheme"`) and verify the filter still applies on that path too.

6. **Issue 6:** Follow SKILL.md Step 12 end-to-end against the **post-change** SKILL.md: extract song_ids from the top proposal, run `sow-admin songset create --dry-run --yes`, then (if desired) persist for real. Confirm `sow-admin songset create --help` examples show slug-style IDs, not `song_0123`.

7. **Step 5 cap:** Open the post-change SKILL.md and confirm Step 5 contains a bullet stating the hard `count ≤ 5` cap.

## Non-Goals

- No changes to `build_transitions.py` output format (the wrapper object is correct; the fix is documentation).
- No changes to `score_songset.py` input format (the merged-object input is correct; the fix is documentation).
- No changes to `fetch_pool.py` (already has `--album-series`).
- No changes to `preflight.sh` (already uses `uv run` internally; `bash` invocation pattern is correct).
- No changes to `sow-admin songset create` command logic (already accepts slug IDs; the fix is documentation + docstring examples).
- No changes to `sow-admin catalog list` command logic (already has `--albums`; the fix is documentation).
- No new machine-readable flag for album-series discovery (deferred; out of scope).
- No `pipefail` / stream-contract / per-step smoke-test additions to SKILL.md (deferred).
- No sync or mirroring between `lab/skills/songset-constructor/` and `.agents/skills/songset-constructor/` (managed externally; out of scope).
