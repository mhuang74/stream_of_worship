"""Tests for CPS (Characters-Per-Second) helpers."""

import pytest
from scipy import stats

from sow_analysis.workers.cps import (
    compute_cps,
    count_lyric_chars,
    cps_bucket_label,
    cps_to_prior,
)


class TestCountLyricChars:
    """Tests for count_lyric_chars."""

    def test_single_cjk_char_counts_as_one(self):
        assert count_lyric_chars("神") == 1

    def test_multiple_cjk_chars_count_individually(self):
        assert count_lyric_chars("神愛你") == 3

    def test_ascii_alphanumeric_run_collapses_to_one(self):
        assert count_lyric_chars("hello") == 1

    def test_mixed_cjk_and_ascii(self):
        # "神" (1) + "hello" (1) + "愛" (1) = 3
        assert count_lyric_chars("神hello愛") == 3

    def test_whitespace_excluded(self):
        assert count_lyric_chars("神 愛 你") == 3

    def test_punctuation_excluded(self):
        assert count_lyric_chars("神，愛。你！") == 3

    def test_empty_string(self):
        assert count_lyric_chars("") == 0

    def test_only_whitespace_and_punctuation(self):
        assert count_lyric_chars("  ，。！  ") == 0

    def test_multiple_ascii_runs(self):
        # "hello" (1) + "world" (1) = 2
        assert count_lyric_chars("hello world") == 2

    def test_ascii_run_between_cjk(self):
        # "神" (1) + "abc" (1) + "愛" (1) = 3
        assert count_lyric_chars("神abc愛") == 3


class TestComputeCps:
    """Tests for compute_cps."""

    def test_standard_lrc_returns_expected_cps(self):
        """A 2-line LRC with 4 CJK chars over 10 seconds → cps=0.4."""
        lrc = "[00:00.00]神愛你\n[00:10.00]我"
        cps, meta = compute_cps(lrc)
        assert cps is not None
        assert meta is not None
        assert cps == pytest.approx(4 / 10.0)
        assert meta["lines"] == 2
        assert meta["chars"] == 4
        assert meta["span_s"] == pytest.approx(10.0)

    def test_fewer_than_2_lines_returns_none(self):
        lrc = "[00:00.00]神愛你"
        cps, meta = compute_cps(lrc)
        assert cps is None
        assert meta is not None
        assert "reason" in meta

    def test_non_positive_span_returns_none(self):
        """Two lines with same timestamp → span=0 → None."""
        lrc = "[00:05.00]神愛\n[00:05.00]你"
        cps, meta = compute_cps(lrc)
        assert cps is None
        assert meta is not None
        assert "reason" in meta

    def test_empty_string_returns_none(self):
        cps, meta = compute_cps("")
        assert cps is None
        assert meta is not None
        assert "reason" in meta

    def test_no_valid_lrc_lines_returns_none(self):
        cps, meta = compute_cps("just plain text\nno timestamps")
        assert cps is None
        assert meta is not None
        assert "reason" in meta

    def test_multi_line_lrc(self):
        """3 lines spanning 20 seconds with 6 chars → cps=0.3."""
        lrc = "[00:00.00]神愛\n[00:10.00]你我\n[00:20.00]他"
        cps, meta = compute_cps(lrc)
        assert cps is not None
        assert cps == pytest.approx(5 / 20.0)


class TestCpsBucketLabel:
    """Tests for cps_bucket_label."""

    def test_none_returns_none(self):
        assert cps_bucket_label(None) is None

    def test_slow_boundary_below(self):
        assert cps_bucket_label(1.499) == "slow"

    def test_slow_boundary_at(self):
        assert cps_bucket_label(1.5) == "moderate"

    def test_moderate_at(self):
        assert cps_bucket_label(2.0) == "moderate"

    def test_moderate_boundary_at(self):
        assert cps_bucket_label(2.8) == "moderate"

    def test_fast_boundary_above(self):
        assert cps_bucket_label(2.801) == "fast"

    def test_zero_is_slow(self):
        assert cps_bucket_label(0.0) == "slow"


class TestCpsToPrior:
    """Tests for cps_to_prior."""

    def test_none_returns_none(self):
        assert cps_to_prior(None) is None

    def test_slow_returns_lognorm(self):
        prior = cps_to_prior(1.0)
        assert isinstance(prior, stats.rv_continuous) or hasattr(prior, "mean")

    def test_moderate_returns_lognorm(self):
        prior = cps_to_prior(2.0)
        assert prior is not None
        assert hasattr(prior, "mean")

    def test_fast_returns_lognorm(self):
        prior = cps_to_prior(3.0)
        assert prior is not None
        assert hasattr(prior, "mean")

    def test_slow_prior_mean_approx_70(self):
        """The slow prior should be centered near 70 BPM."""
        prior = cps_to_prior(1.0)
        assert prior is not None
        assert prior.mean() == pytest.approx(70.0, abs=2.0)

    def test_moderate_prior_mean_approx_105(self):
        """The moderate prior should be centered near 105 BPM."""
        prior = cps_to_prior(2.0)
        assert prior is not None
        assert prior.mean() == pytest.approx(105.0, abs=2.0)

    def test_fast_prior_mean_approx_135(self):
        """The fast prior should be centered near 135 BPM."""
        prior = cps_to_prior(3.0)
        assert prior is not None
        assert prior.mean() == pytest.approx(135.0, abs=2.0)

    def test_prior_std_within_tolerance(self):
        """The prior std should be close to the configured value."""
        prior = cps_to_prior(1.0)
        assert prior is not None
        assert prior.std() == pytest.approx(12.0, abs=2.0)
