"""SHA-256 hashing for audio file identification.

Computes content hashes for audio files to create universal identifiers
used across the system (R2 storage paths, recording lookups).
"""

import hashlib
from pathlib import Path


def compute_file_hash(file_path: Path) -> str:
    """Compute the SHA-256 hash of a file.

    Reads in 8 KiB chunks so arbitrarily large files are handled without
    loading the entire file into memory.

    Args:
        file_path: Path to the file to hash

    Returns:
        Full SHA-256 hex digest (64 characters)

    Raises:
        FileNotFoundError: If the file does not exist
    """
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_hash_prefix(content_hash: str) -> str:
    """Extract the 12-character R2 directory prefix from a full content hash.

    This prefix is used as the directory name on R2 for all assets
    (audio, stems, LRC) associated with a single recording.

    Args:
        content_hash: Full SHA-256 hex digest (64 characters)

    Returns:
        First 12 characters of the hash
    """
    return content_hash[:12]
