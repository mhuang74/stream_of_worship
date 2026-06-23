# Component Deprecation Analysis

**Date:** 2026-06-20  
**Based on:** Repository scan + Graphify analysis  
**Purpose:** Identify components that can be safely removed to reduce repository size and complexity

---

## Executive Summary

The repository contains **~2.8GB** of deprecated/experimental code and data that can be safely removed:

| Category | Directory/Component | Size | Priority |
|----------|-------------------|------|----------|
| **POC Scripts** | `poc/` | 2.8GB | HIGH |
| **Old TUI** | `src/stream_of_worship/tui/` | 132KB | MEDIUM |
| **New TUI (App)** | `src/stream_of_worship/app/` | 392KB | MEDIUM |
| **POC Output** | `poc/output/`, `poc/output_allinone/` | ~800MB | HIGH |
| **Test Audio** | `poc/audio/` | ~50MB | MEDIUM |
| **Stem Output** | `vocal_extraction_output/` | ~10MB | LOW |
| **Tmp Output** | `tmp_output/`, `tmp/` | ~813MB | HIGH |
| **Old Output** | `output_transitions/`, `output_songs/` | ~15MB | LOW |

**Total recoverable space: ~3.6GB**

---

## Component Categorization

### ✅ ACTIVE COMPONENTS (Keep)

#### 1. Web App (`webapp/`)
- **Status:** Primary end-user interface
- **Purpose:** Browser-based worship set editor and player
- **Dependencies:** Next.js, Drizzle ORM, Neon Postgres, Cloudflare R2, AWS SQS
- **Users:** Worship leaders, media teams
- **Keep:** YES - This is the future of the product

#### 2. Render Worker (`services/render-worker/`)
- **Status:** Active AWS Lambda service
- **Purpose:** Serverless video/audio rendering
- **Dependencies:** psycopg2, boto3, Pillow, FFmpeg
- **Keep:** YES - Processes render jobs from Web App

#### 3. Analysis Service (`services/analysis/`)
- **Status:** Active microservice
- **Purpose:** Audio analysis (tempo, key, structure) and LRC generation
- **Dependencies:** PyTorch, Demucs, allin1, FastAPI
- **Keep:** YES - Provides analysis APIs to all components

#### 4. Admin CLI (`src/stream_of_worship/admin/`)
- **Status:** Active backend management tool
- **Purpose:** Catalog management, audio download, database operations
- **Commands:** `sow-admin catalog`, `sow-admin audio`, `sow-admin db`
- **Keep:** YES - Essential for administrators

---

### ❌ DEPRECATED COMPONENTS (Remove)

#### 1. POC Scripts (`poc/`) - **2.8GB**
**Status:** ARCHIVED - Experimental validation complete

**What it contains:**
- `poc/poc_analysis.py` - Original analysis script (replaced by Analysis Service)
- `poc/poc_analysis_allinone.py` - Docker-based analysis (replaced by Analysis Service)
- `poc/gen_lrc_*.py` - Multiple LRC generation experiments (replaced by Analysis Service jobs)
- `poc/gen_clean_vocal_stem*.py` - Vocal extraction experiments (replaced by stem separation service)
- `poc/eval_lrc.py`, `poc/score_lrc_quality.py` - LRC evaluation scripts
- `poc/transition_builder_v2/` - Old TUI (superseded by both app/ and webapp/)
- `poc/audio/` - Test audio files (~50MB)
- `poc/output/`, `poc/output_allinone/` - Analysis output (~800MB)
- `poc/demix/` - Stem separation experiments
- `poc/notebooks/` - Jupyter notebooks
- `poc/experiment_output/` - Experiment results

**Why remove:**
- All functionality has been migrated to Analysis Service or Admin CLI
- Referenced in documentation as "archived" and "legacy"
- Contains 2.8GB of data (majority of repo size)
- No active development since early 2026
- Graphify shows no imports from active components

**Impact:**
- Zero - no active component imports from `poc/`
- Documentation references can be updated to mention "historical reference only"

**Removal checklist:**
- [ ] Remove `poc/` directory entirely
- [ ] Update README.md references (lines 451-452, 530)
- [ ] Update DEVELOPER.md references
- [ ] Keep POC_SUMMARY.md in docs/ for historical reference

---

#### 2. User App TUI (`src/stream_of_worship/app/`) - **392KB**
**Status:** DEPRECATED - Replaced by Web App

**What it contains:**
- `app/app.py` - Main TUI application (Textual-based)
- `app/screens/` - TUI screens (browse, songset_editor, settings, etc.)
- `app/services/` - Audio engine, video engine, export, playback
- `app/db/` - PostgreSQL clients for songsets
- `app/main.py` - CLI entry point (`sow-app`)

**Why remove:**
- README explicitly states: "User App is deprecated; all new development should target the Web App"
- Web App provides superior UX (browser-based, phone-first, second-screen projection)
- TUI doesn't support all Web App features (sharing, offline caching, lyric marks)
- Both Admin CLI and Web App cover all use cases

**Impact:**
- Users should migrate to Web App (`pnpm --filter sow-webapp dev`)
- `sow-app` command will no longer work
- Songset data remains in PostgreSQL (accessible via Web App)

**Removal checklist:**
- [ ] Remove `src/stream_of_worship/app/` directory
- [ ] Remove `sow-app` entry from `pyproject.toml` [project.scripts]
- [ ] Remove `app` extra from `pyproject.toml`
- [ ] Update README.md quick start table
- [ ] Update DEVELOPER.md architecture diagram
- [ ] Add migration note to docs/

---

#### 3. Old TUI (`src/stream_of_worship/tui/`) - **132KB**
**Status:** DEPRECATED - Superseded by app/ (which is also deprecated)

**What it contains:**
- `tui/app.py` - TransitionBuilderApp (Textual-based)
- `tui/screens/` - Playlist, generation, history screens
- `tui/models/` - Song, Transition, Playlist models
- `tui/services/` - Catalog, playback, generation services
- `tui/state.py` - Application state

**Why remove:**
- Even older than `app/` TUI
- No active development
- Functionality fully covered by Web App
- Referenced only in tests and old CLI code

**Impact:**
- Zero - already superseded by `app/` TUI
- `stream-of-worship tui` command (if exists) will stop working

**Removal checklist:**
- [ ] Remove `src/stream_of_worship/tui/` directory
- [ ] Remove references from `src/stream_of_worship/cli/main.py`
- [ ] Remove test files: `tests/unit/test_tui_*.py`
- [ ] Remove `tui` extra from `pyproject.toml`

---

#### 4. POC Output Directories - **~800MB**
**Status:** GENERATED ARTIFACTS - Safe to remove

**What to remove:**
- `poc/output/` - Transition builder output
- `poc/output_allinone/` - All-in-one analysis results including `stems/`
- `poc/experiment_output/` - LRC signal experiments

**Why remove:**
- Generated artifacts, not source code
- Can be regenerated if needed
- Contains no unique data

**Impact:**
- Zero - these are temporary outputs

**Removal checklist:**
- [ ] Remove `poc/output/`
- [ ] Remove `poc/output_allinone/`
- [ ] Remove `poc/experiment_output/`
- [ ] Add to `.gitignore` if not already present

---

#### 5. Test/Sample Audio Files - **~50MB**
**Status:** TEST DATA - Safe to remove

**What to remove:**
- `poc/audio/` - Test audio files for POC scripts

**Why remove:**
- Only used by deprecated POC scripts
- Not needed for production workflow

**Impact:**
- Zero - test data only

**Removal checklist:**
- [ ] Remove `poc/audio/`

---

#### 6. Stem Separation Output - **~10MB**
**Status:** GENERATED ARTIFACTS - Safe to remove

**What to remove:**
- `vocal_extraction_output/` - Extracted vocal stems from experiments

**Why remove:**
- Generated artifacts from POC experiments
- Production stems stored in R2

**Impact:**
- Zero - experimental output only

**Removal checklist:**
- [ ] Remove `vocal_extraction_output/`

---

#### 7. Temporary Output - **~813MB**
**Status:** TEMPORARY FILES - Safe to remove

**What to remove:**
- `tmp_output/` - Temporary LRC generation output (~793MB)
- `tmp/` - Miscellaneous temporary files (~20MB)
- `output_transitions/` - Old transition outputs (~7.9MB)
- `output_songs/` - Old song outputs (~7.4MB)

**Why remove:**
- All are generated temporary files
- Can be regenerated
- Not tracked in git (should be in .gitignore)

**Impact:**
- Zero - temporary files

**Removal checklist:**
- [ ] Remove `tmp_output/`
- [ ] Remove `tmp/`
- [ ] Remove `output_transitions/`
- [ ] Remove `output_songs/`
- [ ] Verify `.gitignore` includes these patterns

---

#### 8. Scraped Song Data Files
**Status:** LEGACY DATA - Safe to remove (if any exist)

**What to check:**
- `data/lyrics/` - Contains scraped lyrics data (currently empty directory)
- Any `.json` or `.txt` files with scraped data

**Why remove:**
- Production data lives in PostgreSQL database
- Scraped data should be in database, not files

**Impact:**
- Zero if data is already in database

**Removal checklist:**
- [ ] Check `data/` directory for actual data files
- [ ] Remove any static data files
- [ ] Keep directory structure if needed

---

## Component Dependency Map

```
┌─────────────────────────────────────────────────────────────┐
│                     ACTIVE COMPONENTS                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐                                           │
│  │  Web App    │ ◄── Primary user interface                │
│  │  (webapp/)  │                                           │
│  └──────┬──────┘                                           │
│         │                                                   │
│         │ (HTTP API, SSE)                                  │
│         ▼                                                   │
│  ┌─────────────┐     ┌─────────────┐                       │
│  │   Render    │     │  Analysis   │                       │
│  │   Worker    │     │   Service   │                       │
│  │  (Lambda)   │     │ (Docker)    │                       │
│  └──────┬──────┘     └──────┬──────┘                       │
│         │                   │                                │
│         │                   │ (HTTP API)                    │
│         │                   ▼                                │
│  ┌─────────────┐     ┌─────────────┐                       │
│  │  PostgreSQL │◄────│  Admin CLI  │                       │
│  │   (Neon)    │     │  (sow-admin)│                       │
│  └──────┬──────┘     └─────────────┘                       │
│         │                                                   │
│         │ (Read-only access)                               │
│         ▼                                                   │
│  ┌─────────────┐                                           │
│  │ Cloudflare  │                                           │
│  │     R2      │                                           │
│  └─────────────┘                                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                   DEPRECATED COMPONENTS                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐                                           │
│  │  User App   │                                           │
│  │ (app/) TUI  │ ──┐                                       │
│  └─────────────┘   │                                       │
│                    ├─────► No dependencies from active     │
│  ┌─────────────┐   │                                       │
│  │  Old TUI    │ ──┘                                       │
│  │  (tui/)     │                                           │
│  └─────────────┘                                           │
│                                                             │
│  ┌─────────────┐                                           │
│  │ POC Scripts │ ──► Archived, no active imports           │
│  │   (poc/)    │                                           │
│  └─────────────┘                                           │
│                                                             │
│  ┌─────────────┐                                           │
│  │   Output    │ ──► Generated artifacts (tmp files)       │
│  │ Directories │                                           │
│  └─────────────┘                                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Removal Priority

### Phase 1: High Priority (Immediate - 2.8GB)
1. **Remove `poc/` directory** - 2.8GB
   - All POC scripts and experiments
   - Test audio files
   - Historical only

2. **Remove output directories** - ~800MB
   - `poc/output/`
   - `poc/output_allinone/`
   - `poc/experiment_output/`
   - `tmp_output/`
   - `tmp/`
   - `output_transitions/`
   - `output_songs/`

### Phase 2: Medium Priority (After migration period - 524KB)
3. **Remove User App TUI** - 392KB
   - `src/stream_of_worship/app/`
   - Wait for Web App feature parity confirmation
   - Announce deprecation to users

4. **Remove Old TUI** - 132KB
   - `src/stream_of_worship/tui/`
   - No dependencies, safe to remove anytime

### Phase 3: Low Priority (Cleanup - ~15MB)
5. **Remove stem/vocal outputs**
   - `vocal_extraction_output/`
   - Any other generated artifacts

---

## Documentation Updates Required

After removal, update these files:

### README.md
- Remove POC section (lines 426-476)
- Update User App section to mark as deprecated
- Remove `sow-app` from quick start table
- Update architecture diagram

### DEVELOPER.md
- Mark POC as "archived - historical reference only"
- Remove User App from active components
- Update component table
- Remove POC references from troubleshooting

### pyproject.toml
- Remove `[project.optional-dependencies]` entries:
  - `tui`
  - `app`
  - `poc_qwen3_align`
  - `poc_qwen3_mlx`
  - `poc_qwen3_asr`
  - `score_lrc`
  - `score_lrc_base`
  - `fix_lrc`
- Remove `stream-of-worship` script entry (if only used for TUI)

### .gitignore
- Ensure all output directories are ignored:
  - `output_*/`
  - `tmp/`
  - `tmp_output/`
  - `vocal_extraction_output/`
  - `poc/output*/`

---

## Migration Guide for Users

### For TUI Users
```bash
# Old workflow (deprecated)
uv run --extra app sow-app run

# New workflow (Web App)
cd webapp && pnpm install
pnpm dev  # → http://localhost:8080
```

### For POC Script Users
```bash
# Old workflow (deprecated)
python poc/poc_analysis_allinone.py

# New workflow (Analysis Service)
cd services/analysis && docker compose up -d
curl http://localhost:8000/api/v1/jobs/analyze -X POST ...
```

---

## Risks and Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Users still rely on TUI | LOW | MEDIUM | Announce deprecation 2 weeks before removal |
| Need to regenerate POC outputs | LOW | LOW | Tag repository before removal, keep in git history |
| Documentation links break | MEDIUM | LOW | Update all docs before removal, add redirects |
| Accidental imports from removed code | LOW | HIGH | Grep for imports before removal, run tests |

---

## Verification Steps

Before removal:
```bash
# 1. Check for imports from deprecated code
grep -r "from poc" src/ tests/ services/
grep -r "from stream_of_worship.tui" src/stream_of_worship/app/ src/stream_of_worship/admin/
grep -r "from stream_of_worship.app" src/stream_of_worship/admin/ services/

# 2. Run all tests
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test pytest tests/admin/ -v
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/app/ -v

# 3. Check git status
git status
git tag before-deprecation-2026-06
```

After removal:
```bash
# 1. Verify build still works
uv build

# 2. Run tests again
PYTHONPATH=src uv run --python 3.11 --extra admin --extra test pytest tests/ -v

# 3. Check repository size
du -sh .
git gc --aggressive
```

---

## Timeline Recommendation

- **Week 1:** Phase 1 removal (POC + outputs) - immediate space savings
- **Week 2-3:** Announce TUI deprecation, help users migrate to Web App
- **Week 4:** Phase 2 removal (TUI components)
- **Week 5:** Phase 3 cleanup + documentation updates

---

## Summary

**Total space to recover:** ~3.6GB  
**Active components to keep:** 4 (Web App, Render Worker, Analysis Service, Admin CLI)  
**Deprecated components to remove:** 8 categories  

**Key insight:** The POC directory alone accounts for 77% of the repository size and has zero dependencies from active components. It can be safely removed immediately.

The TUI components (both old and new) are small in size but create confusion about the product direction. Removing them clarifies that the Web App is the primary user interface going forward.

---

**Next Steps:**
1. Review this analysis with stakeholders
2. Create git tag for preservation
3. Execute Phase 1 removal
4. Update documentation
5. Announce TUI deprecation timeline
6. Execute Phase 2-3 removal
