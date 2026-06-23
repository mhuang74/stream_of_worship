"""Tests for hasher service."""

import hashlib

import pytest

from stream_of_worship.admin.services.hasher import compute_file_hash, get_hash_prefix


class TestComputeFileHash:
    """Tests for compute_file_hash."""

    def test_known_content(self, tmp_path):
        """Hash matches hashlib output for known content."""
        content = b"hello world"
        file_path = tmp_path / "test.txt"
        file_path.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        assert compute_file_hash(file_path) == expected

    def test_empty_file(self, tmp_path):
        """Empty file produces the SHA-256 of zero bytes."""
        file_path = tmp_path / "empty.bin"
        file_path.write_bytes(b"")

        expected = hashlib.sha256(b"").hexdigest()
        assert compute_file_hash(file_path) == expected

    def test_same_content_same_hash(self, tmp_path):
        """Two files with identical content produce the same hash."""
        content = b"identical content"
        file_a = tmp_path / "a.bin"
        file_b = tmp_path / "b.bin"
        file_a.write_bytes(content)
        file_b.write_bytes(content)

        assert compute_file_hash(file_a) == compute_file_hash(file_b)

    def test_different_content_different_hash(self, tmp_path):
        """Two files with different content produce different hashes."""
        file_a = tmp_path / "a.bin"
        file_b = tmp_path / "b.bin"
        file_a.write_bytes(b"content A")
        file_b.write_bytes(b"content B")

        assert compute_file_hash(file_a) != compute_file_hash(file_b)

    def test_multi_chunk_file(self, tmp_path):
        """Hash is correct for files larger than the 8 KiB read buffer."""
        content = b"x" * (8192 * 3 + 100)  # spans multiple chunks
        file_path = tmp_path / "large.bin"
        file_path.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        assert compute_file_hash(file_path) == expected

    def test_nonexistent_file_raises(self, tmp_path):
        """Raises FileNotFoundError for a missing file."""
        with pytest.raises(FileNotFoundError):
            compute_file_hash(tmp_path / "nope.bin")

    def test_output_is_64_hex_chars(self, tmp_path):
        """Output is always a 64-character lowercase hex string."""
        file_path = tmp_path / "test.bin"
        file_path.write_bytes(b"any content")

        result = compute_file_hash(file_path)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestGetHashPrefix:
    """Tests for get_hash_prefix."""

    def test_extracts_first_12_chars(self):
        """Returns exactly the first 12 characters."""
        full_hash = "abcdef123456" + "0" * 52
        assert get_hash_prefix(full_hash) == "abcdef123456"

    def test_consistent_with_compute_file_hash(self, tmp_path):
        """Prefix matches the first 12 characters of the full hash."""
        file_path = tmp_path / "test.bin"
        file_path.write_bytes(b"prefix test content")

        full_hash = compute_file_hash(file_path)
        assert get_hash_prefix(full_hash) == full_hash[:12]

    def test_real_sha256_prefix_length(self):
        """Prefix of a real SHA-256 hash is always 12 characters."""
        full_hash = hashlib.sha256(b"sample").hexdigest()
        prefix = get_hash_prefix(full_hash)
        assert len(prefix) == 12
