"""Ingestion pipeline for Stream of Worship.

This module provides tools for:
- Audio analysis using AllInOne
- Lyrics scraping
- LRC generation (Whisper + LLM)
- Metadata generation via LLM
- Stem separation
"""

from sow_legacy_cli_tui.ingestion.lrc_generator import LRCGenerator
from sow_legacy_cli_tui.ingestion.metadata_generator import MetadataGenerator

__all__ = ["LRCGenerator", "MetadataGenerator"]
