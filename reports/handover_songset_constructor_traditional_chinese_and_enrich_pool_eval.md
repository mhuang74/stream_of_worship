# Handover: Traditional Chinese Theme Matching + enrich_pool Evaluation & Diversity Improvements

## Status: NOT STARTED

This document hands over the full implementation of the spec at
`specs/songset-constructor-traditional-chinese-and-enrich-pool-eval-v1.md` to another agent.

**No code changes have been made yet.** This handover captures the complete context
gathered from reading the spec and every relevant source file, so the next agent can
execute the implementation without re-discovering the lay of the land.

The spec has three coordinated parts plus an evaluation workflow requested by the user:

1. **Part 1** — Convert all theme keys, matching terms, anchor texts, and docs from
   mixed Simplified/Traditional Chinese to **Traditional Chinese only**.
2. **Part 2** — Add a `--only-evaluate-pool-enrichment` CLI flag that runs the graph
   only through `enrich_pool`, then writes a distribution report.
3. **Part 3** — Recommend (post-evaluation) concrete `enrich_pool` diversity adjustments.
4. **User-requested evaluation workflow** — Run the core `enrich_pool` flow
   **before** the Traditional conversion (baseline), then **after** (post-enhancement),
   to measure the impact of the Simplified/Traditional mismatch. Finally run with
   `--only-evaluate-pool-enrichment` post-enhancement and review results.

---

## Critical Context for the Next Agent

### What the user explicitly asked for

> implement @specs/songset-constructor-traditional-chinese-and-enrich-pool-eval-v1.md
> fully. Consider executing the core enrich_pool flow before enrichment and compare to
> post-enhancement to evaluate the impact that mixed Simplified/Traditional characters
> had. And run with '--only-evaluate-enrich-pool' flag post-enhancement and review the
> results and recommend next steps with me.

Key points:
- The user wrote `--only-evaluate-enrich-pool` but the spec (and the flag name that
  should be implemented) is `--only-evaluate-pool-enrichment`. Implement the spec's
  name. (The spec's verification step 3 on line 537 confirms this name.)
- "Before enrichment" means: run `enrich_pool` against the real catalog **before**
  applying Part 1's Traditional conversion, to capture a baseline distribution.
  Then run again **after** the conversion to compare. This requires the eval flag
  (Part 2) to exist first, OR a standalone script that invokes `enrich_pool` and
  prints the same metrics. The cleanest path is: implement Part 1 + Part 2 first,
  then run the eval twice — once on a git stash / pre-conversion checkout, once on
  the converted code. Alternatively, since the conversion is the only change
  between the two runs, you can: (a) implement Part 2 on the pre-conversion code,
  run baseline, (b) apply Part 1, run post-enhancement. The user wants a
  comparison.
- After the post-enhancement eval, **review the results and recommend next steps
  with the user** (i.e., which of Part 3's Recommendations A–E to implement).

### Environment notes

- Package manager: `uv`. The songset constructor lives under
  `lab/poc-scripts/` with extra `songset_constructor`.
- Tests: `uv run --project lab/poc-scripts --extra songset_constructor --extra test pytest lab/poc-scripts/tests -v`
- Run the constructor: `uv run --project lab/poc-scripts --extra songset_constructor python lab/poc-scripts/construct_songset_agent.py ...`
- The constructor reads from PostgreSQL (read-only). The env file at `/opt/sow/.env`
  is auto-loaded. DB access requires `SOW_DATABASE_URL` or the app config.
- `--no-llm` mode requires no LLM credentials. The eval flag should similarly
  require no LLM (enrichment is deterministic).
- The `theme_anchors.json` file is a single-line JSON with 1536-dim vectors per
  theme. Regenerating requires `SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL`.
  If unavailable, **manually rename the JSON keys** from Simplified to Traditional
  (vectors stay the same) and note that a full regeneration should happen later.
  This is explicitly allowed by the spec (lines 170–171).

### graphify

Per AGENTS.md, after modifying code files, run `graphify update .` to keep the
knowledge graph current. Consider reading `graphify-out/GRAPH_REPORT.md` first if
you want god-node / community context.

---

## Part 1: Full Traditional Chinese Conversion

### Rationale

All SOP.org song lyrics are in Traditional Chinese. The current `THEME_VOCAB` uses
Simplified Chinese for theme keys with sporadic Traditional variants mixed in as
matching terms. Simplified-only keywords (e.g., `悔改`→same, `传扬`→傳揚,
`宝血`→寶血, `门徒`→門徒) will only match Simplified text, but the actual lyrics
are Traditional. Some keywords are duplicates (e.g., `赞美` and `讚美` both in the
same vocab entry).

### Files to modify (in implementation order)

#### 1. `lab/poc-scripts/poc/songset_constructor/rules/themes.py` (lines 12–27)

Current state (verified by reading the file):

```python
THEMES = ("赞美", "感恩", "敬拜", "奉献", "认罪", "差遣", "信心", "祈祷", "复兴", "圣灵", "十字架", "跟随")

THEME_VOCAB: dict[str, tuple[str, ...]] = {
    "赞美": ("赞美", "讚美", "歌唱", "欢呼", "hallelujah", "praise", "zan mei"),
    "感恩": ("感恩", "感谢", "謝謝", "恩典", "grace", "thanks", "gan en"),
    "敬拜": ("敬拜", "俯伏", "尊崇", "荣耀", "worship", "adore", "jing bai"),
    "奉献": ("奉献", "献上", "擺上", "祭", "offering", "dedicate", "feng xian"),
    "认罪": ("认罪", "悔改", "赦免", "洁净", "forgive", "repent", "ren zui"),
    "差遣": ("差遣", "宣教", "传扬", "万民", "send", "mission", "chai qian"),
    "信心": ("信心", "相信", "倚靠", "盼望", "faith", "trust", "xin xin"),
    "祈祷": ("祷告", "祈祷", "呼求", "垂听", "prayer", "pray", "qi dao"),
    "复兴": ("复兴", "復興", "更新", "燃烧", "revival", "renew", "fu xing"),
    "圣灵": ("圣灵", "聖靈", "灵火", "充满", "holy spirit", "sheng ling"),
    "十字架": ("十字架", "宝血", "羔羊", "救赎", "cross", "blood", "shi zi jia"),
    "跟随": ("跟随", "跟從", "道路", "门徒", "follow", "disciple", "gen sui"),
}
```

Target (from spec lines 38–141). Replace with:

```python
THEMES = ("讚美", "感恩", "敬拜", "奉獻", "認罪", "差遣", "信心", "祈禱", "復興", "聖靈", "十字架", "跟隨")

THEME_VOCAB: dict[str, tuple[str, ...]] = {
    "讚美": ("讚美", "歌唱", "歡呼", "hallelujah", "praise", "zan mei"),
    "感恩": ("感恩", "感謝", "謝謝", "恩典", "grace", "thanks", "gan en"),
    "敬拜": ("敬拜", "俯伏", "尊崇", "榮耀", "worship", "adore", "jing bai"),
    "奉獻": ("奉獻", "獻上", "擺上", "祭", "offering", "dedicate", "feng xian"),
    "認罪": ("認罪", "悔改", "赦免", "潔淨", "forgive", "repent", "ren zui"),
    "差遣": ("差遣", "宣教", "傳揚", "萬民", "send", "mission", "chai qian"),
    "信心": ("信心", "相信", "倚靠", "盼望", "faith", "trust", "xin xin"),
    "祈禱": ("禱告", "祈禱", "呼求", "垂聽", "prayer", "pray", "qi dao"),
    "復興": ("復興", "更新", "燃燒", "revival", "renew", "fu xing"),
    "聖靈": ("聖靈", "靈火", "充滿", "holy spirit", "sheng ling"),
    "十字架": ("十字架", "寶血", "羔羊", "救贖", "cross", "blood", "shi zi jia"),
    "跟隨": ("跟隨", "跟從", "道路", "門徒", "follow", "disciple", "gen sui"),
}
```

Notes:
- Remove Simplified/Traditional duplicate pairs (e.g., drop `赞美` since `讚美` is
  present; drop `圣灵` since `聖靈` is present; drop `复兴` since `復興` is present).
- English and pinyin terms remain unchanged.
- The rest of `themes.py` (the classifier functions) needs no changes — they
  iterate over `THEMES` and `THEME_VOCAB` dynamically.

#### 2. `lab/poc-scripts/poc/songset_constructor/rules/phases.py` (lines 7–20, 48–63, 66–69)

Current `THEME_TO_PHASE` (lines 7–20):

```python
THEME_TO_PHASE = {
    "赞美": 1,
    "感恩": 2,
    "敬拜": 3,
    "祈祷": 3,
    "信心": 3,
    "圣灵": 3,
    "奉献": 4,
    "认罪": 4,
    "十字架": 4,
    "差遣": 5,
    "跟随": 5,
    "复兴": 5,
}
```

Target:

```python
THEME_TO_PHASE = {
    "讚美": 1,
    "感恩": 2,
    "敬拜": 3,
    "祈禱": 3,
    "信心": 3,
    "聖靈": 3,
    "奉獻": 4,
    "認罪": 4,
    "十字架": 4,
    "差遣": 5,
    "跟隨": 5,
    "復興": 5,
}
```

`apply_seasonal_bias` (lines 48–63) — replace all Simplified theme keys with Traditional:

```python
def apply_seasonal_bias(fused: dict[str, float], season: str | None) -> dict[str, float]:
    if season not in {"advent", "christmas", "lent", "easter", "pentecost"}:
        return fused
    biased = dict(fused)
    if season in {"advent", "christmas"}:
        biased["讚美"] = max(biased.get("讚美", 0.0), 0.7)
        biased["感恩"] = max(biased.get("感恩", 0.0), 0.5)
    elif season == "lent":
        biased["認罪"] = max(biased.get("認罪", 0.0), 0.7)
        biased["十字架"] = max(biased.get("十字架", 0.0), 0.65)
    elif season == "easter":
        biased["復興"] = max(biased.get("復興", 0.0), 0.65)
        biased["讚美"] = max(biased.get("讚美", 0.0), 0.65)
    elif season == "pentecost":
        biased["聖靈"] = max(biased.get("聖靈", 0.0), 0.75)
    return biased
```

`infer_phase` (line 69) — change the `圣灵` check to `聖靈`:

```python
if theme == "聖靈" and tempo_bpm is not None and tempo_bpm < 70:
```

#### 3. `lab/poc-scripts/poc/songset_constructor/regen_theme_anchors.py` (lines 16–29)

Current `ANCHOR_TEXTS`:

```python
ANCHOR_TEXTS = {
    "赞美": "赞美 歌唱 哈利路亚 praise worship joyful song",
    "感恩": "感恩 感谢 恩典 grace thanksgiving thank you Lord",
    "敬拜": "敬拜 尊崇 荣耀 俯伏 worship adore glory",
    "奉献": "奉献 献上 摆上 offering dedicate surrender",
    "认罪": "认罪 悔改 赦免 洁净 repentance confession forgiveness",
    "差遣": "差遣 宣教 传扬 万民 mission send proclaim",
    "信心": "信心 相信 倚靠 盼望 faith trust hope",
    "祈祷": "祷告 祈祷 呼求 垂听 prayer intercession cry out",
    "复兴": "复兴 更新 燃烧 revival renewal awaken",
    "圣灵": "圣灵 充满 灵火 Holy Spirit fill fire",
    "十字架": "十字架 宝血 羔羊 救赎 cross blood lamb redemption",
    "跟随": "跟随 道路 门徒 顺服 follow disciple obedience",
}
```

Target (convert all Chinese to Traditional; English unchanged):

```python
ANCHOR_TEXTS = {
    "讚美": "讚美 歌唱 哈利路亞 praise worship joyful song",
    "感恩": "感恩 感謝 恩典 grace thanksgiving thank you Lord",
    "敬拜": "敬拜 尊崇 榮耀 俯伏 worship adore glory",
    "奉獻": "奉獻 獻上 擺上 offering dedicate surrender",
    "認罪": "認罪 悔改 赦免 潔淨 repentance confession forgiveness",
    "差遣": "差遣 宣教 傳揚 萬民 mission send proclaim",
    "信心": "信心 相信 倚靠 盼望 faith trust hope",
    "祈禱": "禱告 祈禱 呼求 垂聽 prayer intercession cry out",
    "復興": "復興 更新 燃燒 revival renewal awaken",
    "聖靈": "聖靈 充滿 靈火 Holy Spirit fill fire",
    "十字架": "十字架 寶血 羔羊 救贖 cross blood lamb redemption",
    "跟隨": "跟隨 道路 門徒 順服 follow disciple obedience",
}
```

Note: `哈利路亚` → `哈利路亞` (亚→亞). The spec line 164 shows
"赞美 歌唱 哈利路亚" → "讚美 歌唱 哈利路亞".

#### 4. `lab/poc-scripts/poc/songset_constructor/data/theme_anchors.json`

This is a single-line JSON. Current keys (verified via `python3 -c "import json; ..."`):

```
['赞美', '感恩', '敬拜', '奉献', '认罪', '差遣', '信心', '祈祷', '复兴', '圣灵', '十字架', '跟随']
```

**Rename the keys to Traditional** (vectors unchanged). The cleanest way is a small
Python script that loads the JSON, renames keys via the same mapping as `THEMES`,
and writes it back. Example:

```python
import json
from pathlib import Path

mapping = {
    "赞美": "讚美", "感恩": "感恩", "敬拜": "敬拜", "奉献": "奉獻",
    "认罪": "認罪", "差遣": "差遣", "信心": "信心", "祈祷": "祈禱",
    "复兴": "復興", "圣灵": "聖靈", "十字架": "十字架", "跟随": "跟隨",
}
path = Path("lab/poc-scripts/poc/songset_constructor/data/theme_anchors.json")
payload = json.loads(path.read_text(encoding="utf-8"))
payload["anchors"] = {mapping[k]: v for k, v in payload["anchors"].items()}
path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
```

**Important:** Preserve the `model_version` and `dim` fields. Keep the file as a
single line with no indentation (matching the original format). Do NOT regenerate
vectors unless `SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL` are available —
per spec lines 167–171, manual key rename is the fallback.

#### 5. `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py` (line 16)

`THEMES` is imported from `themes.py`, so no direct changes needed — it will
automatically reference the Traditional keys. Verified: line 16 is
`from poc.songset_constructor.rules.themes import THEMES`. No edit required.

#### 6. Test files

**`lab/poc-scripts/tests/conftest.py`** (lines 18–23) — the `synthetic_pool` fixture.
Replace:

- Line 18: `title="赞美主"` → `title="讚美主"`, `themes={"赞美": 1}` → `themes={"讚美": 1}`
- Line 19: `感恩` — unchanged
- Line 20: `敬拜` — unchanged
- Line 21: `十字架` — unchanged
- Line 22: `title="跟随主"` → `title="跟隨主"`, `themes={"跟随": 1}` → `themes={"跟隨": 1}`
- Line 23: `title="复兴"` → `title="復興"`, `themes={"复兴": 1}` → `themes={"復興": 1}`

**`lab/poc-scripts/tests/test_songset_constructor_rules.py`** (lines 30–33):

```python
def test_theme_fusion_and_phase_inference():
    title = classify_title_themes("讚美主")
    lyrics = classify_lyrics_themes("我要讚美\n感謝你的恩典")
    fused = fuse_themes(title, lyrics, {}, {})
    assert max(fused, key=fused.get) == "讚美"
    assert infer_phase(fused, 124) == 1
```

**`lab/poc-scripts/tests/test_songset_constructor_graph.py`** (line 103):

```python
assert "h001: 讚美主" in prompt
```

**`lab/poc-scripts/tests/test_songset_constructor_artifacts.py`** — many lines.
The full list of Simplified references found via grep (file:line:content):

- Line 27: `title="赞美主"` → `title="讚美主"`
- Line 29: `themes=["赞美"]` → `themes=["讚美"]`
- Line 63: `assert "赞美主" in prompt` → `assert "讚美主" in prompt`
- Line 75: `| 1 | 赞美主 |` → `| 1 | 讚美主 |`
- Line 88: `assert "赞美主" in report` → `assert "讚美主" in report`
- Line 109: `| 1 | 赞美主 | 1 | 124 | G maj | 赞美 | shift 0, gap 2 beats |` →
  replace both `赞美主`→`讚美主` and `赞美`→`讚美`
- Line 130: `assert "赞美主" in report` → `assert "讚美主" in report`
- Line 152: `title: str = "赞美主"` → `title: str = "讚美主"`
- Line 166: `themes if themes is not None else ["赞美"]` → `["讚美"]`
- Line 254: `_item(1, phase=1, themes=["赞美"])` → `themes=["讚美"]`
- Line 262: `assert "赞美" in narrative` → `assert "讚美" in narrative`
- Line 263: `assert "敬拜" in narrative` — unchanged
- Line 264: `assert "差遣" in narrative` — unchanged
- Line 269: `_item(1, phase=1, themes=["赞美"])` → `["讚美"]`; `themes=["奉献"]` → `["奉獻"]`
- Line 281: `themes=["认罪"]` → `themes=["認罪"]`
- Line 304: `_item(1, "赞美主", "s1", 1, ["赞美"], 124, "G", "maj")` →
  `_item(1, "讚美主", "s1", 1, ["讚美"], 124, "G", "maj")`
- Line 320: same pattern → `讚美主`, `["讚美"]`
- Line 360: same pattern → `讚美主`, `["讚美"]`
- Line 366: `_item(1, "跟随主", "s3", 5, ["跟随"], 78, "B", "min")` →
  `_item(1, "跟隨主", "s3", 5, ["跟隨"], 78, "B", "min")`
- Line 402: `_proposal_with_items([_item(1, "赞美主", "s1", 1)])` → `"讚美主"`
- Line 404: `_item(1, "跟随主", "s3", 5)` → `"跟隨主"`
- Line 424: `"赞美主"` → `"讚美主"`
- Line 426: `"跟随主"` → `"跟隨主"`
- Line 449: `"赞美主"` → `"讚美主"`
- Line 476: `"赞美主"` → `"讚美主"`
- Line 484: `_item(1, "赞美主", "s1", 1, ["赞美"], 124, "G", "maj")` → `讚美主`, `["讚美"]`
- Line 486: `_item(3, "跟随主", "s5", 5, ["跟随"], 78, "B", "min")` → `跟隨主`, `["跟隨"]`
- Line 492: same as 484
- Line 494: same as 486
- Line 500: same as 484
- Line 502: `_item(3, "复兴", "s6", 5, ["复兴"], 82, "F#", "min")` →
  `_item(3, "復興", "s6", 5, ["復興"], 82, "F#", "min")`
- Line 520: `assert "赞美主" in text` → `assert "讚美主" in text`
- Line 551: `_item(1, "赞美主", "s1", 1, ["赞美"])` → `讚美主`, `["讚美"]`
- Line 559: `_item(2, "跟随主", "s5", 5, ["跟随"])` → `跟隨主`, `["跟隨"]`
- Line 560: `_item(3, "复兴", "s6", 5, ["复兴"])` → `復興`, `["復興"]`
- Line 578: `讚美主`, `["讚美"]`
- Line 580: `跟隨主`, `["跟隨"]`
- Line 586: `讚美主`, `["讚美"]`
- Line 588: `復興`, `["復興"]`
- Line 594: `讚美主`, `["讚美"]`
- Line 596: `跟隨主`, `["跟隨"]`
- Line 616: `_proposal_with_items([_item(1, "赞美主", "s1", 1)])` → `"讚美主"`

**Tip:** For `test_songset_constructor_artifacts.py`, the simplest approach is a
search-and-replace across the whole file:
- `赞美主` → `讚美主`
- `["赞美"]` → `["讚美"]`
- `"赞美"` (as a standalone theme string in asserts/lists) → `"讚美"`
- `跟随主` → `跟隨主`
- `["跟随"]` → `["跟隨"]`
- `"跟随"` → `"跟隨"`
- `复兴` → `復興` (both as title and theme)
- `["复兴"]` → `["復興"]`
- `奉献` → `奉獻` (as theme)
- `认罪` → `認罪` (as theme)

Be careful with `感恩`, `敬拜`, `差遣`, `十字架`, `信心` — these are the same in
Simplified and Traditional, so they need no change.

**`lab/poc-scripts/tests/test_songset_constructor_db.py`** — verified by reading
the full file (8 lines). It only checks SQL query string fragments, no Chinese
theme references. **No changes needed.**

**`lab/poc-scripts/tests/test_songset_constructor_config.py`** — verified by reading
the full file (116 lines). No Chinese theme references. **No changes needed.**

**`lab/poc-scripts/tests/test_songset_constructor_cli.py`** — verified by reading
the full file (332 lines). No Simplified theme references in output assertions.
**No changes needed.** (The CLI tests use `synthetic_pool` from conftest, which
will be updated.)

**`lab/poc-scripts/tests/test_eval_lrc.py`** line 69 — contains `全心赞美` but this
is in the LRC eval test, unrelated to songset constructor themes. **Do not change.**

#### 7. `docs/agent_guide_songset_constructor.md`

Full doc read. Sections to update:

- **5-Phase Worship Arc table** (lines 62–68): Update theme names to Traditional.
  - Line 64: `赞美 (Praise)` → `讚美 (Praise)`
  - Line 66: `敬拜/祈祷/信心/圣灵 (Worship)` → `敬拜/祈禱/信心/聖靈 (Worship)`
  - Line 67: `奉献/认罪/十字架 (Response)` → `奉獻/認罪/十字架 (Response)`
  - Line 68: `差遣/跟随/复兴 (Commission)` → `差遣/跟隨/復興 (Commission)`

- **Seasonal bias examples** (lines 70–72):
  - Line 71: `讚美/感恩` for christmas (already `讚美`? No — currently `赞美/感恩`)
  - Line 72: `认罪/十字架` → `認罪/十字架` for lent

- **enrich_pool Deep Dive** section (lines 136–320): Update all code examples:
  - Line 169: `THEME_VOCAB` example — replace with Traditional keys/terms
  - Lines 259–269: `apply_seasonal_bias` code block — replace Simplified keys
  - Lines 281–286: `THEME_TO_PHASE` mapping — replace with Traditional keys
  - Line 291: `if theme == "圣灵"` → `if theme == "聖靈"`
  - Line 292: comment `# slow 圣灵 → Response` → `# slow 聖靈 → Response`

- **Add new section** after the enrich_pool Deep Dive: "Traditional Chinese Matching
  Rationale" — explain why all matching uses Traditional Chinese (catalog lyrics are
  Traditional, eliminates duplicate term pairs, ensures correct matching).

- **Add new section**: "Pool Enrichment Evaluation" — document the
  `--only-evaluate-pool-enrichment` flag, what it reports, and how to interpret the
  distribution metrics. (This depends on Part 2 being implemented.)

- **CLI Options table** (lines 28–46): Add a row for `--only-evaluate-pool-enrichment`.

- **Recipes section** (around line 488): Add a recipe for evaluating pool enrichment:
  ```bash
  uv run --project lab/poc-scripts --extra songset_constructor \
    python lab/poc-scripts/construct_songset_agent.py \
    --pool-limit 500 --only-evaluate-pool-enrichment
  ```

- **Update all occurrences** of Simplified theme names throughout the document:
  `赞美`→`讚美`, `认罪`→`認罪`, `圣灵`→`聖靈`, `复兴`→`復興`, `奉献`→`奉獻`,
  `祈祷`→`祈禱`, `跟随`→`跟隨`.

#### 8. `lab/poc-scripts/poc/songset_constructor/README.md`

Verified by reading the full file (56 lines). No Simplified Chinese theme references
found. **No changes needed.**

---

## Part 2: enrich_pool Evaluation Flag

### 2.1 `RunConfig` (`lab/poc-scripts/poc/songset_constructor/config.py`)

Add a new field to the `RunConfig` dataclass (after line 59, the `relax_h5_cfd` field):

```python
only_evaluate_pool_enrichment: bool = False
```

Add to `to_dict()` return dict (after line 168, `"relax_h5_cfd": self.relax_h5_cfd,`):

```python
"only_evaluate_pool_enrichment": self.only_evaluate_pool_enrichment,
```

The `to_dict()` method is at lines 143–169. The `__post_init__` method (lines 61–95)
needs no change for this field (no validation required).

### 2.2 CLI (`lab/poc-scripts/poc/songset_constructor/cli.py`)

The `construct` command starts at line 324. Its parameters end at line 349
(`relax_h5_cfd`). Add a new Typer option after `relax_h5_cfd`:

```python
only_evaluate_pool_enrichment: Annotated[
    bool, typer.Option("--only-evaluate-pool-enrichment/--full-run")
] = False,
```

Pass it to the `RunConfig(...)` constructor (the constructor call spans lines
353–377). Add:

```python
only_evaluate_pool_enrichment=only_evaluate_pool_enrichment,
```

**Important behavior change** (spec lines 259–263): When
`config.only_evaluate_pool_enrichment` is True:
- **Skip `config.validate_environment()`** for LLM (since no LLM is needed for
  enrichment-only). The current call is at line 378:
  `config.validate_environment()`. Wrap it:
  ```python
  if not config.only_evaluate_pool_enrichment:
      config.validate_environment()
  ```
  Note: `validate_environment()` is a no-op when `no_llm=True` (returns early at
  line 129), so the eval flag effectively also skips the LLM check. But to be
  safe and explicit, gate it on the eval flag.
- After the graph completes, the `write_enrichment_report` node will handle
  writing the report and the CLI's existing `_print_output_files` will print the
  artifact path. The console summary is printed by the node itself (via the
  trace) or by a dedicated CLI branch. The spec (lines 342–371) shows a console
  summary format. The cleanest approach: have the `write_enrichment_report` node
  write `enrichment_report.md` to `output_dir` and return `artifact_paths`, then
  the CLI's existing flow (`_print_output_files` at line 404) will print it. The
  console summary can be printed by the node via `console.print` — but nodes
  don't have access to the CLI's `console`. Instead, print the summary in the CLI
  after the graph completes, by reading the report data from the result state.

  **Recommended approach:** Add a CLI branch after `result = _run_graph_with_traces(...)`:
  ```python
  if config.only_evaluate_pool_enrichment:
      _print_enrichment_summary(config, result)
      paths = result.get("artifact_paths", {})
      _print_output_files(paths)
      return
  ```
  Implement `_print_enrichment_summary` to render the console format from spec
  lines 347–371. The data comes from the `write_enrichment_report` node's trace
  event or from a dedicated state field. Simplest: have the node store the
  computed metrics in the state (e.g., `enrichment_metrics: dict`) and the CLI
  reads `result.get("enrichment_metrics")`.

### 2.3 Graph Early Exit (`lab/poc-scripts/poc/songset_constructor/graph/builder.py`)

Current builder (76 lines, fully read). The relevant edges:

```python
builder.add_edge(START, "load_catalog")
builder.add_edge("load_catalog", "enrich_pool")
builder.add_edge("enrich_pool", "build_transition_matrix")  # <-- replace this
```

Replace the `enrich_pool` → `build_transition_matrix` edge with a conditional edge:

```python
def route_after_enrich(state: ConstructorState) -> str:
    if state["config"].only_evaluate_pool_enrichment:
        return "write_enrichment_report"
    return "build_transition_matrix"

# In build_graph:
builder.add_node("write_enrichment_report", write_enrichment_report)
builder.add_conditional_edges(
    "enrich_pool",
    route_after_enrich,
    {
        "write_enrichment_report": "write_enrichment_report",
        "build_transition_matrix": "build_transition_matrix",
    },
)
builder.add_edge("write_enrichment_report", END)
```

Add the import of `write_enrichment_report` and `route_after_enrich` to the
import block at lines 9–26.

### 2.4 New node (`lab/poc-scripts/poc/songset_constructor/graph/nodes.py`)

Add a `write_enrichment_report` node and the `route_after_enrich` function. The
node delegates to a new `artifacts/enrichment_report.py` module (recommended) for
the actual report generation.

```python
def write_enrichment_report(state: ConstructorState) -> dict:
    config = state["config"]
    pool = state.get("pool", [])
    trace = [*_trace(state, "write_enrichment_report", "exit")]
    metrics, report_text = build_enrichment_report(
        pool=pool,
        config=config,
        load_trace=_latest_trace_data(state.get("trace", []), "load_catalog"),
        enrich_trace=_latest_trace_data(state.get("trace", []), "enrich_pool"),
    )
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "enrichment_report.md"
    report_path.write_text(report_text, encoding="utf-8")
    return {
        "artifact_paths": {"enrichment_report": str(report_path)},
        "enrichment_metrics": metrics,
        "trace": trace,
    }


def route_after_enrich(state: ConstructorState) -> str:
    if state["config"].only_evaluate_pool_enrichment:
        return "write_enrichment_report"
    return "build_transition_matrix"
```

You'll need to add imports: `from pathlib import Path`, the new
`build_enrichment_report` function, and a `_latest_trace_data` helper (or inline
the trace lookup). Note that `nodes.py` currently doesn't import `Path`.

### 2.5 Enrichment Report Content (`lab/poc-scripts/poc/songset_constructor/artifacts/enrichment_report.py`)

**Create a new file** `artifacts/enrichment_report.py`. This keeps the report
logic separate from `writer.py` (which is already 916 lines).

The report must compute and output (spec lines 297–341):

**A. Pool Overview**
- Total songs loaded from catalog (`pool_size` before enrichment) — from
  `load_catalog` trace data, key `pool_size`.
- Total songs after enrichment (after dropping missing metadata) — `len(pool)`.
- Drop count + drop reasons — from `enrich_pool` trace data, keys `dropped` and
  `drop_reasons` (a dict) and `dropped_samples`.

**B. Phase Distribution**
- Count of songs per phase (1–5) — `Counter(c.phase for c in pool)`.
- Percentage of pool in each phase.
- Phase balance indicator: mark phases < 15% as "underrepresented".

**C. Theme Distribution**
- For each of the 12 themes: how many songs have it as their dominant (highest
  fused score) theme. Compute: for each candidate, find
  `max(c.themes.items(), key=lambda x: x[1])` if any score > 0.
- How many songs have zero themes detected (all fused scores = 0).
- Top 5 most common dominant themes.
- Top 5 least common dominant themes.

**D. Phase Inference Source**
- How many songs had phase inferred from themes (fused score > 0) — i.e., songs
  where `max(c.themes.values()) > 0`.
- How many fell back to tempo-only inference — songs where all theme scores are 0
  but `tempo_bpm` is not None.
- How many of the tempo-fallback songs have no tempo (phase = 3 default) — songs
  where all theme scores are 0 AND `tempo_bpm is None`.

**E. Theme Signal Coverage**
- How many songs have title theme hits — this requires re-running
  `classify_title_themes` per song, OR storing this during enrichment. Since
  `enrich_pool` doesn't store the per-classifier breakdown, you'll need to
  recompute it here. Import `classify_title_themes`, `classify_lyrics_themes`,
  and `load_theme_anchors` + `classify_embedding_themes`.
- How many songs have lyrics theme hits.
- How many songs have song embedding (not None).
- How many songs have line embeddings (non-empty list).

**F. Tempo & Key Coverage**
- How many songs have `tempo_bpm` (known vs missing).
- BPM range: min, max, median.
- How many songs have `musical_key` (known vs missing).
- How many songs have `key_confidence < 0.6`.

**G. Album Series Distribution**
- Count per `album_series`.

**H. Diversity Assessment**
- Unique theme coverage: how many of the 12 themes appear as dominant for at
  least one song.
- Theme entropy: Shannon entropy of dominant theme distribution.
  `H = -sum(p_i * log2(p_i))`. Max = `log2(12)` ≈ 3.585.
- Phase entropy: Shannon entropy of phase distribution. Max = `log2(5)` ≈ 2.322.

**Console output format** (spec lines 347–371):

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

The `build_enrichment_report` function should return a tuple
`(metrics: dict, report_text: str)` where `metrics` is JSON-serializable for the
console summary, and `report_text` is the markdown content for the file.

### 2.6 State schema (`lab/poc-scripts/poc/songset_constructor/graph/state.py`)

Add optional fields to `ConstructorState` (after line 32, `artifact_paths`):

```python
enrichment_metrics: dict[str, Any]
```

This is optional (`total=False` is already set on the TypedDict, so no other
change needed).

---

## Part 3: enrich_pool Diversity Recommendations

These are **recommendations to evaluate after running the enrichment report**,
NOT to implement upfront. The spec (lines 542–550) explicitly says:

> 1. Implement Part 1 (Traditional conversion) + Part 2 (eval flag) first
> 2. Run `--only-evaluate-pool-enrichment --pool-limit 500` against the real catalog
> 3. Analyze the distribution report
> 4. Based on findings, implement the most impactful recommendations from Part 3
>    (likely A + B + C)
> 5. Re-run the eval to measure improvement

The five recommendations (spec lines 411–487):

- **A: Expand THEME_VOCAB** with additional Traditional Chinese keywords (table at
  lines 417–430). Add 3–5 more keywords per theme.
- **B: Add Multi-Phase Tagging** for borderline songs — add `secondary_phase` to
  `SongCandidate`, modify `infer_phase` to set it when runner-up theme score ratio
  > 0.75, modify beam search to accept `secondary_phase` matches.
- **C: Lower Embedding Influence** when title/lyrics agree — dynamic weight
  adjustment in `fuse_themes` (boost title+lyrics from 60% to 75% when they agree
  on dominant theme).
- **D: Fallback Theme Detection** for common worship words — add
  `WORSHIP_FALLBACK_VOCAB` and `classify_worship_fallback(lyrics_raw)`. In
  `infer_phase`, when all fused scores are 0, check worship fallback before
  tempo-only inference.
- **E: Normalize Embedding Anchor Texts** with longer, richer descriptions (8–12
  words, sentence-like, bilingual).

**Do NOT implement these now.** Wait for the eval results, then discuss with the
user which to implement.

---

## Evaluation Workflow (User's Request)

The user wants:

1. **Baseline (pre-enhancement)**: Run the core `enrich_pool` flow against the real
   catalog **before** applying Part 1's Traditional conversion, to capture the
   distribution with the current mixed Simplified/Traditional vocab.

2. **Post-enhancement**: Run the same eval **after** Part 1 conversion, to measure
   the impact of the mismatch.

3. **Final eval**: Run with `--only-evaluate-pool-enrichment` post-enhancement and
   review results.

### Recommended execution order

Since the eval flag (Part 2) doesn't exist yet, and the baseline must run on
pre-conversion code, the cleanest sequence is:

1. **Implement Part 2 first** (config flag, CLI flag, graph node, report module)
   on the **current** (pre-conversion) codebase. This gives you the eval tool.
2. **Run baseline eval**:
   ```bash
   set -a && source /opt/sow/.env && set +a
   uv run --project lab/poc-scripts --extra songset_constructor \
     python lab/poc-scripts/construct_songset_agent.py \
     --pool-limit 500 --only-evaluate-pool-enrichment
   ```
   Save the output to `reports/` (e.g., `enrichment_eval_baseline_pre_traditional.md`).
3. **Implement Part 1** (Traditional conversion of all files + tests).
4. **Run unit tests** to verify the conversion:
   ```bash
   uv run --project lab/poc-scripts --extra songset_constructor --extra test pytest lab/poc-scripts/tests -v
   ```
5. **Run post-enhancement eval**:
   ```bash
   uv run --project lab/poc-scripts --extra songset_constructor \
     python lab/poc-scripts/construct_songset_agent.py \
     --pool-limit 500 --only-evaluate-pool-enrichment
   ```
   Save the output to `reports/` (e.g., `enrichment_eval_post_traditional.md`).
6. **Compare** the two reports: phase distribution, theme dominance, signal
   coverage, entropy. The key metrics to compare:
   - Phase 3 (敬拜) dominance — did it decrease?
   - Zero-theme songs count — did it decrease?
   - Theme entropy — did it increase?
   - Signal coverage (title/lyrics hits) — did they increase?
7. **Review results with the user** and recommend which of Part 3's
   Recommendations A–E to implement next.

### Alternative: git-stash approach

If you prefer to implement Part 1 first (since it's the foundational change), you
can:
1. Implement Part 1 + Part 2 together.
2. `git stash` the Part 1 changes (keep Part 2).
3. Run baseline eval (with Simplified vocab + eval flag).
4. `git stash pop` to restore Part 1.
5. Run post-enhancement eval.

This is riskier because the eval flag's tests may depend on Traditional keys.
The first approach (Part 2 first, then Part 1) is cleaner.

### DB access

The eval requires PostgreSQL access. The constructor reads from the catalog
database via `ReadOnlyClient`. Ensure `/opt/sow/.env` has `SOW_DATABASE_URL` or
the app config is set up. If DB is not available, the eval cannot run against the
real catalog — in that case, skip the baseline/post comparison and just verify
the eval flag works with the synthetic test pool.

---

## Verification Steps (from spec lines 533–539)

1. **Unit tests pass**:
   ```bash
   uv run --project lab/poc-scripts --extra songset_constructor --extra test pytest lab/poc-scripts/tests -v
   ```

2. **No Simplified Chinese theme keys remain** in the constructor source:
   ```bash
   grep -r "赞美\|认罪\|圣灵\|复兴\|奉献\|祈祷\|跟随" lab/poc-scripts/poc/songset_constructor/
   ```
   This should return nothing (excluding the spec doc and `theme_anchors.json`
   which should now have Traditional keys). Note: `regen_theme_anchors.py` will
   have Traditional keys after conversion, so it should be clean too.

3. **Enrichment eval runs**:
   ```bash
   uv run --project lab/poc-scripts --extra songset_constructor \
     python lab/poc-scripts/construct_songset_agent.py \
     --pool-limit 500 --only-evaluate-pool-enrichment
   ```
   Should succeed and print distribution summary.

4. **Regular run still works**:
   ```bash
   uv run --project lab/poc-scripts --extra songset_constructor \
     python lab/poc-scripts/construct_songset_agent.py \
     --songs 4 --pool-limit 500 --top-k 5 --no-llm
   ```
   Should produce proposals with Traditional Chinese theme names in output
   artifacts.

---

## Implementation Order (from spec lines 516–530)

1. `themes.py` — Convert `THEMES` + `THEME_VOCAB` to Traditional (foundational)
2. `phases.py` — Convert `THEME_TO_PHASE`, `apply_seasonal_bias`, `infer_phase`
3. `regen_theme_anchors.py` — Convert `ANCHOR_TEXTS` to Traditional
4. `theme_anchors.json` — Regenerate (or manually rename keys if endpoint unavailable)
5. Run tests — Fix all test files with Simplified references
6. `config.py` — Add `only_evaluate_pool_enrichment` field
7. `cli.py` — Add `--only-evaluate-pool-enrichment` flag
8. `graph/nodes.py` — Add `write_enrichment_report` node + `route_after_enrich`
9. `graph/builder.py` — Wire conditional edge + new node
10. `artifacts/enrichment_report.py` (new) — Implement report generation
11. `docs/agent_guide_songset_constructor.md` — Full documentation update
12. Acceptance test: Run `--only-evaluate-pool-enrichment --pool-limit 500` against
    real catalog, verify distribution report, document findings

**However**, for the user's evaluation workflow (baseline vs post-enhancement),
swap steps 1–5 and 6–10: implement Part 2 (eval flag) first, run baseline, then
implement Part 1 (Traditional conversion), run post-enhancement. See the
"Evaluation Workflow" section above.

---

## Session Completion (MANDATORY per AGENTS.md)

After all work is done:

```bash
git pull --rebase
git push
git status  # MUST show "up to date with origin"
```

Never stop before pushing. Never say "ready to push when you are" — YOU must push.
If push fails, resolve and retry until it succeeds.

Also run `graphify update .` after modifying code files to keep the knowledge graph
current.

---

## Key File Locations (verified)

| File | Purpose |
|------|---------|
| `lab/poc-scripts/construct_songset_agent.py` | CLI entrypoint (8 lines, just calls `app()`) |
| `lab/poc-scripts/poc/songset_constructor/cli.py` | Typer CLI with all options (412 lines) |
| `lab/poc-scripts/poc/songset_constructor/config.py` | RunConfig dataclass (169 lines) |
| `lab/poc-scripts/poc/songset_constructor/graph/builder.py` | LangGraph state machine (76 lines) |
| `lab/poc-scripts/poc/songset_constructor/graph/nodes.py` | Graph node implementations (395 lines) |
| `lab/poc-scripts/poc/songset_constructor/graph/state.py` | ConstructorState TypedDict (33 lines) |
| `lab/poc-scripts/poc/songset_constructor/rules/themes.py` | THEME_VOCAB + classifiers (79 lines) |
| `lab/poc-scripts/poc/songset_constructor/rules/phases.py` | THEME_TO_PHASE, fuse_themes, infer_phase (85 lines) |
| `lab/poc-scripts/poc/songset_constructor/rules/embeddings.py` | cosine + load_theme_anchors (41 lines) |
| `lab/poc-scripts/poc/songset_constructor/rules/diagnostics.py` | enrichment_drop_diagnostics (217 lines) |
| `lab/poc-scripts/poc/songset_constructor/regen_theme_anchors.py` | ANCHOR_TEXTS + regen script (58 lines) |
| `lab/poc-scripts/poc/songset_constructor/data/theme_anchors.json` | 1536-dim anchor vectors (1 line JSON) |
| `lab/poc-scripts/poc/songset_constructor/artifacts/writer.py` | Output file generation (916 lines) |
| `lab/poc-scripts/poc/songset_constructor/models.py` | SongCandidate, SongsetProposal, etc. (106 lines) |
| `lab/poc-scripts/poc/songset_constructor/db.py` | Read-only catalog pool query (120 lines) |
| `lab/poc-scripts/tests/conftest.py` | synthetic_pool fixture (24 lines) |
| `lab/poc-scripts/tests/test_songset_constructor_rules.py` | Rule tests (628 lines) |
| `lab/poc-scripts/tests/test_songset_constructor_graph.py` | Graph tests (144 lines) |
| `lab/poc-scripts/tests/test_songset_constructor_artifacts.py` | Artifact tests (625 lines) |
| `lab/poc-scripts/tests/test_songset_constructor_cli.py` | CLI tests (332 lines) |
| `lab/poc-scripts/tests/test_songset_constructor_config.py` | Config tests (116 lines) |
| `lab/poc-scripts/tests/test_songset_constructor_db.py` | DB query tests (8 lines) |
| `docs/agent_guide_songset_constructor.md` | Agent guide (609 lines) |

---

## Summary of What Needs to Be Done

1. **Part 1 (Traditional conversion):** Edit 4 source files (`themes.py`,
   `phases.py`, `regen_theme_anchors.py`, `theme_anchors.json`), 3 test files
   (`conftest.py`, `test_songset_constructor_rules.py`,
   `test_songset_constructor_graph.py`, `test_songset_constructor_artifacts.py`),
   and 1 doc (`agent_guide_songset_constructor.md`). No changes needed to
   `writer.py`, `README.md`, `test_songset_constructor_db.py`,
   `test_songset_constructor_config.py`, `test_songset_constructor_cli.py`.

2. **Part 2 (eval flag):** Edit 4 source files (`config.py`, `cli.py`,
   `graph/nodes.py`, `graph/builder.py`, `graph/state.py`), create 1 new file
   (`artifacts/enrichment_report.py`), update 1 doc (`agent_guide_songset_constructor.md`).

3. **Evaluation workflow:** Run baseline eval (pre-conversion), apply Part 1, run
   post-enhancement eval, compare, review with user.

4. **Part 3 (recommendations):** Do NOT implement. Discuss with user after eval.

5. **Verify:** Run unit tests, grep for Simplified remnants, run eval flag, run
   regular constructor. Push to git. Run `graphify update .`.
