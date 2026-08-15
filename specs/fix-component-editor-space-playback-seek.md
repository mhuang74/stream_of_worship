# Fix: Component Editor SPACE Playback Does Not Seek to Component Start

## Overview

In the Component Metadata Editor TUI (`sow-admin audio review-components`),
pressing SPACE to start playback from the highlighted component does **not**
jump to the component's `start_time`. Audio restarts from `0:00` regardless of
which component is highlighted.

A previous fix (screen.py:1170-1190) simplified
`action_toggle_playback_for_component` to always seek to `comp.start_time`
when starting playback (removing the inside `[start, end]` check). That fix
is correct in intent but **does not work** because of a logical ordering bug
between `seek()` and `play()` in `PlaybackService`.

---

## Root Cause

### The broken two-step: `seek()` then `play()`

`action_toggle_playback_for_component` (screen.py:1170-1189) currently does:

```python
comp = self.state.get_selected_component()
if comp is not None and comp.start_time is not None:
    self.playback.seek(comp.start_time)   # (A) sets _position_seconds = start_time
    self._update_lyrics_highlight()
self.playback.play()                       # (B) play() with default start_seconds=0.0
```

### Why `play()` defeats `seek()`

`PlaybackService.play()` (`services/playback.py:200-257`) has signature
`play(self, file_path=None, start_seconds: float = 0.0)`. When called with no
arguments, `start_seconds` defaults to `0.0`. Inside `play()`:

1. Line 209: `target_position = start_seconds` → `0.0`
2. Line 212: `self.stop(clear_source=False)` → zeroes `_position_seconds`
3. Line 222: `self._position_seconds = target_position` → `0.0`
4. Line 227: `start_sample_index = int(self._position_seconds * ...)` → sample 0

So **`play()` silently overwrites the position set by `seek()`** and audio
restarts from 0, regardless of what `seek()` just did. This is deterministic
(no race), affecting both `STOPPED` and `PAUSED` states.

### Why `seek()` alone doesn't help

`PlaybackService.seek()` (`services/playback.py:333-349`):

```python
def seek(self, position_seconds: float) -> bool:
    with self._lock:
        if not self._current_file or not self._source:
            return False
        position_seconds = max(0.0, min(position_seconds, self._duration_seconds))
        was_playing = self._state == PlaybackState.PLAYING

    if was_playing:
        return self.play(start_seconds=position_seconds)
    else:
        with self._lock:
            self._position_seconds = position_seconds
        ...
        return True
```

When not playing, `seek()` only writes `_position_seconds`. It does **not**
start playback. The subsequent bare `play()` then ignores that value.

### The correct pattern already exists

`PlaybackService.resume()` (`services/playback.py:292-302`) does it right:

```python
def resume(self) -> bool:
    with self._lock:
        if self._state != PlaybackState.PAUSED:
            return False
        saved_position = self._position_seconds
        current_file = self._current_file
    if not current_file:
        return False
    return self.play(start_seconds=saved_position)   # pass position TO play()
```

And `seek()` itself delegates to `play(start_seconds=...)` when already
playing (line 341). The fix is to follow the same pattern in the action
handler.

---

## Why Tests Didn't Catch It

The test stub `_PlaybackStub` (`tests/admin/component_editor/test_screen.py:29-86`)
has three independent counters (`seek_calls`, `play_calls`, `pause_calls`)
with **no combined ordered event log**. Its `play()` method is a no-op that
does not mutate `position_seconds`:

```python
def play(self, *args, **kwargs):
    self.play_calls += 1
```

Tests assert "seek(10.0) was called AND play() was called" but would pass
equally if the order were `play(); seek(10.0)`. The stub's `play()` doesn't
reset `position_seconds` the way the real one does, so the bug is invisible
to the test suite.

Additionally, the D3 suite (lines 680-752) and "Issue B" suite (lines
1190-1245) cover nearly identical scenarios, duplicating maintenance cost
without adding coverage.

---

## Fix Plan

### Part 1: Fix `action_toggle_playback_for_component` in `screen.py`

**File:** `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py`
**Lines:** 1170-1189

Replace the broken `seek()` + `play()` two-step with a single
`play(start_seconds=...)` call that matches `resume()`'s pattern:

```python
def action_toggle_playback_for_component(self) -> None:
    """Play or pause the song, anchored to the highlighted component.

    - If playing: pause.
    - If paused/stopped: start playback from the highlighted component's
      start_time (so SPACE reliably restarts the component).
    - If no component: best-effort play() from the beginning.
    """
    if self._guard_active_edit():
        return
    if self.playback.is_playing:
        self.playback.pause()
        return
    comp = self.state.get_selected_component()
    start = comp.start_time if (comp is not None and comp.start_time is not None) else 0.0
    self.playback.play(start_seconds=start)
    self._update_lyrics_highlight()
```

**Why this works:**
- `play(start_seconds=...)` directly sets `_position_seconds` and computes
  `start_sample_index` from it (playback.py lines 209, 222, 227) — no
  intervening `stop()`/`seek()` pair that could clobber the value.
- Matches the existing `resume()` implementation pattern
  (`play(start_seconds=saved_position)`).
- When no component is highlighted, `start=0.0` collapses to the old behavior.

**No other production changes needed.** `PlaybackService.play()` already
supports `start_seconds`; we're just using the existing contract correctly.

---

### Part 2: Strengthen `_PlaybackStub`

**File:** `ops/admin-cli/tests/admin/component_editor/test_screen.py`
**Lines:** 29-86

Make the stub's `play()` mirror the real `PlaybackService.play()`'s
`start_seconds` semantics, so tests would have caught this bug:

```python
class _PlaybackStub:
    """Minimal playback stub for screen tests."""

    def __init__(self):
        self._state = PlaybackState.STOPPED
        self._position_seconds = 0.0
        self.duration_seconds = 180.0
        self.seek_calls: list[float] = []
        self.play_calls: int = 0
        self.pause_calls: int = 0
        self.play_start_seconds: list[float] = []  # NEW
        self.set_state_on_play: PlaybackState | None = PlaybackState.PLAYING  # NEW

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, val):
        self._state = val

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
        self.seek_calls.append(seconds)
        self._position_seconds = seconds

    def pause(self):
        self.pause_calls += 1
        self._state = PlaybackState.PAUSED  # NEW — enables paused→play lifecycle

    def play(self, start_seconds: float = 0.0, file_path=None) -> bool:
        # Mirror real PlaybackService: play() resets position to start_seconds.
        self.play_calls += 1
        self.play_start_seconds.append(start_seconds)  # NEW
        self._position_seconds = start_seconds          # NEW — would have caught the bug
        if self.set_state_on_play is not None:
            self._state = self.set_state_on_play        # NEW — enables lifecycle tests
        return True
```

**Key changes:**
- `play()` accepts `start_seconds` (as a keyword) and sets `_position_seconds`
  to it — mirroring real `PlaybackService.play()` behavior. Tests asserting
  `playback.position_seconds == 10.0` after `action_toggle_playback_for_component()`
  would have caught the original bug.
- `play()` and `pause()` actually mutate state, enabling true lifecycle tests
  (play→pause→play).
- New `play_start_seconds` list records arguments to `play()`, so tests can
  assert the exact arg passed.

---

### Part 3: Consolidate duplicated test suites

The D3 suite (lines 680-752) and "Issue B" suite (lines 1190-1245) cover
nearly identical scenarios:

| D3 suite | Issue B suite | Scenario |
|---|---|---|
| `test_d3_space_seeks_to_component_start_then_plays` (686) | `test_space_always_seeks_to_component_start_when_paused_inside` (1195) | STOPPED outside/inside [start,end] |
| `test_d3_space_when_playing_pauses` (717) | `test_space_playing_pauses_without_seek` (1211) | PLAYING → pause |
| `test_d3_space_both_components_none_plays_without_seek` (731) | `test_space_no_component_plays_without_seek` (1225) | No component → play |

**Plan:**
- **Delete** the "Issue B" suite (lines 1188-1245) — the D3 suite is more
  comprehensive (it has both the outside-range and inside-range cases).
- **Rewrite** the D3 suite at lines 680-752 to assert against the new stub
  fields and add new test cases:

```python
# =============================================================================
# v4 D3 regression: toggle_playback_for_component semantics
# =============================================================================


@pytest.mark.asyncio
async def test_space_seeks_to_component_start_then_plays():
    """Stopped outside [start,end] -> play(start_seconds=start_time)."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    playback.position_seconds = 0.0  # outside [10.0, 20.0]
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0  # entry component, start_time=10.0
        app.screen.action_toggle_playback_for_component()
        assert playback.play_calls == 1
        assert playback.pause_calls == 0
        assert playback.play_start_seconds == [10.0]  # NEW: arg-level assertion
        assert playback.position_seconds == 10.0     # NEW: would have caught the original bug


@pytest.mark.asyncio
async def test_space_inside_component_still_seeks_to_start():
    """Stopped inside [start,end] -> still play(start_seconds=start_time)
    (SPACE reliably restarts the highlighted component)."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    playback.position_seconds = 15.0  # inside [10.0, 20.0]
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        app.screen.action_toggle_playback_for_component()
        assert playback.play_calls == 1
        assert playback.play_start_seconds == [10.0]
        assert playback.position_seconds == 10.0


@pytest.mark.asyncio
async def test_space_when_playing_pauses():
    """Playing -> pause, no play()."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.PLAYING
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        app.screen.action_toggle_playback_for_component()
        assert playback.pause_calls == 1
        assert playback.play_calls == 0
        assert playback.play_start_seconds == []


@pytest.mark.asyncio
async def test_space_no_component_plays_from_zero():
    """No component highlighted -> play(start_seconds=0.0)."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    session = SongSession(
        song_id="song_001",
        song_title="Test Song",
        hash_prefix="abc123def456",
        audio_path="/tmp/audio.mp3",
        audio_duration=180.0,
        components={},
        entry_component=None,
        exit_component=None,
    )
    app, state = _make_app(sessions=[session], playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        app.screen.action_toggle_playback_for_component()
        assert playback.play_calls == 1
        assert playback.play_start_seconds == [0.0]  # NEW: explicit zero arg
        assert playback.position_seconds == 0.0


@pytest.mark.asyncio
async def test_space_paused_then_space_plays_from_component_start():
    """Lifecycle: PAUSED -> SPACE -> play from start; PLAYING -> SPACE -> pause."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.PAUSED
    playback.position_seconds = 15.0  # inside [10.0, 20.0]
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        # First SPACE: PAUSED -> play(start_seconds=10.0)
        app.screen.action_toggle_playback_for_component()
        assert playback.play_calls == 1
        assert playback.play_start_seconds == [10.0]
        assert playback.is_playing  # stub now mutates state
        # Second SPACE: PLAYING -> pause
        app.screen.action_toggle_playback_for_component()
        assert playback.pause_calls == 1
        assert playback.play_calls == 1


@pytest.mark.asyncio
async def test_space_with_edit_active_is_noop():
    """Edit overlay active -> SPACE is a no-op."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 0
        # Simulate active edit guard returning True
        app.screen._guard_active_edit = lambda: True
        app.screen.action_toggle_playback_for_component()
        assert playback.play_calls == 0
        assert playback.pause_calls == 0
        assert playback.seek_calls == []


@pytest.mark.asyncio
async def test_space_uses_exit_component_start_time():
    """Highlight exit component -> play from its start_time."""
    playback = _PlaybackStub()
    playback.state = PlaybackState.STOPPED
    app, state = _make_app(playback=playback)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        state.selected_row = 1  # exit component
        app.screen.action_toggle_playback_for_component()
        assert playback.play_start_seconds == [10.0]  # _make_component default
        assert playback.position_seconds == 10.0
```

**New test coverage:**
1. Assert `play_start_seconds == [10.0]` — would catch the bug immediately if
   anyone reintroduces `play()` without `start_seconds`.
2. Assert `position_seconds == 10.0` — would catch the bug even if
   `play_start_seconds` weren't tracked.
3. Add a lifecycle test (play→pause→play) — was missing.
4. Add an active-edit guard test — was missing.
5. Test the `exit` component — only `entry` (row 0) was tested before.

---

## Files Affected

| File | Change |
|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/component_editor/screen.py` | Rewrite `action_toggle_playback_for_component` (lines 1170-1189) to use `play(start_seconds=...)` |
| `ops/admin-cli/tests/admin/component_editor/test_screen.py` | Strengthen `_PlaybackStub` (lines 29-86); rewrite D3 test suite (lines 680-752); delete "Issue B" suite (lines 1188-1245) |

No changes to `services/playback.py` — its API already supports `start_seconds`.

---

## Verification

```bash
# Run the affected test module
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest \
    tests/admin/component_editor/test_screen.py -v -k "space"

# Full admin-cli test suite
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v
```

---

## Out of Scope

- `PlaybackService.play()` / `seek()` / `resume()` implementations are
  unchanged — they already have the correct API contract.
- `action_jump_to_component` (screen.py:1201-1210) already calls
  `seek()` correctly (it does not call `play()` afterward).
- Other action handlers that use `playback.play()` without `start_seconds`
  are not affected (they intentionally start from the beginning).
