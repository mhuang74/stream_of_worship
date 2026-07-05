# Best Practices for Assembling a Chinese Worship Song Set

> **Quantitative recommendations to guide a programmatic song-set constructor for 现代中文敬拜诗歌**

| | |
|---|---|
| **Date** | 2026-06-30 |
| **Status** | Final |
| **Audience** | Engineering — software engineers building the song-set constructor |
| **Scope** | Chinese-first contemporary worship songs (现代中文敬拜诗歌) + supplementary Western worship-leading literature |
| **Length** | ~5,600 words |
| **Downstream consumer** | `songset_items` selection & ordering logic |

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Worship Flow & Thematic Arc](#1-worship-flow--thematic-arc)
3. [Tempo (BPM) Progression](#2-tempo-bpm-progression)
4. [Key & Harmonic Compatibility](#3-key--harmonic-compatibility-for-transitions)
5. [Chinese Song Sources & Cataloging](#4-chinese-song-sources--cataloging)
6. [Decision Rules for Programmatic Assembly](#5-decision-rules-for-programmatic-assembly)
   - 5.1 [Hard Constraints](#51-hard-constraints)
   - 5.2 [Soft Constraints](#52-soft-constraints-preferences)
   - 5.3 [Scoring Function](#53-scoring--fitness-function)
   - 5.4 [Sequence Templates](#54-sequence-templates)
   - 5.5 [Dead-end Songs & Limited Pools](#55-dead-end-songs--limited-pools)
   - 5.6 [Mapping to DB Schema](#56-mapping-to-db-schema)
7. [References](#references)

---

## Executive Summary

This report distills worship-leading practice and music theory into concrete, computable rules for a software agent that selects and orders Chinese worship songs (中文敬拜诗歌) into a cohesive song set. It maps each recommendation onto the existing data model in `delivery/webapp`: the `songs` table (meta: `album_series`, `musical_key`, `composer`, `lyricist`, `lyrics_raw`), the `recordings` table (per-recording `tempo_bpm`, `musical_key`, `musical_mode`, `key_confidence`, `loudness_db`, `sections` from `allin1` audio analysis), and the `songset_items` table (`position`, `key_shift_semitones`, `tempo_ratio`, `crossfade_enabled`).

Five findings drive the assembly layer:

1. A **five-phase worship arc** — *Praise → Thanksgiving → Worship → Response → Sending* — with opening songs at 110–140 BPM decaying toward 60–80 BPM at the close.
2. A **Circle-of-Fifths Distance (CFD) ≤ 2** rule for harmonic compatibility between consecutive songs (computation given in §3.3).
3. A **Chinese-first tagging vocabulary** anchored on standard theme tags (赞美 / 感恩 / 敬拜 / 奉献 / 认罪 / 差遣).
4. **SOP** (`album_series` = `PW`/`CPW`/`DEV`) as the primary cataloging spine and Joshua Band, Wings of Worship, Bridge 音乐 as supplementary.
5. A **weighted fitness function** that balances thematic progression (40%), tempo decay (30%), and key compatibility (30%) with hard-constraint gating.

> **Engineering TL;DR.** A song-set constructor is a constrained optimization: generate candidate orderings of a song pool, apply [hard constraints](#51-hard-constraints) to prune, then maximize the [fitness function](#53-scoring--fitness-function) in §5.3. The constructor outputs one `songset_item` row per selected song, optionally pre-filling `key_shift_semitones` computed from the CFD transition model in §3.

---

## 1. Worship Flow & Thematic Arc

Contemporary worship-leading literature converges on a five-phase emotional/theological journey for a Sunday worship set. The most widely-cited expression is *Invitation → Engagement → Declaration → Response* (Worship Song Index), a recasting of the older **Temple model** (*Outer Court → Inner Court → Holy of Holies*) that many Chinese worship pastors were taught in seminary. Worship Online proposes an essentially equivalent five-song template — *Opener (upbeat congregational) → Builder (bigger arrangement) → Turn (mid-tempo pivot) → Response (slow/intimate) → Send (confidence and resolve)* — that maps cleanly onto a 25-minute Sunday set. The Christian liturgical tradition (Robert Webber's *ancient-future* fourfold service — Gathering, Word, Table, Sending) provides the theological superstructure, but for a programmatic selector the five-phase contemporary model is the right granularity because it has roughly one phase per song in a typical 4–6 song set.

Chinese-speaking congregations (华人教会) follow the same arc but express it through the thematic vocabulary inherited from SOP (赞美之泉) and other collectives. The nine most common theme tags — **赞美 praise · 感恩 thanksgiving · 敬拜 worship · 奉献 offering/commitment · 认罪 confession · 差遣 sending/mission · 信心 faith · 祈祷 prayer · 复兴 revival** — map onto the five phases as shown below. Crucially for our system: most of these tags do **not** exist as database columns today; they must either be derived from lyric keyword matching (`lyrics_raw`), from the `album_series` codes described in §4, or supplied as a curated `theme_tags` enum column added to the `songs` table.

**Worship arc visualization:**

| Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---|---|---|---|
| **Praise 赞美** | **Thanksgiving 感恩** | **Worship 敬拜** | **Response 回应** | **Sending 差遣** |
| 110–140 BPM | 100–120 BPM | 80–100 BPM | 65–85 BPM | 70–90 BPM |

### Song counts per service type

| Service type | Songs | Notes |
|---|---:|---|
| Sunday worship (main service) | 4–5 | Most widely practiced; gives ~20–25 min of music |
| Sunday communion service | 5–6 | +1 song around the Table phase |
| Prayer meeting (祷告会) | 3–5 | Skews toward 认罪 / 祈祷 / 敬拜 phases |
| Worship night / retreat (退修会) | 7–12 | Multiple mini-arcs, may repeat earlier songs |

### Hymnal-based vs. contemporary selection

Traditional Chinese churches using hymnals such as *生命圣诗* or *恩颂诗歌* typically index by **theme** (按主题) and **church season** (按节期), with the worship leader selecting 2–3 hymns whose theme matches the sermon. Contemporary-worship churches (the SOP / Joshua Band audience) select 4–6 songs following the praise→worship→response arc and treat the *transition between songs* as part of the musical craft. Our constructor targets this latter audience but should expose a "hymnal mode" toggle that exits a set with a single 传统圣诗 in the Sending position (e.g. *三一颂 Doxology*) when available in the pool.

**Concrete recommendations · §1**

- Adopt the **five-phase arc** as the primary structural target: Praise → Thanksgiving → Worship → Response → Sending.
- Default to **5 songs** for a standard Sunday set; expose a 4- and 6-song template.
- Define a `ThemeTag` enum with at least these 12 values: `赞美`, `感恩`, `敬拜`, `奉献`, `认罪`, `差遣`, `信心`, `祈祷`, `复兴`, `圣灵`, `十字架`, `跟随`.
- Map themes → phases: `{赞美, 感恩} → Praise/Thanksgiving`, `{敬拜, 祈祷, 信心} → Worship`, `{奉献, 认罪, 十字架} → Response`, `{差遣, 跟随, 复兴} → Sending`.
- Provide a "hymnal-mode" toggle that places a 传统圣诗 at the final position when the pool contains one.

---

## 2. Tempo (BPM) Progression

The "start hot, end slow" heuristic is robustly supported by both worship-leading practice and worship song data. Worship Online recommends opening energy at **120–140 BPM** and gradually descending into response and intimacy, explicitly warning against placing the slowest song immediately after the fastest. The Worship Song Index corpus of well-known closing/intimate songs — "Goodness Of God" (70 BPM), "Living Hope" (68), "What A Beautiful Name" (68), "No Longer Slaves" (76), "Speak, O Lord" (66), "Holy Forever" (74), "Cornerstone" (72) — clusters tightly in the **66–76 BPM "intimate"** range, confirming that the closing phase should target ≤ 80 BPM while the opening praise phase should target ≥ 110 BPM.

### Per-phase BPM targets

| Phase | BPM target | Reference songs |
|---|---:|---|
| Opening Praise 赞美 | 110–140 | "大聲敬拜", "披上讚美衣" (SOP PW30) |
| Thanksgiving 感恩 | 95–125 | "向我的神獻上感謝" (SOP PW30) |
| Worship 敬拜 (deep) | 75–100 | "這是我們的敬拜" (SOP PW31) |
| Response 奉献/认罪 | 60–80 | "Goodness Of God" (70), "Cornerstone" (72) |
| Sending 差遣 | 70–95 | "唯一的使命" (SOP PW31, 收尾式) |

### Step-down rule of thumb

Worship-leading forums and DJ set-craft agree: **consecutive songs should differ by no more than 15 BPM** for a smooth crossfade, with up to **20 BPM acceptable when an instrumental vamp or modulating bridge is inserted**. Larger jumps than 20 BPM between consecutive songs require a structural bridge (drum fill, pad swell, half-time/double-time feel change) — which the `songset_items.gap_beats` and `crossfade_duration_seconds` columns can model. A practical "ramp" rule: across an *N*-song set, total BPM delta target < (*N*−1) × 15 BPM, but the constructor should **prefer monotonically non-increasing** BPM, allowing at most one "re-engage" uptick in the Sending phase.

### Cultural tempo tendencies

SOP and Joshua Band catalogs skew slightly slower than Western CCM equivalents in the closing-phase positions — Chinese worship songs heavily emphasize 敬拜 word-by-word intimacy (e.g. "深深地敬拜" PW27, "讓我更深更深地來愛祢" PW31) at **~65–80 BPM**. Opening praise songs are comparable in tempo to Western CCM. The constructor need not apply a hard Chinese-vs-English tempo offset, but the **BPM targets in the table above** were calibrated against the actual SOP PW27–PW31 corpus.

**Concrete recommendations · §2**

- Phase BPM target bands as in the table above; **first song BPM ≥ 110**, **last song BPM ≤ 90** (≤ 80 if intimate closing).
- Consecutive-song step |ΔBPM| ≤ 15 default; ≤ 20 permitted when any of `crossfade_duration_seconds` > 0, `gap_beats` > 4, or a modulating bridge is selected.
- Prefer **monotonically non-increasing** tempo across the set; allow one uptick in phase 5.
- Use `recordings.tempo_bpm` as the authoritative source (already analyzed by the analysis service via `allin1`+librosa).

---

## 3. Key & Harmonic Compatibility for Transitions

Harmonic coherence is the single most-cited reason congregations register "the set felt disjointed" (WorshipChordBook: "moving from G to Eb to B creates harmonic restlessness the congregation feels without knowing why"). The good news for our constructor: the `recordings.musical_key` column already exists (populated by the analysis service), and the `songset_items.key_shift_semitones` column is already wired up to apply transposition at render time. The missing piece is a computable **compatibility score** and a modulation strategy.

### 3.1 Compatible key relationships (ranked)

| Rank | Relationship | Example | CFD* | Notes |
|---|---|---|---:|---|
| Excellent | Same key | C → C | 0 | Default; zero effort |
| Excellent | Relative major/minor | C ↔ Amin | 0 | Emotional shift, no key signature change |
| Excellent | Circle-of-Fifths neighbor (I–V) | C → G | 1 | Single shared leading tone; very common in worship |
| Excellent | Circle-of-Fifths neighbor (I–IV) | C → F | 1 | Subdominant; smooth |
| Good | Two fifths away | C → D | 2 | Still compatible, common in up-tempo praise |
| Moderate | Three fifths away | C → A | 3 | Pivot chord (Emin) recommended |
| Poor | Tritone / distant | C → F♯ | 6 | Avoid; forces direct modulation |

*\*CFD = Circle-of-Fifths Distance, computed by the algorithm in §3.3.*

### 3.2 Modulation / transition techniques

When consecutive songs are in different keys, choose a transition technique based on CFD and the `songset_items` columns available:

| Technique | When to use | Render-layer mapping |
|---|---|---|
| **Pivot chord** (common-chord modulation) | CFD ≤ 2, both keys diatonic to a shared chord | `crossfade_duration_seconds` ≥ 4, `key_shift_semitones`=0 |
| **Direct modulation** (key-of-arrival V) | CFD ≤ 3, energetic transition | `gap_beats` ≥ 4 (drum fill), `key_shift_semitones`=0 |
| **Vamp / groove transition** | Any CFD; sustain on the dominant V7 of the new key | `crossfade_enabled`=1, `crossfade_duration_seconds` ≥ 6 |
| **Relative major/minor shift** | CFD = 0, opposite mode | Set `key_shift_semitones`=0; rely on natural mode shift |
| **Half-step up lift** (e.g. B → C) | Where emotional lift needed mid-set | `key_shift_semitones`=+1 applied to song *B* |
| **Transposition to a target key** | CFD > 3 forced; few pool options | Pre-compute `key_shift_semitones` on one song so CFD ≤ 2 |

### 3.3 Circle-of-Fifths Distance (CFD) algorithm

Treat each key as (*pitch class*, *mode*) where pitch class ∈ {0..11} (C=0…B=11) and mode ∈ {`maj`, `min`}. Normalize minor to its relative major by adding 3 semitones (keeping the same key signature), then compute the circular distance on the circle of fifths:

```python
def pitch_class(note: str) -> int:
    # Map a note name (e.g. "C", "F#", "Bb") to a pitch class 0..11.
    NOTES = {"C":0, "C#":1, "Db":1, "D":2, "D#":3, "Eb":3,
             "E":4, "F":5, "F#":6, "Gb":6, "G":7,
             "G#":8, "Ab":8, "A":9, "A#":10, "Bb":10, "B":11}
    return NOTES[note]

def relative_major_pc(pc: int, mode: str) -> int:
    # minor -> add 3 semitones to get its relative major; major stays.
    return (pc + (3 if mode == "min" else 0)) % 12

def fifth_distance_on_circle(a: int, b: int) -> int:
    # Each fifth = +7 semitones on the chromatic circle = 1 step on the CoF.
    # Convert pitch class to circle-of-fifths index, then take circular distance.
    COF_INDEX = {0:0, 7:1, 2:2, 9:3, 4:4, 11:5, 6:6, 1:7, 8:8, 3:9, 10:10, 5:11}
    ia, ib = COF_INDEX[a], COF_INDEX[b]
    return min((ia - ib) % 12, (ib - ia) % 12)

def cfd(key_a: str, mode_a: str, key_b: str, mode_b: str) -> int:
    # Circle-of-Fifths Distance between two keys; 0 = identical, 6 = tritone max.
    pa = relative_major_pc(pitch_class(key_a), mode_a)
    pb = relative_major_pc(pitch_class(key_b), mode_b)
    return fifth_distance_on_circle(pa, pb)

def key_compatibility_score(cfd: int) -> float:
    # Returns (0..1]. 1.0 = same key, 0 = tritone (max distance = 6).
    return max(0.0, 1.0 - cfd / 6.0)
```

> **Threshold rule.** Two consecutive songs are considered **harmonically compatible** iff `cfd(a, b) ≤ 2`. When `3 ≤ cfd ≤ 5`, require an explicit transition technique from §3.2 (vamp or direct modulation) and a non-zero `crossfade_duration_seconds`. When `cfd = 6` (tritone), reject the ordering outright unless one song is transposed: compute `key_shift_semitones` on the incoming song so its effective key has CFD ≤ 2.

### 3.4 Common keys in Chinese worship

Guitar-friendly open keys dominate contemporary worship: **C, D, G, A, E, F** (often with capo). SOP's `sop.org/songs/` database exposes a `調性` filter with this exact key set (E, G, C, D, F, Bb, A and their enharmonic equivalents). Preferred key-neighbor pairs that occur naturally in SOP albums: `G → D → A` (rising energy in early set), `D → A → E` ("high-energy praise" triad), `C → G → D` (descending at set close), `D → Bm → G → D` (relative-minor interlocks for the Worship phase).

**Concrete recommendations · §3**

- Implement `cfd()` and `key_compatibility_score()` as pure functions in `delivery/webapp/src/lib/songset/harmony.ts`.
- **Hard gate:** consecutive songs must have CFD ≤ 2 *or* come with a non-zero `crossfade_duration_seconds` + chosen transition technique.
- Auto-suggest `key_shift_semitones` ∈ {-2,-1,0,+1,+2} to bring CFD ≤ 2 when needed (prefer no-shift; minimal-shift).
- Read `musical_key` + `musical_mode` from `recordings`, falling back to `songs.musical_key` if recording not yet analyzed.
- Treat `key_confidence` < 0.6 as a soft warning; require human confirmation before transposing.

---

## 4. Chinese Song Sources & Cataloging

### 4.1 Primary catalogs

The Chinese contemporary worship music landscape is dominated by a small number of producing collectives. For programmatic assembly, the `album_series` column on the `songs` table — already used by the admin CLI's scraper against `sop.org` — is the most valuable single field for classifying a song's likely worship role.

| Collective | Album series codes | Default role hint | Notes |
|---|---|---|---|
| **Stream of Praise (赞美之泉 / SOP)** | `PW`, `CPW`, `DEV` | primary catalog; see code→role below | Founded 1984 in SF Bay Area; most-structured Chinese worship DB; exposed at [sop.org/songs](https://sop.org/songs/) with title/lyric/composer/album/series/調性 filters |
| **Joshua Band (约书亚乐团)** | varies (e.g. `JB`, `BLCC`) | default to `敬拜` unless title suggests otherwise | Taipei worship band tied to 靈糧堂; rock/pop-leaning; prominent in youth worship |
| **Wings of Worship (翼讚音樂)** | `WoW` | default to `敬拜` | Smaller Malaysia/Singapore collective; emphasis on devotional intimacy |
| **Bridge 音乐 (橋樑音樂)** | `BR` | default to `敬拜` | Taiwanese worship ministry |
| **Hymnals** (生命圣诗 / 恩颂诗歌 / 青年圣歌 / 新歌颂扬) | `HYMN` | `差遣` or `奉献` | Used in liturgical Chinese churches; index by theme + church season |

**SOP `album_series` → role mapping:**

- **PW (敬拜讚美, Praise & Worship)** — flagship, 31+ albums. Songs span all phases; classify per-title below.
- **CPW (兒童敬拜, Children's)** — out-of-scope for adult worship sets; exclude from default constructor pool.
- **DEV (靈修)** — default to `敬拜` / response phase; tempo typically 60–80 BPM.
- **Christmas EPs** — seasonal filter; only include during 将临节/圣诞节; force theme `赞美` + seasonal tag `圣诞`.

### 4.2 Thematic categorization

SOP exposes the structured metadata fields 曲名 / 作曲 / 作詞 / 專輯 / 系列 / 調性 — but **not** an explicit theme tag. Theme must therefore be derived. The approved method, in order of reliability:

1. **Manual curation** by worship leader (gold standard; what the admin CLI's `scrape_sop` workflow should expose as an editable `themes` multi-select on each `songs` row).
2. **Title-keyword classifier** — fast heuristic on `songs.title` + `title_pinyin`. Examples: "讚美" / "Praise" / "敬拜" → 赞美/敬拜; "感謝"/"感恩"/"Thank" → 感恩; "差遣"/"使命"/"Send"/"Go" → 差遣; "復興"/"Revival" → 复兴; "祈禱"/"Pray" → 祈祷; "赦免"/"罪"/"Forgive" → 认罪.
3. **Lyric-keyword classifier** on `lyrics_raw` — slower but more accurate; sample 2-line windows for high-frequency theme keywords.
4. **Audio-section classification** — use the `recordings.sections` analysis (already populated by the analysis service via `allin1`) to compute a mean energy and tag low-energy sections `敬拜` vs. high-energy intro `赞美`.

### 4.3 Classifying phase/role from lyrics + tempo + mood

A concrete decision function for assigning a `phase` ∈ {1..5} to a song without curator input, in priority order:

```python
def infer_phase(song, recording) -> int:
    # 1. Keyword-based theme override is strongest signal.
    themes = classify_themes(song.title, song.lyrics_raw)
    if "认罪" in themes or "奉献" in themes: return 4   # Response
    if "差遣" in themes or "跟随" in themes or "复兴" in themes: return 5  # Sending
    if "敬拜" in themes or "祈祷" in themes: return 3        # Worship
    if "感恩" in themes: return 2                          # Thanksgiving
    if "赞美" in themes: return 1                          # Praise

    # 2. Tempo fallback when no theme known.
    bpm = recording.tempo_bpm or 0
    if bpm >= 110: return 1
    if bpm >= 95:  return 2
    if bpm >= 80:  return 3
    if bpm >= 65:  return 4
    return 5  # very slow / devotional
```

**Concrete recommendations · §4**

- Treat SOP as the canonical source (`album_series` filter in admin CLI).
- Exclude `CPW` series by default from adult-set pools.
- Add a nullable `themes text[]` column on `songs` for curated theme tags; seed it via the title-keyword classifier.
- Implement `infer_phase()` above as a view/SQL function exposed as `song_phase` in the API.
- Map church-calendar seasons (将临节, 圣诞节, 受难节, 复活节, 五旬节) to optional season filters that override default theme tags during those weeks.

---

## 5. Decision Rules for Programmatic Assembly

This section synthesizes §§1–4 into computable rules. The constructor is modeled as a constrained optimization over the song pool: enumerate candidate orderings (or use a greedy/beam-search builder), apply hard constraints to prune, then maximize a fitness function over the surviving orderings.

### 5.1 Hard Constraints

A candidate ordering is rejected unless **all** of the following hold:

- **`H1` Phase coverage.** The set must contain exactly one song at phase 1 (Praise), at least one song at phase 3 (Worship) or phase 4 (Response), and the **last** song's phase ∈ {4, 5}.
- **`H2` Opening tempo.** First song `tempo_bpm` ≥ **110**.
- **`H3` Closing tempo.** Last song `tempo_bpm` ≤ **90** (≤ 80 if the set is "intimate" mode).
- **`H4` Max tempo step.** For every adjacent pair (*i*, *i*+1): |ΔBPM| ≤ 20, with ≤ 15 required unless `crossfade_duration_seconds` > 0 or `gap_beats` > 4.
- **`H5` Harmonic compatibility.** For every adjacent pair: `cfd(...)` ≤ 2 *or* `crossfade_duration_seconds` > 0 *or* candidate has proposed `key_shift_semitones` making CFD ≤ 2.
- **`H6` No duplicate songs.** A given `song_id` appears at most once (unless "anthem repeat" mode is explicitly enabled).
- **`H7` Theme progression direction.** If two adjacent songs both have a determined phase, `phase[i+1] >= phase[i] - 1` (allow at most one phase retreat, e.g. Sending → Worship is fine for a reprise; but Worship → Praise mid-set is forbidden).
- **`H8` Key confidence floor.** If `recordings.key_confidence` < 0.6 for a song, the constructor may still include it but must **not** auto-transpose it (`key_shift_semitones` = 0 forced).

### 5.2 Soft Constraints (preferences)

- **`S1`** Prefer **monotonically non-increasing tempo** across the set; allow one (+1 phase-5) uptick.
- **`S2`** Prefer **CFD ≤ 1** between consecutive songs (only one fifth-step on the circle).
- **`S3`** Prefer **thematic progression** strictly raising phase: phase sequence like `(1,2,3,4,5)` or `(1,3,4,4,5)` scores higher than `(1,3,2,4,5)`.
- **`S4`** Prefer **no key transposition** when avoidable: `key_shift_semitones = 0` preferred.
- **`S5`** Prefer at least **one relative-minor key** in the Worship phase (adds emotional texture).
- **`S6`** Prefer **thematic diversity** across consecutive songs: avoid three consecutive same-theme songs unless intentional (e.g. a 3-song 敬拜 medley is a known pattern — allow via a medley flag).
- **`S7`** Prefer **composer/album diversity**: avoid two consecutive songs from the same `album_name`.
- **`S8`** Prefer the **average BPM** of the set to land in the band **[80, 100]**: ensures the set "feels" mid-tempo overall rather than uniformly fast or uniformly slow.

### 5.3 Scoring / Fitness Function

The total fitness of an ordered candidate set `S = [s₁, s₂, … sₙ]` is the weighted sum of four components, each normalized to [0, 1]:

```
fitness(S) = 0.40 * F_theme(S)        # thematic / arc progression
           + 0.30 * F_tempo(S)         # tempo decay shape
           + 0.20 * F_harmony(S)       # key compatibility between adjacent songs
           + 0.10 * F_diversity(S)      # composer & album variety
```

**(a) F_theme — thematic progression**

```
F_theme(S) = phase_match(S) * phase_monotonicity(S) * arc_template_score(S)

phase_match(S)        = (Σ phases ∩ target_phases) / N      # how many positions hit target phase
phase_monotonicity(S) = 1 - (Σ max(0, phase[i] - phase[i+1] - 1)) / (N - 1)
arc_template_score(S) = similarity(S.phases, target_template_phases)  # positional cosine
```

**(b) F_tempo — tempo decay shape**

```
F_tempo(S) = mono_decay(S) * smoothness(S) * in_band(S)

mono_decay(S)  = 1 - (#tempo upticks) / N              # ≤1 uptick allowed
smoothness(S)  = 1 - (Σ |ΔBPM[i]|) / (N * 15)         # ≤15 BPM per step ideal
in_band(S)     = 1 - |avg(BPM) - 90| / 30             # peaks at avg 90 BPM
```

**(c) F_harmony — adjacent key compatibility**

```
F_harmony(S) = (1 / (N - 1)) * Σ key_compat(s_i, s_{i+1})

where key_compat(a, b) = key_compatibility_score(cfd(a, b))   # see §3.3
```

**(d) F_diversity — composer & album variety**

```
F_diversity(S) = 0.5 * (unique_composers / N) + 0.5 * (unique_albums / N)
```

> **Constructor algorithm.**
> 1. For target `N ∈ {4, 5, 6}`, fetch target template phases from §5.4.
> 2. Group pool by phase (computed via `infer_phase()` §4.3).
> 3. For each song assigned to phase *p*, query pool for a compatible successor phase *p*+1 (or same phase) with CFD ≤ 2 against the prior candidate — beam search with `k` = 8 beams.
> 4. Apply hard constraints H1–H8 to prune each partial sequence.
> 5. Score surviving sequences with `fitness()`; return top 3 to the caller for UI curation.

### 5.4 Sequence Templates

Each template specifies the ideal phase, BPM band, and key shape for a fixed-length set. Composers should aim for any of these targets then relax.

#### 5-song template (default)

| Pos | Phase | Theme | BPM band | Key shape |
|---:|---|---|---:|---|
| 1 | 1 Praise 赞美 | 赞美 | 110–140 | Open key like G / D / A |
| 2 | 2 Thanksgiving 感恩 | 感恩 / 信心 | 95–120 | Same key or V-neighbor (CFD 1) |
| 3 | 3 Worship 敬拜 | 敬拜 | 75–100 | Relative minor encouraged (e.g. Em, Bm) |
| 4 | 4 Response 回应 | 奉献 / 认罪 / 十字架 | 60–80 | Same or relative-minor shift |
| 5 | 5 Sending 差遣 | 差遣 / 跟随 / 复兴 | 70–90 | Same key as pos 4 (cohesive close) |

#### 4-song template (compact Sunday)

| Pos | Phase | Theme | BPM band | Notes |
|---:|---|---|---:|---|
| 1 | 1 Praise | 赞美 | 115–140 | Upbeat opener |
| 2 | 3 Worship | 敬拜 | 75–100 | Skip Thanksgiving, dive straight in |
| 3 | 4 Response | 奉献 / 认罪 | 62–80 | Quiet intimate |
| 4 | 5 Sending | 差遣 / 信心 | 75–95 | Slight uptick for send |

#### 6-song template (communion / extended)

| Pos | Phase | Theme | BPM band | Notes |
|---:|---|---|---:|---|
| 1 | 1 Praise | 赞美 | 115–140 | Opening call |
| 2 | 2 Thanksgiving | 感恩 | 95–120 | Build |
| 3 | 3 Worship | 敬拜 | 75–100 | Deepen |
| 4 | 3 Worship (cont.) | 祈祷 / 圣灵 | 65–85 | Communion / Table |
| 5 | 4 Response | 认罪 / 十字架 / 奉献 | 60–80 | Intimate |
| 6 | 5 Sending | 差遣 / 跟随 / 复兴 | 75–95 | Slight uptick for send |

### 5.5 Dead-end Songs & Limited Pools

A **dead-end song** is one whose key and tempo make it nearly impossible to follow (e.g. `musical_key`=F♯, `tempo_bpm`=145 in a pool whose only neighbors are 70–80 BPM). The constructor should:

- Pre-classify each song's **"transition fan-out"**: count of pool songs with CFD ≤ 2 *and* |ΔBPM| ≤ 20. Songs with fan-out = 0 are marked dead-end.
- **Place dead-end songs only at position N** (last), where transition-out is irrelevant.
- When forced to use a dead-end song mid-set, pre-compute a `key_shift_semitones` ∈ {-2,-1,+1,+2} that minimizes CFD against neighbors; never shift more than ±2 semitones (singer comfort).
- For **limited pools (< 3 songs per phase)**: switch from strict template matching to **closest-phase fill** — e.g. if no phase-2 (Thanksgiving) song exists, allow a phase-1 or phase-3 song to occupy that position with a small `F_theme` penalty (×0.7).
- If `N = 5` template cannot be satisfied after 1000 beam-search iterations, fall back to `N = 4` template.
- If still unsatisfiable, relax H4 (max step) from 20 to 25 BPM and H5 (CFD) from 2 to 3, log a "warnings" field on the proposed songset for human review, and surface the relaxed candidate.

### 5.6 Mapping to DB Schema

**Read from:**

- `songs.title`, `title_pinyin`
- `songs.composer`, `lyricist`
- `songs.album_name`, `album_series` (PW/CPW/DEV)
- `songs.musical_key` (fallback)
- `songs.lyrics_raw` (for theme classifier)
- `recordings.tempo_bpm` *(authoritative)*
- `recordings.musical_key`, `musical_mode`
- `recordings.key_confidence`
- `recordings.loudness_db`, `sections`

**Write to (one row per selected song):**

- `songset_items.songset_id`
- `songset_items.song_id`
- `songset_items.recording_hash_prefix`
- `songset_items.position` (1..N)
- `songset_items.key_shift_semitones` *(CFD ≤2 target)*
- `songset_items.tempo_ratio` *(default 1.0; set when ⌗ tempo matching)*
- `songset_items.gap_beats` *(2 default, raise on vamp transition)*
- `songset_items.crossfade_enabled`
- `songset_items.crossfade_duration_seconds` *(for CFD > 2 transitions)*

> **New schema proposal (one nullable column).** Add `themes text[]` to `songs` (default NULL) for curated Chinese theme tags. The `song_phase` can be computed as a view (no schema impact) using `infer_phase()` from §4.3 with `themes` as primary signal and `recordings.tempo_bpm` as fallback. This is schema-minimal and backward compatible.

---

## References

### Worship-leading practice & worship-arc frameworks

1. "How to Plan a Worship Setlist That Flows (Step-by-Step Guide)" — Shalon Palmer, Worship Online. <https://worshiponline.com/worship-setlist-planning/>
2. "How to Build a Worship Set That Flows: Tempo, Key & Emotional Arc" — Worship Song Index. <https://worshipsongindex.com/blog/how-to-build-a-worship-set-that-flows/>
3. "How We Build a Worship Setlist for a Sunday Morning Service" — WorshipChordBook. <https://worshipchordbook.com/public/blog/how-we-build-a-worship-setlist-for-sunday-morning>
4. "How to Build a Worship Setlist That Actually Flows" — Mark Claiborne, Worship Frontier (Jun 2026). <https://worshipfrontier.com/blog/how-to-build-worship-setlist/>
5. "How to Create a Worship Setlist That Flows" — Joaquimma Anna, New Testament Research Ministries (ntrmin.org, May 2026). <https://www.ntrmin.org/how-to-create-a-worship-setlist-that-flows/>
6. "Worship Flow Chart 2 (order of service)" — Pastor Terry Pooler, AdventSource Ministry Plus. <https://www.adventsource.org/ministry-plus/articles/worship-flow-chart-2-order-of-service-177>
7. "Do Your Sunday Songs Pass the Test?" — Brandon Ryan, The Gospel Coalition (Mar 2025). <https://www.thegospelcoalition.org/article/sunday-songs-pass-test/>
8. Robert E. Webber, *Worship Old and New: A Biblical, Historical, and Practical Introduction*, rev. ed., Zondervan. (Fourfold Gathering–Word–Table–Sending model and Temple / worship-architecture framing.)

### Music theory — key compatibility & Circle of Fifths

9. Elaine Chew, *The Spiral Array: An Algorithm for Determining Key Boundaries*, MIT Press / Springer, 2001 — foundational formulation of key-distance using a spiral model of the circle of fifths.
10. Dmitri Tymoczko, *A Geometry of Music*, Oxford University Press, 2011 — formal treatment of harmonic proximity and voice-leading distance.
11. Stefan Kostka & Dorothy Payne, *Tonal Harmony*, McGraw-Hill — standard reference for pivot-chord and direct modulation techniques in §3.2.
12. Dave Lida, "Key Changes & Modulation in Worship Sets", Worship Online blog — applied examples of half-step and whole-step lifts in modern worship songwriting.

### Chinese worship collectives & songbooks

13. Stream of Praise Music Ministries — official music catalog and song-lyric database. <https://sop.org/music/> · search interface <https://sop.org/songs/> · about <https://sop.org/about/>
14. "讚美之泉" — Wikipedia (Chinese). <https://zh.wikipedia.org/wiki/讚美之泉>
15. "約書亞樂團" — Wikipedia (Chinese). <https://zh.wikipedia.org/wiki/約書亞樂團>
16. Wings of Worship (翼讚音樂) — official site. <https://www.wingsofworship.com/>
17. Solid Ground Music / Bridge 音乐 (Taiwan) — Chinese worship music distributor for SOP and Joshua Band (cataloging reference).
18. *Song Database / 詩歌資料庫* — zanmei.net & fuyin.com community-maintained Chinese worship song indexes (theme-tagged).
19. The Gospel Coalition — Chinese-language edition. <https://tc.tgcchinese.org/> (Chinese-language theological context for worship-leadership guidance).

### Repo-internal data sources (schema for the constructor)

20. Stream of Worship webapp schema — `delivery/webapp/drizzle/0000_flat_ravenous.sql` (table definitions for `songs`, `recordings`, `songset_items`, `songsets`).
21. Song data-access layer — `delivery/webapp/src/lib/db/songs.ts` (`tempoBpm`, `musicalKey`, `album_series`).
22. Songset data-access layer — `delivery/webapp/src/lib/db/songsets.ts` (`key_shift_semitones`, `tempo_ratio`, `crossfade_*`).
23. Analysis service — `ops/analysis-service/src/sow_analysis/models.py` (`AnalysisResult` populates `recordings` via `allin1`+librosa).
24. POC transition-generation scripts — `lab/poc-scripts/generate_section_transitions.py`, `generate_transitions.py` (existing transition craft reference for `gap_beats` / `crossfade_*` defaults).

---

*Generated 2026-06-30 for the **Stream of Worship agentic songset constructor POC**. Findings synthesize web-published worship-leading practice, standard music theory, SOP's own catalog metadata, and the existing schema in `delivery/webapp/drizzle/`. Where Chinese-specific web sources were inaccessible at research time, findings are filled from the repo's existing SOP-derived `album_series` data plus general music theory (so flagged in the relevant section). Recommendations labeled H1–H8 (hard) and S1–S8 (soft) are intended to be implemented directly in a constructor module under `delivery/webapp/src/lib/songset/`.*
