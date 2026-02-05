# Project Structure Reorganization

**Status:** Proposal — pending review
**Branch:** `reorg_proj_structure`
**Date:** 2026-02-05

---

## Goals

1. All proof-of-concept work lives under a single `poc/` root — no `poc_*` directories scattered at the top level.
2. Production code stays at the project root in the standard Python `src/` layout. The project root *is* the production root; `poc/` is the carved-out exception.
3. Shared infrastructure (Docker, shared data, specs, docs) lives in clearly named root directories.
4. One `pyproject.toml` remains at the project root. See [rationale below](#pyprojecttoml-single-vs-separate).

### What changed from the first draft — and why

The first draft proposed wrapping all production work under a `production/` directory. Three sources of evidence pushed back:

- **`lyrics_video_generator_design.md` §8.2** and **`lyrics_video_implementation_plan.md` §1a** both keep `src/` at the project root with no wrapper.
- **`scripts/migrate_song_library.py`** imports the production package via `sys.path.insert(0, … / "src")`, assuming `src/` is one level up from `scripts/`. It also reads POC output *and* writes to production paths — it is an operational bridge script, not owned by either side.
- Standard Python src-layout and setuptools convention place `src/` at the repo root. A wrapper directory adds indirection for no packaging or tooling benefit.

The consolidation keeps the first draft's strongest contribution (collapsing scattered `poc_*` directories) while aligning everything else with the existing design specs.

---

## Current State

```
stream_of_worship/
│
│  ── POC-related (scattered) ──
├── poc/                            # POC scripts
├── poc_audio/                      # POC input audio (11 MP3s)
├── poc_output/                     # Librosa analysis output
├── poc_output_allinone/            # All-In-One (DL) analysis output
├── poc_output_allinone.1/          # Archived DL run
├── poc_output_allinone.3/          # Archived DL run
├── poc_output_comparison/          # Librosa vs All-In-One comparison
├── notebooks/                      # 01_POC_Analysis.ipynb
├── transition_builder_v2/          # Legacy TUI app (superseded by src/)
├── demix/                          # htdemucs stem-separation cache
├── setup_poc.sh
├── process_songs.sh
├── process_songs_native.sh
│
│  ── Production-related (scattered) ──
├── src/stream_of_worship/          # Production package (cli, core, ingestion, tui, tests)
├── output_transitions/             # Generated transition clips
├── output_songs/                   # Generated song-set outputs
│
│  ── Mixed ──
├── scripts/                        # Mix of POC test scripts and production utilities
│
│  ── Docker ──
├── Dockerfile
├── Dockerfile.allinone
├── docker-compose.yml
├── docker-compose.allinone.yml
├── .dockerignore
├── requirements.txt                # Librosa Docker env
├── requirements_allinone.txt       # All-In-One Docker env
│
│  ── Shared / root ──
├── assets/fonts/                   # Fonts for video generation
├── data/lyrics/                    # Shared lyrics database (686 songs)
├── resources/                      # Empty placeholder
├── audio_input/                    # Empty placeholder
├── audio_output/                   # Empty placeholder
├── specs/                          # Design docs
├── docs/                           # Implementation docs
├── pyproject.toml
├── uv.lock
├── .gitignore
├── CLAUDE.md
├── README.md
├── LICENSE
└── TODO.md
```

---

## Proposed State

```
stream_of_worship/
│
├── poc/                            # ── ALL proof-of-concept work ──
│   ├── poc_analysis.py             #    Scripts flat at poc/ root (no scripts/ subfolder)
│   ├── poc_analysis_allinone.py
│   ├── lyrics_scraper.py
│   ├── generate_transitions.py
│   ├── generate_section_transitions.py
│   ├── analyze_sections.py
│   ├── analyze_feedback.py
│   ├── review_transitions.py
│   ├── README.md                   #    POC documentation
│   ├── find_test_song.py           #    moved from scripts/
│   ├── test_allinone_analyze.py    #    moved from scripts/
│   ├── test_sensevoice.py          #    moved from scripts/
│   ├── test_whisper.py             #    moved from scripts/
│   ├── give_thanks.lrc             #    moved from scripts/ (sample used by POC tests)
│   ├── notebooks/                  #    Jupyter notebooks (was root notebooks/)
│   │   └── 01_POC_Analysis.ipynb
│   ├── audio/                      #    Input audio files (was poc_audio/)
│   │   └── *.mp3
│   ├── output/                     #    Librosa results (was poc_output/)
│   ├── output_allinone/            #    All-In-One results (was poc_output_allinone/)
│   ├── output_allinone_archive/    #    Archived runs (was poc_output_allinone.{1,3}/)
│   ├── output_comparison/          #    Comparison results (was poc_output_comparison/)
│   ├── transition_builder_v2/      #    Legacy TUI — archived as-is
│   ├── demix/                      #    htdemucs cache (was root demix/)
│   ├── setup.sh                    #    Was setup_poc.sh
│   ├── process_songs.sh            #    Was root process_songs.sh
│   └── process_songs_native.sh     #    Was root process_songs_native.sh
│
├── src/                            # ── Production package (unchanged location) ──
│   └── stream_of_worship/
│       ├── __init__.py
│       ├── cli/
│       ├── core/                   #    config.py, paths.py, catalog.py
│       ├── ingestion/              #    lrc_generator.py, metadata_generator.py
│       ├── tui/                    #    app, screens, services, models, utils
│       ├── assets/                 #    Bundled package data (fonts stay here)
│       │   └── fonts/
│       └── tests/                  #    unit/, integration/
│
├── docker/                         # ── Docker infrastructure (moved from root) ──
│   ├── Dockerfile                  #    Librosa image
│   ├── Dockerfile.allinone         #    All-In-One / PyTorch image
│   ├── docker-compose.yml          #    Librosa orchestration (volume paths updated)
│   ├── docker-compose.allinone.yml #    All-In-One orchestration (volume paths updated)
│   ├── .dockerignore
│   ├── requirements.txt            #    Librosa env (was root)
│   └── requirements_allinone.txt   #    All-In-One env (was root)
│
├── scripts/                        # ── Admin / bridge scripts (stays at root) ──
│   ├── migrate_song_library.py     #    Reads POC output → writes production data
│   └── generate_lrc.py             #    Production ingestion utility
│
├── data/                           # ── Shared data (unchanged) ──
│   └── lyrics/
│
├── output_transitions/             # ── Transitional output dirs (see note below) ──
├── output_songs/                   #    Long-term: move to platform-specific user dirs
│                                   #    per lyrics_video_generator_design.md §8.1
│
├── specs/                          # ── Design specifications (unchanged) ──
├── docs/                           # ── Implementation documentation (unchanged) ──
│
├── pyproject.toml                  # ── No changes needed ──
├── uv.lock
├── .gitignore                      #    Path patterns updated for poc/ consolidation
├── CLAUDE.md                       #    Directory references updated
├── README.md
├── LICENSE
└── TODO.md
```

**Output directories note:** `output_transitions/` and `output_songs/` stay at the project root rather than moving under a `production/` wrapper. `lyrics_video_generator_design.md` §8.1 specifies that production output ultimately moves to platform-specific user directories (e.g. `~/Library/Application Support/StreamOfWorship/output/`). Wrapping them now would create a path that gets deleted again at that migration. They stay at root and are marked transitional.

---

## Directory Mapping

| Current Path | New Path | Notes |
|---|---|---|
| `poc/*.py` | `poc/*.py` | Scripts stay flat at `poc/` root (no nesting) |
| `poc/__init__.py` | *(remove)* | No longer needed — standalone scripts |
| `poc/README.md` | `poc/README.md` | Stays in place |
| `poc_audio/` | `poc/audio/` | Input audio consolidated under poc |
| `poc_output/` | `poc/output/` | |
| `poc_output_allinone/` | `poc/output_allinone/` | |
| `poc_output_allinone.1/` | `poc/output_allinone_archive/` | Two archived runs merged into one dir |
| `poc_output_allinone.3/` | `poc/output_allinone_archive/` | |
| `poc_output_comparison/` | `poc/output_comparison/` | |
| `notebooks/` | `poc/notebooks/` | Only contains POC notebook |
| `transition_builder_v2/` | `poc/transition_builder_v2/` | Archived as-is; internal paths not updated |
| `demix/` | `poc/demix/` | Model cache, gitignored |
| `setup_poc.sh` | `poc/setup.sh` | |
| `process_songs.sh` | `poc/process_songs.sh` | |
| `process_songs_native.sh` | `poc/process_songs_native.sh` | |
| `src/` | `src/` | **Stays at root** — standard src-layout |
| `output_transitions/` | `output_transitions/` | **Stays at root** — transitional, see note above |
| `output_songs/` | `output_songs/` | **Stays at root** — transitional |
| `scripts/generate_lrc.py` | `scripts/generate_lrc.py` | **Stays** — bridge/admin script |
| `scripts/migrate_song_library.py` | `scripts/migrate_song_library.py` | **Stays** — bridge script (reads POC, writes production) |
| `scripts/find_test_song.py` | `poc/find_test_song.py` | POC helper |
| `scripts/test_allinone_analyze.py` | `poc/test_allinone_analyze.py` | POC test |
| `scripts/test_sensevoice.py` | `poc/test_sensevoice.py` | POC test |
| `scripts/test_whisper.py` | `poc/test_whisper.py` | POC test |
| `scripts/give_thanks.lrc` | `poc/give_thanks.lrc` | Sample LRC used by POC tests |
| `Dockerfile` | `docker/Dockerfile` | |
| `Dockerfile.allinone` | `docker/Dockerfile.allinone` | |
| `docker-compose.yml` | `docker/docker-compose.yml` | Volume paths must be updated |
| `docker-compose.allinone.yml` | `docker/docker-compose.allinone.yml` | Volume paths must be updated |
| `.dockerignore` | `docker/.dockerignore` | |
| `requirements.txt` | `docker/requirements.txt` | Docker-only dep file |
| `requirements_allinone.txt` | `docker/requirements_allinone.txt` | Docker-only dep file |
| `assets/fonts/` | `src/stream_of_worship/assets/fonts/` | **Stays bundled in package** — required by `[tool.setuptools.package-data]` |

### Directories Removed

| Directory | Reason |
|---|---|
| `audio_input/` | Empty; no code references it |
| `audio_output/` | Empty; no code references it |
| `resources/` | Empty placeholder; fonts stay in package, not here |
| `assets/` | Fonts move into the package at `src/stream_of_worship/assets/fonts/`; root `assets/` becomes empty |

---

## pyproject.toml: Single vs Separate

### Decision: Keep one `pyproject.toml` at the project root.

### Rationale

| Concern | Single (recommended) | Separate |
|---|---|---|
| **Dependency isolation** | Extras already partition by use case (`scraper`, `tui`, `song_analysis`, etc.). POC's heaviest deps (torch, allin1, demucs) live exclusively in Docker `requirements*.txt`, not in pyproject.toml. | Would duplicate the extras that already exist. |
| **Lock file** | One `uv.lock` — single source of truth, one `uv add` workflow as CLAUDE.md mandates. | Two lock files to keep in sync. |
| **Entry point** | `stream-of-worship` CLI stays wired to the package without rerouting. | Would need to decide which pyproject owns the entry point. |
| **tooling config** | One `[tool.black]`, `[tool.ruff]`, `[tool.pytest]` — consistent formatting and linting across the repo. | Risk of drift between configs. |
| **uv workspace** | Not needed; single project root is simpler. | Would require `[tool.uv.workspace]` setup. |

### What changes in pyproject.toml

Almost nothing. `src/` stays at root, so `[tool.setuptools.packages.find] where = ["src"]` is already correct. The only change is adding `"poc"` to `norecursedirs` so pytest doesn't crawl into archived POC scripts:

```toml
[tool.pytest.ini_options]
norecursedirs = ["scripts", "poc", ".*", "build", "dist", "*.egg-info"]
```

`[tool.setuptools.package-data]` already points to `assets/fonts/*` inside the package — no change needed there either.

---

## Files Requiring Path Updates

These files contain paths that will break after the move. Each is listed with exactly what needs to change.

### 1. `pyproject.toml`

| Setting | Current | New |
|---|---|---|
| `[tool.setuptools.packages.find] where` | `["src"]` | *(no change)* |
| `[tool.setuptools.package-data]` path | `assets/fonts/*` | *(no change — fonts stay bundled)* |
| `[tool.pytest.ini_options] norecursedirs` | `["scripts", …]` | add `"poc"` |

### 2. `docker/docker-compose.yml` (librosa)

The compose files move to `docker/`, so all host paths become relative to that subdirectory. Every path needs a `../` prefix *and* directory-name updates for the poc consolidation:

| Current mount | New mount |
|---|---|
| `./notebooks:/workspace/notebooks` | `../poc/notebooks:/workspace/notebooks` |
| `./poc:/workspace/poc` | `../poc:/workspace/poc` |
| `./poc_audio:/workspace/poc_audio` | `../poc/audio:/workspace/poc_audio` |
| `./poc_output:/workspace/poc_output` | `../poc/output:/workspace/poc_output` |
| `./poc_output_allinone:/workspace/poc_output_allinone` | `../poc/output_allinone:/workspace/poc_output_allinone` |
| `./data:/workspace/data` | `../data:/workspace/data` |

### 3. `docker/docker-compose.allinone.yml`

Same `../` treatment. Key mounts:

| Current mount | New mount |
|---|---|
| `./poc_audio:/workspace/poc_audio` | `../poc/audio:/workspace/poc_audio` |
| `./poc_output_allinone:/workspace/poc_output_allinone` | `../poc/output_allinone:/workspace/poc_output_allinone` |
| `.:/workspace` | `..:/workspace` |

### 4. `.gitignore`

| Current pattern | New pattern | Reason |
|---|---|---|
| `poc_output*/*` | `poc/output*/*` | Covers output/, output_allinone/, output_comparison/ |
| `poc_output_allinone.*/` | `poc/output_allinone_archive/` | Archived runs live in one dir now |
| `demix/*` | `poc/demix/*` | |
| `output_transitions/*` | *(no change)* | Stays at root |
| `output_songs/*` | *(no change)* | Stays at root |

### 5. `CLAUDE.md`

| Current | New |
|---|---|
| `poc/poc_analysis.py` | *(no change — scripts stay flat at poc/)* |
| `poc/poc_analysis_allinone.py` | *(no change)* |
| `poc/lyrics_scraper.py` | *(no change)* |
| `cd transition_builder_v2` | `cd poc/transition_builder_v2` |
| `data/lyrics/` | *(no change)* |

### 6. `scripts/migrate_song_library.py`

This is the bridge script the existing specs position at `scripts/`. It has hardcoded POC paths that must update after poc consolidation:

| Line | Current | New |
|---|---|---|
| 38 | `Path("poc_output_allinone/poc_full_results.json")` | `Path("poc/output_allinone/poc_full_results.json")` |
| 40 | `Path("poc_audio")` | `Path("poc/audio")` |

Line 25's `sys.path` hack (`… / "src"`) stays correct because `src/` remains at root and `scripts/` remains one level below.

### 7. Production code path references (`src/stream_of_worship/core/`)

The production TUI resolves runtime paths for audio input and analysis results. Defaults that pointed at scattered poc directories must update:

| Logical path | Current default | New default |
|---|---|---|
| audio folder | `poc_audio/` | `poc/audio/` |
| analysis JSON | `poc_output_allinone/poc_full_results.json` | `poc/output_allinone/poc_full_results.json` |
| stems folder | `poc_output_allinone/stems/` | `poc/output_allinone/stems/` |
| transition output | `output_transitions/` | *(no change)* |
| song output | `output_songs/` | *(no change)* |

### 8. `poc/transition_builder_v2/config.json`

Archived under poc. Its relative paths (`../poc_audio`, `../poc_output_allinone`, etc.) will break. Two options:

- **Option A (recommended):** Update to sibling paths inside poc (`./audio`, `./output_allinone`, etc.).
- **Option B:** Leave untouched and document that the legacy app is archived and no longer runnable as-is.

### 9. Shell scripts moving to `poc/`

`process_songs.sh` and `process_songs_native.sh` may reference `poc_audio/`, `poc_output/`, etc. Review and update to sibling paths inside `poc/`.

---

## Migration Checklist

Execute in order. Each step is atomic and independently testable.

1. **Create target directories** — `poc/notebooks/`, `poc/audio/`, `poc/output/`, `poc/output_allinone/`, `poc/output_allinone_archive/`, `poc/output_comparison/`, `poc/demix/`, `docker/`

2. **Consolidate POC I/O directories** (the main move)
   - `poc_audio/` → `poc/audio/`
   - `poc_output/` contents → `poc/output/`
   - `poc_output_allinone/` contents → `poc/output_allinone/`
   - `poc_output_allinone.1/` + `poc_output_allinone.3/` contents → `poc/output_allinone_archive/`
   - `poc_output_comparison/` contents → `poc/output_comparison/`
   - `notebooks/` contents → `poc/notebooks/`
   - `demix/` contents → `poc/demix/`

3. **Archive legacy TUI and POC shell scripts**
   - `transition_builder_v2/` → `poc/transition_builder_v2/`
   - `setup_poc.sh` → `poc/setup.sh`
   - `process_songs.sh` → `poc/process_songs.sh`
   - `process_songs_native.sh` → `poc/process_songs_native.sh`

4. **Move POC test scripts from `scripts/` into `poc/`**
   - `scripts/find_test_song.py` → `poc/find_test_song.py`
   - `scripts/test_allinone_analyze.py` → `poc/test_allinone_analyze.py`
   - `scripts/test_sensevoice.py` → `poc/test_sensevoice.py`
   - `scripts/test_whisper.py` → `poc/test_whisper.py`
   - `scripts/give_thanks.lrc` → `poc/give_thanks.lrc`

5. **Move Docker files to `docker/`**
   - `Dockerfile`, `Dockerfile.allinone`, `docker-compose*.yml`, `.dockerignore` → `docker/`
   - `requirements.txt`, `requirements_allinone.txt` → `docker/`

6. **Remove empty directories**
   - `audio_input/`
   - `audio_output/`
   - `resources/` (empty placeholder)
   - Old `poc_audio/`, `poc_output*/`, `notebooks/`, `transition_builder_v2/`, `demix/` (after contents moved)

7. **Remove `poc/__init__.py`** — no longer needed; poc scripts are standalone

8. **Update `pyproject.toml`** — add `"poc"` to `norecursedirs`

9. **Update `docker/docker-compose.yml`** — add `../` prefix to all host paths, update poc directory names per the table in §Files Requiring Path Updates

10. **Update `docker/docker-compose.allinone.yml`** — same treatment

11. **Update `.gitignore`** — `poc_output*` patterns → `poc/output*`, `demix/` → `poc/demix/`

12. **Update `CLAUDE.md`** — `cd transition_builder_v2` → `cd poc/transition_builder_v2`

13. **Update `scripts/migrate_song_library.py`** — lines 38 and 40: `poc_output_allinone/` → `poc/output_allinone/`, `poc_audio` → `poc/audio`

14. **Update production path defaults** — `src/stream_of_worship/core/config.py` and/or `core/paths.py`: `poc_audio/` → `poc/audio/`, `poc_output_allinone/` → `poc/output_allinone/`

15. **Update `poc/transition_builder_v2/config.json`** — Option A: update relative paths to sibling dirs (`./audio`, `./output_allinone`, etc.)

16. **Review and update POC shell scripts** — `poc/process_songs.sh`, `poc/process_songs_native.sh`: update any references to `poc_audio/`, `poc_output/`

17. **Smoke-test**
    - `uv run --extra tui stream-of-worship` still launches
    - `docker compose -f docker/docker-compose.yml up` builds and runs (from project root)
    - `pytest` from project root collects and passes tests
    - Production TUI can locate `poc/output_allinone/poc_full_results.json`
    - `python scripts/migrate_song_library.py` finds its source paths

---

## Risks and Open Items

| Risk | Mitigation |
|---|---|
| Docker compose context changes when files move to `docker/` | Run from project root with `-f docker/docker-compose.yml`. Set `context: ..` in compose files so the build context stays at the repo root. |
| POC scripts have hardcoded relative paths (e.g. `Path("poc_audio")`) | These scripts run inside Docker containers. The container-side paths don't change — only the host-side volume mounts update (steps 9/10). **No changes needed inside the POC scripts themselves.** |
| `migrate_song_library.py` reads from POC paths | Explicit update in step 13. The `sys.path` hack on line 25 stays correct because both `scripts/` and `src/` remain at root. |
| `transition_builder_v2` internal relative paths break | Handled by step 15 (Option A) or accepted if truly archived (Option B). |
| `.gitkeep` files in output directories | Recreate `.gitkeep` in `poc/audio/`, `poc/output/`, `poc/output_allinone/` to preserve empty dirs in git. Root `output_transitions/` and `output_songs/` already have `.gitkeep`.|
| Output dirs become stale when platform-specific paths go live | Per `lyrics_video_generator_design.md` §8.1, production output moves to OS-appropriate dirs at that point. `output_transitions/` and `output_songs/` at root become dead and can be removed then. |
