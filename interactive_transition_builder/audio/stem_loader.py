"""
Stem loader with caching.

Loads 4-stem audio files (bass, drums, other, vocals) from the
poc_output_allinone/stems directory with LRU caching.
"""

import librosa
import numpy as np
from pathlib import Path
from typing import Dict, Tuple, Optional
from collections import OrderedDict

from ..models import Section


class StemLoader:
    """
    Loads and caches audio stems for efficient access.

    Implements simple LRU cache to avoid repeated disk I/O for the same stems.
    """

    def __init__(self, stems_base_dir: Path = None, sample_rate: int = 44100, cache_size: int = 10):
        """
        Initialize stem loader.

        Args:
            stems_base_dir: Base directory containing stem subdirectories
                           (default: poc_output_allinone/stems)
            sample_rate: Target sample rate for audio loading
            cache_size: Maximum number of stem sets to cache
        """
        if stems_base_dir is None:
            stems_base_dir = Path(__file__).parent.parent.parent / "poc_output_allinone" / "stems"

        self.stems_base_dir = Path(stems_base_dir)
        self.sample_rate = sample_rate
        self.cache_size = cache_size

        # LRU cache: OrderedDict mapping (song_filename, section_index) -> stems_dict
        self.cache = OrderedDict()

        # Available stem names
        self.STEM_NAMES = ['bass', 'drums', 'other', 'vocals']

    def _get_stem_dir(self, song_filename: str) -> Path:
        """
        Get stem directory for a song.

        Args:
            song_filename: Song filename (e.g., "do_it_again.mp3")

        Returns:
            Path to stem directory
        """
        # Remove extension to get stem directory name
        song_stem = Path(song_filename).stem
        return self.stems_base_dir / song_stem

    def _verify_stems_exist(self, stem_dir: Path) -> Tuple[bool, Optional[str]]:
        """
        Verify all required stems exist for a song.

        Args:
            stem_dir: Path to stem directory

        Returns:
            Tuple of (exists, error_message)
        """
        if not stem_dir.exists():
            return False, f"Stem directory not found: {stem_dir}"

        for stem_name in self.STEM_NAMES:
            stem_path = stem_dir / f"{stem_name}.wav"
            if not stem_path.exists():
                return False, f"Stem not found: {stem_path}"

        return True, None

    def _load_stem_section(
        self,
        stem_path: Path,
        section_start: float,
        section_end: float
    ) -> np.ndarray:
        """
        Load a specific section from a stem file.

        Args:
            stem_path: Path to stem WAV file
            section_start: Start time in seconds
            section_end: End time in seconds

        Returns:
            Stereo audio array (2, num_samples)
        """
        # Load audio with librosa
        y, sr = librosa.load(str(stem_path), sr=self.sample_rate, mono=False)

        # Ensure stereo format
        if y.ndim == 1:
            y = np.stack([y, y])

        # Extract section samples
        start_sample = int(section_start * self.sample_rate)
        end_sample = int(section_end * self.sample_rate)
        section_audio = y[:, start_sample:end_sample]

        return section_audio

    def load_section_stems(
        self,
        song_filename: str,
        section: Section,
        use_cache: bool = True
    ) -> Dict[str, np.ndarray]:
        """
        Load all 4 stems for a specific section.

        Args:
            song_filename: Song filename
            section: Section object with start/end times
            use_cache: Whether to use cache (default: True)

        Returns:
            Dictionary mapping stem name to stereo audio array:
            {
                'bass': array (2, num_samples),
                'drums': array (2, num_samples),
                'other': array (2, num_samples),
                'vocals': array (2, num_samples)
            }

        Raises:
            FileNotFoundError: If stems don't exist for this song
            ValueError: If audio loading fails
        """
        # Check cache first
        cache_key = (song_filename, section.index)
        if use_cache and cache_key in self.cache:
            # Move to end (most recently used)
            self.cache.move_to_end(cache_key)
            return self.cache[cache_key]

        # Get stem directory
        stem_dir = self._get_stem_dir(song_filename)

        # Verify stems exist
        exists, error_msg = self._verify_stems_exist(stem_dir)
        if not exists:
            raise FileNotFoundError(
                f"{error_msg}\n"
                f"Run stem generation first: python poc/poc_analysis_allinone.py --generate-stems"
            )

        # Load each stem
        stems = {}
        for stem_name in self.STEM_NAMES:
            stem_path = stem_dir / f"{stem_name}.wav"
            try:
                stem_audio = self._load_stem_section(stem_path, section.start, section.end)
                stems[stem_name] = stem_audio
            except Exception as e:
                raise ValueError(f"Failed to load stem {stem_name}: {e}")

        # Add to cache (LRU eviction)
        if use_cache:
            self.cache[cache_key] = stems
            # Evict oldest if cache is full
            if len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)  # Remove oldest (first) item

        return stems

    def load_partial_section_stems(
        self,
        song_filename: str,
        section: Section,
        start_offset: float = 0.0,
        duration: Optional[float] = None
    ) -> Dict[str, np.ndarray]:
        """
        Load a partial range of a section's stems.

        Args:
            song_filename: Song filename
            section: Section object
            start_offset: Offset from section start in seconds
            duration: Duration to load (None = to end of section)

        Returns:
            Dictionary mapping stem name to stereo audio array
        """
        # Calculate absolute start/end times
        absolute_start = section.start + start_offset
        if duration is None:
            absolute_end = section.end
        else:
            absolute_end = min(section.end, absolute_start + duration)

        # Create temporary section object for this range
        temp_section = Section(
            song_filename=section.song_filename,
            index=section.index,
            label=section.label,
            start=absolute_start,
            end=absolute_end,
            duration=absolute_end - absolute_start,
            tempo=section.tempo,
            key=section.key,
            energy_score=section.energy_score,
            loudness_db=section.loudness_db,
            spectral_centroid=section.spectral_centroid
        )

        # Load stems for this range (don't cache partial sections)
        return self.load_section_stems(song_filename, temp_section, use_cache=False)

    def clear_cache(self):
        """Clear the stem cache."""
        self.cache.clear()

    def get_cache_info(self) -> Dict:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache size and keys
        """
        return {
            'size': len(self.cache),
            'max_size': self.cache_size,
            'keys': list(self.cache.keys())
        }
