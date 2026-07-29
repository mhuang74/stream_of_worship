"""Regression test: no top-level songset_constructor imports in commands/songset.py.

Ensures the ``constructor`` extra (pydantic, langgraph) is not required
at CLI startup.  ``commands/songset.py`` must only import
``songset_constructor`` submodules inside function bodies (after
``_import_constructor()`` has guarded the import).
"""

from __future__ import annotations

import ast
from pathlib import Path


def _songset_command_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "stream_of_worship"
        / "admin"
        / "commands"
        / "songset.py"
    )


def test_no_top_level_songset_constructor_imports():
    """commands/songset.py must not import songset_constructor at module scope."""
    source = _songset_command_path().read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_songset_command_path()))

    violations: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "songset_constructor" in alias.name:
                    violations.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "songset_constructor" in module:
                violations.append(f"line {node.lineno}: from {module} import ...")

    assert not violations, (
        "commands/songset.py must not import songset_constructor at module scope "
        "(breaks --extra admin without --extra constructor). Violations:\n"
        + "\n".join(violations)
    )
