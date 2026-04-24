# Changes

## lrc_eval_poc Branch

### Overview
LRC evaluation POC with multi-engine speech recognition support and comprehensive lyrics analysis.

### Summary
- **13 files changed** (+4,312 insertions, -435 deletions)

### New Features

#### LRC Evaluation Script
- Added `poc/eval_lrc.py` - comprehensive lyrics timing and accuracy analysis
- Pinyin accuracy metrics evaluation
- LRC-based segmentation with VAD preprocessing
- Per-line alignment support

#### Multi-Engine Speech Recognition
- Added SenseVoice support (`poc/gen_lrc_sensevoice.py`)
- Added OmniSenseVoice support (`poc/gen_lrc_omnisensevoice.py`)
- Enhanced Qwen3 lyrics generation (`poc/gen_lrc_qwen3.py`)
- Enhanced Whisper (`poc/gen_lrc_whisper.py`) with smart lyrics prompting
- Enhanced WhisperX (`poc/gen_lrc_whisperx.py`)

#### Analysis Improvements
- VAD pre-processing and per-line alignment for LRC evaluation
- Time offset normalization for VAD-induced timing shifts
- Side-by-side LRC and transcription display in verbose output
- Raw ASR transcription output in verbose mode
- Original lyrics sections display in verbose output

### Bug Fixes
- Fixed pinyin for 祢 (ni, not mi) in worship context
- Improved paraformer transcription with VAD model
- Fixed module import error for `poc.utils` in `eval_lrc.py`

### Infrastructure
- Updated `.gitignore` with new patterns
- Added test coverage (`tests/poc/test_eval_lrc.py`)
- Added handover documentation (`report/handover_visibility_status.md`)
- Updated dependencies in `pyproject.toml`
- Refactored vocal stem generation (`poc/gen_clean_vocal_stem.py`)

### Commits (12)
1. fix: Resolve module import error for poc.utils in eval_lrc.py
2. feat: Add smart lyrics prompting for Whisper transcription
3. feat: Add LRC-based segmentation and pinyin accuracy analysis
4. feat: Add VAD pre-processing and per-line alignment for LRC evaluation
5. feat: Display LRC and transcription side-by-side in verbose output
6. feat: Show raw ASR transcription in verbose output
7. feat: Add original lyrics sections to verbose output
8. feat: Add time offset normalization for VAD-induced timing shifts
9. fix: Correct pinyin for 祢 (ni, not mi) in worship context
10. fix: Improve paraformer transcription with VAD model
11. feat: Add SenseVoice and OmniSenseVoice experiments, refine lyric generation
12. feat: Add LRC evaluation POC script with multi-engine support