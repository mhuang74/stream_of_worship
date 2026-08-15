"""Unit tests for ComponentDetailPanel (v2 fixes).

Covers:
- Issue A: can_focus = True on ComponentDetailPanel
- Issue C: new section ordering (Song Info → Component → Confidence → Lifecycle)
- Issue D: _format_timestamp helper
"""

from pathlib import Path

import pytest

from stream_of_worship.admin.component_editor.app import ComponentEditorApp
from stream_of_worship.admin.component_editor.detail_panel import (
    ComponentDetailPanel,
    _format_timestamp,
)
from stream_of_worship.admin.component_editor.state import (
    ComponentEditorState,
    SongSession,
)
from stream_of_worship.admin.db.models import SongComponent
from stream_of_worship.admin.services.playback import PlaybackState

# =============================================================================
# Issue D: _format_timestamp (pure function, no app context needed)
# =============================================================================


def test_format_timestamp_strips_microseconds():
    assert _format_timestamp("2026-08-15T14:23:45.123456+00:00") == "2026-08-15 14:23:45 UTC"


def test_format_timestamp_handles_none():
    assert _format_timestamp(None) == "—"


def test_format_timestamp_handles_naive_datetime():
    assert _format_timestamp("2026-08-15T14:23:45") == "2026-08-15 14:23:45 UTC"


def test_format_timestamp_passes_through_garbage():
    assert _format_timestamp("not a date") == "not a date"


def test_format_timestamp_converts_non_utc_timezone():
    # +02:00 offset -> 12:23:45 UTC
    assert _format_timestamp("2026-08-15T14:23:45+02:00") == "2026-08-15 12:23:45 UTC"


# =============================================================================
# Issue A: can_focus = True (class attribute, no app context needed)
# =============================================================================


def test_detail_panel_can_focus():
    """ComponentDetailPanel.can_focus must be True so .focus() works."""
    assert ComponentDetailPanel.can_focus is True


# =============================================================================
# Helpers for app-context tests
# =============================================================================


class _PlaybackStub:
    state = PlaybackState.STOPPED
    position_seconds = 0.0
    duration_seconds = 180.0

    def set_callbacks(self, *args, **kwargs):
        pass

    def load(self, path: Path):
        pass

    def stop(self, *args, **kwargs):
        pass

    def seek(self, seconds: float):
        pass

    def pause(self):
        pass

    def play(self, *args, **kwargs):
        pass


def _make_component(
    role: str = "entry",
    cid: int = 1,
    start_time: float = 10.0,
    end_time: float = 20.0,
    created_at: str | None = "2026-08-15T14:23:45.123456+00:00",
    updated_at: str | None = "2026-08-15T14:23:45.123456+00:00",
) -> SongComponent:
    return SongComponent(
        id=cid,
        song_id="song_001",
        content_hash="abc123def4567",
        component_type="chorus",
        occurrence_index=1,
        role=role,
        start_time=start_time,
        end_time=end_time,
        bpm=80.0,
        key="G",
        groove_density=0.5,
        backbeat_strength=1.0,
        energy_level=-18.0,
        confidence=0.9,
        theme="讚美",
        vocal_posture="To God",
        theme_reasoning="LLM theme reasoning text",
        posture_reasoning="LLM posture reasoning text",
        created_at=created_at,
        updated_at=updated_at,
    )


def _make_session(comp: SongComponent | None = None) -> SongSession:
    if comp is None:
        comp = _make_component()
    return SongSession(
        song_id="song_001",
        song_title="Test Song",
        hash_prefix="abc123def456",
        audio_path="/tmp/audio.mp3",
        audio_duration=180.0,
        components={"entry": comp, "exit": comp},
        entry_component=comp,
        exit_component=comp,
    )


def _make_app(
    sessions: list[SongSession] | None = None,
    cache_dir: Path | None = None,
) -> tuple[ComponentEditorApp, ComponentEditorState]:
    if sessions is None:
        sessions = [_make_session()]
    state = ComponentEditorState(sessions=sessions)
    app = ComponentEditorApp(
        editor_state=state,
        playback_service=_PlaybackStub(),
        cache_dir=cache_dir,
        r2_client=None,
        db_client=None,
    )
    return app, state


def _detail_text(app: ComponentEditorApp) -> str:
    """Extract plain text from the detail panel."""
    panel = app.screen.query_one("#detail-panel", ComponentDetailPanel)
    content = panel.content
    if content is None:
        return ""
    if hasattr(content, "plain"):
        return content.plain
    return str(content)


# =============================================================================
# Issue C: Section ordering (requires app context for Static.update)
# =============================================================================


@pytest.mark.asyncio
async def test_section_ordering_component_before_confidence_before_lifecycle(tmp_path):
    """Issue C: rendered text must have Component section before Confidence
    Breakdown before Lifecycle."""
    app, _state = _make_app(cache_dir=tmp_path)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        # Switch to details mode
        app.screen._right_panel_mode = "details"
        app.screen._active_panel = "right"
        app.screen._apply_right_panel_mode()
        await pilot.pause()
        rendered = _detail_text(app)
        comp_idx = rendered.find("-- Component")
        conf_idx = rendered.find("-- Confidence Breakdown --")
        life_idx = rendered.find("-- Lifecycle --")
        assert comp_idx != -1, "Component section not found"
        assert conf_idx != -1, "Confidence Breakdown section not found"
        assert life_idx != -1, "Lifecycle section not found"
        assert comp_idx < conf_idx < life_idx


@pytest.mark.asyncio
async def test_section_ordering_editable_and_reasoning_inside_component(tmp_path):
    """Issue C: Editable and Reasoning sub-headers must appear between
    Component header and Confidence Breakdown header."""
    app, _state = _make_app(cache_dir=tmp_path)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        app.screen._right_panel_mode = "details"
        app.screen._active_panel = "right"
        app.screen._apply_right_panel_mode()
        await pilot.pause()
        rendered = _detail_text(app)
        comp_idx = rendered.find("-- Component")
        editable_idx = rendered.find("-- Editable --")
        reasoning_idx = rendered.find("-- Reasoning --")
        conf_idx = rendered.find("-- Confidence Breakdown --")
        assert comp_idx < editable_idx < reasoning_idx < conf_idx


@pytest.mark.asyncio
async def test_no_separate_editable_fields_header(tmp_path):
    """Issue C: the old '-- Editable Fields --' header should be gone."""
    app, _state = _make_app(cache_dir=tmp_path)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        app.screen._right_panel_mode = "details"
        app.screen._active_panel = "right"
        app.screen._apply_right_panel_mode()
        await pilot.pause()
        rendered = _detail_text(app)
        assert "-- Editable Fields --" not in rendered


@pytest.mark.asyncio
async def test_no_separate_reasoning_top_level_header(tmp_path):
    """Issue C: the old top-level '-- Reasoning --' header should be a sub-header."""
    app, _state = _make_app(cache_dir=tmp_path)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        app.screen._right_panel_mode = "details"
        app.screen._active_panel = "right"
        app.screen._apply_right_panel_mode()
        await pilot.pause()
        rendered = _detail_text(app)
        # The old top-level header was "-- Reasoning --\n" at column 0
        # The new sub-header is "  -- Reasoning --\n" (indented)
        assert "\n-- Reasoning --\n" not in rendered


# =============================================================================
# Issue D: Lifecycle timestamp formatting (requires app context)
# =============================================================================


@pytest.mark.asyncio
async def test_lifecycle_timestamps_formatted(tmp_path):
    """Issue D: Lifecycle timestamps should be formatted to nearest second."""
    app, _state = _make_app(cache_dir=tmp_path)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        app.screen._right_panel_mode = "details"
        app.screen._active_panel = "right"
        app.screen._apply_right_panel_mode()
        await pilot.pause()
        rendered = _detail_text(app)
        assert "2026-08-15 14:23:45 UTC" in rendered
        assert "2026-08-15T14:23:45.123456+00:00" not in rendered


@pytest.mark.asyncio
async def test_lifecycle_timestamps_none_shows_dash(tmp_path):
    """Issue D: None timestamps should show em-dash."""
    comp = _make_component(created_at=None, updated_at=None)
    app, _state = _make_app(sessions=[_make_session(comp=comp)], cache_dir=tmp_path)
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        app.screen._right_panel_mode = "details"
        app.screen._active_panel = "right"
        app.screen._apply_right_panel_mode()
        await pilot.pause()
        rendered = _detail_text(app)
        life_idx = rendered.find("-- Lifecycle --")
        life_section = rendered[life_idx:]
        assert "—" in life_section


# =============================================================================
# Issue C: get_editable_field_line_offset (pure method, no app context needed)
# =============================================================================


def test_get_editable_field_line_offset_theme():
    panel = ComponentDetailPanel()
    assert panel.get_editable_field_line_offset("theme") == 19


def test_get_editable_field_line_offset_vocal_posture():
    panel = ComponentDetailPanel()
    assert panel.get_editable_field_line_offset("vocal_posture") == 20


def test_get_editable_field_line_offset_groove_density():
    panel = ComponentDetailPanel()
    assert panel.get_editable_field_line_offset("groove_density") == 21


def test_get_editable_field_line_offset_energy_level():
    panel = ComponentDetailPanel()
    assert panel.get_editable_field_line_offset("energy_level") == 22


def test_get_editable_field_line_offset_unknown_field():
    panel = ComponentDetailPanel()
    assert panel.get_editable_field_line_offset("nonexistent") == 0
