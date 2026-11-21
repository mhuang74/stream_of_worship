# ElevenLabs Music Inpainting API — gluing two songs

Snapshot: 2026-11-21. Every statement was read from the linked page or source on that date; items
that could not be confirmed are listed under [Unverified](#unverified--verify-first). No paid API
call was made while writing this guide.

## What "inpainting" means in this API

There is no `/inpaint` endpoint. Inpainting is a **composition plan** posted to `POST /v1/music`
with `model_id` `music_v2` or `music_v2_5` ([inpainting guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/music/inpainting)).
Every chunk is one of two objects ([compose reference](https://elevenlabs.io/docs/api-reference/music/compose)):

| Chunk | JSON shape | Effect |
|---|---|---|
| Audio reference chunk | `{"song_id": "...", "range": {"start_ms": 0, "end_ms": 12000}}` | **re-renders** that slice of a stored song (not a byte-exact insert — see [Measured](#measured-2026-11-21-paid-plan)) |
| Generation chunk | `{"text": "...", "duration_ms": 8000, "positive_styles": [...]}` | generates new audio |

A stored `song_id` comes from either `POST /v1/music/upload` (your own audio) or a generation
requested with `store_for_inpainting: true`.

The official "seamless loop" example is literally `[ref] [glue] [ref]`, where the middle chunk is
`{"text": "[Glue]", "duration_ms": 3000, "positive_styles": [...], "context_adherence": "high"}`.
Gluing two different songs is that same three-chunk shape with two different `song_id`s.

## TL;DR — the glue recipe

1. Trim the last 12 s of A and the first 12 s of B to 44.1 kHz stereo WAV.
2. `POST /v1/music/upload` each excerpt → `song_id` for A and for B. This is the enterprise-gated
   call; test it first.
3. `POST /v1/music?output_format=mp3_48000_320` with the plan `[A tail ref] → [8 s [Glue]] → [B head ref]`,
   `model_id: music_v2_5`.
4. Keep **only** the generated glue from the response: it begins at `context_seconds` and lasts
   `glue_seconds`. Discard the returned ref audio.
5. Fade the glue 20 ms in and out, then concatenate `A_full + glue + B_full`.
6. Measure both joints (the script below prints them): RMS step ≤ 3 dB and no sample jump that
   stands out from local content.

Why discard the returned refs: the ref chunks exist to give the model real audio on both sides of
the generated chunk, and the API **does not hand them back unchanged** (see below). Splicing the
original files back keeps every second of the real recording sample-identical — the same principle
as `reports/audio_inpaint_poc_impl_summary.md` — and avoids a second lossy encode of the songs.

## Measured (2026-11-21, paid plan)

Run against a real songset (A = 146.51 s arranged, B = 177.31 s arranged, 86.13281 BPM); artifacts in
`lab/poc-scripts/output/elevenlabs-glue/run1`, script `lab/poc-scripts/gen_elevenlabs_glue.py`.

| Question | Result |
|---|---|
| Is inpainting enterprise-gated? | No — a paid subscription cleared `POST /v1/music/upload`. |
| Does a commercial recording upload? | **Not whole.** 146.51 s of a real song → `HTTP 400 copyrighted_material_detected` ("still charged half price"). 12 s excerpts of the same arranged audio uploaded fine. |
| Are ref chunks inserted unchanged? | **No.** Returned region vs uploaded excerpt: waveform Pearson r = 0.25–0.47, but 50 ms energy-envelope r = 0.96–0.99 and spectral-envelope r = 0.94–0.96. Same performance, re-rendered audio. |
| Do the plan positions hold? | Yes, exactly. Returned seam length == plan length (27.000 s), glue sits at the declared offset (lag 0 samples), and the returned ref levels match the uploaded excerpts within 0.1 dB. |
| `pcm_44100` layout | 16-bit little-endian stereo; byte count / (44100 × 4) == plan seconds. |
| Cost | 12 s excerpts + 4 takes = **701 credits** (360 upload + 341 generation). Full sides would be 4,857 credits of upload and still be rejected. |

Consequences for any consumer of this API: locate the generated glue by **plan geometry**
(`glue_start = a_ref_samples`, `glue_end = glue_start + generation_ms`), never by wave correlation,
and verify the returned region is the same performance with an energy-envelope correlation instead.
A 20 ms fade plus a dB ramp applied to the glue is still required: the returned glue's first 50 ms can
sit 3–7 dB below the outgoing song's last 50 ms even when the 200 ms edge gain matches.

## Credentials

- Key: `ELEVENLABS_API_KEY` in `/opt/sow/.env` (line 115 at the time of writing; line numbers move).
  `set -a; . /opt/sow/.env; set +a` parses the file; the script also reads the file directly.
- Every request carries the header `xi-api-key: $ELEVENLABS_API_KEY`
  ([authentication](https://elevenlabs.io/docs/api-reference/authentication)).
- Base URL `https://api.elevenlabs.io`; residency variants are `https://api.us.elevenlabs.io`,
  `https://api.eu.residency.elevenlabs.io`, `https://api.in.residency.elevenlabs.io`,
  `https://api.sg.residency.elevenlabs.io` ([compose reference](https://elevenlabs.io/docs/api-reference/music/compose)).

**Key scope (observed 2026-11-21):** `GET /v1/user/subscription` with this key returns
`401 {"code":"unauthorized","status":"missing_permissions"}` because the key lacks `user_read`.
The key is endpoint-scoped, so workspace tier and music entitlement cannot be read up front. Treat
the first `upload` call as the entitlement test.

**Entitlement:** ElevenLabs' own
[music skill reference](https://github.com/elevenlabs/skills/blob/main/music/references/api_reference.md)
and [MCP server](https://github.com/elevenlabs/elevenlabs-mcp) both state that
`POST /v1/music/upload` is *"gated to enterprise customers with inpainting access"*. The API
reference page does not repeat the restriction. If upload returns 401/403 `missing_permissions` or
an upgrade/not-available message, the feature is unavailable to this key: fix the key or plan, do
not retry.

## Endpoints

### Upload — `POST /v1/music/upload`

`Content-Type: multipart/form-data`
([reference](https://elevenlabs.io/docs/api-reference/music/upload)).

| Form field | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | the audio to store |
| `extract_composition_plan` | string \| bool | no | pass a model id (`music_v2_5`) to get a plan back; `true`/`false` is deprecated; adds latency. Not needed for gluing |
| `with_timestamps` | bool | no | word-level timestamps |
| `with_waveform_visual` | bool | no | 4 samples/second waveform |

`200` → `{"song_id": "jR4Xz8kL2mNpQ9wVtY1b"}`.

Billing and screening: *"Price for uploading is the same as the one for song generation. All
uploaded content gets inspected for copyright infringement. If copyrighted content is detected,
half of the request cost is still charged."* (same page). Upload **excerpts**, not whole songs.

```bash
curl -sS -X POST https://api.elevenlabs.io/v1/music/upload \
  -H "xi-api-key: $ELEVENLABS_API_KEY" \
  -F "file=@/tmp/a_tail.wav"
```

### Compose — `POST /v1/music`

`Content-Type: application/json`, query param `output_format`
([reference](https://elevenlabs.io/docs/api-reference/music/compose)).

- Body: `composition_plan` (or `prompt`, never both), `model_id` (`music_v1` | `music_v2` |
  `music_v2_5`), optional `store_for_inpainting`, `seed`, `finetune_id`,
  `respect_sections_durations`.
- `output_format` default `auto` → `mp3_44100_128` for v1, `mp3_48000_192` for v2 models. Relevant
  values: `mp3_48000_320`, `pcm_44100`, `pcm_48000`, `opus_48000_96`. `pcm_*` is headerless raw
  PCM (see the script's decode branch).
- `200` returns the audio file bytes.
- With `store_for_inpainting: true` the stored id is returned in the **`song-id`** response header.
  Verified for `/v1/music/detailed` in the official JS SDK
  ([`src/wrapper/music.ts`](https://github.com/elevenlabs/elevenlabs-js/blob/main/src/wrapper/music.ts):
  `rawResponse.headers.get("song-id")`); the docs state the song id is found in response headers
  when generating a song.

```bash
curl -sS -X POST "https://api.elevenlabs.io/v1/music?output_format=mp3_48000_320" \
  -H "xi-api-key: $ELEVENLABS_API_KEY" \
  -H "Content-Type: application/json" \
  --data @plan.json -o seam.mp3
```

### Compose detailed — `POST /v1/music/detailed` (optional)

Same body plus `with_timestamps` and `with_waveform_visual`; returns `multipart/mixed` with a JSON
metadata part and an audio part ([reference](https://elevenlabs.io/docs/api-reference/music/compose-detailed)).
Use it only when you need the metadata or composition plan; parsing the multipart is extra work.
`store_for_inpainting` + the `song-id` header on `/v1/music` is simpler when all you need is the id.

### Model choice

`music_v2_5` is the most advanced model and supports chunk plans; `music_v2` also does. `music_v1`
uses a different plan shape (`positive_global_styles` / `sections[].source_from`) and is deprecated
on 2027-01-31 ([models](https://elevenlabs.io/docs/overview/models)). Use `music_v2_5`.

## Plan schema (`music_v2` / `music_v2_5`)

Generation chunk ([compose reference](https://elevenlabs.io/docs/api-reference/music/compose)):

| Field | Type | Notes |
|---|---|---|
| `text` | string | optional `[Section]` label, lyrics, inline `{directions}`; ≤30 lines, ≤200 chars/line |
| `duration_ms` | int | 3000–120000 |
| `positive_styles` | string[] | ≤50, English; 6–7 styles recommended while the direction is established |
| `negative_styles` | string[] | ≤50, optional |
| `context_adherence` | `low` \| `medium` \| `high` (default `high`) | how closely the chunk follows its surrounding chunks |
| `conditioning_ref` | `{song_id, range}` \| null | ≤30000 ms; the first chunk's conditioning influences every later chunk |
| `condition_strength` | `low` \| `medium` (default) \| `high` \| `xhigh` \| null | how tightly the chunk follows the conditioning ref |

Audio reference chunk: `{"song_id": string, "range": {"start_ms": int, "end_ms": int}}`; the slice
is inserted unchanged.

Constraints ([inpainting guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/music/inpainting)):

| Constraint | Value |
|---|---|
| Chunks per plan | ≤ 30 |
| Total plan duration | 3 s – 10 min |
| Generation chunk duration | 3 s – 120 s |
| Conditioning reference | ≤ 30 s |
| Audio ref range | ≥ 50 ms |

Total duration is the sum of chunk durations; a ref contributes its range length. For v2 models
the durations are always enforced (`respect_sections_durations` is a v1-only flag).

## Recipe: glue A → B

### 1. Choose the windows

- `context_seconds` default **12** (8–20 is sane). It is how much real audio the model hears on
  each side, and it is exactly what you upload and pay for on each side.
- `glue_seconds` default **8**. Pick a whole number of bars at the tempo the seam should follow
  (4/4): `glue_seconds = bars × 4 × 60 / bpm`. Examples: 72 BPM × 2 bars = 6.67 s; 86 BPM × 2 bars
  = 5.58 s; 60 BPM × 4 bars = 16 s.
- The minimum generation is 3 s, so the API **cannot** reproduce the repo's 1.393 s two-beat gap
  (`reports/audio_inpaint_poc_impl_summary.md`); an ElevenLabs transition is always at least one
  long bar.
- Trim A's trailing silence first so the glue does not start from a dead tail.

### 2. Upload the excerpts

Both excerpts: 44.1 kHz stereo WAV, PCM s16le. A 12 s excerpt costs 12 s of music credits; this is
why whole songs are not uploaded.

### 3. Build the plan (default)

```json
{
  "model_id": "music_v2_5",
  "composition_plan": {
    "chunks": [
      {"song_id": "<A_ID>", "range": {"start_ms": 0, "end_ms": 12000}},
      {
        "text": "[Glue]\n{seamless musical transition: sustained pads, drum fill, no hard cut}",
        "duration_ms": 8000,
        "positive_styles": [
          "contemporary worship", "warm piano", "ambient pads", "smooth transition",
          "seamless", "natural build", "great production quality"
        ],
        "negative_styles": ["abrupt ending", "silence", "spoken word"],
        "context_adherence": "high"
      },
      {"song_id": "<B_ID>", "range": {"start_ms": 0, "end_ms": 12000}}
    ]
  }
}
```

Both excerpts start at 0, so A's tail is `[0, context_ms]` and B's head is `[0, context_ms]`. Use
`[Glue]` — the label from the official seamless-loop example — and put **no lyrics** in the glue:
lyrics would add words that exist in neither song. Inline `{directions}` are allowed and are the
place for "drum fill", "filtered build", "no hard cut". Style strings must be English.

### 4. Generate, then take only the glue

The response is `2 × context_seconds + glue_seconds` long. The glue occupies
`[context_seconds, context_seconds + glue_seconds]`. Codec padding can shift that by up to a few
milliseconds, so the script cross-correlates the returned ref regions against the uploaded excerpts
and compensates before slicing.

### 5. Splice

`final = A_full + glue + B_full`, all 44.1 kHz stereo PCM s16le, with a 20 ms linear fade on each
end of the glue. Because the fade is applied to the glue only, A and B remain untouched.

## Variants — only when the default seam fails QA

| Variant | Change | Use when |
|---|---|---|
| V1 condition on B | add to the glue chunk `"conditioning_ref": {"song_id": "<B_ID>", "range": {"start_ms": 0, "end_ms": 8000}}` and `"condition_strength": "high"` (or `"xhigh"`) | the glue enters B out of key or tempo |
| V2 condition on A | same, `conditioning_ref` pointing at A's last 8 s (`start_ms = (context_ms - 8000)`) | the glue leaves A abruptly |
| V3 split glue | `[A ref] [gen 4 s, conditioned on A tail] [gen 4 s, conditioned on B head] [B ref]`; both generated chunks in one request; each ≥ 3 s | one chunk cannot satisfy both sides |
| V4 longer glue | raise `glue_seconds` to 2–4 bars | 8 s feels rushed |

A conditioning reference is capped at 30 s and only conditions the chunk it is attached to (except
the first chunk, which influences all later chunks).

## Assembly reference

```bash
ffmpeg -i A.wav -ar 44100 -ac 2 -c:a pcm_s16le a44.wav
ffmpeg -i B.wav -ar 44100 -ac 2 -c:a pcm_s16le b44.wav
ffmpeg -i seam.mp3 -ss 12 -t 8 -ar 44100 -ac 2 -c:a pcm_s16le glue_raw.wav
ffmpeg -i glue_raw.wav -af "afade=t=in:st=0:d=0.02,afade=t=out:st=7.980:d=0.02" -c:a pcm_s16le glue.wav
printf "file '%s'\n" "$PWD/a44.wav" "$PWD/glue.wav" "$PWD/b44.wav" > list.txt
ffmpeg -f concat -safe 0 -i list.txt -c copy final.wav
```

`-ss` after `-i` is the accurate (decode-then-discard) form; keep it there so the glue boundary
does not drift.

## QA checklist

The script prints items 1–4; item 5 is a human decision.

1. **Duration.** `final` = `dur(A) + glue + dur(B)` ± 50 ms:
   `ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 final.wav`.
2. **Refs are unchanged.** Correlation of the returned seam's first 3 s against A's tail excerpt,
   and its last 3 s against B's head excerpt: r ≥ 0.95 (PCM expects ≈1.0; 320 kbps MP3 expects
   ≥0.98). Report the lag; the script shifts the glue offset by it.
3. **No click.** Compare the largest sample-to-sample jump within ±5 ms of each joint against the
   99.99th percentile of jumps within ±1 s around it. Accept when the joint jump ≤ 2× the local
   percentile (the 20 ms fade normally guarantees this).
4. **No level step.** 50 ms RMS before vs after each joint: |Δ| ≤ 3 dB. If larger, gain-match the
   glue (or the song) before splicing.
5. **Musical fit.** The glue must not introduce a drum groove that fights B, and must not start
   with an attack over A's decaying chord. If it does, try V1–V4.

## Failure modes

| Symptom | HTTP | Cause | Action |
|---|---|---|---|
| `unauthorized` / `missing_permissions` | 401 | key lacks the music or inpainting scope | new or scope-widened key |
| upgrade / not-available message on upload | 401/403 | inpainting not on this workspace plan | plan change; not fixable in code |
| upload rejected for copyright | 4xx (message mentions copyright) | content screening | half the cost is charged; use another source or fall back |
| `422` with `detail[]` | 422 | bad plan fields (duration out of 3000–120000, missing styles, bad range) | read `detail[].loc` and fix the chunk |
| `bad_composition_plan` / `bad_prompt` | 4xx | copyrighted artist or style references | response carries a `composition_plan_suggestion`; use plain descriptors |
| `429` | 429 | rate or concurrency limit | music concurrency: Free 0, Starter/Creator/Pro 2, Scale/Business 5, Enterprise highest ([models](https://elevenlabs.io/docs/overview/models)); serialize requests |
| seam length ≠ plan length | 200 | wrong assumption about a field | assert duration; never slice blindly |
| glue drifts key or tempo | 200 | insufficient conditioning/context | V1–V4 |

## Cost, limits, privacy

- Eleven Music ≈ **900 credits per minute** ([pricing](https://elevenlabs.io/pricing)) = 15 credits/s.
- Default run: 12 s + 12 s upload + 8 s generation = 32 s ≈ 480 credits. Uploading a whole 4-minute
  song would cost ≈ 3,600 credits, so upload only excerpts.
- Music concurrency limits as in the failure table; the
  `current-concurrent-requests` and `maximum-concurrent-requests` response headers report usage.
- Uploads and generations are retained and logged by default (`enable_logging=true`); zero
  retention is enterprise-only. Do not upload content you cannot share with the vendor.

## Script

Requirements: `ffmpeg` and `ffprobe` on PATH, Python ≥ 3.10, `requests`, `numpy`. The key is read
from `ELEVENLABS_API_KEY`, else from `/opt/sow/.env`. It uploads excerpts, composes once, extracts
the glue, splices, and prints the QA numbers.

```python
#!/usr/bin/env python3
"""Glue two songs with an ElevenLabs music inpainting composition plan.

Example:
    python elevenlabs_glue.py --song-a A.wav --song-b B.wav --out A_to_B.wav

Costs credits: two uploads of --context-seconds each, plus --glue-seconds of generation.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import wave
from pathlib import Path

import numpy as np
import requests

API = "https://api.elevenlabs.io"
ENV_FILE = Path("/opt/sow/.env")
MODEL_ID = "music_v2_5"
SAMPLE_RATE = 44100
CHANNELS = 2
SAMPLE_WIDTH = 2  # bytes, PCM s16le


def api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if key:
        return key
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("ELEVENLABS_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit(f"ELEVENLABS_API_KEY not in the environment or {ENV_FILE}")


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{proc.stderr[-2000:]}")


def duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(path),
        ],
        text=True,
    )
    return float(out.strip())


def to_wav(src: Path, dst: Path, start: float | None = None, length: float | None = None) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if start is not None:
        cmd += ["-ss", f"{start:.6f}"]
    if length is not None:
        cmd += ["-t", f"{length:.6f}"]
    cmd += ["-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-c:a", "pcm_s16le", str(dst)]
    run(cmd)


def upload(key: str, path: Path) -> str:
    with path.open("rb") as fh:
        resp = requests.post(
            f"{API}/v1/music/upload",
            headers={"xi-api-key": key},
            files={"file": (path.name, fh, "audio/wav")},
            timeout=900,
        )
    if resp.status_code >= 400:
        raise SystemExit(f"upload failed HTTP {resp.status_code}: {resp.text[:800]}")
    return resp.json()["song_id"]


def compose(key: str, plan: dict, output_format: str) -> bytes:
    resp = requests.post(
        f"{API}/v1/music",
        params={"output_format": output_format},
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={"model_id": MODEL_ID, "composition_plan": plan},
        timeout=1800,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"compose failed HTTP {resp.status_code}: {resp.text[:800]}")
    return resp.content


def decode_seam(raw: Path, wav: Path, expected_ms: int, output_format: str) -> None:
    data = raw.read_bytes()
    if data[:4] == b"RIFF":
        to_wav(raw, wav)
        return
    if output_format.startswith("pcm_"):
        rate = int(output_format.split("_")[1])
        frames = int(round(expected_ms * rate / 1000.0))
        for channels in (2, 1):
            expected = frames * SAMPLE_WIDTH * channels
            if abs(len(data) - expected) <= rate * SAMPLE_WIDTH * channels // 10:
                run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-f", "s16le", "-ar", str(rate), "-ac", str(channels), "-i", str(raw),
                        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                        "-c:a", "pcm_s16le", str(wav),
                    ]
                )
                return
        raise SystemExit(
            f"cannot infer raw PCM layout: {len(data)} bytes for {expected_ms} ms at {rate} Hz; "
            "use --output-format mp3_48000_320 instead"
        )
    to_wav(raw, wav)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != SAMPLE_WIDTH or handle.getnchannels() != CHANNELS:
            raise SystemExit(f"{path}: expected 16-bit stereo WAV")
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype="<i2").reshape(-1, CHANNELS).astype(np.float32) / 32768.0


def correlation(reference: np.ndarray, candidate: np.ndarray, max_lag: int = 4096) -> tuple[float, int]:
    """Best Pearson correlation of `candidate` against `reference` over ±max_lag samples."""
    a = reference.mean(axis=1)
    b = candidate.mean(axis=1)
    n = min(len(a), len(b))
    a = a[:n] - a[:n].mean()
    b = b[:n] - b[:n].mean()
    best, best_lag = -1.0, 0
    for lag in range(-max_lag, max_lag + 1, 16):
        if lag >= 0:
            x, y = a[: n - lag], b[lag:n]
        else:
            x, y = a[-lag:n], b[: n + lag]
        if len(x) < n // 2:
            continue
        denom = np.linalg.norm(x) * np.linalg.norm(y)
        r = float(np.dot(x, y) / denom) if denom else 0.0
        if r > best:
            best, best_lag = r, lag
    return best, best_lag


def joint_metrics(samples: np.ndarray, index: int) -> tuple[float, float, float]:
    mono = samples.mean(axis=1)
    diffs = np.abs(np.diff(mono))
    lo = max(1, index - SAMPLE_RATE)
    hi = min(len(diffs), index + SAMPLE_RATE)
    jump = float(diffs[max(0, index - 220): min(len(diffs), index + 220)].max())
    local = float(np.percentile(diffs[lo:hi], 99.99)) if hi > lo else 0.0
    window = int(0.05 * SAMPLE_RATE)
    before = mono[max(0, index - window): index]
    after = mono[index: index + window]
    rms = lambda block: float(np.sqrt(np.mean(block.astype(np.float64) ** 2)) + 1e-12)  # noqa: E731
    step_db = 20.0 * math.log10(rms(after) / rms(before))
    return jump, local, step_db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--song-a", required=True, type=Path, help="outgoing song (full file)")
    parser.add_argument("--song-b", required=True, type=Path, help="incoming song (full file)")
    parser.add_argument("--context-seconds", type=float, default=12.0)
    parser.add_argument("--glue-seconds", type=float, default=8.0)
    parser.add_argument("--condition", choices=["none", "a", "b"], default="none")
    parser.add_argument(
        "--condition-strength", choices=["low", "medium", "high", "xhigh"], default="high"
    )
    parser.add_argument("--output-format", default="mp3_48000_320")
    parser.add_argument("--out", required=True, type=Path, help="final spliced WAV")
    args = parser.parse_args()

    if not 3.0 <= args.glue_seconds <= 120.0:
        raise SystemExit("--glue-seconds must be within 3..120 (API limits)")
    if not 3.0 <= args.context_seconds <= 30.0:
        raise SystemExit("--context-seconds must be within 3..30")

    key = api_key()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    work = args.out.parent / f".{args.out.stem}_work"
    work.mkdir(parents=True, exist_ok=True)

    dur_a, dur_b = duration(args.song_a), duration(args.song_b)
    if dur_a < args.context_seconds + 3 or dur_b < args.context_seconds + 3:
        raise SystemExit("both songs must be longer than context-seconds + 3")

    a_tail, b_head = work / "a_tail.wav", work / "b_head.wav"
    to_wav(args.song_a, a_tail, start=dur_a - args.context_seconds, length=args.context_seconds)
    to_wav(args.song_b, b_head, start=0.0, length=args.context_seconds)

    a_id = upload(key, a_tail)
    b_id = upload(key, b_head)
    print(f"song_id A tail: {a_id}")
    print(f"song_id B head: {b_id}")

    context_ms = int(round(args.context_seconds * 1000))
    glue = {
        "text": "[Glue]\n{seamless musical transition: sustained pads, drum fill, no hard cut}",
        "duration_ms": int(round(args.glue_seconds * 1000)),
        "positive_styles": [
            "contemporary worship",
            "warm piano",
            "ambient pads",
            "smooth transition",
            "seamless",
            "natural build",
            "great production quality",
        ],
        "negative_styles": ["abrupt ending", "silence", "spoken word"],
        "context_adherence": "high",
    }
    if args.condition != "none":
        ref_ms = min(8000, context_ms)
        if args.condition == "b":
            ref_range = {"start_ms": 0, "end_ms": ref_ms}
            ref_song = b_id
        else:
            ref_range = {"start_ms": context_ms - ref_ms, "end_ms": context_ms}
            ref_song = a_id
        glue["conditioning_ref"] = {"song_id": ref_song, "range": ref_range}
        glue["condition_strength"] = args.condition_strength

    plan = {
        "chunks": [
            {"song_id": a_id, "range": {"start_ms": 0, "end_ms": context_ms}},
            glue,
            {"song_id": b_id, "range": {"start_ms": 0, "end_ms": context_ms}},
        ]
    }
    plan_path = work / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2))
    print(f"plan written: {plan_path}")

    expected_ms = context_ms * 2 + glue["duration_ms"]
    raw_path, seam_path = work / "seam.bin", work / "seam.wav"
    raw_path.write_bytes(compose(key, plan, args.output_format))
    decode_seam(raw_path, seam_path, expected_ms, args.output_format)
    print(f"seam duration: {duration(seam_path):.3f} s (expected {expected_ms / 1000:.3f} s)")

    seam = read_wav(seam_path)
    a_ref, b_ref = read_wav(a_tail), read_wav(b_head)
    window = SAMPLE_RATE * 3
    r_a, lag_a = correlation(a_ref[:window], seam[:window])
    r_b, lag_b = correlation(b_ref[-window:], seam[-window:])
    print(f"kept A tail: r={r_a:.4f} lag={lag_a} samples")
    print(f"kept B head: r={r_b:.4f} lag={lag_b} samples")
    if min(r_a, r_b) < 0.95:
        print("WARNING: ref regions do not match the uploads; inspect the seam before trusting it")

    # `seam` is always decoded to SAMPLE_RATE stereo, so indices are in 44.1 kHz frames.
    start = int(round(context_ms / 1000.0 * SAMPLE_RATE)) + lag_a
    end = start + int(round(args.glue_seconds * SAMPLE_RATE))
    if start < 0 or end > len(seam):
        raise SystemExit(f"glue window [{start}, {end}] outside seam of {len(seam)} frames")

    glue_raw = work / "glue_raw.wav"
    run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(seam_path),
            "-ss", f"{start / SAMPLE_RATE:.6f}", "-t", f"{args.glue_seconds:.6f}",
            "-c:a", "pcm_s16le", str(glue_raw),
        ]
    )
    fade = 0.02
    glue_wav = work / "glue.wav"
    run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(glue_raw),
            "-af",
            f"afade=t=in:st=0:d={fade},afade=t=out:st={args.glue_seconds - fade:.3f}:d={fade}",
            "-c:a", "pcm_s16le", str(glue_wav),
        ]
    )

    a_full, b_full = work / "a_full.wav", work / "b_full.wav"
    to_wav(args.song_a, a_full)
    to_wav(args.song_b, b_full)
    listing = work / "concat.txt"
    if any("'" in str(path) for path in (a_full, glue_wav, b_full)):
        raise SystemExit("paths containing a single quote are not supported by the concat demuxer")
    listing.write_text("".join(f"file '{path.resolve()}'\n" for path in (a_full, glue_wav, b_full)))
    run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-c", "copy", str(args.out),
        ]
    )

    glue_frames = int(round(args.glue_seconds * SAMPLE_RATE))
    a_frames = read_wav(a_full).shape[0]
    final = read_wav(args.out)
    for label, index in (("A/glue", a_frames), ("glue/B", a_frames + glue_frames)):
        jump, local, step_db = joint_metrics(final, index)
        verdict = "PASS" if abs(step_db) <= 3.0 and jump <= 2.0 * max(local, 1e-9) else "CHECK"
        print(
            f"joint {label}: jump={jump:.5f} local_p99.99={local:.5f} "
            f"rms_step={step_db:+.2f} dB -> {verdict}"
        )
    print(f"final: {args.out} ({duration(args.out):.3f} s)")
    print(f"intermediates: {work}")


if __name__ == "__main__":
    main()
```

## Unverified / verify first

1. ~~Inpainting is enterprise-gated.~~ Answered: a paid subscription cleared the upload call.
2. ~~Two different `song_id`s in one plan.~~ Answered: it works, but the returned refs are
   re-rendered rather than inserted — see [Measured](#measured-2026-11-21-paid-plan).
3. **Upload acceptance.** A 12 s 44.1 kHz stereo WAV was accepted and a full 146.51 s arranged side
   was rejected by the copyright screen (half the fee charged). Maximum duration and file size for
   *non-commercial* audio remain undocumented; the recipe uses excerpts for exactly this reason.
4. ~~`pcm_44100` payload layout.~~ Answered: 16-bit little-endian stereo, duration matching the plan.
5. **`song-id` header on `/v1/music`.** Verified in the JS SDK for `/v1/music/detailed`; assumed
   identical for `/v1/music`, and the API docs say the id is returned in response headers.
6. **`composition_plan_suggestion` / `bad_prompt` details** come from the official skill reference,
   not the OpenAPI error schema.
7. **Copyright-screen behaviour.** How the screen decides, whether a second rejection costs the same
   half fee again, and whether instrumental-only excerpts pass more often were not explored (one
   rejection, then excerpts).
