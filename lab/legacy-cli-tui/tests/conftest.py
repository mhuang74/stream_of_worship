"""Pytest configuration and fixtures for Stream of Worship tests."""

import sys
from pathlib import Path

# Add parent directories to path for imports
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
