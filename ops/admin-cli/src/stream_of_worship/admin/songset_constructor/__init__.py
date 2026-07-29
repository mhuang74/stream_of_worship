"""Songset constructor subpackage for sow-admin.

Lazily loaded when ``sow-admin songset construct`` is invoked.
Submodules (config, cache, db, etc.) are imported on demand to avoid
requiring the ``constructor`` extra (pydantic, langgraph) at CLI startup.
"""
