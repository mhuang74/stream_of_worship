# POC: Chinese Worship Lyrics via Qwen3-ASR-Flash + Canonical-Line Snap

## Context

**Problem.** Producing accurate, complete LRC files (every word actually sung, with phrase timestamps) for ~685 Chinese worship songs. Today's two production paths — YouTube-transcript-with-LLM-correction, and faster-whisper-large-v3-with-lyrics-prompt-then-LLM-alignment — both fail on the same hard cases: Chinese character accuracy on sung audio, and handling performed repeats that the canonical sop.org lyrics don't mark. Taption.com reportedly nails transcription (timestamps are "close enough").

**Hypothesis.** Taption's edge on Chinese songs is almost certainly a CJK-tuned cloud ASR with **context biasing**. The 2026 SOTA for this is **Qwen3-ASR-Flash** (Alibaba DashScope):

- Best published benchmarks on Chinese singing voice (M4Singer, MIR-1k-vocal, Popcs) and full-song-with-BGM; beats Whisper-large-v3 and FunASR decisively on music-mixed audio.
- Accepts a **system-message context string** (keywords, term lists, or full lyric documents) that biases recognition toward those terms. This is the missing ingredient that should fix Chinese character errors on worship-specific vocabulary.
- Returns word/character-level timestamps via `asr_options={"enable_words": True}`.
- Pricing: ~$0.000035/sec (≈$0.008/song).
- 5 min / 10 MB limit on `qwen3-asr-flash`; `qwen3-asr-flash-filetrans` handles up to 12 h.

**Goal of this POC.** Before touching any production code, build a **self-contained script** that follows the same conventions as the existing Whisper POC, and lets me A/B-test Qwen3-ASR-Flash against current options on real songs from the catalog.

Decision criteria after POC: on a handful of hand-reviewed songs, does Qwen3-ASR-Flash beat the current Whisper+LLM and YouTube paths clearly enough on character accuracy, repeat handling, and timestamp drift to justify a production pipeline refactor?

## Deliverable

One new script: **`poc/gen_lrc_qwen3_asr.py`**.

Follows the conventions of `poc/gen_lrc_whisper.py` exactly:

- Typer CLI, `song_id` as the positional argument (path-or-catalog-ID).
- Reuses `poc.utils.resolve_song_audio_path(song_id, use_vocals=...)` for audio + lyrics lookup.
- Reuses `poc.utils.format_timestamp` for LRC timestamps.
- Output: LRC to stdout by default, or to `--output` path.
- Shell-friendly progress logs to stderr via `typer.echo(..., err=True)`.

### CLI

```
uv run --extra poc python poc/gen_lrc_qwen3_asr.py <song_id> \
  [--use-vocals/--no-use-vocals]   # default True, same default as gen_lrc_whisper.py
  [--output <path>] [-o <path>]
  [--model qwen3-asr-flash]        # or qwen3-asr-flash-filetrans for >5 min
  [--region intl|cn|us]            # default intl
  [--snap/--no-snap]               # default on — canonical-line fuzzy snap
  [--snap-threshold 0.60]
  [--lyrics-context/--no-lyrics-context]  # default on — biasing
  [--save-raw <dir>]               # optional: write asr_raw.json + diagnostic.md
  [--start <s>] [--end <s>]        # optional segment-only mode (matches whisper POC)
```

Notes:
- `--use-vocals` behaves **identically** to `gen_lrc_whisper.py`: `resolve_song_audio_path` returns the vocals-stem path when it's already cached locally, else falls back to the mix. User runs vocal separation out-of-band (e.g. via `poc/gen_clean_vocal_stem.py`); the POC does not invoke it.
- Passing a raw audio file path as `song_id` also works (the resolver handles that); `lyrics` will be `None` in that case, and the user should supply `--lyrics-file` in a follow-up version. **For the first cut, require that the song be resolvable via the catalog** so lyrics are available for biasing and snap — matches how the Whisper POC is used day-to-day.

### Pipeline inside the script

```
[1] Resolve inputs
    audio_path, lyrics = resolve_song_audio_path(song_id, use_vocals=use_vocals)
    if lyrics is None:
        typer.echo("No lyrics from catalog; cannot run biasing/snap.", err=True); raise

    lyrics_text = "\n".join(lyrics)

[2] Build context (if --lyrics-context)
    context = (
      "This is a Chinese Christian worship song. "
      "Use the following canonical lyrics as term/phrase reference for recognition. "
      "The performance may repeat verses and choruses; transcribe what is actually sung.\n\n"
      + lyrics_text
    )
    # Truncate to ~10k chars if needed; log if truncated.

[3] Call Qwen3-ASR-Flash
    import dashscope
    dashscope.base_http_api_url = REGION_URL[region]   # intl/cn/us
    messages = [
        {"role": "system", "content": [{"text": context}]},
        {"role": "user",   "content": [{"audio": f"file://{audio_path.resolve()}"}]},
    ]
    resp = dashscope.MultiModalConversation.call(
        api_key=os.environ["DASHSCOPE_API_KEY"],
        model=model,
        messages=messages,
        result_format="message",
        asr_options={"enable_itn": False, "enable_words": True, "language": "zh"},
    )
    If --save-raw: dump response to <save_raw>/asr_raw.json for inspection.

[4] Extract segments → list of (start, end, text)
    Response shape isn't fully documented for flash; handle both plausible schemas
    (top-level sentences[] vs content[].text+timestamps). Save raw first so surprises
    are visible before parsing.

[5] If --no-snap: emit LRC directly from segments and exit.

[6] Canonical-line snap (default)
    from rapidfuzz import fuzz
    canonical_lines = [l for l in lyrics if l.strip()]
    for start, _end, asr_text in segments:
        scored = [(line, fuzz.token_set_ratio(asr_text, line) / 100.0)
                  for line in canonical_lines]
        best_line, best_score = max(scored, key=lambda x: x[1])
        out_text = best_line if best_score >= snap_threshold else asr_text
        final.append((start, out_text, asr_text, best_line, best_score,
                      best_score >= snap_threshold))
    Preserves order and repeats (same canonical line can appear many times).

[7] Emit LRC
    Sort by start. Print `[mm:ss.xx] text` lines. Write to --output or stdout.

[8] If --save-raw: write diagnostic.md
    Side-by-side table: start | asr_text | matched_canonical | score | replaced?
    Plus summary counters (replaced, kept, total, avg_score, segments/sec).
```

### Dependencies

Add to `pyproject.toml`:

- `dashscope` — Qwen3-ASR Python SDK (Alibaba official).
- `rapidfuzz` — fuzzy matching. Not currently present (confirmed via grep); add it.

Put these in the existing `poc` extra next to `faster-whisper` etc. No other package changes.

Env var: `DASHSCOPE_API_KEY` — required at runtime. Script fails fast with a clear error if missing.

## Key reuse

- `poc/utils.py::resolve_song_audio_path` — identical call shape to Whisper POC.
- `poc/utils.py::format_timestamp` — LRC timestamp formatter.
- `poc/utils.py::extract_audio_segment` — used if `--start/--end` provided (optional).
- Vocal stem production: out-of-band via `poc/gen_clean_vocal_stem.py` (user runs manually). This POC just consumes whatever `resolve_song_audio_path(..., use_vocals=True)` returns — which today loads the vocals stem from the local cache when present.

## Risks & things to validate during POC

1. **Does context biasing actually help?** Run each test song twice: `--lyrics-context` and `--no-lyrics-context`. Compare against the hand-reviewed LRC. If biasing doesn't improve character accuracy materially, the entire hypothesis is wrong.
2. **DashScope response schema for qwen3-asr-flash** isn't fully documented. Always save `asr_raw.json` on first runs to adapt the segment parser.
3. **Long songs (>5 min).** `qwen3-asr-flash` rejects >5 min/>10 MB; `qwen3-asr-flash-filetrans` is async (returns a result URL, must poll). POC supports `--model qwen3-asr-flash-filetrans` so I can test both, but flash is the default.
4. **Snap threshold.** Start at 0.60. Too high → misses correct matches on short lines. Too low → snaps English/ad-lib onto wrong canonical lines.
5. **Vocals-stem vs mix.** Qwen3-ASR is trained on music-mixed audio and may underperform on clean stems (overfit to mix statistics). With `--use-vocals/--no-use-vocals` I can A/B without changing code.
6. **Response segment granularity.** If Qwen3-ASR returns one long segment per song, I'll need to split by punctuation/pauses before the snap step. Decide after seeing real output.

## Verification

For each of 3–5 test songs (pick ones already hand-reviewed, covering short/long and vocal-heavy/band-heavy):

1. `--no-snap --save-raw` → inspect `asr_raw.lrc` vs hand-reviewed. Measure eyeballed character error rate and timestamp drift.
2. Default (snap on) → compare against hand-reviewed.
3. A/B: `--use-vocals` vs `--no-use-vocals`.
4. A/B: `--lyrics-context` vs `--no-lyrics-context`.
5. Eyeball `diagnostic.md` to sanity-check snap decisions.
6. Open the resulting `.lrc` in the existing lyrics preview screen (`src/stream_of_worship/app/screens/lyrics_preview.py`) loaded against the same MP3 to see timing in-app.

Acceptance to move on to a production pipeline refactor: on the test set, the Qwen3-ASR output (with snap + biasing) beats current production LRC clearly on both character accuracy and repeat handling — enough that the `upload-lrc` manual fallback would rarely be needed.

## Out of scope

- No changes to `services/analysis/`, `src/stream_of_worship/`, or admin tool.
- No vocal-separation invocation inside the POC (run `poc/gen_clean_vocal_stem.py` separately if needed).
- No batch/backfill script — one song per invocation.
- No automated CER/WER/MAE computation. Eyeballing `diagnostic.md` is fine at POC stage.
