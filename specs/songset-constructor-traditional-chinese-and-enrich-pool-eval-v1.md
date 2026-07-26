# Spec: Traditional Chinese Theme Matching + enrich_pool Evaluation & Diversity Improvements

> Detailed plan for converting the songset constructor POC to Traditional Chinese-only theme matching,
> adding an `--only-evaluate-pool-enrichment` evaluation flag, and recommending enrich_pool diversity adjustments.
>
> Reference (not to be edited): `specs/songset-constructor-poc-implementation-plan.md`

| | |
|---|---|
| **Date** | 2026-07-26 |
| **Status** | Plan — pending implementation |
| **Component** | `lab/poc-scripts/poc/songset_constructor/` |
| **Read-only** | Postgres reads only; no writes to `songsets` / `songset_items` |
| **Output** | Enrichment report artifacts + updated theme classification |

---

## Overview

Three coordinated changes to the songset constructor POC:

1. **Traditional Chinese only** — Convert all theme keys, matching terms, anchor texts, and documentation from mixed Simplified/Traditional to **Traditional Chinese only**, since all SOP.org song lyrics are in Traditional Chinese.
2. **enrich_pool evaluation flag** — Add `--only-evaluate-pool-enrichment` CLI flag that runs the pipeline only up through `enrich_pool`, then outputs a distribution report to console and an artifact file.
3. **enrich_pool diversity recommendations** — Based on anticipated distribution issues, recommend concrete adjustments to the enrich_pool implementation to improve result diversity.

---

## Part 1: Full Traditional Chinese Conversion

### Rationale

All song lyrics in the SOP.org catalog are in **Traditional Chinese** (established in `specs/fix_snap_traditional_simplified_mismatch.md`). The current `THEME_VOCAB` in `rules/themes.py:14-27` uses **Simplified** Chinese for theme keys (e.g., 赞美, 认罪, 圣灵) with sporadic Traditional variants mixed in as matching terms. This means:

- Simplified-only keywords like `悔改`, `传扬`, `宝血`, `门徒` will only match Simplified text, but the actual lyrics are in Traditional (悔改→same, 传扬→傳揚, 宝血→寶血, 门徒→門徒).
- Some keywords are already duplicates (e.g., `赞美` and `讚美` both in the same vocab entry), creating redundant matching.
- The embedding anchor texts in `regen_theme_anchors.py:17-28` use Simplified Chinese, which may bias embeddings away from Traditional lyric text.

### Simplified → Traditional Conversion Table (Theme Keys)

| Current (Simplified) | Target (Traditional) | Characters Changed |
|---|---|---|
| 赞美 | 讚美 | 赞→讚 |
| 感恩 | 感恩 | (no change) |
| 敬拜 | 敬拜 | (no change) |
| 奉献 | 奉獻 | 献→獻 |
| 认罪 | 認罪 | 认→認 |
| 差遣 | 差遣 | (no change) |
| 信心 | 信心 | (no change) |
| 祈祷 | 祈禱 | 祷→禱 |
| 复兴 | 復興 | 复→復, 兴→興 |
| 圣灵 | 聖靈 | 圣→聖, 灵→靈 |
| 十字架 | 十字架 | (no change) |
| 跟随 | 跟隨 | 随→隨 |

### Simplified → Traditional Conversion Table (THEME_VOCAB Matching Terms)

All matching terms in each theme's tuple will be converted to Traditional Chinese. Here's the full mapping:

#### 讚美 (Praise)
- `赞美` → `讚美` (remove Simplified duplicate, keep only Traditional)
- `讚美` → `讚美` (keep)
- `歌唱` → `歌唱` (same)
- `欢呼` → `歡呼` (欢→歡)
- `hallelujah`, `praise`, `zan mei` → unchanged

#### 感恩 (Thanksgiving)
- `感恩` → `感恩` (same)
- `感谢` → `感謝` (谢→謝)
- `謝謝` → `謝謝` (keep, already Traditional)
- `恩典` → `恩典` (same)
- `grace`, `thanks`, `gan en` → unchanged

#### 敬拜 (Worship)
- `敬拜` → `敬拜` (same)
- `俯伏` → `俯伏` (same)
- `尊崇` → `尊崇` (same)
- `荣耀` → `榮耀` (荣→榮)
- `worship`, `adore`, `jing bai` → unchanged

#### 奉獻 (Offering)
- `奉献` → `奉獻` (献→獻)
- `献上` → `獻上` (献→獻)
- `擺上` → `擺上` (keep, already Traditional)
- `祭` → `祭` (same)
- `offering`, `dedicate`, `feng xian` → unchanged

#### 認罪 (Repentance)
- `认罪` → `認罪` (认→認)
- `悔改` → `悔改` (same)
- `赦免` → `赦免` (same)
- `洁净` → `潔淨` (洁→潔, 净→淨)
- `forgive`, `repent`, `ren zui` → unchanged

#### 差遣 (Commission)
- `差遣` → `差遣` (same)
- `宣教` → `宣教` (same)
- `传扬` → `傳揚` (传→傳, 扬→揚)
- `万民` → `萬民` (万→萬)
- `send`, `mission`, `chai qian` → unchanged

#### 信心 (Faith)
- `信心` → `信心` (same)
- `相信` → `相信` (same)
- `倚靠` → `倚靠` (same)
- `盼望` → `盼望` (same)
- `faith`, `trust`, `xin xin` → unchanged

#### 祈禱 (Prayer)
- `祷告` → `禱告` (祷→禱)
- `祈祷` → `祈禱` (祷→禱)
- `呼求` → `呼求` (same)
- `垂听` → `垂聽` (听→聽)
- `prayer`, `pray`, `qi dao` → unchanged

#### 復興 (Revival)
- `复兴` → `復興` (remove Simplified duplicate, keep only Traditional)
- `復興` → `復興` (keep)
- `更新` → `更新` (same)
- `燃烧` → `燃燒` (烧→燒)
- `revival`, `renew`, `fu xing` → unchanged

#### 聖靈 (Holy Spirit)
- `圣灵` → `聖靈` (remove Simplified duplicate, keep only Traditional)
- `聖靈` → `聖靈` (keep)
- `灵火` → `靈火` (灵→靈)
- `充满` → `充滿` (满→滿)
- `holy spirit`, `sheng ling` → unchanged

#### 十字架 (Cross)
- `十字架` → `十字架` (same)
- `宝血` → `寶血` (宝→寶)
- `羔羊` → `羔羊` (same)
- `救赎` → `救贖` (赎→贖)
- `cross`, `blood`, `shi zi jia` → unchanged

#### 跟隨 (Follow)
- `跟随` → `跟隨` (随→隨)
- `跟從` → `跟從` (keep, already Traditional)
- `道路` → `道路` (same)
- `门徒` → `門徒` (门→門)
- `follow`, `disciple`, `gen sui` → unchanged

### Files to Modify

#### 1. `poc/songset_constructor/rules/themes.py` (lines 12-27)
- Convert `THEMES` tuple (line 12) to Traditional keys
- Convert all `THEME_VOCAB` keys AND matching terms (lines 14-27) to Traditional
- Remove Simplified/Traditional duplicate pairs (e.g., remove `赞美` when `讚美` is present; remove `圣灵` when `聖靈` is present; remove `复兴` when `復興` is present)
- English and pinyin terms remain unchanged

#### 2. `poc/songset_constructor/rules/phases.py` (lines 7-20, 48-63, 66-69)
- Convert all `THEME_TO_PHASE` keys (lines 7-20) to Traditional
- Convert `apply_seasonal_bias` theme keys (lines 53-62) to Traditional:
  - `"赞美"` → `"讚美"`
  - `"感恩"` → `"感恩"`
  - `"认罪"` → `"認罪"`
  - `"十字架"` → `"十字架"`
  - `"复兴"` → `"復興"`
  - `"圣灵"` → `"聖靈"`
- Convert `infer_phase` theme check (line 69): `if theme == "圣灵"` → `if theme == "聖靈"`

#### 3. `poc/songset_constructor/regen_theme_anchors.py` (lines 16-29)
- Convert `ANCHOR_TEXTS` keys to Traditional
- Convert all Chinese text in the anchor phrases to Traditional (e.g., "赞美 歌唱 哈利路亚" → "讚美 歌唱 哈利路亞")

#### 4. `poc/songset_constructor/data/theme_anchors.json`
- **Regenerate** by running `regen_theme_anchors.py` after converting anchor texts (requires `SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL`)
- The JSON keys must change from Simplified to Traditional (e.g., `"赞美"` → `"讚美"`)
- The embedded vectors will be different because the anchor text changes
- If embedding endpoint is unavailable, manually rename keys in the JSON (vectors stay the same but key names change) and note that a full regeneration should happen when the endpoint is available

#### 5. `poc/songset_constructor/artifacts/writer.py` (line 16)
- `THEMES` is imported from `themes.py`, so no direct changes needed — it will automatically reference the Traditional keys

#### 6. Test files

**`tests/conftest.py`** (lines 18-23):
- `title="赞美主"` → `title="讚美主"`, `themes={"赞美": 1}` → `themes={"讚美": 1}`
- `title="感恩的心"` / `themes={"感恩": 1}` → unchanged (already Traditional)
- `title="敬拜你"` / `themes={"敬拜": 1}` → unchanged
- `title="十字架"` / `themes={"十字架": 1}` → unchanged
- `title="跟随主"` → `title="跟隨主"`, `themes={"跟随": 1}` → `themes={"跟隨": 1}`
- `title="复兴"` → `title="復興"`, `themes={"复兴": 1}` → `themes={"復興": 1}`

**`tests/test_songset_constructor_rules.py`** (lines 30-33):
- `classify_title_themes("赞美主")` → `classify_title_themes("讚美主")`
- `classify_lyrics_themes("我要赞美\n感谢你的恩典")` → `classify_lyrics_themes("我要讚美\n感謝你的恩典")`
- `assert max(fused, key=fused.get) == "赞美"` → `assert max(fused, key=fused.get) == "讚美"`

**`tests/test_songset_constructor_graph.py`** (line 103):
- `assert "h001: 赞美主" in prompt` → `assert "h001: 讚美主" in prompt`

**`tests/test_songset_constructor_artifacts.py`** (many lines):
- All Simplified theme references in test fixtures → Traditional equivalents
- `title="赞美主"` → `title="讚美主"` (line 27)
- `themes=["赞美"]` → `themes=["讚美"]` (line 29, 166, 254, 304, 320, etc.)
- `themes=["感恩"]` → unchanged
- `themes=["敬拜"]` → unchanged
- `themes=["差遣"]` → unchanged
- `themes=["认罪"]` → `themes=["認罪"]` (line 281)
- `themes=["跟随"]` → `themes=["跟隨"]` (line 366, 486)
- `themes=["复兴"]` → `themes=["復興"]`
- `themes=["奉献"]` → `themes=["奉獻"]` (line 269)
- Assertions referencing these theme strings must also be updated
- `title="跟随主"` → `title="跟隨主"` (line 366, 486)
- Various assertion strings (lines 262-264, 402-404, 424-426, etc.)

**`tests/test_songset_constructor_db.py`** — Check for any Simplified theme references in synthetic DB fixtures.

**`tests/test_songset_constructor_config.py`** — Check for any Simplified theme references.

**`tests/test_songset_constructor_cli.py`** — Check for any Simplified theme references in output assertions.

#### 7. `docs/agent_guide_songset_constructor.md`
- **Section "5-Phase Worship Arc"** (lines 58-69): Update theme names to Traditional
  - Phase 1: 讚美 (Praise)
  - Phase 2: 感恩 (Thanksgiving) — unchanged
  - Phase 3: 敬拜/祈禱/信心/聖靈 (Worship)
  - Phase 4: 奉獻/認罪/十字架 (Response)
  - Phase 5: 差遣/跟隨/復興 (Commission)
- **Seasonal bias examples** (lines 70-71): `讚美/感恩` for christmas, `認罪/十字架` for lent
- **enrich_pool Deep Dive** section (lines 136-320): Update all `THEME_VOCAB` examples, `classify_title_themes` examples, `fuse_themes` examples, `THEME_TO_PHASE` mapping, seasonal bias code, and `infer_phase` code to use Traditional keys
- **Code blocks**: Update inline code examples showing `THEME_VOCAB` structure (lines 168-273)
- **Key Source Files** table (lines 588-606): No changes needed (paths don't reference Chinese)

#### 8. `poc/songset_constructor/README.md`
- No Simplified Chinese theme references found — no changes needed.

---

## Part 2: enrich_pool Evaluation Flag

### New CLI Flag: `--only-evaluate-pool-enrichment`

Add a new flag to the `construct` command in `cli.py` and to `RunConfig` that:

1. Runs the graph only through `load_catalog` → `enrich_pool`
2. Skips `build_transition_matrix`, `beam_seed_candidates`, ranking, and artifact writing
3. Outputs a distribution report to console (rich-printed) and writes an `enrichment_report.md` artifact

### Implementation Details

#### 2.1 `RunConfig` (`config.py`)
Add field:
```python
only_evaluate_pool_enrichment: bool = False
```
Add to `to_dict()` return dict.

#### 2.2 CLI (`cli.py`)
Add Typer option:
```python
only_evaluate_pool_enrichment: Annotated[
    bool, typer.Option("--only-evaluate-pool-enrichment/--full-run")
] = False,
```
Pass to `RunConfig(...)` constructor.

When `config.only_evaluate_pool_enrichment` is True:
- Skip `config.validate_environment()` for LLM (since no LLM is needed for enrichment-only)
- After graph completes (or via early-exit routing), call the enrichment report writer
- Print summary to console via `console.print()`
- Write `enrichment_report.md` to `output_dir`

#### 2.3 Graph Early Exit (`graph/builder.py`)
Add a conditional edge after `enrich_pool`:
```python
def route_after_enrich(state: ConstructorState) -> str:
    if state["config"].only_evaluate_pool_enrichment:
        return "write_enrichment_report"
    return "build_transition_matrix"
```

Add a new node `write_enrichment_report` to `graph/nodes.py`:

```python
def write_enrichment_report(state: ConstructorState) -> dict:
    pool = state.get("pool", [])
    config = state["config"]
    # ... compute distribution metrics ...
    # Write enrichment_report.md to output_dir
    # Return partial state with artifact_paths and trace
```

Wire in builder:
```python
builder.add_node("write_enrichment_report", write_enrichment_report)
builder.add_conditional_edges(
    "enrich_pool",
    route_after_enrich,
    {"write_enrichment_report": "write_enrichment_report",
     "build_transition_matrix": "build_transition_matrix"},
)
builder.add_edge("write_enrichment_report", END)
```

#### 2.4 Enrichment Report Content

The `write_enrichment_report` node (or a helper function in `artifacts/`) will compute and output:

**A. Pool Overview**
- Total songs loaded from catalog (`pool_size` before enrichment)
- Total songs after enrichment (after dropping missing metadata)
- Drop count + drop reasons (from existing `enrichment_drop_diagnostics`)

**B. Phase Distribution**
- Count of songs per phase (1-5)
- Percentage of pool in each phase
- Phase balance indicator (e.g., "Phase 1: 12% (underrepresented)" if < 15%)

**C. Theme Distribution**
- For each of the 12 themes: how many songs have it as their dominant (highest fused score) theme
- How many songs have zero themes detected (all fused scores = 0)
- Top 5 most common dominant themes
- Top 5 least common dominant themes

**D. Phase Inference Source**
- How many songs had phase inferred from themes (fused score > 0)
- How many fell back to tempo-only inference
- How many of the tempo-fallback songs have no tempo (phase = 3 default)

**E. Theme Signal Coverage**
- How many songs have title theme hits (title classification non-zero)
- How many songs have lyrics theme hits (lyrics classification non-zero)
- How many songs have song embedding (not None)
- How many songs have line embeddings (non-empty list)

**F. Tempo & Key Coverage**
- How many songs have tempo_bpm (known vs missing)
- BPM range: min, max, median
- How many songs have musical_key (known vs missing)
- How many songs have key_confidence < 0.6

**G. Album Series Distribution**
- Count per album_series

**H. Diversity Assessment**
- Unique theme coverage (how many of the 12 themes appear as dominant for at least one song)
- Theme entropy (Shannon entropy of dominant theme distribution — lower = less diverse)
- Phase entropy (Shannon entropy of phase distribution)

#### 2.5 Console Output

After the report is written, print a concise summary:

```
Enrichment Report
=================
Pool: 247 loaded → 235 enriched (12 dropped: missing_tempo_and_key_metadata)

Phase Distribution:
  Phase 1 (讚美):     28 songs (11.9%)  ▓▓▓▓
  Phase 2 (感恩):     19 songs (8.1%)   ▓▓▓
  Phase 3 (敬拜):     98 songs (41.7%)  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
  Phase 4 (奉獻):     45 songs (19.1%)  ▓▓▓▓▓▓▓
  Phase 5 (差遣):     45 songs (19.1%)  ▓▓▓▓▓▓▓

Theme Dominance:
  敬拜: 87 songs (37.0%)
  認罪: 34 songs (14.5%)
  讚美: 28 songs (11.9%)
  ...
  祈禱: 3 songs (1.3%)  ← underrepresented

Phase Inference: 198 from themes, 37 tempo-only fallback
Signal Coverage: title 156/235, lyrics 201/235, embeddings 180/235
Theme Entropy: 2.31 bits (max 3.58) — moderate diversity
Phase Entropy: 1.98 bits (max 2.32) — good balance

Report written to: lab/poc-scripts/output/songset_constructor/<TS>/enrichment_report.md
```

---

## Part 3: enrich_pool Diversity Recommendations

### Anticipated Distribution Issues

Based on the current theme classification approach and the structure of Chinese worship music catalogs, we can predict several diversity problems:

#### Problem 1: Phase 3 (敬拜/Worship) Dominance
**Root cause**: `THEME_TO_PHASE` maps 4 themes to phase 3 (敬拜, 祈禱, 信心, 聖靈) vs only 2-3 themes per other phase. The word "敬拜" appears in a huge proportion of worship song titles and lyrics. The fusion weights (title 35% + lyrics 25%) favor keyword-heavy classification, which naturally biases toward 敬拜.

**Impact**: 40%+ of songs land in phase 3, starving phases 1, 2, and 5 of candidates. This makes openers (phase 1) and closers (phase 4/5) scarce, and middle-slot diversity limited.

#### Problem 2: Zero-Theme Songs (Tempo Fallback)
**Root cause**: Songs whose title and lyrics don't contain any THEME_VOCAB keywords, AND whose embeddings don't strongly match any theme anchor, get all-zero fused themes. These fall to tempo-only phase inference (≥100 BPM → phase 1, ≥90 → phase 2, ≥70 → phase 3, else phase 4). This creates a false phase distribution driven by tempo alone, unrelated to the song's worship theme.

**Impact**: 15-30% of catalog songs may fall into this bucket, creating an artificial phase cluster around the tempo boundaries (100 BPM, 90 BPM, 70 BPM).

#### Problem 3: THEME_VOCAB Keyword Gaps
**Root cause**: The current vocabulary is small (4-7 Chinese keywords per theme). Many common worship terms are missing, e.g.:
- 讚美 (praise): missing `稱頌`, `頌讚`, `高舉`, `哈利路亞` (traditional form)
- 奉獻 (offering): missing `獻祭`, `交託`, `降服`
- 認罪 (repentance): missing `罪孽`, `洗淨`
- 跟隨 (follow): missing `順服`, `遵行`, `背起十字架`
- 復興 (revival): missing `覺醒`, `聖靈澆灌`

**Impact**: Songs with these words in lyrics won't be classified correctly, defaulting to tempo fallback or wrong themes.

#### Problem 4: Embedding Anchor Text Bias
**Root cause**: The anchor texts in `regen_theme_anchors.py` are short phrases in Simplified Chinese. After Part 1 conversion, they'll be in Traditional Chinese, but they're still very short (4-6 words). The embedding model may not capture the full semantic space of each theme.

**Impact**: Songs whose lyrics use different vocabulary than the anchor text for the same theme may get low cosine scores, even though the theme is semantically present.

#### Problem 5: Binary Phase Assignment
**Root cause**: `infer_phase` picks the single highest-scoring theme (`max(fused.items(), key=...)`), then maps it to exactly one phase. A song that is 50% 讚美 and 49% 奉獻 gets assigned phase 1 only, losing the phase 4 signal entirely.

**Impact**: Songs with mixed themes are forced into a single phase bucket, reducing the pool of candidates available for other phases.

### Recommended Adjustments

#### Recommendation A: Expand THEME_VOCAB with Additional Traditional Chinese Keywords

Add 3-5 more keywords per theme using Traditional Chinese:

| Theme | Additional Keywords |
|---|---|
| 讚美 | `稱頌`, `頌讚`, `高舉`, `哈利路亞`, `揚聲` |
| 感恩 | `恩惠`, `慈愛`, `賜福`, `厚恩` |
| 敬拜 | `降臨`, `同在`, `聖潔`, `配得` |
| 奉獻 | `交託`, `降服`, `獻祭`, `全所有` |
| 認罪 | `罪孽`, `洗淨`, `潔除`, `塗抹` |
| 差遣 | `傳福音`, `大使命`, `做門徒`, `萬邦` |
| 信心 | `堅固`, `應許`, `信靠`, `壯膽` |
| 祈禱 | `懇求`, `代求`, `仰望`, `尋求` |
| 復興 | `覺醒`, `澆灌`, `如火`, `挑旺` |
| 聖靈 | `恩膏`, `膏抹`, `恩賜`, `光照` |
| 十字架 | `捨命`, `代贖`, `挽回祭`, `寶血洗淨` |
| 跟隨 | `順服`, `遵行`, `背起`, `效法` |

**Implementation**: Add these terms to the existing tuples in `THEME_VOCAB`. No structural changes needed.

#### Recommendation B: Add Multi-Phase Tagging for Borderline Songs

Currently `infer_phase` assigns exactly one phase. For songs where the top-2 themes map to different phases and both scores are within a threshold (e.g., top score / second score < 1.3), consider:

**Option 1 (recommended)**: Store a `secondary_phase` field on `SongCandidate` alongside `phase`. The beam search can use `phase` as primary but consider `secondary_phase` as a valid alternative when constraints are tight.

**Option 2**: Allow the candidate to appear in the beam search for multiple phase slots when its top-2 themes span different phases. This requires modifying the beam's phase matching to check against a set of valid phases per candidate rather than a single phase.

**Implementation**:
- Add `secondary_phase: int | None = None` to `SongCandidate` model
- In `infer_phase`, if runner-up theme has a score ratio > 0.75 of the top theme, set `secondary_phase` to the runner-up's phase mapping
- In `_sequences()` beam search, when checking phase match for a position, accept candidates whose `phase` OR `secondary_phase` matches the target

#### Recommendation C: Lower Embedding Classification Influence When Title/Lyrics Are Strong

Currently the fusion is: title (35%) + lyrics (25%) + song_emb (25%) + line_emb (15%). If a song has strong title + lyrics signals (both non-zero), the embedding contribution can override them, pulling toward a different theme.

**Recommendation**: Add a dynamic weight adjustment — when title and lyrics agree on the dominant theme (same argmax), boost their combined weight from 60% to 75% and reduce embedding weights proportionally.

**Implementation**:
```python
def fuse_themes(title, lyrics, song_emb, line_emb):
    # If title and lyrics agree on dominant theme, trust keyword classifiers more
    title_top = max(title.items(), key=lambda x: x[1])[0] if any(title.values()) else None
    lyrics_top = max(lyrics.items(), key=lambda x: x[1])[0] if any(lyrics.values()) else None

    if title_top and title_top == lyrics_top:
        weighted_sources = [(0.45, title), (0.30, lyrics), (0.15, song_emb), (0.10, line_emb)]
    else:
        weighted_sources = [(0.35, title), (0.25, lyrics), (0.25, song_emb), (0.15, line_emb)]
    # ... rest unchanged
```

#### Recommendation D: Add Fallback Theme Detection for Common Worship Words

For songs with zero theme hits from all classifiers, add a secondary keyword scan using a broader set of "worship vocabulary" that doesn't map to specific themes but indicates worship content. If at least 2 worship words are found, assign the song to phase 3 (敬拜) with a confidence flag rather than falling to pure tempo inference.

Common worship words to scan for: `主`, `神`, `耶穌`, `上帝`, `耶和華`, `基督`, `天父`, `拯救`, `恩典`, `愛`. If the song contains these but no specific theme keywords, it's likely a general worship song → phase 3.

**Implementation**: Add a `WORSHIP_FALLBACK_VOCAB` tuple and a `classify_worship_fallback(lyrics_raw)` function. In `infer_phase`, when `max(fused.values()) == 0`, check if the song passes the worship fallback threshold before falling to tempo-only inference.

#### Recommendation E: Normalize Embedding Anchor Texts with Longer, Richer Descriptions

Expand each anchor text from 4-6 words to 8-12 words, including a sentence-like description of the theme in both Chinese and English. This gives the embedding model more semantic context.

**Example for 讚美**:
```python
# Before:
"讚美 歌唱 哈利路亞 praise worship joyful song"
# After:
"讚美神 歌唱哈利路亞 用歡呼稱頌耶和華 praise worship joyful song hallelujah exalt"
```

**Implementation**: Update `ANCHOR_TEXTS` in `regen_theme_anchors.py`, then regenerate `theme_anchors.json` using the embedding endpoint.

---

## Part 4: Documentation Updates

### `docs/agent_guide_songset_constructor.md`

1. **5-Phase Worship Arc table** (lines 58-69): Update all theme names to Traditional
2. **enrich_pool Deep Dive** section (lines 136-320):
   - Update `THEME_VOCAB` code example to show Traditional keys and terms
   - Update `classify_title_themes`, `classify_lyrics_themes` examples
   - Update `fuse_themes` weighted_sources list
   - Update `apply_seasonal_bias` code block to use Traditional theme keys
   - Update `THEME_TO_PHASE` mapping to use Traditional keys
   - Update `infer_phase` code block
3. **Add new section**: "Traditional Chinese Matching Rationale" — explain why all matching uses Traditional Chinese (catalog lyrics are Traditional, eliminates duplicate term pairs, ensures correct matching)
4. **Add new section**: "Pool Enrichment Evaluation" — document the `--only-evaluate-pool-enrichment` flag, what it reports, and how to interpret the distribution metrics
5. **CLI Options table** (lines 28-46): Add `--only-evaluate-pool-enrichment` row
6. **Recipes section**: Add a recipe for evaluating pool enrichment:
   ```bash
   uv run --project lab/poc-scripts --extra songset_constructor \
     python lab/poc-scripts/construct_songset_agent.py \
     --pool-limit 500 --only-evaluate-pool-enrichment
   ```
7. **Update all occurrences** of Simplified Chinese theme names throughout the document (赞美→讚美, 认罪→認罪, 圣灵→聖靈, 复兴→復興, 奉献→奉獻, 祈祷→祈禱, 跟随→跟隨)

---

## Implementation Order

1. **themes.py** — Convert `THEMES` + `THEME_VOCAB` to Traditional (foundational — all other files depend on this)
2. **phases.py** — Convert `THEME_TO_PHASE`, `apply_seasonal_bias`, `infer_phase` to Traditional keys
3. **regen_theme_anchors.py** — Convert `ANCHOR_TEXTS` to Traditional
4. **theme_anchors.json** — Regenerate (or manually rename keys if endpoint unavailable)
5. **Run tests** — Fix all test files with Simplified references
6. **config.py** — Add `only_evaluate_pool_enrichment` field
7. **cli.py** — Add `--only-evaluate-pool-enrichment` flag
8. **graph/nodes.py** — Add `write_enrichment_report` node + `route_after_enrich`
9. **graph/builder.py** — Wire conditional edge + new node
10. **artifacts/writer.py** (or new `artifacts/enrichment_report.py`) — Implement report generation
11. **docs/agent_guide_songset_constructor.md** — Full documentation update
12. **Acceptance test**: Run `--only-evaluate-pool-enrichment --pool-limit 500` against real catalog, verify distribution report, document findings

---

## Verification Steps

1. **Unit tests pass**: `uv run --project lab/poc-scripts --extra songset_constructor --extra test pytest lab/poc-scripts/tests -v`
2. **No Simplified Chinese theme keys remain**: `grep -r "赞美\|认罪\|圣灵\|复兴\|奉献\|祈祷\|跟随" lab/poc-scripts/poc/songset_constructor/` returns nothing (excluding this spec doc)
3. **Enrichment eval runs**: `uv run --project lab/poc-scripts --extra songset_constructor python lab/poc-scripts/construct_songset_agent.py --pool-limit 500 --only-evaluate-pool-enrichment` succeeds and prints distribution summary
4. **Regular run still works**: `uv run --project lab/poc-scripts --extra songset_constructor python lab/poc-scripts/construct_songset_agent.py --songs 4 --pool-limit 500 --top-k 5 --no-llm` produces proposals with Traditional Chinese theme names in output artifacts

---

## Open Items for Phase 2 (Post-Evaluation)

The following items from Part 3 (Recommendations A–E) are recommendations to be **evaluated after** running the enrichment report with the full catalog. The plan is:

1. Implement Part 1 (Traditional conversion) + Part 2 (eval flag) first
2. Run `--only-evaluate-pool-enrichment --pool-limit 500` against the real catalog
3. Analyze the distribution report
4. Based on findings, implement the most impactful recommendations from Part 3 (likely A + B + C)
5. Re-run the eval to measure improvement
