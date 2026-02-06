"""Tests for analysis service models."""

import pytest
from sow_analysis.models import (
    AnalyzeJobRequest,
    AnalyzeOptions,
    JobResponse,
    JobResult,
    JobStatus,
    JobType,
    LrcJobRequest,
    LrcOptions,
    Section,
)


class TestAnalyzeOptions:
    """Test AnalyzeOptions model."""

    def test_default_values(self):
        """Test default option values."""
        opts = AnalyzeOptions()
        assert opts.generate_stems is True
        assert opts.stem_model == "htdemucs"
        assert opts.force is False

    def test_custom_values(self):
        """Test custom option values."""
        opts = AnalyzeOptions(generate_stems=False, stem_model="demucs", force=True)
        assert opts.generate_stems is False
        assert opts.stem_model == "demucs"
        assert opts.force is True


class TestAnalyzeJobRequest:
    """Test AnalyzeJobRequest model."""

    def test_required_fields(self):
        """Test required fields."""
        req = AnalyzeJobRequest(
            audio_url="s3://bucket/hash/audio.mp3", content_hash="abc123"
        )
        assert req.audio_url == "s3://bucket/hash/audio.mp3"
        assert req.content_hash == "abc123"
        assert req.options.generate_stems is True

    def test_with_custom_options(self):
        """Test with custom options."""
        req = AnalyzeJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123",
            options=AnalyzeOptions(generate_stems=False),
        )
        assert req.options.generate_stems is False


class TestLrcOptions:
    """Test LrcOptions model."""

    def test_default_values(self):
        """Test default option values."""
        opts = LrcOptions()
        assert opts.whisper_model == "large-v3"


class TestLrcJobRequest:
    """Test LrcJobRequest model."""

    def test_required_fields(self):
        """Test required fields."""
        req = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123",
            lyrics_text="Line 1\nLine 2",
        )
        assert req.audio_url == "s3://bucket/hash/audio.mp3"
        assert req.content_hash == "abc123"
        assert req.lyrics_text == "Line 1\nLine 2"


class TestSection:
    """Test Section model."""

    def test_section_creation(self):
        """Test creating a section."""
        section = Section(label="chorus", start=30.0, end=60.0)
        assert section.label == "chorus"
        assert section.start == 30.0
        assert section.end == 60.0


class TestJobResult:
    """Test JobResult model."""

    def test_empty_result(self):
        """Test empty result."""
        result = JobResult()
        assert result.duration_seconds is None
        assert result.tempo_bpm is None

    def test_full_result(self):
        """Test result with all fields."""
        result = JobResult(
            duration_seconds=180.5,
            tempo_bpm=120.0,
            musical_key="C",
            musical_mode="major",
            key_confidence=0.85,
            loudness_db=-14.0,
            beats=[0.0, 0.5, 1.0],
            downbeats=[0.0, 2.0, 4.0],
            sections=[Section(label="intro", start=0.0, end=15.0)],
            embeddings_shape=[4, 100, 24],
            stems_url="s3://bucket/hash/stems/",
        )
        assert result.duration_seconds == 180.5
        assert result.tempo_bpm == 120.0
        assert result.musical_key == "C"


class TestJobResponse:
    """Test JobResponse model."""

    def test_response_creation(self):
        """Test creating a response."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        response = JobResponse(
            job_id="job_abc123",
            status=JobStatus.QUEUED,
            job_type=JobType.ANALYZE,
            created_at=now,
            updated_at=now,
        )
        assert response.job_id == "job_abc123"
        assert response.status == JobStatus.QUEUED
        assert response.job_type == JobType.ANALYZE
        assert response.progress == 0.0


class TestJobStatus:
    """Test JobStatus enum."""

    def test_enum_values(self):
        """Test enum values."""
        assert JobStatus.QUEUED == "queued"
        assert JobStatus.PROCESSING == "processing"
        assert JobStatus.COMPLETED == "completed"
        assert JobStatus.FAILED == "failed"


class TestJobType:
    """Test JobType enum."""

    def test_enum_values(self):
        """Test enum values."""
        assert JobType.ANALYZE == "analyze"
        assert JobType.LRC == "lrc"
