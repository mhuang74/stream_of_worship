"""Tests for the ComponentHeroPanel widget (v4 NEW).

Tests D2: the Hero panel renders the highlighted component's transition-critical
fields + LLM reasoning, and updates on cursor move / edit.
"""

from pathlib import Path

import pytest

from stream_of_worship.admin.component_editor.app import ComponentEditorApp
from stream_of_worship.admin.component_editor.constants import HERO_PRIMARY_FIELDS
from stream_of_worship.admin.component_editor.state import (
    ComponentEditorState,
    SongSession,
)
from stream_of_worship.admin.db.models import SongComponent
from stream_of_worship.admin.services.playback import PlaybackState


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
    theme: str = "讚美",
    vocal_posture: str = "To God",
    bpm: float = 96.0,
    key: str = "G",
    energy_level: float = -12.0,
    groove_density: float = 0.80,
    backbeat_strength: float = 0.42,
    theme_reasoning: str | None = "主歌中提到受造之物齐声赞美造物主",
    posture_reasoning: str | None = "第二人称开头称呼神",
    start_time: float = 23.0,
    end_time: float = 135.0,
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
        bpm=bpm,
        key=key,
        groove_density=groove_density,
        backbeat_strength=backbeat_strength,
        energy_level=energy_level,
        confidence=0.9,
        theme=theme,
        vocal_posture=vocal_posture,
        theme_reasoning=theme_reasoning,
        posture_reasoning=posture_reasoning,
    )


def _make_session(
    entry: SongComponent | None = None,
    exit_comp: SongComponent | None = None,
) -> SongSession:
    if entry is None:
        entry = _make_component("entry", cid=1)
    if exit_comp is None:
        exit_comp = _make_component("exit", cid=2)
    return SongSession(
        song_id="song_001",
        song_title="Test Song",
        hash_prefix="abc123def456",
        audio_path="/tmp/audio.mp3",
        audio_duration=180.0,
        entry_component=entry,
        exit_component=exit_comp,
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


def _hero_text(app: ComponentEditorApp) -> str:
    """Extract plain text from the Hero panel."""
    panel = app.screen.query_one("ComponentHeroPanel")
    content = panel.content
    if content is None:
        return ""
    if hasattr(content, "plain"):
        return content.plain
    return str(content)


class TestComponentHeroPanel:
    """v4 D2: Hero panel rendering."""

    @pytest.mark.asyncio
    async def test_selected_row_0_shows_entry(self, tmp_path):
        app, state = _make_app(cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            state.selected_row = 0
            app.screen._refresh_hero()
            await pilot.pause()
            assert "ENTRY" in _hero_text(app)

    @pytest.mark.asyncio
    async def test_selected_row_1_shows_exit(self, tmp_path):
        app, state = _make_app(cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            state.selected_row = 1
            app.screen._refresh_hero()
            await pilot.pause()
            assert "EXIT" in _hero_text(app)

    @pytest.mark.asyncio
    async def test_none_component_shows_missing_message(self, tmp_path):
        entry = _make_component("entry", cid=1)
        session = SongSession(
            song_id="song_001",
            song_title="Test Song",
            hash_prefix="abc123def456",
            audio_path="/tmp/audio.mp3",
            audio_duration=180.0,
            entry_component=entry,
            exit_component=None,
        )
        app, state = _make_app(sessions=[session], cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            state.selected_row = 1  # exit is None
            app.screen._refresh_hero()
            await pilot.pause()
            assert "No exit Chorus component" in _hero_text(app)

    @pytest.mark.asyncio
    async def test_primary_row_contains_bpm(self, tmp_path):
        app, _state = _make_app(cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            assert "BPM 96" in _hero_text(app)

    @pytest.mark.asyncio
    async def test_primary_row_contains_key(self, tmp_path):
        app, _state = _make_app(cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            assert "Key G" in _hero_text(app)

    @pytest.mark.asyncio
    async def test_editable_row_shows_theme_and_posture(self, tmp_path):
        app, _state = _make_app(cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            text = _hero_text(app)
            assert "Theme: 讚美" in text
            assert "Vocal posture: To God" in text

    @pytest.mark.asyncio
    async def test_reasoning_rendered_full_text(self, tmp_path):
        app, _state = _make_app(cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            text = _hero_text(app)
            assert "主歌中提到受造之物齐声赞美造物主" in text
            assert "第二人称开头称呼神" in text

    @pytest.mark.asyncio
    async def test_reasoning_none_shows_placeholder(self, tmp_path):
        entry = _make_component("entry", theme_reasoning=None, posture_reasoning=None)
        session = _make_session(entry=entry, exit_comp=_make_component("exit"))
        app, _state = _make_app(sessions=[session], cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            assert "LLM did not supply reasoning" in _hero_text(app)

    @pytest.mark.asyncio
    async def test_hero_updates_after_edit(self, tmp_path):
        """D2 regression: after set_value + _refresh_hero, the editable row
        shows the new theme value."""
        app, state = _make_app(cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            state.set_value("entry", "theme", "敬拜")
            app.screen._refresh_hero()
            await pilot.pause()
            assert "Theme: 敬拜" in _hero_text(app)

    def test_hero_primary_fields_do_not_include_theme(self):
        """Sanity: theme is in the editable row, not the primary metrics row."""
        field_names = [f for f, _, _ in HERO_PRIMARY_FIELDS]
        assert "theme" not in field_names

    @pytest.mark.asyncio
    async def test_time_range_in_header(self, tmp_path):
        app, _state = _make_app(cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            text = _hero_text(app)
            assert "00:23" in text
            assert "02:15" in text

    # -- v6: Song Info row tests --

    @pytest.mark.asyncio
    async def test_hero_contains_song_title(self, tmp_path):
        from stream_of_worship.admin.db.models import Song

        song = Song(
            id="song_001",
            title="Amazing Grace",
            source_url="http://example.com",
            scraped_at="2024-01-01",
        )
        session = _make_session()
        session.song = song
        app, _state = _make_app(sessions=[session], cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            app.screen._refresh_hero()
            await pilot.pause()
            assert "Title: Amazing Grace" in _hero_text(app)

    @pytest.mark.asyncio
    async def test_hero_contains_artist(self, tmp_path):
        from stream_of_worship.admin.db.models import Song

        song = Song(
            id="song_001",
            title="Test Song",
            source_url="http://example.com",
            scraped_at="2024-01-01",
            composer="John Newton",
        )
        session = _make_session()
        session.song = song
        app, _state = _make_app(sessions=[session], cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            app.screen._refresh_hero()
            await pilot.pause()
            assert "Artist: John Newton" in _hero_text(app)

    @pytest.mark.asyncio
    async def test_hero_contains_album(self, tmp_path):
        from stream_of_worship.admin.db.models import Song

        song = Song(
            id="song_001",
            title="Test Song",
            source_url="http://example.com",
            scraped_at="2024-01-01",
            album_name="Hymns Vol 1",
        )
        session = _make_session()
        session.song = song
        app, _state = _make_app(sessions=[session], cache_dir=tmp_path)
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.pause()
            app.screen._refresh_hero()
            await pilot.pause()
            assert "Album: Hymns Vol 1" in _hero_text(app)
