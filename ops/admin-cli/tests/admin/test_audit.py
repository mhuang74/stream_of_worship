"""Unit tests for the admin audit log helper."""

import logging

from stream_of_worship.admin.audit import log_change


def test_log_change_emits_structured_line(caplog):
    caplog.set_level(logging.INFO, logger="stream_of_worship.admin.audit")

    log_change(
        "visibility_change",
        song_id="song_1",
        hash_prefix="abc123",
        previous="review",
        new="published",
    )

    assert (
        "audit event=visibility_change song_id=song_1 hash_prefix=abc123 "
        "previous=review new=published" in caplog.text
    )


def test_log_change_renders_dash_for_missing_ids(caplog):
    caplog.set_level(logging.INFO, logger="stream_of_worship.admin.audit")

    log_change("song_soft_delete", via="catalog_sync")

    assert "audit event=song_soft_delete song_id=- hash_prefix=- via=catalog_sync" in caplog.text


def test_log_change_sanitizes_newlines(caplog):
    caplog.set_level(logging.INFO, logger="stream_of_worship.admin.audit")

    log_change("lrc_save", song_id="song_1", note="line1\nline2")

    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 1
    assert "note=line1\\nline2" in messages[0]
    assert "\n" not in messages[0]
