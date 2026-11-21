# Audio-inpaint POC — songs joined by songset `hbWfCccGxN_JLQGBEqmdu`

Snapshot: 2026-11-21. Sources: `reports/audio_inpaint_poc_impl_summary.md`, the audio-inpaint POC run artifacts under `lab/poc-scripts/output/audio-inpaint/`, and production Postgres rows. The run artifacts are git-ignored; the database values are a snapshot. This note describes the songs and their components, not the inpainting algorithm.

## 1. Songset

| field | value |
|---|---|
| id | `hbWfCccGxN_JLQGBEqmdu` |
| name | Small Group Morning Fortress |
| description | (empty) |
| owner | user id 15 |
| created / updated | 2026-11-12 00:42:30Z / 2026-11-20 07:35:25Z |
| items | 2 (positions 0 and 1) |
| latest render job | `6aEk5qkKeukSiFYfl_Xe6`, completed 2026-11-14 15:09:55Z, 2 songs, 354.663 s, 1080p, audio + video |

The set contains exactly two songs. The POC's **boundary seam is item 0 → item 1**; the same script also builds an **intra seam inside item 0** (verse 1 → chorus 1) as a second variation.

Transition parameters on the boundary (`songset_items` row at position 1):

| parameter | value |
|---|---|
| `gap_beats` | 2 |
| `crossfade_enabled` | false |
| `crossfade_duration_seconds` | null |
| `key_shift_semitones` | 0 |
| `tempo_ratio` | 1.0 |

The renderer therefore does not pitch-shift or tempo-match the two songs; the only written transition is a 2-beat gap at the incoming tempo, with no crossfade.

Caution: every completed render for this songset was created on 2026-11-14 or earlier, while both current `songset_items` rows were last updated on 2026-11-20 (item 0 was created then; item 1 was created 2026-11-12 and updated then). The completed renders predate the current arrangement; use the 2026-11-21 POC runs (not the old render audio) as evidence for this seam.

## 2. Song A — "Here I Bow" (outgoing, position 0)

| field | value |
|---|---|
| song id | `hereibow_a9271bf4` |
| recording hash prefix | `6a410a5c222a` |
| content hash | `6a410a5c222aaa1559dc2b6661bb74478cbcd764217bbac0c764527cffc41cb2` |
| composer / lyricist | Jenn Johnson / Jason Ingram |
| album | After All These Years |
| original filename | `Here I Bow (Official Lyric Video) -  Brian & Jenn Johnson ｜ After All These Years.mp3` |
| visibility / analysis | published / partial |

### Musical facts

| field | value |
|---|---|
| duration | 263.6858 s (4:23.69) |
| tempo | 68.0 BPM (beat = 0.8824 s) |
| key | D major (recording analysis; confidence 0.8247, score margin 0.7935, window agreement 0.9583) |
| loudness | `loudness_db` -12.66; measured integrated -8.9 LUFS for the full recording, -7.6 LUFS for the arranged audio; true peak +0.2 dBTP |
| theme / vocal posture | 敬拜 / To God |
| language | English (vocal) |

`songs.musical_key` is empty; the key and tempo above come from the `recordings` row, which is the authoritative source for the seam.

### Recording section map (`recordings.sections`)

| # | label | start s | end s | length s |
|---|---|---|---|---|
| 0 | verse 1 | 11.85 | 40.55 | 28.70 |
| 1 | chorus | 40.55 | 71.85 | 31.30 |
| 2 | verse 2 | 71.85 | 99.69 | 27.84 |
| 3 | chorus | 99.69 | 130.55 | 30.86 |
| 4 | bridge | 130.55 | 159.68 | 29.13 |
| 5 | bridge | 159.68 | 187.07 | 27.39 |
| 6 | chorus | 187.07 | 217.50 | 30.43 |
| 7 | chorus | 217.50 | 244.04 | 26.54 |

The last 19.65 s of the 263.69 s recording (244.04–263.69) are outside any labeled section.

### What the render actually plays (`songset_items.arrangement_steps`, stored order)

| order | label | section index | recording span s | length s |
|---|---|---|---|---|
| 1 | chorus | 3 | 99.69–130.55 | 30.86 |
| 2 | bridge | 4 | 130.55–159.68 | 29.13 |
| 3 | bridge | 5 | 159.68–187.07 | 27.39 |
| 4 | chorus | 6 | 187.07–217.50 | 30.43 |
| 5 | verse 1 | 0 | 11.85–40.55 | 28.70 |

Total arranged audio: **146.51 s**. The order is not chronological: it starts at 99.69 s, runs forward to 217.50 s, then jumps back to 11.85 s. **The arrangement ends at 40.55 s, exactly the verse-1/chorus boundary; the last 28.70 s the listener hears are verse 1.** The chorus at 40.55–71.85 is not played, but the real audio from 40.55 s onward exists and is available as post-seam head-pad material; the POC uses it when the generated hole has a measured dead head.

### Seam-side character (measured on the arranged audio, 44.1 kHz)

| window | RMS dBFS | spectral centroid |
|---|---|---|
| last 15 s | -18.17 | 1730 Hz |
| last 5 s | -17.63 | 1618 Hz |
| last 2 s | -18.57 | — |
| last 1 s | -24.04 | — |
| last 0.5 s | -25.06 | — |
| last 0.2 s | -25.22 | — |
| last 0.05 s | -25.55 | — |

The tail is a **section-ending decay**: it is loud over the last 5 s but falls to about -25 dBFS in the final 0.2 s. Key estimate by chroma profile: D major over the last 15 s (corr 0.958) and last 5 s (0.802); the final 2 s best matches A major (0.874), the dominant of D, consistent with an open cadence into the cut-off chorus. The 2 s estimate is short-window and should be treated as a hint, not a fact.

The full recording peaks at +0.2 dBTP, so A is a hot/limited master; avoid adding gain.

### `song_components` rows

| component | role | span s | energy dB | bpm | key | theme | posture |
|---|---|---|---|---|---|---|---|
| verse1 | loop_target | 11.01–38.82 | -20.53 | 86.13281 | D | 十字架 | About God |
| chorus1 | entry | 38.82–73.59 | -16.59 | 84.72080 | D | 敬拜 | To God |
| chorus2 | none | 186.63–217.94 | — | — | — | — | — |
| chorus3 | exit | 217.94–244.04 | -15.26 | 83.35433 | D | 敬拜 | To God |

Cautions: the component boundaries are shifted relative to `recordings.sections` (verse1 11.01–38.82 vs 11.85–40.55; chorus1 38.82–73.59 vs 40.55–71.85). The component BPMs are analysis artifacts and are **not** the recording tempo: A's recording is 68 BPM, yet its components report 83.35–86.13 BPM. `chorus2` has no energy/BPM/key/theme values. The seam's reported exit component is `chorus1` because the component window starts at 38.82 s and contains 40.55 s; the section map says the actual playback ends at the start of the chorus.

## 3. Song B — "Here for You" (incoming, position 1)

| field | value |
|---|---|
| song id | `hereforyou_62e79ae9` |
| recording hash prefix | `20379ea26256` |
| content hash | `20379ea2625671e8b9294e3f4a827645947c4f5ee16d1ec9944e6cf01cdec135` |
| composer / lyricist | Jeremy Riddle / Jeremy Riddle |
| album | Here for You |
| original filename | `Here for You.mp3` |
| visibility / analysis | published / partial |

### Musical facts

| field | value |
|---|---|
| duration | 579.5236 s (9:39.52) |
| tempo | 86.13281 BPM (beat = 0.6966 s) |
| key | C major (confidence 0.7213, score margin 0.6713, window agreement 0.9111) |
| loudness | `loudness_db` -19.85; measured integrated -16.2 LUFS for the full recording, -15.8 LUFS for the arranged audio; true peak -2.7 dBTP |
| theme / vocal posture | 敬拜 / To God |
| language | English (vocal) |

### Recording section map (`recordings.sections`)

| # | label | start s | end s | length s |
|---|---|---|---|---|
| 0 | chorus | 0.70 | 22.04 | 21.34 |
| 1 | chorus | 22.04 | 69.33 | 47.29 |
| 2 | verse 1 | 69.33 | 92.61 | 23.28 |
| 3 | verse 2 | 92.61 | 114.49 | 21.88 |
| 4 | chorus | 114.49 | 139.92 | 25.43 |
| 5 | verse 3 | 139.92 | 185.11 | 45.19 |
| 6 | verse 4 | 185.11 | 208.38 | 23.27 |
| 7 | chorus | 208.38 | 231.65 | 23.27 |
| 8 | chorus | 231.65 | 273.31 | 41.66 |
| 9 | bridge 1 | 273.31 | 296.24 | 22.93 |
| 10 | bridge 2 | 296.24 | 340.36 | 44.12 |
| 11 | bridge 1 | 340.36 | 468.48 | 128.12 |
| 12 | verse 4 | 531.64 | 555.62 | 23.98 |

The section map leaves **468.48–531.64 (63.16 s) unlabeled**, and the last 23.90 s (555.62–579.52) is outside any labeled section.

### What the render actually plays

| order | label | section index | recording span s | length s |
|---|---|---|---|---|
| 1 | chorus | 0 | 0.70–22.04 | 21.34 |
| 2 | verse 1 | 2 | 69.33–92.61 | 23.28 |
| 3 | chorus | 8 | 231.65–273.31 | 41.66 |
| 4 | bridge 1 | 9 | 273.31–296.24 | 22.93 |
| 5 | bridge 2 | 10 | 296.24–340.36 | 44.12 |
| 6 | verse 4 | 12 | 531.64–555.62 | 23.98 |

Total arranged audio: **177.31 s**. The arrangement starts at **0.70 s in the opening chorus** (21.34 s) and ends at 555.62 s in verse 4; it is also non-chronological and skips most of the 579.52 s recording.

### Seam-side character (measured on the arranged audio, 44.1 kHz)

| window | RMS dBFS | spectral centroid |
|---|---|---|
| first 15 s | -25.25 | 3040 Hz |
| first 5 s | -25.48 | 3092 Hz |
| first 2 s | -25.44 | — |
| first 1 s | -24.39 | — |
| first 0.5 s | -23.00 | — |
| first 0.2 s | -23.73 | — |
| first 0.05 s | -25.73 | — |

The opening chorus is **quiet and bright**: roughly 7.5–8 dB below A's last 5 s, but only about 1.5 dB below A's final 200 ms. Key estimate: C major over the first 15 s (corr 0.890), first 5 s (0.740), first 2 s (0.844).

### `song_components` rows

| component | role | span s | energy dB | bpm | key | theme | posture |
|---|---|---|---|---|---|---|---|
| verse1 | loop_target | 91.20–113.78 | -19.29 | 86.13281 | C | 敬拜 | To God |
| chorus1 | exit | 113.78–139.20 | -18.04 | 84.72080 | C | 敬拜 | To God |
| chorus1 | entry | 113.78–139.20 | -18.04 | 84.72080 | C | 敬拜 | To God |

Cautions: the component spans are shifted relative to `recordings.sections` (component verse1 91.20–113.78 corresponds to section verse 2 92.61–114.49; component chorus1 113.78–139.20 corresponds to section chorus 114.49–139.92). The `entry` role points at 113.78 s, **not** at the render's actual entry at 0.70 s. The component energy `-18.04 dB` describes that shifted chorus, not the quiet opening chorus (-25.5 dBFS measured). The component BPM 84.72080 is again not the recording's 86.13281 BPM.

## 4. The seam in one table

| property | outgoing A ("Here I Bow") | incoming B ("Here for You") | consequence |
|---|---|---|---|
| recording tempo | 68.0 BPM | 86.13281 BPM | ratio 1.2667 (+26.7%); beats 0.8824 s vs 0.6966 s |
| recording key | D major | C major | down a whole tone (2 semitones); D major and C major share D E G A B (5 of 7 pitch classes), but A has F#/C# where B has F/C |
| full duration | 263.69 s | 579.52 s | B is 2.2× longer |
| arranged duration | 146.51 s | 177.31 s | B's arrangement is longer even though it uses a smaller fraction of its recording |
| arrangement seam | ends at 40.55 s = verse 1 / chorus boundary | starts at 0.70 s in the opening chorus | not a song ending; A ends at a chorus boundary, B starts on a chorus |
| arranged level | -7.6 LUFS | -15.8 LUFS | ≈8.2 LU apart over the whole arranged sides |
| 4 s probe (POC `a_level_db` / `b_level_db`) | -16.96 dBFS | -25.49 dBFS | ≈8.5 dB apart because A's last 4 s is loud and B's opening chorus is quiet |
| seam-edge level (last/first 200 ms) | -25.22 dBFS | -23.73 dBFS | ≈1.5 dB apart at the actual joint; A's tail is decaying |
| brightness (centroid) | 1618–1730 Hz | 3041–3092 Hz | B is brighter by ~1.3–1.5 kHz |
| written transition | 2 beats at incoming tempo = 1.393 s (2 × 60 / 86.13281 = 1.3932 s, rounded); no crossfade; key shift 0; tempo ratio 1.0 | | the gap is a hard hole, not a crossfade |
| theme / posture | 敬拜 / To God (verse 1: 十字架 / About God) | 敬拜 / To God | both tagged 敬拜 / To God; A's verse 1 is tagged 十字架 / About God |
| vocal content | English vocal | English vocal | generated hole can be instrumental or wordless texture, no words |

The POC's boundary request targets the **incoming** side: `bpm = 86`, `keyscale = "C Major"`, `duration = 38.0 s` for an 8 s bridge (15 s A + 8 s hole + 15 s B; the assembled clip is 37.5 s after the 0.5 s handoff), `repaint_start = 15.0`, `repaint_end = 23.0`. Gap mode uses `duration = 31.392993 s` (15 + 1.393 + 15), `repaint_start = 15.0`, `repaint_end = 16.393`. The model is therefore asked to generate C major at 86 BPM while conditioned on a D-major/68-BPM outgoing side.

### Intra seam inside A (secondary variation)

The POC also builds a seam at A's verse 1 → chorus 1 transition at recording 38.82 s: component energy -20.53 dB → -16.59 dB (+3.94 dB), D major on both sides, 68 BPM on both sides, with 8 s of arranged audio per side. This is not the songset boundary; it is a same-song control variation.

## 5. What a generation agent must respect

1. **Use `recordings.tempo_bpm` and `recordings.musical_key` + `musical_mode`.** Do not use `song_components.bpm`; it is an analysis artifact (83–86 BPM for a 68 BPM recording).
2. **Treat the seam as an arrangement cut, not a recording end.** A's playback stops at 40.55 s, the start of its chorus; the last 28.70 s are verse 1. B's playback starts at 0.70 s, its opening chorus. Do not assume A ends on a tonic or B starts at 0.
3. **Stage levels on the joint, not the whole-side mean.** A's last 200 ms is -25.22 dBFS and B's first 200 ms is -23.73 dBFS (≈1.5 dB). The 4 s probes are ~8.5 dB apart because A's last 4 s is loud and B's opening chorus is quiet. An 8 dB ramp at the cut would overshoot; the POC's `EDGE_GAIN_LIMIT_DB = 18.0` applies to the measured 200 ms edges.
4. **Handle a whole-tone modulation D major → C major**, and a +26.7% tempo change from 68 to 86.13281 BPM. The outgoing tail's last 2 s chroma suggests an A-major (dominant) sonority, but that is a short-window hint.
5. **Land exactly on B's entry.** B's first beat grid starts at 0.70 s; the gap is 2 beats at 86.13281 BPM (1.393 s rounded). A fill that stops early leaves dead air; a fill that rings into B's downbeat is acceptable.
6. **Use the arrangement order, not chronological order.** `songset_items.arrangement_steps` is stored in playback order; sorting by `startSeconds` would move the seam away from 40.55 s / 0.70 s.

## 6. Data-quality cautions

- Both recordings are `analysis_status = 'partial'`; key confidence is 0.82 (A) and 0.72 (B).
- `song_components` is partial and boundary-shifted; `recordings.sections` is the better source for section labels and seam offsets.
- The component BPM values are not the recording tempos. The POC summary documents this: A's components report 84.72/86.13 BPM while the recording is 68 BPM.
- The completed songset renders predate the current arrangement (renders ≤ 2026-11-14; current `songset_items` 2026-11-20). Do not use the old render's seam as evidence.
- A's full master peaks at +0.2 dBTP; B's at -2.7 dBTP. Do not add gain to A.

## 7. Provenance

- Song facts: `songs`, `recordings`, `song_components`, `songset_items`, `songsets`, and `render_jobs` in the production database (query with `psql "$SOW_DATABASE_URL"`).
- Arrangement and seam geometry: `songset_items.arrangement_steps`, `recordings.sections`, `recordings.tempo_bpm`, `recordings.musical_key`, `recordings.musical_mode`.
- POC artifacts: `lab/poc-scripts/output/audio-inpaint/run5-arranged/index.json`, `run6-full-arranged/index.json`, and `context/boundary__a.flac` / `context/boundary__b.flac`. These runs were generated on 2026-11-21 from the current arrangement.
- Measured levels/centroids: `ffmpeg` `ebur128` and `librosa` at the native 44.1 kHz sample rate; the seam-edge values are the POC's `a_edge_db` / `b_edge_db` (last/first 200 ms).
- Implementation summary and run record: `reports/audio_inpaint_poc_impl_summary.md` and `lab/poc-scripts/AUDIO_INPAINT_RUNS.md` (branch `audio_imprint_poc`).
- The run artifacts are git-ignored; a clone can reproduce the song facts from the database but not the local audio without re-running the POC.
