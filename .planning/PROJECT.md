# Stream of Worship

## What This Is

A Chinese worship music transition system that analyzes songs (tempo, key, structure) and generates smooth transitions between them. The system includes an Admin CLI for catalog management, an Analysis Service for CPU/GPU-intensive audio processing, and a User App TUI for creating songsets and exporting audio/video.

## Core Value

Users can seamlessly create worship songsets with accurate lyrics videos that display at exactly the right time — no more early or late lyrics.

## Requirements

### Validated

- ✓ Scrape sop.org for song metadata — existing
- ✓ Download YouTube audio to R2 storage — existing
- ✓ Audio analysis (BPM, key detection) — existing
- ✓ Stem separation — existing
- ✓ LRC generation via Whisper + LLM — existing
- ✓ TUI for browsing catalog and creating songsets — existing
- ✓ Audio/video export with transitions — existing

### Active

- [ ] LRC timecode refinement via Qwen3 Force Aligner — Milestone v1.1

### Out of Scope

- Real-time lyrics display — not needed for export workflow
- Alternative ASR models — Whisper + Qwen3 is the chosen pipeline

## Context

**Current LRC Pipeline:**
1. Whisper transcribes audio → phrase-level timestamps
2. LLM aligns phrases to known lyrics → LRC format
3. **Gap:** Timestamps can be early/late, causing lyrics to display at wrong time in video

**Solution:** Add Qwen3-ForcedAligner-0.6B as refinement step
- Takes Whisper output + audio → fine-tuned word-level alignment
- Corrects timing drift, handles musical sections better
- Requires specific PyTorch version → separate Docker service

## Constraints

- **Tech Stack:** Python 3.11, FastAPI, Docker, PyTorch
- **Integration:** Must work with existing Analysis Service job queue
- **Performance:** Should not significantly increase LRC generation time
- **Compatibility:** Qwen3 may need isolated environment (PyTorch version conflicts)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Qwen3 as refinement (not replacement) | Whisper+LLM gets us 80% there; Qwen3 fixes the last 20% timing accuracy | — Pending |
| Separate Docker service for Qwen3 | PyTorch version isolation, resource management | — Pending |

## Current Milestone: v1.1 Qwen3 LRC Refinement

**Goal:** Integrate Qwen3-ForcedAligner-0.6B to eliminate early/late lyrics display issues.

**Target features:**
- Qwen3 Force Aligner service (Docker)
- Refinement API endpoint
- Integration with existing LRC pipeline
- Validation/quality metrics for timing accuracy

---
*Last updated: 2026-02-13 after milestone v1.1 kickoff*
