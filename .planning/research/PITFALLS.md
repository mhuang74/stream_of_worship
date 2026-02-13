# Pitfalls Research

**Domain:** Forced alignment integration into LRC generation pipeline
**Researched:** 2026-02-13
**Confidence:** MEDIUM

---

## Critical Pitfalls

Mistakes that cause rewrites or major issues.

### Pitfall 1: Silent PyTorch/torchaudio version conflicts during integration

**What goes wrong:**
Qwen3 ForcedAligner (via `qwen-asr`) or WhisperX introduce PyTorch/torchaudio dependencies that conflict with existing `torch>=2.8.0,<2.9.0` and `torchaudio>=2.8.0,<2.9.0` constraints in `pyproject.toml`. This causes:
- Runtime failures with cryptic import errors
- Model loading crashing with CUDA/MPS incompatibility messages
- Works in development (where env was set up manually) but fails in production/CI

**Why it happens:**
- `qwen-asr` is a new package without explicit version pins in pyproject.toml
- PyTorch ecosystem rapid release cycle creates breaking changes frequently
- torchaudio 2.9.0+ removed `AudioMetaData` which pyannote.audio requires (already noted in comments)
- uv's dependency resolution may pick incompatible subtly different versions

**Warning signs:**
- Import errors mentioning `torch` or `torchaudio` symbol not found
- Runtime warnings about device incompatibility (CUDA/MPS mismatch)
- `ModuleNotFoundError: No module named 'torch.nn.modules'` type errors
- Tests pass locally but fail in CI/docker environment

**How to avoid:**
1. **Do NOT add `qwen-asr` without version pin** — use `qwen-asr==X.Y.Z` or specify a tested range
2. **Create separate dependency group**: `transcription_qwen3 = ["qwen-asr==...", "torch==2.8.2", "torchaudio==2.8.2"]`
3. **Add integration test**: Verify model loads and runs with the exact dependency versions
4. **Document torch/torchaudio version matrix** in pyproject.toml comments indicating which versions are tested together
5. **Test in clean environment**: Run `uv run --extra transcription_qwen3 python -c "from qwen_asr import Qwen3ForcedAligner"` as a smoke test

**Phase to address:** Phase 1 (Dependency setup) — before any code integration

---

### Pitfall 2: Timestamp regression making lyrics sync worse than Whisper alone

**What goes wrong:**
Qwen3 forced alignment produces character-level timestamps that, when mapped to lyric lines via `map_segments_to_lines()`, result in:
- Timing that drifts progressively through the song
- Incorrect line assignments due to lyric repetition handling
- Worse sync quality than original Whisper timestamps

**Why it happens:**
- Character-to-line mapping logic (`map_segments_to_lines()`) is brittle and makes assumptions about text matching
- Worship songs have repeated verses/choruses — the aligner may align to the wrong instance of a repeated phrase
- The normalization logic (removing spaces, punctuation) may cause misalignment when lyrics have minor variations
- Edge case in line 142-158 of `gen_lrc_qwen3.py`: when `line_start == -1` (line not found), falls back to interpolation which is inaccurate

**Warning signs:**
- LRC timestamps are consistently early or late by the same increasing amount throughout the song
- Chorus lines misaligned to verse timing or vice versa
- Manual testing reveals lyrics "lip-sync" is visibly worse than baseline
- Metrics show alignment error > 0.5 seconds for multiple lines

**How to avoid:**
1. **Baseline first**: Measure current Whisper+LLM alignment quality on a test set before adding Qwen3
2. **Test on repeated-section-heavy songs** before integration — use songs with 3+ chorus repetitions
3. **Validate mapping logic** with unit tests that verify `map_segments_to_lines()` on known-good inputs
4. **Add regression test**: Store golden LRC output for 2-3 reference songs, fail if Qwen3 output deviates beyond threshold
5. **Implement detection**: Add post-alignment validation that checks for monotonic timestamp progression and flags anomalies

**Phase to address:** Phase 3 (Integration testing) — validate before making default

---

### Pitfall 3: 5-minute audio limit causing silent pipeline degradation

**What goes wrong:**
Qwen3 ForcedAligner has a hard 5-minute (300 second) audio length limit. When songs exceed this:
- Alignment fails silently or raises ValueError that's not caught
- Pipeline falls back to Whisper timestamps, but this happens without clear logging
- Long worship songs (many are 5-7 minutes) get "worse" LRC quality compared to baseline

**Why it happens:**
- Line 223-227 of `gen_lrc_qwen3.py` raises `ValueError` for `audio_duration > 300`
- Pipeline needs graceful degradation, but current implementation may not surface this condition clearly
- 5-minute limit is a model constraint, not a configuration parameter users can tune

**Warning signs:**
- ValueError: "Audio duration (301.2s) exceeds 5 minute limit"
- Sudden drop in LRC quality for specific songs in batch processing
- Logs showing alignment being skipped without clear explanation
- Songs > 5 minutes produce identical output with/without Qwen3 enabled

**How to avoid:**
1. **Early validation**: Check audio duration BEFORE loading Qwen3 model (avoid unnecessary model load)
2. **Clear logging**: Log at WARNING level when skipping Qwen3 due to length limit, include actual duration
3. **Consider chunking strategy**: Plan for segmenting long songs into <5 minute chunks (future enhancement)
4. **Feature flag**: Make max duration configurable in `LrcOptions.qwen3_max_duration` (already planned)
5. **Track metrics**: Log what % of songs hit this limit to understand impact on song library

**Phase to address:** Phase 2 (Implementation) — add duration check and fallback logic

---

### Pitfall 4: Model loading overhead causing 10x slowdown

**What goes wrong:**
Each LRC generation loads Qwen3ForcedAligner from scratch (takes 2-5 seconds). For batch processing:
- Processing 100 songs takes 200-500s just for model loading
- Analysis service becomes latency-bound by model loading time
- Memory leaks if models aren't properly unloaded

**Why it happens:**
- Line 271-278 in `gen_lrc_qwen3.py`: `Qwen3ForcedAligner.from_pretrained()` called per song
- No model caching/reuse between requests
- ~1.2GB model loaded and discarded each invocation
- Service is async/asyncio-based, but model loading is blocking CPU work

**Warning signs:**
- Per-song processing time dominated by "Model loaded" step in logs
- Memory usage grows unbounded with concurrent processing
- Service becomes unresponsive under load due to CPU/memory pressure
- Profiling shows `Qwen3ForcedAligner.from_pretrained()` as hot path

**How to avoid:**
1. **Model singleton**: Implement global model cache loaded on first request and reused
2. **Lazy loading**: Load-on-demand with `lru_cache` or service-level model manager
3. **Warm-start**: Preload model during service startup if using Qwen3
4. **Benchmark**: Measure model load time, include in processing time budget
5. **Consider tradeoff**: For single-song CLI tool, loading per-run is acceptable (skip optimization until service scaling)

**Phase to address:** Phase 4 (Performance) — optimize after functional verification

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Use unversioned `qwen-asr` dependency | Faster setup, easier to get latest fixes | Breaks when package updates with breaking changes | Never — always pin version |
| Use Whisper transcript as forced alignment input text | Avoid writing character-to-lyrics mapping | Assumes Whisper transcription is accurate (often wrong for repeated sections | Only for POC validation |
| Skip audio duration validation check | Faster implementation | Silent failures on long songs, debug nightmare | Never — add validation |
| Use `device="auto"` without logging selected device | Simpler code | Hard to debug CUDA/MPS/CPU issues | acceptable in POC, log in production |
| Ignore model loading overhead | Simpler code, works for single songs | Unscalable for batch/service processing | acceptable for CLI only, refactor for service |

---

## Integration Gotchas

Common mistakes when connecting to external services.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| qwen-asr | Assuming device auto-detection matches user expectation | Log actual device selected, allow explicit override via flag |
| HuggingFace cache | Assuming `~/.cache/huggingface` is sufficient | Respect `HF_HOME` and `XDG_CACHE_HOME` environment variables |
| PyTorch CPU/MPS/CUDA | Using `torch.device("cuda")` unconditionally | Check `torch.cuda.is_available()` and fallback to CPU/MPS |
| pydub audio duration | Trusting `recording.duration_seconds` from DB | Verify actual file duration via `AudioSegment.from_file()` |
| Existing pipeline | Assuming Qwen3 can replace LLM alignment cleanly | Keep LLM as fallback, add feature flag for gradual rollout |

---

## Performance Traps

Patterns that work at small scale but fail as usage grows.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Model reload per request | Processing time dominated by load time | Implement singleton model cache | >10 songs in batch |
| No concurrent processing limit | Service goes OOM under load | Semaphore limit on concurrent alignment jobs | >5 parallel requests |
| GPU memory not freed | Gradual memory leak, eventually crashes | Explicit `torch.cuda.empty_cache()` after each job | After 50+ songs |
| String normalization in hot path | CPU bound on text processing | Cache normalized versions of repeated lyrics | Songs with many repeated lines |
| No caching of alignment results | Re-alignment of same audio file | Cache alignment by audio hash | Multiple passes on same song |

---

## Security Mistakes

Domain-specific security issues beyond general web security.

| Mistake | Risk | Prevention |
|---------|------|------------|
| Loading Qwen3 model from arbitrary URL | Supply chain attack, malicious model injection | Only allow specific model names, validate against allowlist |
| Passing user-provided text directly to model | Prompt injection, model poisoning | Sanitize/limit text length, escape special characters |
| No resource quotas on background jobs | DoS via expensive alignment requests | Add per-user rate limits and job timeout |
| Exposing model paths in error messages | Information disclosure about infrastructure | Generic error messages, debug info only in dev mode |

---

## UX Pitfalls

Common user experience mistakes in this domain.

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| No progress indicator during model loading | User thinks process hung | Show "Loading Qwen3 model..." with spinner |
| Silent fallback to Whisper when Qwen3 fails | User confused why alignment is off | Log clear warning: "Qwen3 skipped (audio too long), using Whisper timestamps" |
| No explanation of 5-minute limit | User frustrated when song rejected | Include in error message with suggestion to use instrumental or split |
| Hidden config for device selection | Users on mac can't use MPS | Default to "auto" but document how to force mps/cuda/cpu |

---

## "Looks Done But Isn't" Checklist

Things that appear complete but are missing critical pieces.

- **Qwen3 alignment works on one song** — Often missing: Handle repeated verses/choruses correctly across diverse song structures
- **Integration passes unit tests** — Often missing: Real-world audio files with background music, variable recording quality
- **CLI works for manual testing** — Often missing: Service-level model caching, concurrent request handling, error recovery
- **Timestamps look reasonable on first verse** — Often missing: Validation that timing holds steady through entire song (no drift)
- **Fallback logic exists** — Often missing: Clear logging so users know WHEN and WHY fallback was triggered

---

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| PyTorch version conflict | HIGH | 1. Roll back to previous working version in pyproject.toml. 2. Run `uv sync --refresh` to clear lockfile. 3. Clean reinstall in venv. |
| Timestamp regression | MEDIUM | 1. Disable Qwen3 via feature flag. 2. Investigate `map_segments_to_lines()` with test songs. 3. Add unit tests for edge cases. |
| Audio length limit hits | LOW | 1. Add logging to identify affected songs. 2. Consider segmenting audio into chunks. 3. Document as known limitation. |
| Model loading perf | MEDIUM | 1. Implement model singleton as hotfix. 2. Monitor memory usage. 3. Add metrics to verify improvement. |
| Device mismatch issues | LOW | 1. Add `--device mps/cuda/cpu` override flag. 2. Document default behavior in help text. |

---

## Pitfall-to-Phase Mapping

How roadmap phases should address these pitfalls.

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| PyTorch version conflicts | Phase 1: Add dependency groups with version pins | Integration test imports and loads model |
| Timestamp regression | Phase 2: Write map_segments_to_lines tests, Phase 3: A/B test | Compare alignment metrics vs baseline |
| Audio length limit | Phase 2: Add validation + logging, Phase 4: Consider chunking | Check logs for skipped alignments |
| Model loading overhead | Phase 4: Benchmark profile, implement caching | Measure time saved per batch |
| Device detection issues | Phase 1: Add device logging and override flags | Test on macOS MPS, Linux CPU, CUDA if available |
| Memory management | Phase 4: Add cleanup, semaphore limits | Monitor memory during batch of 50+ songs |

---

## Sources

- Analysis of existing code: `/home/mhuang/Development/stream_of_worship/poc/gen_lrc_qwen3.py`
- Dependency constraints: `/home/mhuang/Development/stream_of_worship/pyproject.toml`
- Current LRC pipeline: `/home/mhuang/Development/stream_of_worship/services/analysis/src/sow_analysis/workers/lrc.py`
- Implementation plan: `/home/mhuang/Development/stream_of_worship/specs/improve_timecode_accuracy_with_qwen3_aligner.md`

**Note:** Web searches for `qwen-asr` and Qwen3 ForcedAligner documentation did not return authoritative sources. Confidence is MEDIUM based on code analysis and PyTorch ecosystem patterns rather than official documentation.

---

*Pitfalls research for: Forced alignment integration into LRC generation pipeline*
*Researched: 2026-02-13*
