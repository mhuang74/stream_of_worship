"""Audio transcoding helpers shared by the Modal clients."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def transcode_to_16k_mono_wav(audio_path: Path) -> bytes:
    """Transcode a local audio file to 16 kHz mono PCM-16 WAV bytes.

    Blocking (librosa decode + resample); callers run it via asyncio.to_thread.
    16 kHz mono is the input rate of every speech model we host on Modal, so the
    remote side loses no information. The temp file descriptor is closed before
    soundfile writes to it (mkstemp keeps the fd open otherwise).
    """
    import librosa
    import soundfile as sf

    y, _ = librosa.load(str(audio_path), sr=16000, mono=True)
    fd, name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    tmp_path = Path(name)
    try:
        sf.write(str(tmp_path), y, 16000, subtype="PCM_16")
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
