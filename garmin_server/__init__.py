"""Garmin Connect MCP server.

Importing the package registers every tool on `mcp`:
  curated  - hand-written tools with compact output (group "core")
  catalog  - one tool per garminconnect get_* method, grouped by area
"""
from . import curated, catalog  # noqa: F401  (import registers the tools)
from .core import ENABLED_GROUPS, REGISTERED, mcp

__all__ = ["mcp", "REGISTERED", "ENABLED_GROUPS"]
