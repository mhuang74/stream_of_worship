"""Tests for LyricsPanel scrolling behavior (auto-scroll + manual scroll).

Covers:
- Auto-scroll to center highlighted line during playback
- Manual Up/Down scroll when lyrics panel is focused
- Short songs don't cause unwanted scrolling
- No scroll when highlighted_index is -1 (scroll to top)
- Up/Down are no-ops when table (left panel) is focused
- Up/Down are no-ops when right panel is hidden
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stream_of_worship.admin.component_editor.app import ComponentEditorApp
from stream_of_worship.admin.component_editor.lyrics_panel import LyricsPanel
from stream_of_worship.admin.component_editor.state import (
    ComponentEditorState,
    SongSession,
)
from stream_of_worship.admin.db.models import SongComponent
from stream_of_worship.admin.services.lrc_parser import (
    LRCLine,
    LRCParsedContent,
    LRCPreservedLine,
)
from stream_of_worship.admin.services.playback import PlaybackState


class _PlaybackStub:
    def __init__(self):
        self._state = PlaybackState.STOPPED
        self._position_seconds = 0.0
        self.duration_seconds = 180.0

    @property
    def state(self):
        return self._state

    @property
    def is_playing(self):
        return self._state == PlaybackState.PLAYING

    @property
    def position_seconds(self):
        return self._position_seconds

    @position_seconds.setter
    def position_seconds(self, val):
        self._position_seconds = val

    def set_callbacks(self, *args, **kwargs):
        pass

    def load(self, path: Path):
        pass

    def stop(self, *args, **kwargs):
        pass

    def toggle_play_pause(self):
        pass

    def skip_forward(self, seconds: float):
        pass

    def skip_backward(self, seconds: float):
        pass

    def seek(self, seconds: float):
        self._position_seconds = seconds

    def pause(self):
        self._state = PlaybackState.PAUSED

    def play(self, start_seconds: float = 0.0, file_path=None) -> bool:
        self._state = PlaybackState.PLAYING
        self._position_seconds = start_seconds
        return True


def _make_component(
    role: str = "entry",
    cid: int = 1,
) -> SongComponent:
    return SongComponent(
        id=cid,
        song_id="song_001",
        content_hash="abc123def4567",
        component_type="chorus",
        occurrence_index=1,
        role=role,
        start_time=10.0,
        end_time=20.0,
        bpm=80.0,
        key="G",
        groove_density=0.5,
        backbeat_strength=1.0,
        energy_level=-18.0,
        confidence=0.9,
        theme="讚美",
        vocal_posture="To God",
    )


def _make_session() -> SongSession:
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


def _make_app() -> tuple[ComponentEditorApp, ComponentEditorState]:
    state = ComponentEditorState(sessions=[_make_session()])
    app = ComponentEditorApp(
        editor_state=state,
        playback_service=_PlaybackStub(),
        cache_dir=Path("/tmp/nonexistent_cache_dir_for_tests"),
        r2_client=MagicMock(),
        db_client=MagicMock(),
    )
    return app, state


async def _setup_lyrics(
    pilot,
    parsed: LRCParsedContent,
    highlighted_index: int = -1,
) -> LyricsPanel:
    """Populate state.lrc_parsed and refresh the lyrics panel.

    This prevents the async LRC prefetch worker from overwriting test data
    when it completes during pilot.pause(). Also sets the playback position
    to match the highlighted_index so the 5Hz timer doesn't reset it.
    """
    panel = pilot.app.screen.query_one("#lyrics-panel", LyricsPanel)
    state = pilot.app.screen.state
    song_id = state.current.song_id
    state.lrc_parsed[song_id] = parsed
    state.lrc_prefetch_in_progress = False
    if highlighted_index >= 0 and highlighted_index < len(parsed.timed_lines):
        pilot.app.screen.playback._position_seconds = (
            parsed.timed_lines[highlighted_index].time_seconds
        )
    else:
        pilot.app.screen.playback._position_seconds = -1.0
    panel.update_lrc(parsed, state.current.song_title, highlighted_index=highlighted_index)
    await pilot.pause()
    await pilot.pause()
    await pilot.pause()
    return panel


def _make_parsed(
    num_lines: int = 30,
    preserved: list[LRCPreservedLine] | None = None,
) -> LRCParsedContent:
    """Build a parsed LRC with many timed lines for scroll testing."""
    if preserved is None:
        preserved = [
            LRCPreservedLine(raw="[ti:Test Song]", tag="ti", value="Test Song"),
            LRCPreservedLine(raw="[ar:Test Artist]", tag="ar", value="Test Artist"),
        ]
    timed = [
        LRCLine(
            time_seconds=float(i * 5),
            text=f"Line {i}",
            raw_timestamp=f"[00:{i * 5 // 60:02d}.{i * 5 % 60:02d}]",
        )
        for i in range(num_lines)
    ]
    return LRCParsedContent(
        timed_lines=timed,
        preserved_lines=preserved,
        raw_content="",
    )


# =============================================================================
# _compute_highlighted_line_y (pure method, no app context needed)
# =============================================================================


def test_compute_highlighted_line_y_with_metadata():
    panel = LyricsPanel()
    panel._parsed = _make_parsed(num_lines=10)
    # 2 preserved lines + 1 blank separator = 3 header lines
    # highlighted_index=5 -> y = 3 + 5 = 8
    assert panel._compute_highlighted_line_y(5) == 8


def test_compute_highlighted_line_y_no_metadata():
    panel = LyricsPanel()
    panel._parsed = _make_parsed(num_lines=10, preserved=[])
    # 0 preserved lines, no blank separator added
    # highlighted_index=5 -> y = 0 + 5 = 5
    assert panel._compute_highlighted_line_y(5) == 5


def test_compute_highlighted_line_y_no_parsed():
    panel = LyricsPanel()
    panel._parsed = None
    assert panel._compute_highlighted_line_y(5) == 0


def test_compute_highlighted_line_y_zero_index():
    panel = LyricsPanel()
    panel._parsed = _make_parsed(num_lines=10)
    # 2 preserved + 1 blank = 3 header lines, index 0 -> y = 3
    assert panel._compute_highlighted_line_y(0) == 3


# =============================================================================
# Auto-scroll tests (require app context)
# =============================================================================


@pytest.mark.asyncio
async def test_lyrics_auto_scroll_to_highlighted_line():
    """When highlight changes to a line below viewport, panel scrolls to center it."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=25)
        # After auto-scroll, scroll_y should be > 0 (content was scrolled down)
        assert panel.scroll_y > 0


@pytest.mark.asyncio
async def test_lyrics_no_scroll_without_highlight():
    """When highlighted_index is -1, panel scrolls to top."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=10)
        assert panel.scroll_y > 0
        # Now reset with no highlight
        panel.update_lrc(parsed, "Test Song", highlighted_index=-1)
        pilot.app.screen.playback._position_seconds = -1.0
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        assert panel.scroll_y == 0


@pytest.mark.asyncio
async def test_lyrics_scroll_preserved_for_short_songs():
    """Short lyrics that fit in viewport don't cause unwanted scrolling."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=5)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=2)
        # Short content fits in viewport, no scroll needed
        assert panel.scroll_y == 0


# =============================================================================
# Manual scroll tests (Up/Down keys)
# =============================================================================


@pytest.mark.asyncio
async def test_lyrics_manual_scroll_up_down():
    """Up/Down keys scroll the lyrics panel when focused in lyrics mode."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=20)
        initial_scroll = panel.scroll_y
        assert initial_scroll > 0
        # Scroll down manually — check immediately before layout can reset
        panel.scroll_line_down()
        assert panel.scroll_y == initial_scroll + 1
        # Scroll up manually
        panel.scroll_line_up()
        assert panel.scroll_y == initial_scroll


@pytest.mark.asyncio
async def test_lyrics_manual_scroll_up_clamped_at_zero():
    """Scrolling up at the top stays at 0."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=5)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=-1)
        assert panel.scroll_y == 0
        panel.scroll_line_up()
        await pilot.pause()
        assert panel.scroll_y == 0


@pytest.mark.asyncio
async def test_up_down_noop_when_table_focused():
    """Up/Down do not scroll lyrics when left panel (table) is focused."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=20)
        initial_scroll = panel.scroll_y
        # Focus left panel (table)
        app.screen._active_panel = "left"
        # Trigger up/down actions
        app.screen.action_detail_focus_up()
        await pilot.pause()
        assert panel.scroll_y == initial_scroll
        app.screen.action_detail_focus_down()
        await pilot.pause()
        assert panel.scroll_y == initial_scroll


@pytest.mark.asyncio
async def test_up_down_noop_in_hidden_mode():
    """Up/Down are no-ops when right panel is hidden."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=20)
        initial_scroll = panel.scroll_y
        # Set right panel mode to hidden
        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "hidden"
        app.screen.action_detail_focus_up()
        await pilot.pause()
        assert panel.scroll_y == initial_scroll
        app.screen.action_detail_focus_down()
        await pilot.pause()
        assert panel.scroll_y == initial_scroll


@pytest.mark.asyncio
async def test_up_down_scrolls_lyrics_in_lyrics_mode():
    """Up/Down actions scroll lyrics panel when right panel is in lyrics mode."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=20)
        initial_scroll = panel.scroll_y
        # Set to lyrics mode + right panel focused
        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "lyrics"
        app.screen.action_detail_focus_down()
        assert panel.scroll_y == initial_scroll + 1
        app.screen.action_detail_focus_up()
        assert panel.scroll_y == initial_scroll


# =============================================================================
# Regression tests: scroll persistence after layout cycle
# =============================================================================


@pytest.mark.asyncio
async def test_lyrics_manual_scroll_persists_after_pause():
    """Manual scroll_line_down should persist after pilot.pause().

    Regression: set_reactive bypassed watch_scroll_y → widget was never marked
    dirty → compositor did not re-render with the new scroll offset → scroll
    appeared to not work.
    """
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=20)
        initial_scroll = panel.scroll_y
        assert initial_scroll > 0
        panel.scroll_line_down()
        assert panel.scroll_y == initial_scroll + 1
        await pilot.pause()
        assert panel.scroll_y == initial_scroll + 1


@pytest.mark.asyncio
async def test_lyrics_keyboard_scroll_down_persists():
    """Pressing Down on the lyrics panel scrolls and persists after pause."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=20)
        initial_scroll = panel.scroll_y
        assert initial_scroll > 0
        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "lyrics"
        panel.focus()
        await pilot.press("down")
        await pilot.pause()
        assert panel.scroll_y == initial_scroll + 1


@pytest.mark.asyncio
async def test_lyrics_max_scroll_y_matches_virtual_size():
    """max_scroll_y should match virtual_size-based computation, not _compute_content_height.

    Regression: custom max_scroll_y override used _compute_content_height() which
    doesn't account for line wrapping, resulting in a smaller max_scroll_y than
    the actual scrollable range.
    """
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=-1)
        assert panel.virtual_size.height > 0
        expected_max = max(
            0,
            panel.virtual_size.height
            - (panel.container_size.height - panel.scrollbar_size_horizontal),
        )
        assert panel.max_scroll_y == expected_max
        panel.scroll_to(y=panel.max_scroll_y, animate=False, immediate=True)
        await pilot.pause()
        assert panel.scroll_y == panel.max_scroll_y


# =============================================================================
# v2 Regression tests: rendered content must shift on scroll (not just scroll_y state)
# =============================================================================


@pytest.mark.asyncio
async def test_lyrics_rendered_content_shifts_on_scroll_down():
    """Regression v2: rendered strips must shift when scroll_y changes.

    v1 bug: `panel.scroll_y` was being incremented correctly (so the scrollbar
    thumb visibly moved and `scroll_y == X` assertions passed), but
    `Widget.render_line(y)` returned `self._render_cache.lines[y]` verbatim
    with no `scroll_offset.y` offset, so the first visible line of the panel
    stayed pinned to lyric line 0 regardless of scroll_y. Long songs could
    never be visually scrolled past the first viewport.
    """
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=-1)
        assert panel.scroll_y == 0

        pre_strips = panel.render_lines(panel.outer_size.region)
        pre_text = "\n".join(strip.text for strip in pre_strips)
        assert "Line 0" in pre_text

        for _ in range(5):
            panel.scroll_line_down()
        await pilot.pause()
        assert panel.scroll_y == 5

        post_strips = panel.render_lines(panel.outer_size.region)
        post_text = "\n".join(strip.text for strip in post_strips)
        assert "Line 5" in post_text
        assert "Line 0" not in post_text


@pytest.mark.asyncio
async def test_lyrics_rendered_content_shifts_on_scroll_up():
    """Counterpart: scrolling back up returns visible strips to lyric line 0."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=-1)

        for _ in range(5):
            panel.scroll_line_down()
        await pilot.pause()
        for _ in range(5):
            panel.scroll_line_up()
        await pilot.pause()
        assert panel.scroll_y == 0

        stripped = panel.render_lines(panel.outer_size.region)
        text = "\n".join(strip.text for strip in stripped)
        assert "Line 0" in text


@pytest.mark.asyncio
async def test_lyrics_keyboard_scroll_shifts_rendered_content():
    """v1 regression: pressing Down arrow shifts the visible strips,
    not just the scroll_y state.
    """
    app, _state = _make_app()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=40)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=20)
        await pilot.pause()
        initial_strips = panel.render_lines(panel.outer_size.region)
        initial_first = initial_strips[0].text

        app.screen._active_panel = "right"
        app.screen._right_panel_mode = "lyrics"
        panel.focus()
        for _ in range(3):
            await pilot.press("down")
        await pilot.pause()

        new_strips = panel.render_lines(panel.outer_size.region)
        new_first = new_strips[0].text
        assert new_first != initial_first


@pytest.mark.asyncio
async def test_lyrics_render_line_blank_beyond_content():
    """render_line(y) for y beyond content returns blank, not crashed."""
    app, _state = _make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        parsed = _make_parsed(num_lines=5)
        panel = await _setup_lyrics(pilot, parsed, highlighted_index=-1)
        content_h = len(panel._content_strips)
        empty = panel.render_line(content_h)
        from textual.strip import Strip

        assert isinstance(empty, Strip)
        assert empty.cell_length == panel.scrollable_content_region.width or empty.cell_length == 0
