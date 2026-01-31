"""AI metadata generation for worship songs.

This module uses LLM to generate:
- AI summary: One-sentence description
- Themes: Theme tags (Praise, Worship, etc.)
- Bible verses: Related Scripture references
- Vocalist: male/female/mixed classification
"""

import json
from dataclasses import dataclass
from typing import Optional, List
from pathlib import Path

try:
    import openai
except ImportError:
    openai = None


@dataclass
class SongMetadata:
    """AI-generated metadata for a song."""

    ai_summary: str
    themes: List[str]
    bible_verses: List[str]
    vocalist: str  # "male", "female", or "mixed"

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "ai_summary": self.ai_summary,
            "themes": self.themes,
            "bible_verses": self.bible_verses,
            "vocalist": self.vocalist,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SongMetadata":
        """Create from dictionary."""
        return cls(
            ai_summary=data.get("ai_summary", ""),
            themes=data.get("themes", []),
            bible_verses=data.get("bible_verses", []),
            vocalist=data.get("vocalist", "mixed"),
        )


class MetadataGenerator:
    """Generate AI metadata for worship songs using LLM."""

    # Valid theme options for the catalog
    VALID_THEMES = [
        "Praise",
        "Worship",
        "Thanksgiving",
        "Lament",
        "Victory",
        "Grace",
        "Love",
        "Presence",
        "Glory",
        "Hope",
        "Faith",
        "Restoration",
        "Salvation",
        "Adoration",
        "Surrender",
        "Healing",
        "Revival",
        "Cross",
        "Resurrection",
        "Heaven",
    ]

    def __init__(
        self,
        model: str = "openai/gpt-4o-mini",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ):
        """Initialize metadata generator.

        Args:
            model: LLM model identifier (default: openai/gpt-4o-mini)
            api_key: OpenRouter API key (if None, reads from OPENROUTER_API_KEY env var)
            api_base: Custom API base URL (defaults to https://openrouter.ai/api/v1)
        """
        self.model = model
        self.api_key = api_key
        self.api_base = api_base or "https://openrouter.ai/api/v1"
        self._client = None

    @property
    def client(self):
        """Get or create LLM client."""
        if openai is None:
            raise ImportError(
                "openai is required for metadata generation. "
                "Install with: uv add --extra lrc_generation openai"
            )
        if self._client is None:
            # Try to get API key from environment if not provided
            key = self.api_key
            if key is None:
                import os

                key = os.environ.get("OPENROUTER_API_KEY")
            if not key:
                raise ValueError(
                    "OpenRouter API key required. Set OPENROUTER_API_KEY environment variable "
                    "or pass api_key parameter."
                )
            self._client = openai.OpenAI(api_key=key, base_url=self.api_base)
        return self._client

    def generate(
        self,
        title: str,
        artist: str,
        lyrics_text: str,
        key: str = "Unknown",
        bpm: float = 0.0,
    ) -> SongMetadata:
        """Generate metadata for a song.

        Args:
            title: Song title
            artist: Artist name
            lyrics_text: Full lyrics text
            key: Musical key (optional)
            bpm: Tempo in BPM (optional)

        Returns:
            SongMetadata with generated fields
        """
        # Determine tempo category
        tempo_category = self._get_tempo_category(bpm)

        # Build prompt
        prompt = self._build_prompt(
            title, artist, lyrics_text, key, tempo_category
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=1500,
            )

            content = response.choices[0].message.content.strip()

            # Clean up markdown code blocks
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            data = json.loads(content)

            # Validate and filter themes
            themes = self._filter_valid_themes(data.get("themes", []))

            # Validate vocalist type
            vocalist = data.get("vocalist", "mixed").lower()
            if vocalist not in ["male", "female", "mixed"]:
                vocalist = "mixed"

            return SongMetadata(
                ai_summary=data.get("ai_summary", ""),
                themes=themes,
                bible_verses=data.get("bible_verses", []),
                vocalist=vocalist,
            )

        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM returned invalid JSON: {e}")
        except Exception as e:
            raise RuntimeError(f"Metadata generation failed: {e}")

    def _build_prompt(
        self, title: str, artist: str, lyrics: str, key: str, tempo: str
    ) -> str:
        """Build the LLM prompt for metadata generation.

        Args:
            title: Song title
            artist: Artist name
            lyrics: Lyrics text
            key: Musical key
            tempo: Tempo category

        Returns:
            Prompt string
        """
        themes_list = ", ".join(self.VALID_THEMES)

        prompt = f"""You are analyzing a Chinese worship song to generate metadata.

## Song Information
- Title: {title}
- Artist: {artist}
- Musical Key: {key}
- Tempo: {tempo}

## Lyrics
```
{lyrics}
```

## Task
Generate the following metadata:

1. **ai_summary**: A one-sentence English description of what this song is about, its main theme, and its emotional tone.

2. **themes**: Select 2-4 theme tags from this list that best fit the song:
{themes_list}

3. **bible_verses**: Identify 1-3 Bible verses (in English with reference) that relate to this song's message. Include the verse reference (e.g., "Psalm 23:1") and a brief quote or paraphrase.

4. **vocalist**: Classify as "male", "female", or "mixed" based on typical worship service arrangement (most worship songs are mixed).

## Output Format
Return ONLY a JSON object like this:
```json
{{
  "ai_summary": "A brief one-sentence description",
  "themes": ["Praise", "Worship"],
  "bible_verses": [
    "Psalm 23:1 - The LORD is my shepherd, I lack nothing."
  ],
  "vocalist": "mixed"
}}
```

Do not include any markdown code blocks or extra text. Just the JSON object."""

        return prompt

    def _get_tempo_category(self, bpm: float) -> str:
        """Get tempo category from BPM.

        Args:
            bpm: Beats per minute

        Returns:
            Tempo category string
        """
        if bpm < 90:
            return "slow"
        elif bpm < 130:
            return "medium"
        else:
            return "fast"

    def _filter_valid_themes(self, themes: List[str]) -> List[str]:
        """Filter themes to only valid ones.

        Args:
            themes: List of theme strings

        Returns:
            List of valid themes
        """
        valid_upper = {t.upper() for t in self.VALID_THEMES}
        filtered = []
        for t in themes:
            if t.upper() in valid_upper:
                # Use the canonical capitalization
                for valid in self.VALID_THEMES:
                    if t.upper() == valid.upper():
                        filtered.append(valid)
                        break
        return filtered

    def batch_generate(
        self,
        songs: List[tuple[str, str, str, str, float]],
        progress_callback=None,
    ) -> dict[str, SongMetadata]:
        """Generate metadata for multiple songs.

        Args:
            songs: List of (title, artist, lyrics, key, bpm) tuples
            progress_callback: Optional callback for progress updates

        Returns:
            Dictionary mapping song identifier to metadata
        """
        results = {}
        total = len(songs)

        for i, (title, artist, lyrics, key, bpm) in enumerate(songs):
            if progress_callback:
                progress_callback(f"Processing {i + 1}/{total}: {title}", i / total)

            song_id = f"{title}_{artist}".replace(" ", "_").lower()
            try:
                metadata = self.generate(title, artist, lyrics, key, bpm)
                results[song_id] = metadata
            except Exception as e:
                print(f"Failed to generate metadata for {title}: {e}")

        return results
