# Songset Constructor Skill — Usability Improvements

**Date:** 2026-08-03
**Skill location:** `lab/skills/songset-constructor/`
**Spec type:** Documentation + minor code enhancement (no implementation in this spec)

## Goal

Fix six usability gaps discovered during real-world use of the songset-constructor skill. All six issues caused the agent to either fail, produce corrupted artifacts, or require manual database introspection to proceed. The fixes are primarily **documentation** (SKILL.md + README.md) with one small **code enhancement** (add `--album-series` to `semantic_search.py`).

## Background — Root Cause Analysis

### Issue 1: Terminal lacked `python` — zsh couldn't find it

The agent's shell environment had no `python` on `PATH` (only `python3` via `uv run`). When the agent ran `python scripts/fetch_pool.py ... > /tmp/pool_raw.json`, zsh failed to find `python`, the redirect created an **empty** `/tmp/pool_raw.json`, and downstream `enrich_pool.py` failed with a JSON decode error on the empty file.

**Root cause:** SKILL.md:29-31 documents the `uv run --project ops/admin-cli --extra admin --extra constructor python ...` invocation, but the agent sometimes strips the `uv run` prefix when constructing shell commands, assuming a bare `python` exists. The SKILL.md does not explicitly warn that **bare `python` is not on PATH** and that omitting `uv run` will silently produce empty output files due to the redirect.

**Relevant files:**
- `lab/skills/songset-constructor/SKILL.md:27-34` (Script Invocation section)
- `lab/skills/songset-constructor/scripts/preflight.sh:77,90` (already uses `uv run` internally)

### Issue 2: Transitions JSON wrapper mismatch

`build_transitions.py:70` outputs `{"transitions": [...], "pool": [...]}` — a JSON **object** with two keys. But `score_songset.py:67-71` expects `data.get("transitions", [])` as a top-level array within the input object, and `write_report.py` also expects `transitions` as a list inside the input object.

The mismatch: when piping `build_transitions.py` output directly into `score_songset.py`, the agent must **unwrap** the `{"transitions": [...], "pool": [...]}` object and re-wrap it into the score_songset input format `{"items": [...], "pool": [...], "transitions": [...], "config": {...}}`. SKILL.md:77-82 shows the pipe `enrich_pool.py | build_transitions.py` but never documents that build_transitions output is a **wrapper object**, not a bare array. The agent tried to use the transitions list directly from build_transitions output, causing a shape mismatch.

**Root cause:** SKILL.md Step 4 (line 77-82) describes the transition matrix output fields but does not document the **output JSON shape** (`{"transitions": [...], "pool": [...]}`). SKILL.md Step 6 (line 128-129) shows the score_songset input shape but doesn't explain how to bridge from build_transitions output to score_songset input.

**Relevant files:**
- `lab/skills/songset-constructor/scripts/build_transitions.py:70` — outputs `{"transitions": [...], "pool": [...]}`
- `lab/skills/songset-constructor/scripts/score_songset.py:9-19,64-71` — expects `{"items": [...], "pool": [...], "transitions": [...], "config": {...}}`
- `lab/skills/songset-constructor/SKILL.md:75-82` (Step 4), `125-135` (Step 6)

### Issue 3: `album_series` discovery — no table listing exposed

The agent needed to find valid `album_series` values to pass to `fetch_pool.py --album-series`. The admin DB module (`ReadOnlyClient`) doesn't expose a `list_album_series()` method. The agent had to inspect `information_schema.columns` to discover that the `songs` table uses `id` (not `song_id`), that the user table is named `user` (not `users`), and then query `SELECT DISTINCT album_series FROM songs` directly.

**Root cause:** SKILL.md Step 2 (line 49-57) documents `--album-series "敬拜讚美 (1)"` as a filter but never tells the agent **how to discover available album series values**. The Admin CLI already has `sow-admin catalog list --albums` (catalog.py:717-720, 766-793) which lists album names + series + song counts, but this is not documented in the skill.

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

**Relevant files:**
- `lab/skills/songset-constructor/scripts/semantic_search.py:28-46` (argparse), `122-188` (`_pgvector_search`), `191-256` (`_theme_vocab_search`)
- `lab/skills/songset-constructor/scripts/fetch_pool.py:31-35` (has `--album-series`)
- `lab/skills/songset-constructor/SKILL.md:119` (documents `semantic_search.py` without album-series)

### Issue 6: Need to document `sow-admin songset create` with song_id

The agent needs to persist the top-ranked proposal to the database as a songset. SKILL.md has no Step 12 for persistence. The agent doesn't know:
- That `sow-admin songset create` accepts slug IDs like `wo_de_ye_su_4c27d159` (not `song_XXXX`-style IDs — the docstring example at songset.py:716-717 uses `song_0123` which is a **placeholder**, not the real format)
- That ambiguous titles should be avoided by passing the `song_id` field from SongCandidate directly
- That `--user` or `SOW_DEFAULT_USER` env var is required
- That `--dry-run` can validate resolution without persisting
- That `--yes` skips the confirmation prompt (needed for non-interactive agent runs)

**Root cause:** No documentation in SKILL.md or README.md about the persistence step.

**Relevant files:**
- `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py:650-895`
- `ops/admin-cli/src/stream_of_worship/admin/commands/_songset_create_helpers.py:18-96`
- `lab/skills/songset-constructor/SKILL.md` — missing Step 12

## Design Decisions

1. **Issue 1 (python not on PATH):** Add a prominent warning box in SKILL.md's Script Invocation section that bare `python` is not available; all scripts MUST be run via `uv run --project ops/admin-cli --extra admin --extra constructor python <script>.py`. Document the available extra groups (`admin`, `constructor`) and what they provide. Add a troubleshooting note about empty output files from failed `python` invocations.

2. **Issue 2 (transitions JSON wrapper):** Add explicit "Output Shape" documentation to SKILL.md Step 4 showing the `{"transitions": [...], "pool": [...]}` wrapper. Add a "Bridging to score_songset.py" subsection in Step 6 showing how to extract `transitions` and `pool` from build_transitions output and combine them with `items` and `config` into the score_songset input. Add a concrete jq or Python one-liner example.

3. **Issue 3 (album_series discovery):** Add a "Discovering Album Series" subsection to SKILL.md Step 2 documenting `sow-admin catalog list --albums` as the canonical way to find available album series values. Show example output format. Note that `--sort series` groups by series number.

4. **Issue 4 (song resolution format):** Document in the new Step 12 that `song_id` from SongCandidate (format: `{slug}_{8-hex-chars}`, e.g., `wo_de_ye_su_4c27d159`) is the correct token to pass to `sow-admin songset create`. Warn that `recording_hash_prefix` (12-char hex) is NOT a valid songset create token. Warn that the docstring example `song_0123` is a placeholder — real IDs are slug-based.

5. **Issue 5 (semantic search album-series):** Add `--album-series` (repeatable) flag to `semantic_search.py`. Modify `_pgvector_search` and `_theme_vocab_search` SQL queries to add `AND (cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s))` clause, mirroring `fetch_pool.py`'s POOL_QUERY (db.py:35). Document the new flag in SKILL.md Step 5's optional tools section.

6. **Issue 6 (document songset create):** Add Step 12 — "Persist Songset to DB (Optional)" to SKILL.md. Show how to extract `song_id` values from the top proposal's items, construct the `sow-admin songset create` command with `--user`, `--yes`, and optionally `--dry-run` first. Include the `SOW_DEFAULT_USER` env var shortcut. Document the `--name` flag for custom naming.

## Files to Change

### 1. `lab/skills/songset-constructor/SKILL.md` (documentation — primary)

**Script Invocation section (lines 27-34):** Add warning about bare `python` not being on PATH. Document available extra groups:

```markdown
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
`uv run` internally — invoke it with `bash scripts/preflight.sh` (no `uv run`
prefix needed for the shell script itself).

**Troubleshooting empty output files:** If a downstream script fails with
`json.JSONDecodeError` on a file that should contain JSON, check that the
upstream command was invoked with `uv run` and that the file is not 0 bytes.
```

**Step 2 (lines 47-57):** Add "Discovering Album Series" subsection:

```markdown
### Step 2 — Fetch Catalog Pool

**Discovering available album series:**
Before using `--album-series`, find valid values via the Admin CLI:
```bash
uv run --project ops/admin-cli --extra admin sow-admin catalog list --albums --sort series
```
This prints a table of album names, album series, and song counts. Use the
exact `album_series` string (e.g., `敬拜讚美 (1)`, `HYMN`, `CPW`) as the
`--album-series` argument. The `--albums` flag shows aggregated counts;
omit it to list individual songs.

Run `scripts/fetch_pool.py` with appropriate flags:
...
```

**Step 4 (lines 75-82):** Add output shape documentation:

```markdown
### Step 4 — Build Transition Matrix

Pipe the enriched pool into `scripts/build_transitions.py`:
```bash
scripts/enrich_pool.py ... | scripts/build_transitions.py
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
```

**Step 5 optional tools (line 119):** Add `--album-series` to semantic_search:

```markdown
- Use `scripts/semantic_search.py --query "感恩" --limit 20` to find songs matching a specific theme you need to fill a template slot
  - Add `--album-series "敬拜讚美 (1)"` (repeatable) to restrict search to specific album series, mirroring `fetch_pool.py`'s filter
```

**Step 6 (lines 125-135):** Add bridging instructions:

```markdown
### Step 6 — Score and Validate

**Bridging from build_transitions.py output to score_songset.py input:**

`build_transitions.py` outputs `{"transitions": [...], "pool": [...]}`.
`score_songset.py` expects `{"items": [...], "pool": [...], "transitions": [...], "config": {...}}`.

You must merge the transitions+pool from build_transitions with your draft
items and config. Example using a temp file:

```bash
# Save build_transitions output
... | scripts/build_transitions.py > /tmp/transitions_pool.json

# Build the score_songset input by merging with your draft items
uv run --project ops/admin-cli --extra admin --extra constructor python -c "
import json
tp = json.load(open('/tmp/transitions_pool.json'))
payload = {
    'items': [
        {'position': 1, 'recording_hash_prefix': 'a1b2c3d4e5f6', 'key_shift_semitones': 0, ...},
        ...
    ],
    'pool': tp['pool'],
    'transitions': tp['transitions'],
    'config': {'count': 4, 'intimate': False, 'relax_h1': True, ...}
}
json.dump(payload, open('/tmp/score_input.json', 'w'), ensure_ascii=False)
"
scripts/score_songset.py --input /tmp/score_input.json
```

Submit your draft songset to `scripts/score_songset.py`:
...
```

**New Step 12 — Persist Songset to DB (Optional):** Add after Step 11:

```markdown
### Step 12 — Persist Songset to DB (Optional)

If the user wants to persist the top-ranked proposal as a songset in the
database, use the Admin CLI `songset create` command.

**Song ID format:** The `song_id` field in SongCandidate objects follows the
format `{slug}_{8-char-hex}` (e.g., `wo_de_ye_su_4c27d159`). This is the
correct token to pass to `songset create`. Do NOT pass
`recording_hash_prefix` (a 12-char hex like `a1b2c3d4e5f6`) — it is not a
valid song ID.

> **Note:** The `songset create` docstring example uses `song_0123` as a
> placeholder. Real song IDs are slug-based (e.g., `wo_de_ye_su_4c27d159`),
> not numeric `song_XXXX` IDs.

**Extract song_ids from the top proposal:**
The top proposal's `items` list contains `song_id` fields in position order.
Extract them and pass to `songset create`:

```bash
# Set the user email (or pass --user each time)
export SOW_DEFAULT_USER=alice@example.com

# Dry-run first to validate resolution
uv run --project ops/admin-cli --extra admin sow-admin songset create \
    wo_de_ye_su_4c27d159 en_dian_zhi_lu_a1b2c3d4 \
    --dry-run --yes

# Persist for real
uv run --project ops/admin-cli --extra admin sow-admin songset create \
    wo_de_ye_su_4c27d159 en_dian_zhi_lu_a1b2c3d4 \
    --name "Sunday_Worship_Set_1" --yes
```

**Flags:**
- `--user <email>` / `-u` — Owner email (or set `SOW_DEFAULT_USER` env var)
- `--name <name>` / `-n` — Custom songset name (auto-generated from titles if omitted)
- `--yes` / `-y` — Skip confirmation prompt (required for non-interactive agent runs)
- `--dry-run` — Resolve + validate but skip DB writes (recommended first)
- `--description <text>` / `-d` — Optional description

**Constraints enforced by songset create:**
- Max 5 songs (SONGSET_MAX_SONGS)
- Max 25 minutes total recording duration (SONGSET_MAX_DURATION_SECONDS=1500)
- Latest active recording selected automatically (latest-active-wins rule)

**Avoiding ambiguous title matches:** If you pass a title instead of a song_id
and multiple songs match, the command errors in `--yes` mode. Always use the
`song_id` field from SongCandidate for deterministic resolution.
```

### 2. `lab/skills/songset-constructor/README.md` (documentation — secondary)

Add a "Script Reference" section documenting each script's input/output shape:

```markdown
## Script Reference

| Script | Input | Output |
|--------|-------|--------|
| `preflight.sh` | none | exit code 0/1 + diagnostic text to stdout |
| `fetch_pool.py` | CLI flags | JSON array of SongCandidate (stdout) |
| `enrich_pool.py` | JSON array of SongCandidate (stdin/`--input`) | JSON array of enriched SongCandidate (stdout) |
| `build_transitions.py` | JSON array of enriched SongCandidate (stdin/`--input`) | JSON object `{"transitions": [...], "pool": [...]}` (stdout) |
| `score_songset.py` | JSON object `{"items": [...], "pool": [...], "transitions": [...], "config": {...}}` (stdin/`--input`) | JSON object `{"score": {...}, "validation": {...}, "proposal": {...}}` (stdout) |
| `semantic_search.py` | CLI flags (`--query`, `--album-series`) | JSON array of song dicts (stdout) |
| `get_lyrics.py` | CLI flags (`--hash-prefix` or `--song-id`) | LRC/raw lyrics text (stdout) |
| `write_report.py` | JSON object `{"proposals": [...], "pool": [...], "config": {...}, "transitions": [...]}` (stdin) | file path to `proposal_report.md` (stdout) |

### Pipeline Data Flow

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

### Persisting a Songset

See SKILL.md Step 12 for using `sow-admin songset create` to persist the
top-ranked proposal. Use the `song_id` field (format: `{slug}_{8-hex}`,
e.g., `wo_de_ye_su_4c27d159`) from SongCandidate objects — not
`recording_hash_prefix`.
```

### 3. `lab/skills/songset-constructor/scripts/semantic_search.py` (code enhancement)

Add `--album-series` flag (repeatable) and filter SQL queries:

**argparse (after line 45):**
```python
parser.add_argument(
    "--album-series",
    action="append",
    default=None,
    help='Filter by album series (e.g., "敬拜讚美 (1)"). Repeatable.',
)
```

**`_pgvector_search` (lines 152-168):** Add `AND (cardinality(%s::text[]) = 0 OR s.album_series = ANY(%s))` to the WHERE clause, passing `album_series` list twice (matching fetch_pool.py's POOL_QUERY pattern at db.py:35).

**`_theme_vocab_search` (lines 215-235):** Same filter clause added to the WHERE clause.

**`_keyword_search` (lines 73-99):** Add post-filter on `song.album_series` if `album_series` is set (simpler than modifying `search_songs` API).

**Pass-through:** `main()` passes `args.album_series` to `_semantic_search` and `_keyword_search`.

## Verification

After implementation, verify:

1. **Issue 1:** Run `bash scripts/preflight.sh` — should work without `python` on PATH. Run any script without `uv run` prefix — should fail with a clear error, not silently produce empty output.

2. **Issue 2:** Pipe `build_transitions.py` output through `python -c "import json,sys; d=json.load(sys.stdin); print(type(d), list(d.keys()))"` — should print `<class 'dict'> ['transitions', 'pool']`.

3. **Issue 3:** Run `uv run --project ops/admin-cli --extra admin sow-admin catalog list --albums --sort series` — should print a table of album series with song counts.

4. **Issue 4:** Extract a `song_id` from an enriched pool JSON and pass it to `sow-admin songset create --dry-run --yes <song_id>` — should resolve successfully.

5. **Issue 5:** Run `semantic_search.py --query "感恩" --album-series "敬拜讚美 (1)" --limit 5` — results should only contain songs from that album series.

6. **Issue 6:** Follow SKILL.md Step 12 end-to-end: extract song_ids from top proposal, run `songset create --dry-run --yes`, then persist for real.

## Non-Goals

- No changes to `build_transitions.py` output format (the wrapper object is correct; the fix is documentation)
- No changes to `score_songset.py` input format (the merged-object input is correct; the fix is documentation)
- No changes to `fetch_pool.py` (already has `--album-series`)
- No changes to `preflight.sh` (already uses `uv run` internally)
- No changes to `sow-admin songset create` command itself (already accepts slug IDs; the fix is documentation)
- No changes to `sow-admin catalog list` command (already has `--albums`; the fix is documentation)
- No script docstring updates (focusing on SKILL.md + README.md per decision)
