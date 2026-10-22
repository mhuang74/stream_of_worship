"""Pilot-driven tests for the lyrics feedback TUI (issue #261).

Covers the two regressions found during development:
- ``SadFeedbackDialog.AUTO_FOCUS = ""``: without it the note ``Input``
  auto-focuses on open and digits type into the note instead of firing the
  1-4 reason bindings.
- Reason keys choose-and-focus (not submit): ``2`` stores the reason and
  focuses the note; Enter submits.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stream_of_worship.admin.db.models import FeedbackRow
from stream_of_worship.admin.editor.app import LRCEditorApp
from stream_of_worship.admin.editor.screen import (
    StatusIndicator,
    _FeedbackSummary,
    _format_feedback_row,
)
from stream_of_worship.admin.editor.state import EditorState
from stream_of_worship.admin.services.playback import PlaybackState
from stream_of_worship.admin.services.lrc_parser import LRCLine
from stream_of_worship.admin.services.r2 import R2ObjectIdentity


class _PlaybackStub:
    """Same shape as the stub in test_lrc_editor_screen.py (copied, not
    imported: the tests dirs are not a package)."""

    state = PlaybackState.STOPPED
    position_seconds = 0.0
    duration_seconds = 120.0

    def set_callbacks(self, *args, **kwargs):
        pass

    def load(self, path):
        pass

    def stop(self, *args, **kwargs):
        pass


HASH = "hash-full-test"


def _feedback_row(**overrides) -> FeedbackRow:
    defaults = dict(
        email="admin@x.com",
        rating="happy",
        reason=None,
        note=None,
        created_at="2026-01-01",
        resolved_at=None,
    )
    defaults.update(overrides)
    return FeedbackRow(**defaults)


class StubDB:
    """Records upserts/deletes/resolves; serves a fixed row list."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.upserts = []
        self.deletes = []
        self.resolves = 0

    def get_recording_feedback_rows(self, content_hash):
        return list(self.rows)

    def upsert_recording_feedback(self, *args):
        self.upserts.append(args)
        content_hash, _user, rating, reason, note = args
        self.rows.insert(
            0,
            FeedbackRow(
                email="admin@x.com",
                rating=rating,
                reason=reason,
                note=note,
                created_at="2026-01-01",
                resolved_at=None,
            ),
        )

    def delete_recording_feedback(self, *args):
        self.deletes.append(args)

    def resolve_recording_feedback(self, content_hash):
        self.resolves += 1
        return len(self.rows)

    def get_recording_feedback_summary(self, content_hash):
        from stream_of_worship.admin.db.models import FeedbackSummary

        open_rows = [r for r in self.rows if r.resolved_at is None]
        reasons: dict[str, int] = {}
        for r in open_rows:
            if r.reason:
                reasons[r.reason] = reasons.get(r.reason, 0) + 1
        return FeedbackSummary(
            open_sad_count=sum(1 for r in open_rows if r.rating == "sad"),
            reason_counts=reasons,
        )


def _make_app(db: StubDB, feedback_user_id=7):
    lines = [
        LRCLine(time_seconds=float(i), text=f"Line {i + 1}", raw_timestamp="[00:00.00]")
        for i in range(3)
    ]
    state = EditorState(
        timed_lines=lines,
        preserved_lines=[],
        original_serialized="",
        original_preserved_lines=[],
        transcribed_identity=R2ObjectIdentity(exists=False),
    )
    app = LRCEditorApp(
        editor_state=state,
        playback_service=_PlaybackStub(),
        cache_dir=Path("/tmp"),
        r2_client=MagicMock(),
        db_client=db,
        hash_prefix="hashtest",
        original_transcribed_content=None,
        feedback_user_id=feedback_user_id,
        recording_content_hash=HASH,
    )
    return app


async def _bar_text(app) -> str:
    return str(app.screen.query_one(StatusIndicator).visual)


@pytest.mark.asyncio
async def test_happy_flow_upserts_and_updates_status_summary(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="stream_of_worship.admin.audit")
    db = StubDB([])
    app = _make_app(db)
    async with app.run_test() as pilot:
        await pilot.press("f")
        await pilot.pause()
        await pilot.press("h")
        for _ in range(5):
            await pilot.pause()
        assert db.upserts == [(HASH, 7, "happy", None, None)], db.upserts
        bar = await _bar_text(app)
        assert "Feedback: 👍1 👎0 · open 1" in bar, bar
        assert "audit event=feedback_add" in caplog.text
        assert "rating=happy" in caplog.text


@pytest.mark.asyncio
async def test_sad_flow_reason_then_enter_submits():
    """Reason digit stores the reason; Enter on the note submits (no note)."""
    db = StubDB([])
    app = _make_app(db)
    async with app.run_test() as pilot:
        await pilot.press("f", "s", "2")
        await pilot.pause()
        from stream_of_worship.admin.editor.screen import SadFeedbackDialog

        assert isinstance(app.screen, SadFeedbackDialog)
        assert app.screen._reason == "timing"  # digit chose, did not submit
        assert db.upserts == []
        await pilot.press("enter")
        for _ in range(5):
            await pilot.pause()
        assert db.upserts == [(HASH, 7, "sad", "timing", None)], db.upserts
        bar = await _bar_text(app)
        assert "Feedback: 👍0 👎1 · open 1" in bar, bar


@pytest.mark.asyncio
async def test_sad_flow_digits_reach_reason_bindings_not_note():
    """AUTO_FOCUS='': on open, the note Input must NOT be focused."""
    db = StubDB([])
    app = _make_app(db)
    async with app.run_test() as pilot:
        await pilot.press("f", "s")
        await pilot.pause()
        from textual.widgets import Input

        from stream_of_worship.admin.editor.screen import SadFeedbackDialog

        assert isinstance(app.screen, SadFeedbackDialog)
        assert not isinstance(app.focused, Input), repr(app.focused)
        await pilot.press("3")
        await pilot.pause()
        note = app.screen.query_one("#sad-note-input", Input)
        assert note.value == ""  # digit did not type into the note
        assert app.screen._reason == "wrong_text"
        assert isinstance(app.focused, Input)  # note focused after choose


@pytest.mark.asyncio
async def test_sad_flow_with_note_text():
    db = StubDB([])
    app = _make_app(db)
    async with app.run_test() as pilot:
        await pilot.press("f", "s", "3")
        await pilot.pause()
        for ch in "bridge timing off":
            await pilot.press(ch)
        await pilot.press("enter")
        for _ in range(5):
            await pilot.pause()
        assert db.upserts == [(HASH, 7, "sad", "wrong_text", "bridge timing off")], db.upserts


@pytest.mark.asyncio
async def test_retract_deletes_own_row(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="stream_of_worship.admin.audit")
    db = StubDB([_feedback_row(email="me@x.com")])
    app = _make_app(db)
    async with app.run_test() as pilot:
        await pilot.press("f", "r")
        for _ in range(5):
            await pilot.pause()
        assert db.deletes == [(HASH, 7)], db.deletes
        assert db.upserts == []
        assert "audit event=feedback_clear" in caplog.text
        assert "mode=retract" in caplog.text


@pytest.mark.asyncio
async def test_resolve_all_calls_resolve(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="stream_of_worship.admin.audit")
    db = StubDB([_feedback_row(rating="sad", reason="timing")])
    app = _make_app(db)
    async with app.run_test() as pilot:
        await pilot.press("f", "x")
        for _ in range(5):
            await pilot.pause()
        assert db.resolves == 1
        assert db.upserts == []
        assert "audit event=feedback_clear" in caplog.text
        assert "mode=resolve_all" in caplog.text


@pytest.mark.asyncio
async def test_feedback_disabled_when_no_user_id():
    db = StubDB([])
    app = _make_app(db, feedback_user_id=None)
    async with app.run_test() as pilot:
        await pilot.press("f")
        await pilot.pause()
        from stream_of_worship.admin.editor.screen import FeedbackDialog

        assert not isinstance(app.screen, FeedbackDialog)  # notify, no dialog
        await pilot.press("h")
        await pilot.pause()
        assert db.upserts == []


@pytest.mark.asyncio
async def test_summary_builder_formats_rows_and_handles_db_error():
    db = StubDB(
        [
            _feedback_row(rating="sad", reason="timing", note="x" * 50),
            _feedback_row(email="b@x.com", rating="happy", resolved_at="2026-01-02"),
        ]
    )
    app = _make_app(db)

    class BoomDB(StubDB):
        def get_recording_feedback_rows(self, content_hash):
            raise RuntimeError("db down")

    async with app.run_test() as pilot:
        await pilot.pause()
        text = _FeedbackSummary.summary_for(app.screen)
        assert "Feedback: 👍1 👎1 · open 1" in text
        assert "xxxx…" in text  # 40-char snippet with ellipsis
        assert "admin@x.com" in text

        app.screen.db_client = BoomDB([])
        text = _FeedbackSummary.summary_for(app.screen)
        assert "Feedback: unavailable" in text


def test_format_feedback_row_rendering():
    row = _feedback_row(rating="sad", reason="timing", note="off beat")
    line = _format_feedback_row(row)
    assert "👎" in line and "timing" in line and '"off beat"' in line
    resolved = _format_feedback_row(_feedback_row(resolved_at="2026-01-02"))
    assert "(resolved)" in resolved


@pytest.mark.asyncio
async def test_feedback_dialog_hint_line_not_markup_mangled():
    """Bare [h]/[s]/[r]/[x]/[c] parse as Rich/Textual styles; keys must render as text."""
    db = StubDB([_feedback_row(rating="sad", reason="timing")])
    app = _make_app(db)
    async with app.run_test() as pilot:
        await pilot.press("f")
        await pilot.pause()
        from textual.widgets import Label

        from stream_of_worship.admin.editor.screen import FeedbackDialog

        assert isinstance(app.screen, FeedbackDialog)
        labels = list(app.screen.query(Label))
        help_label = labels[-1]  # last Label yielded by compose = hint line
        text = str(help_label.visual)
        # Every action name must be present; no markup-tag residue in rendered text.
        for action in ("Happy", "Sad", "Remove mine", "Resolve all open", "Cancel"):
            assert action in text, text
        # The key letters themselves survive markup parsing (bare [s] etc. would not).
        assert "s Sad" in text and "r Remove mine" in text, text
