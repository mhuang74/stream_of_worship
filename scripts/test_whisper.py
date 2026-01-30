import os
from faster_whisper import WhisperModel

def export_to_lrc(whisper_result, output_fname):
    """
    Manually parses the result into an LRC file.
    Works whether result is a dictionary or a StableWhisper object.
    """
    # Handle both object and dictionary formats
    segments = whisper_result.segments if hasattr(whisper_result, 'segments') else whisper_result['segments']
    
    with open(output_fname, "w", encoding="utf-8") as f:
        for seg in segments:
            # Convert seconds to [mm:ss.xx]
            s = seg.get('start') if isinstance(seg, dict) else seg.start
            text = seg.get('text') if isinstance(seg, dict) else seg.text
            
            mm = int(s // 60)
            ss = s % 60
            timestamp = f"[{mm:02d}:{ss:05.2f}]"
            
            f.write(f"{timestamp} {text.strip()}\n")
    print(f"Successfully saved to {output_fname}")

cache_dir = "/Users/mhuang/.cache/whisper"
os.makedirs(cache_dir, exist_ok=True)

# Set compute_type="float16" for GPU or "int8" for CPU efficiency
model = WhisperModel(
    "large-v3", 
    device="cpu",            # Change to "cpu" if you don't have a GPU
    download_root=cache_dir
)


# Use Large-v3 and provide a Traditional or Simplified Chinese prompt
result = model.transcribe(
    "/Users/mhuang/Projects/Development/stream_of_worship/poc_output_allinone/stems/give_thanks/vocals.wav", 
    language="zh", 
    initial_prompt="这是一首中文敬拜歌的歌詞，來自讚美之泉的‘我要一心稱謝你’",
    beam_size=5,
    vad_filter=True,          # REMOVES BACKGROUND NOISE: Vital for songs
    vad_parameters=dict(min_silence_duration_ms=500),
)

export_to_lrc(result, "give_thanks_large.lrc")
