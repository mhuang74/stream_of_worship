#!/usr/bin/env python3
"""Simple test to verify miniaudio playback works."""

import miniaudio
from pathlib import Path

# Test file path
audio_file = Path("/Users/mhuang/.config/sow-app/cache/c105e75972f7/audio/audio.mp3")

if not audio_file.exists():
    print(f"Audio file not found: {audio_file}")
    exit(1)

print(f"Testing playback of: {audio_file}")
print("This should play audio for 5 seconds using miniaudio...")

try:
    # Decode the file first
    print("Decoding audio file...")
    decoded = miniaudio.decode_file(
        str(audio_file),
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=2,
        sample_rate=44100,
    )

    print(f"Decoded: {decoded.sample_rate}Hz, {decoded.nchannels}ch")
    print(f"Samples type: {type(decoded.samples)}, length: {len(decoded.samples)}")
    print(f"Sample format: {decoded.sample_format}")

    # Check if it's already an array or bytes
    if hasattr(decoded.samples, 'dtype'):
        print(f"Samples dtype: {decoded.samples.dtype}")

    print("Starting playback for 5 seconds...")

    # Use the simple play function
    import time
    start = time.time()

    # Play in a background thread
    def play_samples():
        device = miniaudio.PlaybackDevice(
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=2,
            sample_rate=44100,
        )

        # Create a simple generator that yields numpy arrays
        import numpy as np

        def sample_generator():
            sample_pos = 0  # Position in samples (int16 values), not bytes
            # Prime the generator
            num_frames = yield np.zeros((0, 2), dtype=np.int16)
            print(f"Generator primed, requested {num_frames} frames")

            count = 0
            while sample_pos < len(decoded.samples) and count < 100:  # Limit iterations
                count += 1
                if num_frames is None or num_frames <= 0:
                    print(f"Invalid request: {num_frames}")
                    break

                # Calculate number of samples needed (frames * channels)
                samples_needed = num_frames * 2  # 2 channels
                chunk = decoded.samples[sample_pos:sample_pos + samples_needed]

                # Convert array.array to numpy array
                samples = np.array(chunk, dtype=np.int16)

                # Pad if needed
                if len(samples) < samples_needed:
                    samples = np.concatenate([samples, np.zeros(samples_needed - len(samples), dtype=np.int16)])

                # Reshape to (frames, channels)
                samples = samples.reshape((num_frames, 2))

                print(f"Yielding {samples.shape} at sample pos {sample_pos}")
                sample_pos += samples_needed

                num_frames = yield samples
                print(f"Next request: {num_frames} frames")

        gen = sample_generator()
        next(gen)  # Prime it
        device.start(gen)

        # Keep device alive
        time.sleep(5)
        device.stop()
        device.close()
        print("Playback stopped")

    import threading
    thread = threading.Thread(target=play_samples, daemon=True)
    thread.start()
    thread.join(timeout=6)

    print("Test complete! Did you hear audio? (y/n)")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
