"""Compatibility namespace for relocated POC scripts.

The scripts in this directory historically imported helpers as ``poc.*``.
Keep that import path working after moving the files under ``lab/poc-scripts``
by letting the ``poc`` package resolve modules from this directory.
"""

from pathlib import Path

__path__.append(str(Path(__file__).resolve().parent.parent))
