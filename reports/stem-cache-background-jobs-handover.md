# Handover: SOW stem-cache background jobs (issue #247)

**Audience:** an agent (or human) resuming work on this host after a reboot or
session loss. Everything here was verified live on 2026-10-10.

## What runs, and why

Two long-running jobs fill the LRC-review stem cache
(`lab/poc-scripts/eval/lrc_truth/stem_cache/manifest.json`):

1. **MVSEP drain** — quota-bound cloud separation (~25 songs/day; MVSEP free
   tier = 50 creates/24h rolling window, 2 creates per song). Runs only when a
   watcher sees ≥2 free slots.
2. **Local producer** — quota-free CPU separation on this host (~4.8 h/song,
   two-stage audio-separator). Runs on a disjoint tail slice.

Both write into one cache: `~/.cache/stream-of-worship/<hash_prefix>/stems/clean_vocals.flac`.
The **manifest is the single source of truth** and is committed to the repo;
exactly one writer (the drain side) owns it. The producer writes stems + claim
files only; `--record-claims` or the drain's own step-1 disk check folds them
in at zero quota.

## Current state (2026-10-10 ~07:30 UTC)

- Manifest: **171 terminal** (cached/fallback), **274 failed** (MVSEP
  quota-400s, retried automatically), review queue = 391 songs.
- Producer: `--produce-only --produce-slice tail:30`, on song 1–3 of 30
  (last logged: `rong_yao_rong_yao_rong_yao_088f5c99`), ~4.8 h/song → ~6 days.
- Watcher v5: 22h+ uptime, probing every 15 min.

## What dies on reboot

| Job | Survives session exit? | Survives reboot? |
|---|---|---|
| Producer (`--produce-only`) | Yes (PPID 1, setsid) | **No** |
| Watcher `/tmp/sow_quota_watcher5.sh` | Yes | **No** — and `/tmp` is wiped |

No systemd unit, no cron entry exists. After a reboot **nothing restarts**;
you must relaunch both (below).

## Restart procedure after reboot

From the repo root `/home/mhuang/Development/sow_let_ai_design_lrc_automation`.

**0. Preconditions**

```bash
# env
MVSEP_API_KEY must be set in the environment (drain only; the producer
does not need it). The watcher reads it inside its history-API probe.
# models (already present, ~1GB audio-separator cache)
ls ~/.cache/audio-separator/   # expect UVR-De-Echo-Normal.pth, model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt
# disk
df -h ~   # needs a few GB free per song (FLAC stems)
```

**1. Relaunch the local producer** (quota-free; safe to start immediately):

```bash
cd /home/mhuang/Development/sow_let_ai_design_lrc_automation
nohup setsid env AUDIO_SEPARATOR_MODEL_DIR="$HOME/.cache/audio-separator" \
  lab/poc-scripts/.venv/bin/python lab/poc-scripts/run_stem_cache.py \
  --set review_queue --produce-only --produce-slice tail:30 \
  > /tmp/sow_producer.log 2>&1 &
```

- It holds only `serial.produce.lock` (repo:
  `lab/poc-scripts/eval/lrc_truth/stem_cache/serial.produce.lock`), so it
  cannot block the drain.
- It is **resumable**: already-claimed/recorded songs are skipped. The slice
  recomputes from the current manifest each start, so after days of drain
  progress the tail slice may contain different songs — that is fine and
  correct (it always picks the *currently* unclaimed tail).
- Verify after ~1 min: `tail /tmp/sow_producer.log` shows
  `separating <song> via local` or `producing <song> (local)`.

**2. Relaunch the drain watcher** (drives all MVSEP work):

```bash
nohup setsid bash lab/poc-scripts/scripts/quota_watcher.sh > /tmp/sow_watcher5.log 2>&1 &
# (if /tmp/sow_quota_watcher5.sh still exists, either path works)
```

⚠️ `/tmp/sow_quota_watcher5.sh` itself was wiped by the reboot. Recreate it
from the copy checked into the repo (see "Recovering the watcher" below), then
run it from the repo path.

**3. Verify both are healthy:**

```bash
pgrep -fa "run_stem_cache|quota_watcher"
# lock state: producer HELD, drain FREE (or HELD only during a drain pass)
python3 - <<'EOF'
import fcntl
from pathlib import Path
for name in ("serial.lock", "serial.produce.lock"):
    p = Path("lab/poc-scripts/eval/lrc_truth/stem_cache") / name
    h = open(p, "w")
    try:
        fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB); print(name, "FREE")
    except OSError: print(name, "HELD")
    finally: h.close()
EOF
```

## Recovering the watcher script

The watcher is uncommitted scaffolding (`/tmp/sow_quota_watcher5.sh`). Its
full logic: every 15 min, count MVSEP creates in the rolling 24h window via
`GET https://mvsep.com/api/app/separation_history?api_token=...&start=&limit=100`
(paginated, ≤2000 rows; any API error → fall back to one real probe attempt);
when ≥2 slots free, run one drain pass
`lab/poc-scripts/.venv/bin/python lab/poc-scripts/run_stem_cache.py --set review_queue`
(`timeout 7200`), else sleep. `count_free = max(0, 50 - creates_in_window)`.

If it is lost, either re-transcribe it from that description or (preferred)
commit it into the repo under `lab/poc-scripts/scripts/` with a systemd unit —
see "Recommended hardening".

## Recommended hardening (do this once)

1. **Commit the watcher** into the repo (`lab/poc-scripts/scripts/
   quota_watcher.sh`) so `/tmp` volatility can't lose it.
2. **Add a systemd user unit** (`~/.config/systemd/user/`) for both jobs
   (`Restart=on-failure`) instead of nohup; `loginctl enable-linger` so they
   run without an active login session.
3. Optionally extend the watcher loop: when the producer is not running and
   the tail slice still has unclaimed songs, relaunch step 1. This makes the
   pipeline fully self-driving for the ~9 remaining drain days.

## Common failure modes

| Symptom | Cause / fix |
|---|---|
| `StemCacheError: another stem-cache run is already running (lock ... held)` | Same-lock contention. Producer uses `serial.produce.lock`, drain `serial.lock`. A second producer is an error; a second drain should wait. |
| `MVSEP_API_KEY not set` | Export it before launching the watcher/drain. |
| Producer exits immediately, log shows `model dir ... does not exist` | `AUDIO_SEPARATOR_MODEL_DIR` unset/wrong; point at `~/.cache/audio-separator`. |
| Manifest `failed` entries with `MVSEP submit HTTP 400` | Quota wall; expected. They retry on the next pass. Do **not** hand-edit them out. |
| Manifest grows but producer stems stay unrecorded | Claims are folded by the drain's step-1 disk check on each pass, or run `--record-claims` (needs the drain lock free, ~seconds). |

## Progress checks

```bash
python3 - <<'EOF'
import json
d = json.load(open('lab/poc-scripts/eval/lrc_truth/stem_cache/manifest.json'))['songs']
term = sum(1 for v in d.values() if v['status'] in ('cached','fallback'))
print(f"terminal {term}/391 review-queue songs")
EOF
```

Acceptance for issue #247 criterion 2 = **391/391**. When the watcher prints
"cache complete" (no failed entries remain), the drain is done; at that point
also confirm no `--produce-only` worker is running and every produced stem has
a manifest entry (`--record-claims` run once more is a cheap final sweep).
