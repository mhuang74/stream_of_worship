"""Application state for sow-app.

Manages reactive state for the TUI with observable properties.
Provides centralized state management for screens.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional

from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.services.catalog import SongWithRecording


class AppScreen(Enum):
    """Available screens in the app."""

    SONGSET_LIST = auto()
    BROWSE = auto()
    SONGSET_EDITOR = auto()
    TRANSITION_DETAIL = auto()
    EXPORT_PROGRESS = auto()
    SETTINGS = auto()


@dataclass
class AppState:
    """Reactive application state.

    Provides observable properties that screens can watch for changes.
    Centralizes all mutable application state.

    Attributes:
        current_screen: Currently active screen
        previous_screen: Screen to return to (for back navigation)
        selected_songset: Currently selected songset
        selected_item: Currently selected songset item
        current_songset_items: Items in the current songset
        selected_song: Currently selected song in browse
        search_query: Current search query
        is_loading: Whether an async operation is in progress
        error_message: Current error message to display
    """

    # Navigation
    current_screen: AppScreen = AppScreen.SONGSET_LIST
    previous_screen: Optional[AppScreen] = None

    # Songset management
    selected_songset: Optional[Songset] = None
    selected_item: Optional[SongsetItem] = None
    current_songset_items: list[SongsetItem] = field(default_factory=list)

    # Browse
    selected_song: Optional[SongWithRecording] = None
    search_query: str = ""

    # UI state
    is_loading: bool = False
    error_message: Optional[str] = None

    # Callbacks for state changes
    _listeners: dict[str, list[Callable]] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize listener dictionary."""
        if self._listeners is None:
            self._listeners = {}

    def add_listener(self, property_name: str, callback: Callable) -> None:
        """Add a listener for a property change.

        Args:
            property_name: Name of the property to watch
            callback: Function to call when property changes
        """
        if property_name not in self._listeners:
            self._listeners[property_name] = []
        self._listeners[property_name].append(callback)

    def remove_listener(self, property_name: str, callback: Callable) -> None:
        """Remove a property change listener.

        Args:
            property_name: Name of the property
            callback: Callback to remove
        """
        if property_name in self._listeners:
            self._listeners[property_name] = [
                cb for cb in self._listeners[property_name] if cb != callback
            ]

    def _notify(self, property_name: str, value) -> None:
        """Notify listeners of a property change."""
        if property_name in self._listeners:
            for callback in self._listeners[property_name]:
                try:
                    callback(value)
                except Exception:
                    pass

    def navigate_to(self, screen: AppScreen) -> None:
        """Navigate to a screen, saving current for back navigation.

        Args:
            screen: Screen to navigate to
        """
        self.previous_screen = self.current_screen
        self.current_screen = screen
        self._notify("current_screen", screen)

    def navigate_back(self) -> bool:
        """Navigate back to the previous screen.

        Returns:
            True if navigation occurred
        """
        if self.previous_screen:
            self.current_screen = self.previous_screen
            self.previous_screen = None
            self._notify("current_screen", self.current_screen)
            return True
        return False

    def select_songset(self, songset: Optional[Songset]) -> None:
        """Select a songset.

        Args:
            songset: Songset to select (None to clear)
        """
        self.selected_songset = songset
        self.current_songset_items = []
        self._notify("selected_songset", songset)

    def select_item(self, item: Optional[SongsetItem]) -> None:
        """Select a songset item.

        Args:
            item: Item to select (None to clear)
        """
        self.selected_item = item
        self._notify("selected_item", item)

    def update_songset_items(self, items: list[SongsetItem]) -> None:
        """Update the current songset items.

        Args:
            items: New list of items
        """
        self.current_songset_items = items
        self._notify("current_songset_items", items)

    def select_song(self, song: Optional[SongWithRecording]) -> None:
        """Select a song from the catalog.

        Args:
            song: Song to select (None to clear)
        """
        self.selected_song = song
        self._notify("selected_song", song)

    def set_search_query(self, query: str) -> None:
        """Update the search query.

        Args:
            query: New search query
        """
        self.search_query = query
        self._notify("search_query", query)

    def set_loading(self, loading: bool) -> None:
        """Set loading state.

        Args:
            loading: Whether loading
        """
        self.is_loading = loading
        self._notify("is_loading", loading)

    def set_error(self, message: Optional[str]) -> None:
        """Set error message.

        Args:
            message: Error message (None to clear)
        """
        self.error_message = message
        self._notify("error_message", message)

    def clear_error(self) -> None:
        """Clear the current error message."""
        self.set_error(None)
