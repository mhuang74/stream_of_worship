# SenseVoice Tuning Results for LRC Evaluation

## Summary

After extensive parameter tuning, we improved SenseVoice transcription accuracy from **23.5% to 28.5%** for Chinese song lyrics evaluation. However, achieving >90% accuracy is not possible with parameter tuning alone due to fundamental limitations of ASR models for singing transcription.

## Parameter Tuning Results

### Best Configuration Found (Now Default)
```bash
uv run --extra lrc_eval poc/eval_lrc.py \
  wo_yao_yi_xin_cheng_xie_mi_247 \
  --engine sensevoice
```

The optimized VAD parameters are now the default:
- `--sensevoice-vad-threshold 0.5` (was 0.8)
- `--sensevoice-vad-max-silence 1000` (was 300)

### Comparison Table

| Configuration | Audio Words | Matched | Text Accuracy | Final Score |
|---------------|-------------|---------|---------------|-------------|
| Old Default (threshold=0.8, silence=300) | 281 | 97 | 23.5% | 14.1 |
| **New Default (threshold=0.5, silence=1000)** | **164** | **101** | **28.5%** | **17.1** |

Note: The optimized VAD parameters (threshold=0.5, max_silence=1000) are now the default.

### New Parameters Added

The script now supports these SenseVoice-specific parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--sensevoice-use-itn` | False | Enable inverse text normalization |
| `--sensevoice-batch-size` | 60 | Batch size in seconds |
| `--sensevoice-disable-vad` | False | Disable VAD entirely |
| `--sensevoice-vad-max-silence` | 1000 | VAD max end silence (ms) |
| `--sensevoice-vad-threshold` | 0.5 | VAD speech/noise threshold (lower = more sensitive) |
| `--pinyin-mode` | False | Score based on pronunciation accuracy |

## Key Findings

### 1. VAD is the Main Bottleneck
The VAD (Voice Activity Detection) cuts out most of the singing:
- LRC has 544 syllables
- SenseVoice transcribes only 164-281 syllables (30-50% coverage)
- Even with optimized VAD parameters, coverage remains low

### 2. Homophones are a Major Issue
Chinese has many homophones (same pronunciation, different characters):
- Example: "呈" (cheng2) vs "称" (cheng1) - both pronounced "cheng"
- ASR models output wrong characters even when pronunciation is correct

### 3. Timing is Poor
- RMS timing error: ~116 seconds
- This is because VAD segments don't align well with LRC timestamps

## Root Cause of Poor Alignment

The verbose output shows the core problem:

```
[00:00.00] 我要一心稱謝祢
  Audio:  我   要    一   心    称     -∅   -∅  +现    +你  +你  +你  +你  +歌  +黎  +献...
```

SenseVoice returns **1 segment** with all ~164 characters concatenated into one long string:
```
我要一心称现你
在主身面前歌颂你我要向你的圣殿你慈歌世赞黎明...
```

### Why This Happens

1. **VAD Cannot Segment Singing**: The Voice Activity Detection is designed for speech, which has natural pauses. Singing is continuous, so VAD treats the entire song as one segment.

2. **Timestamp Interpolation Fails**: SenseVoice returns ~60 timestamps for 164 characters. The remaining 100+ characters get interpolated across the entire segment duration (0-30 seconds), causing them to all align to the first LRC line.

3. **Character Garbage**: The transcription itself is garbled ("称现你" instead of "稱謝祢", "主身" instead of "諸神"), suggesting the model struggles with singing pronunciation.

### Why >90% is Not Achievable with Parameter Tuning

1. **Singing vs Speech**: ASR models are trained on speech, not singing
   - Vocal pitch, rhythm, and pronunciation differ significantly
   - No parameter can make a speech model understand singing

2. **Chinese Homophones**: Same pronunciation maps to different characters
   - ASR models choose characters based on context
   - Lyrics often have unusual word combinations that confuse ASR

3. **VAD Limitations**: Voice Activity Detection is designed for speech
   - Singing has different spectral characteristics
   - Long sustained notes may be cut off

4. **No Fine-tuning**: The model isn't trained on worship songs/lyrics
   - Domain mismatch causes poor recognition

## Recommendations for >90% Accuracy

### Immediate Fix for Alignment

**Split audio by LRC timestamps before transcription:**

```python
# Pseudocode for the fix:
for each LRC line with start_time and end_time:
    audio_segment = extract_audio_segment(vocals.wav, start_time, end_time)
    transcription = sensevoice.transcribe(audio_segment)
    # Assign transcription to this LRC line's time window
```

This would ensure each transcription segment aligns with the correct LRC line, fixing the character garbage issue.

### Short Term (Most Practical)

1. **Use Pinyin-Based Scoring** (`--pinyin-mode`)
   - Scores pronunciation accuracy instead of exact characters
   - Accounts for Chinese homophones
   - More meaningful metric for singing evaluation

2. **Pre-process Audio**
   - Normalize vocal volume
   - Remove instrumental sections before transcription
   - Use vocal separation (already done with stems)

3. **Post-process with LLM**
   - Use GPT/Claude to correct ASR output based on known lyrics
   - Match pinyin and pick correct characters from LRC file

### Medium Term

4. **Use Larger Models**
   - Try `iic/SenseVoiceLarge` instead of `SenseVoiceSmall`
   - Requires more memory but may improve accuracy

5. **Chunk-Based Processing**
   - Split audio by LRC timestamps first
   - Transcribe each chunk separately
   - Better alignment and context

### Long Term (Required for 90%+)

6. **Fine-tune on Chinese Worship Songs**
   - Collect dataset of Chinese worship songs with transcripts
   - Fine-tune SenseVoice on singing data
   - This is the only way to get true >90% accuracy

7. **Hybrid Approach**
   - Use audio fingerprinting instead of ASR
   - Align known lyrics to audio using chromaprint/shazam-like technology
   - Skip transcription entirely

## Conclusion

Parameter tuning improved SenseVoice from 23.5% to 28.5% accuracy, but fundamental limitations prevent reaching 90%. The most practical path forward is:

1. Accept pinyin-based scoring as the primary metric
2. Use LLM post-processing to correct characters
3. Consider fine-tuning a model on Chinese worship songs

The script has been enhanced with:
- VAD parameter tuning options
- Pinyin accuracy analysis
- `--pinyin-mode` for pronunciation-based scoring

These improvements make the evaluation more meaningful for the specific use case of Chinese worship song lyrics.
