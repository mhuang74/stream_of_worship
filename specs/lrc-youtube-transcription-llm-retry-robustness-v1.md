# LRC Pipeline: Robust LLM Alignment Retry for YouTube Transcription Path

## Context

Log evidence (job_11d44eb841fe, 2026-07-13 11:32):

```
WARNING - LLM retry budget exhausted for LLM correction (qwen3.6-35b)
  (304.8s elapsed, 300.0s budget) — giving up
WARNING - LRC GENERATION: YouTube transcript path FAILED
WARNING - Reason: LLM correction failed after rate-limit retries:
  Error code: 429 - concurrent_budget_exceeded (3/3 slots in use)
WARNING - Falling back to LLM-based ASR...
```

### Root cause

The YouTube transcript path's `_llm_correct` step (the final LLM alignment in
`youtube_transcript.py:728–813`) delegates to the shared `call_llm_with_retry`
utility (`llm_rate_limit.py:468–612`). That utility has two exhaustion
conditions:

1. **Wall-clock budget** — `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS` (default 300s / 5 min)
2. **Attempt count** — `SOW_LLM_RATE_LIMIT_MAX_RETRIES` (default 8)

The failure above triggered **condition 1** at 304.8s — only ~5s past budget.
The provider (OpenRouter / `Qwen/Qwen3.6-35B-A3B`) was returning
`concurrent_budget_exceeded` (concurrent drain waiting pattern), meaning
open slots were about to free up. We gave up at the worst possible moment.

### Why raise limits

- The YouTube-transcript path is **fast** when it works (~50–640s vs
  ~900–1300s for ASR fallback + stem separation), and avoids spawning a
  redundant MVSEP stem-separation job.
- The provider's `retryable: true` + `retry_after: 1.0` + `max_delay_s: 30.0`
  guidance all signal that patience will be rewarded.
- The user-confirmed direction:
  - Budget: **20 minutes (1200s)**
  - Max attempts: **16**
  - Scope: **global defaults** (affects `_llm_correct` AND `_llm_align`)
  - `max_delay`: **raise from 30s → 90s**

### Mathematical sanity check

Worst case at new settings (16 attempts, base 2s, cap 90s, no provider override):

```
Attempt:   0    1    2     3      4       5        6         7
Delay:     2    4    8     16     32      64       90        90
Attempt:   8    9   10    11     12      13       14        15
Delay:     90   90   90    90     90      90       90        (no sleep — last)
Sum ≈ 2+4+8+16+32+64+90*9 = ~978s, well under 1200s budget
```

So with the new defaults, the budget acts as a real safety net rather than
triggering prematurely, and 16 attempts give plenty of opportunity for the
provider's concurrent slots to drain.

---

## Change Set

### File 1: `ops/analysis-service/src/sow_analysis/config.py` (lines 96–119)

| Setting | Old | New | Rationale |
|---------|-----|-----|-----------|
| `SOW_LLM_RATE_LIMIT_MAX_RETRIES` | `8` | `16` | Doubles retry opportunities; budget acts as real ceiling |
| `SOW_LLM_RATE_LIMIT_MAX_DELAY` | `30.0` | `90.0` | Tolerates longer concurrent-drain waits; provider `max_delay_s: 30.0` is honored dynamically when present (overrides) but our cap is now higher |
| `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS` | `300` | `1200` | 5 min → 20 min. Was the actual trigger in the log (304.8s) |

Field docstrings updated to reflect the new behavior:
- `MAX_RETRIES`: explain this is global to `_llm_correct` and `_llm_align`.
- `MAX_DELAY`: explain that provider-reported `retry_strategy.max_delay_s`
  still overrides when present, the new value only widens our local cap.
- `TIMEOUT_SECONDS`: clarify this is the wall-clock ceiling for the full
  retry sequence (drains, not just 429s); raised because concurrent-pressure
  windows at OpenRouter can last several minutes.

```python
# LLM Rate-Limit Retry Configuration
SOW_LLM_MAX_CONCURRENT: int = 3

SOW_LLM_RATE_LIMIT_MAX_RETRIES: int = 16
# Max retry attempts on 429 / retryable errors. Applies globally to both the
# YouTube-transcript LLM correction step (_llm_correct) and the ASR-fallback
# LLM alignment step (_llm_align). The wall-clock SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS
# is the primary ceiling; this count is a secondary guard.

SOW_LLM_RATE_LIMIT_BASE_DELAY: float = 2.0

SOW_LLM_RATE_LIMIT_MAX_DELAY: float = 90.0
# Cap on single backoff delay. Provider-reported retry_strategy.max_delay_s
# from the OpenRouter error body overrides this dynamically (e.g. provider says
# 30s), but our local cap is now 90s so we can wait longer when the provider's
# guidance permits or is absent.

SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS: int = 1200
# Total wall-clock budget for the retry sequence (20 minutes). Raised from 5 min
# because OpenRouter concurrent_budget_exceeded (3/3 slots) windows frequently
# outlast 5 min; the YouTube transcript path is much faster than ASR fallback
# (~50-640s vs ~900-1300s), so patience here avoids an expensive fallback.

SOW_LLM_MIN_INTERVAL_SECONDS: float = 2.0
```

### File 2: `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py`

Two documentation-only clarifications — no functional changes (the retry loop
at lines 468–612 already honors `MAX_RETRIES`, `MAX_DELAY`, and
`TIMEOUT_SECONDS`).

- Line ~502–505: expand comment block above `max_attempts = ...` / `total_timeout = ...` indicating the values are now global defaults covering both LLM-using paths and that provider-reported `max_delay_s` still overrides locally.
- Line ~534 (`remaining_budget = total_timeout - elapsed`): retain `logger.warning` phrasing — make it slightly more actionable by referencing that the budget is env-overridable.

These are minor; the loop logic itself does not need to change because it
already:
- Releases the semaphore before backoff sleep (line 595–596) → other jobs
  can use the slot during long waits.
- Honors `retry_after` from the provider JSON body via `_extract_retry_after`
  (line ~567–574).
- Honors `retry_strategy.backoff_base` / `retry_strategy.max_delay_s` via
  `_extract_backoff_config` (line ~575–582) — so `max_delay_s: 30.0` from
  the provider **will still cap per-attempt sleep at 30s** even with our
  new 90s local cap. This is fine: 30s × 15 attempts = 450s, still under
  the 1200s budget. The 90s cap only matters when the provider omits
  retry guidance.

### File 3: `ops/analysis-service/DEPLOYMENT.md`

If this file contains an env-var defaults table mentioning
`SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300` (or similar), update the documented
defaults to match. Same for any `docker-compose.yml` hardcoded overrides.

(Very likely these are all sourced from config.py defaults via environment —
confirm during implementation.)

### File 4: `ops/analysis-service/tests/test_llm_rate_limit.py`

Add or update four test cases (or extrapolate existing patterns):

1. **`test_retry_budget_1200s_sustained`** — multiple 429s with short
   `retry_after`, verify retry continues past 305s and propagates after
   1200s. Use a mock that records monotonic elapsed.
2. **`test_sixteen_attempts_before_giveup`** — verify the loop honors
   `max_attempts = 16` when each attempt fails immediately with a
   retryable 429 (use monkeypatched `time.monotonic` to falsify the budget).
3. **`test_max_delay_90s_when_provider_omits_guidance`** — single 429 with
   no `retry_strategy` in the error body, assert sleep ≤ 90s + 25% jitter
   on the longest attempt.
4. **`test_provider_max_delay_30s_still_honored`** — provider JSON includes
   `retry_strategy.max_delay_s: 30.0`; assert `delay ≤ 30s` even though
   our local cap is now 90s.

(If the existing test file uses a different naming pattern, mirror it.)

---

## What Does NOT Change

- `SOW_LLM_MAX_CONCURRENT = 3` — matches provider's 3 concurrent slots; raising below it would only cause self-inflicted 429s.
- `SOW_LLM_MIN_INTERVAL_SECONDS = 2.0` — pacing for in-flight accounting lag.
- `SOW_LLM_RATE_LIMIT_BASE_DELAY = 2.0` — start of exponential curve; provider-override when present.
- OpenAI SDK `max_retries=0` in `_llm_correct` (youtube_transcript.py:784) and `_llm_align` (lrc.py:621) — our retry layer remains the single source of truth.
- Circuit breaker, semaphore, provider-guidance extraction (`_extract_retry_after`, `_extract_backoff_config`)
- The outer `for attempt in range(max_retries=3)` retry loop in `_llm_align` (lrc.py:636–652) — orthogonal layer of retries that wraps `call_llm_with_retry` for `JSONDecodeError`/`ValueError`. Raising limits there is out of scope per the user's decision to modify global defaults only.

---

## Sequence-of-Events After Change (expected behavior)

Repeat of the failing scenario with new defaults:

1. `_llm_correct` calls `call_llm_with_retry` with new `max_attempts=16`,
   `max_delay=90s`, `total_timeout=1200s`.
2. Provider returns 429 `concurrent_budget_exceeded (3/3 in use)`,
   `retry_after: 1.0`, `retry_strategy.max_delay_s: 30.0`.
3. We honor provider's `max_delay_s: 30.0` — per-attempt sleeps remain
   ~30s + jitter, not 90s.
4. Loop continues retrying every ~30s for up to 20 minutes, releasing
   the LLM semaphore during each sleep so other LRC jobs can make calls
   in the same window.
5. As existing in-flight requests drain, provider returns 200; retry
   succeeds typically within 1–5 minutes.
6. `_llm_correct` returns successfully → `parse_lrc_response` → LRC file.
7. **ASR fallback no longer triggered**, avoiding the ~600–800s penalty
   plus a wasted MVSEP stem-separation job.

---

## Acceptance Criteria

- `config.py` defaults match: `MAX_RETRIES=16`, `MAX_DELAY=90.0`,
  `TIMEOUT_SECONDS=1200`.
- New tests pass:
  ```bash
  cd ops/analysis-service && uv run --extra dev pytest tests/test_llm_rate_limit.py -v
  ```
- Full suite passes:
  ```bash
  cd ops/analysis-service && uv run --extra dev pytest tests/ -v
  ```
- No code path outside `_llm_correct` / `_llm_align` is affected (both
  consume the shared utility, both benefit from the same ceiling).
- (Optional, post-deploy verification) Next batch run shows the
  `LLM retry budget exhausted` warning absent for jobs that previously
  failed at the 300s mark, with successful YouTube-transcript LRC output.

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| LRC jobs now take up to 20 min to fail if the provider is truly down | Medium | Acceptable — current behavior 5-min fail triggers the slower ASR path (>20 min total), so net latency is still better. |
| Per-attempt cap mismatch (provider 30s vs our 90s) | Low | Existing `_extract_backoff_config` honors provider's `max_delay_s` when present; our 90s only widens the local cap for responses without retry guidance. |
| Test flakiness from time mocking | Low | Use monkeypatched `time.monotonic` consistently; mirror existing test patterns. |
