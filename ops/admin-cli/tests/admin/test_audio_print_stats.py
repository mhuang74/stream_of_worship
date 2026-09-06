"""Tests for the consolidated manual-intervention section in ``_print_stats``.

``_print_stats`` previously printed five per-step "Failed X" sections that
only surfaced hard failures. The consolidated builder replaces them with a
single "Requires manual intervention" section listing every song that needs
attention — failed steps AND data-gap skips — plus a ``Needs attention``
count row in the Batch Summary panel.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from rich.console import Console

from stream_of_worship.admin.commands.audio import _print_stats


def _render(results: dict, selected_steps=None) -> str:
    db_client = MagicMock()
    db_client.get_song.side_effect = lambda song_id: SimpleNamespace(title=f"Title {song_id}")
    console = Console(record=True, width=120, force_terminal=False)
    _print_stats(results, db_client, console, "rich", selected_steps=selected_steps)
    return console.export_text()


def _bullet_index(text: str, song_id: str) -> int:
    return text.index(f"[{song_id}]")


class TestDownloadFailureInference:
    """A failed download names the song and infers the LRC skip."""

    def test_download_failed_lists_both_clauses_when_lrc_selected(self):
        results = {
            "s_dl": {
                "download": "failed",
                "error": "no matching title in top 5 search results",
            }
        }
        text = _render(results, selected_steps=["download", "lrc"])

        assert "Requires manual intervention:" in text
        dl = text.index("download: failed — no matching title in top 5 search results")
        lrc = text.index("lrc: skipped (download failed)")
        bullet = _bullet_index(text, "s_dl")
        assert bullet < dl < lrc, "both reasons must be sub-lines of the single song bullet"

    def test_download_failed_no_lrc_clause_when_lrc_not_selected(self):
        results = {
            "s_dl": {
                "download": "failed",
                "error": "no matching title in top 5 search results",
            }
        }
        text = _render(results, selected_steps=["download"])

        assert "download: failed — no matching title in top 5 search results" in text
        assert "lrc: skipped (download failed)" not in text

    def test_download_failed_with_unknown_selected_steps_infers_lrc_skip(self):
        results = {"s_dl": {"download": "failed", "error": "boom"}}
        text = _render(results, selected_steps=None)

        assert "download: failed — boom" in text
        assert "lrc: skipped (download failed)" in text

    def test_real_lrc_skip_wins_over_inferred_download_skip(self):
        results = {
            "s_dl": {
                "download": "failed",
                "error": "boom",
                "lrc": "skipped_no_lyrics",
            }
        }
        text = _render(results, selected_steps=["download", "lrc"])

        assert "lrc: skipped (no lyrics in catalog)" in text
        assert "lrc: skipped (download failed)" not in text


class TestSkipStatuses:
    """Data-gap skips are surfaced with a human-readable reason."""

    def test_lrc_skipped_no_lyrics(self):
        text = _render({"s1": {"lrc": "skipped_no_lyrics"}}, selected_steps=["lrc"])

        assert "lrc: skipped (no lyrics in catalog)" in text
        assert _bullet_index(text, "s1")

    def test_lrc_skipped_no_recording(self):
        text = _render({"s1": {"lrc": "skipped_no_recording"}}, selected_steps=["lrc"])

        assert "lrc: skipped (no recording)" in text

    def test_components_skipped_no_sections(self):
        text = _render({"s1": {"components": "skipped_no_sections"}}, selected_steps=["components"])

        assert "components: skipped (no sections or LRC — run 'audio lrc' first)" in text

    def test_components_skipped_no_recording(self):
        text = _render({"s1": {"components": "skipped_no_recording"}}, selected_steps=["components"])

        assert "components: skipped (no recording or audio)" in text


class TestCompletedSongsNotListed:
    def test_fully_completed_song_absent_and_count_zero(self):
        results = {
            "s_ok": {
                "download": "skipped_r2",
                "lrc": "completed",
                "lrc_source": "r2_preexisting",
                "analyze": "completed",
                "embedding": "completed",
                "components": "completed",
            }
        }
        text = _render(results, selected_steps=["download", "lrc", "analyze", "embedding", "components"])

        assert "Requires manual intervention:" not in text
        assert "s_ok" not in text
        assert "Needs attention:" in text

        row = next(line for line in text.splitlines() if "Needs attention:" in line)
        assert row.split()[-2] == "0", f"count row should read 0: {row!r}"


class TestMultipleIssuesSingleBullet:
    """One song with several failed steps renders one bullet, losslessly."""

    def test_download_and_embedding_failures_share_one_bullet(self):
        results = {
            "s1": {
                "download": "failed",
                "error": "boom-dl",
                "embedding": "failed",
                "embedding_error": "boom-emb",
            }
        }
        text = _render(results, selected_steps=["download", "embedding"])

        assert text.count("  - Title s1 [s1]") == 1
        dl = text.index("download: failed — boom-dl")
        emb = text.index("embedding: failed — boom-emb")
        bullet = _bullet_index(text, "s1")
        assert bullet < dl < emb
        # Old per-step section headers are replaced, not duplicated.
        assert "Failed downloads:" not in text
        assert "Failed embedding:" not in text

    def test_two_songs_get_two_bullets(self):
        results = {
            "s1": {"download": "failed", "error": "boom-1"},
            "s2": {"analyze": "failed", "analyze_error": "boom-2"},
        }
        text = _render(results, selected_steps=["download", "analyze"])

        assert text.count("  - Title s1 [s1]") == 1
        assert text.count("  - Title s2 [s2]") == 1
        assert "download: failed — boom-1" in text
        assert "analyze: failed — boom-2" in text


class TestBackfillFailure:
    def test_backfill_lyrics_failed_listed(self):
        text = _render({"s1": {"backfill_lyrics": "failed"}}, selected_steps=["backfill_lyrics"])

        assert "backfill_lyrics: failed — structured-lyrics backfill" in text


class TestNeedsAttentionPanelRow:
    def test_count_row_matches_listed_songs(self):
        results = {
            "s1": {"download": "failed", "error": "boom"},
            "s2": {"lrc": "skipped_no_lyrics"},
            "s_ok": {"lrc": "completed"},
        }
        text = _render(results, selected_steps=["download", "lrc"])

        row = next(line for line in text.splitlines() if "Needs attention:" in line)
        assert row.split()[-2] == "2", f"count row should read 2: {row!r}"