"""Pytest configuration for analysis service tests."""

import sys
from pathlib import Path

# Add ops/analysis-service/src to path
analysis_src = Path(__file__).parents[2] / "src"
sys.path.insert(0, str(analysis_src))
