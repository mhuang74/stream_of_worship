"""
Metadata loader for song and section data.

Loads JSON metadata from poc_output_allinone directory and creates
Song and Section data models.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from ..models import Song, Section


class MetadataLoader:
    """
    Loads song and section metadata from JSON files.

    Sources:
        - section_features.json: Section-level data (tempo, key, energy, timestamps)
        - poc_full_results.json: Song-level data (BPM, key, duration, beats)
    """

    def __init__(self, base_dir: Path = None):
        """
        Initialize metadata loader.

        Args:
            base_dir: Base directory containing metadata files
                     (default: poc_output_allinone)
        """
        if base_dir is None:
            # Default to poc_output_allinone in project root
            base_dir = Path(__file__).parent.parent.parent / "poc_output_allinone"

        self.base_dir = Path(base_dir)
        self.section_features_path = self.base_dir / "section_features.json"
        self.poc_full_results_path = self.base_dir / "poc_full_results.json"
        self.poc_audio_dir = self.base_dir.parent / "poc_audio"

        # Cached data
        self._section_features = None
        self._poc_full_results = None
        self._songs = None

    def load_section_features(self) -> dict:
        """
        Load section features from JSON.

        Returns:
            Dictionary with section features data
        """
        if self._section_features is None:
            if not self.section_features_path.exists():
                raise FileNotFoundError(
                    f"Section features file not found: {self.section_features_path}"
                )

            with open(self.section_features_path, 'r') as f:
                self._section_features = json.load(f)

        return self._section_features

    def load_poc_full_results(self) -> dict:
        """
        Load full results from JSON.

        Returns:
            Dictionary with full results data
        """
        if self._poc_full_results is None:
            if not self.poc_full_results_path.exists():
                raise FileNotFoundError(
                    f"POC full results file not found: {self.poc_full_results_path}"
                )

            with open(self.poc_full_results_path, 'r') as f:
                self._poc_full_results = json.load(f)

        return self._poc_full_results

    def load_all_songs(self) -> Dict[str, Song]:
        """
        Load all songs with sections from metadata files.

        Returns:
            Dictionary mapping filename to Song object
        """
        if self._songs is not None:
            return self._songs

        section_features = self.load_section_features()
        poc_full_results = self.load_poc_full_results()

        # Build map of song filename to sections
        sections_by_song = {}
        for section_data in section_features['sections']:
            filename = section_data['song_filename']
            if filename not in sections_by_song:
                sections_by_song[filename] = []

            section = Section(
                song_filename=filename,
                index=section_data['section_index'],
                label=section_data['label'],
                start=section_data['start'],
                end=section_data['end'],
                duration=section_data['duration'],
                tempo=section_data['tempo'],
                key=section_data['full_key'],
                energy_score=section_data['energy_score'],
                loudness_db=section_data['loudness_db'],
                spectral_centroid=section_data['spectral_centroid']
            )
            sections_by_song[filename].append(section)

        # Build Song objects with sections
        songs = {}
        # Handle both list format and dict with 'results' key
        results_list = poc_full_results if isinstance(poc_full_results, list) else poc_full_results.get('results', [])
        for song_data in results_list:
            filename = song_data['filename']

            # Get full key (combine key + mode)
            key = song_data.get('key', 'Unknown')
            mode = song_data.get('mode', 'major')
            full_key = f"{key} {mode}"

            song = Song(
                filename=filename,
                filepath=self.poc_audio_dir / filename,
                duration=song_data['duration'],
                tempo=song_data['tempo'],
                key=full_key,
                loudness_db=song_data.get('loudness_db', 0.0),
                spectral_centroid=song_data.get('spectral_centroid', 0.0),
                sections=sections_by_song.get(filename, [])
            )
            songs[filename] = song

        # Sort sections by index
        for song in songs.values():
            song.sections.sort(key=lambda s: s.index)

        self._songs = songs
        return songs

    def get_song(self, filename: str) -> Optional[Song]:
        """
        Get a specific song by filename.

        Args:
            filename: Song filename (e.g., "do_it_again.mp3")

        Returns:
            Song object or None if not found
        """
        songs = self.load_all_songs()
        return songs.get(filename)

    def get_song_list(self) -> List[Song]:
        """
        Get list of all songs sorted by filename.

        Returns:
            List of Song objects
        """
        songs = self.load_all_songs()
        return sorted(songs.values(), key=lambda s: s.filename)

    def get_song_names(self) -> List[str]:
        """
        Get list of all song filenames sorted alphabetically.

        Returns:
            List of song filenames
        """
        songs = self.load_all_songs()
        return sorted(songs.keys())
