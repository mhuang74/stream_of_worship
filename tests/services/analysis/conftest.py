"""Pytest configuration for analysis service tests."""

import sys
from pathlib import Path

# Add services/analysis/src to path
analysis_src = Path(__file__).parent.parent.parent.parent / "services" / "analysis" / "src"
sys.path.insert(0, str(analysis_src))
