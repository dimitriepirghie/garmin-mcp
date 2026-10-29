"""Shared pieces: the MCP server object, the Garmin client, the tool decorator
and helpers used by every tool module."""
from __future__ import annotations

import functools
import json
import logging
import os
from datetime import date, timedelta
from typing import Any, Callable

import anyio
from garminconnect import Garmin

try:  # mcp >= 2.0 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server
    from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

log = logging.getLogger("garmin_server")

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_DIR = os.environ.get("GARMIN_TOKEN_DIR") or os.path.join(_HERE, ".garmintokens")

mcp = Server("garmin")

# ------------------------------------------------------------------ client

_client: Garmin | None = None


def api() -> Garmin:
    """Lazy client: authenticates on first use from the saved token.

    Never logs in with a password, so restarts cannot trigger SSO limits.
    """
    global _client
    if _client is None:
        if not os.path.isdir(TOKEN_DIR):
            raise RuntimeError(f"No token found in {TOKEN_DIR}. Run `uv run login.py` first.")
        client = Garmin()
        client.login(TOKEN_DIR)
        _client = client
    return _client


# ------------------------------------------------------------ tool decorator


def _annotations() -> ToolAnnotations:
    """Read-only, talks to an external service. Field names differ in mcp 1.x/2.x."""
    fields = ToolAnnotations.model_fields
    snake = "read_only_hint" in fields
    return ToolAnnotations(
        **{
            ("read_only_hint" if snake else "readOnlyHint"): True,
            ("destructive_hint" if snake else "destructiveHint"): False,
            ("open_world_hint" if snake else "openWorldHint"): True,
        }
    )


READ_ONLY = _annotations()
_limiter: anyio.CapacityLimiter | None = None
REGISTERED: dict[str, str] = {}  # tool name -> group


def _garmin_limiter() -> anyio.CapacityLimiter:
    # One Garmin call at a time: the HTTP session is shared and the Pi is slow.
    # Calls run on a worker thread, so the event loop (auth checks, /healthz,
    # other requests queueing) never blocks on Garmin.
    global _limiter
    if _limiter is None:
        _limiter = anyio.CapacityLimiter(1)
    return _limiter


def tool(
    group: str, name: str | None = None, description: str | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a synchronous function as a read-only MCP tool in `group`.

    Skipped when the group is disabled via GARMIN_TOOL_GROUPS.
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or fn.__name__
        if not group_enabled(group):
            return fn

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            call = functools.partial(fn, *args, **kwargs)
            try:
                return await anyio.to_thread.run_sync(call, limiter=_garmin_limiter())
            except ToolError:
                raise
            except Exception as exc:
                # Without this, mcp 2.x shows the model only "Error executing
                # tool X". Garmin's message (no token, 404, rate limit) is what
                # Claude needs to explain or retry; the traceback goes to the log.
                log.exception("tool %s failed", tool_name)
                raise ToolError(f"{type(exc).__name__}: {str(exc)[:500]}") from exc

        mcp.tool(name=tool_name, description=description, annotations=READ_ONLY)(wrapper)
        REGISTERED[tool_name] = group
        return fn

    return deco


# ------------------------------------------------------------- tool groups

GROUPS = (
    "core", "sleep", "health", "body", "activities", "training", "workouts_calendar",
    "profile", "devices", "gear", "badges_challenges", "courses", "nutrition",
    "womens_health", "golf", "other",
)


def _enabled_groups() -> set[str]:
    raw = os.environ.get("GARMIN_TOOL_GROUPS", "all").replace(" ", "").lower()
    if raw in ("", "all"):
        return set(GROUPS)
    wanted = set(raw.split(","))
    unknown = wanted - set(GROUPS)
    if unknown:
        raise SystemExit(f"GARMIN_TOOL_GROUPS: unknown group(s) {sorted(unknown)}. Valid: {', '.join(GROUPS)}")
    return wanted | {"core"}  # core (curated + garmin_get) is always on


ENABLED_GROUPS = _enabled_groups()


def group_enabled(group: str) -> bool:
    return group in ENABLED_GROUPS


# ----------------------------------------------------------------- helpers

MAX_RESULT_CHARS = int(os.environ.get("GARMIN_MAX_RESULT_CHARS", "150000"))
LIST_SAMPLE = 200


def shrink(result: Any) -> Any:
    """Keep responses small enough for a chat.

    Under MAX_RESULT_CHARS of JSON: returned unchanged. Over it: every list
    longer than LIST_SAMPLE items is evenly sampled, and the result is wrapped
    with a note listing what was sampled. Still too large: the first
    MAX_RESULT_CHARS characters are returned.
    """
    try:
        text = json.dumps(result, default=str)
    except (TypeError, ValueError):
        return result
    if len(text) <= MAX_RESULT_CHARS:
        return result

    sampled: list[str] = []

    def walk(obj: Any, path: str) -> Any:
        if isinstance(obj, list):
            if len(obj) > LIST_SAMPLE:
                step = len(obj) / LIST_SAMPLE
                sampled.append(f"{path or '$'}: {len(obj)} -> {LIST_SAMPLE}")
                obj = [obj[int(i * step)] for i in range(LIST_SAMPLE)]
            return [walk(item, f"{path}[]") for item in obj]
        if isinstance(obj, dict):
            return {k: walk(v, f"{path}.{k}" if path else str(k)) for k, v in obj.items()}
        return obj

    small = walk(result, "")
    small_text = json.dumps(small, default=str)
    if len(small_text) <= MAX_RESULT_CHARS:
        return {
            "_note": "Large response: long lists were evenly sampled (path: original -> kept). "
            "Use a shorter date range or a dedicated tool for full detail.",
            "_sampled": sampled[:50],
            "data": small,
        }
    return {
        "_note": "Response too large even after sampling; first characters only. "
        "Use a shorter date range or a dedicated tool.",
        "chars": len(text),
        "head": small_text[:MAX_RESULT_CHARS],
    }


def days(start: str, end: str, limit: int = 400) -> list[str]:
    """Inclusive list of ISO dates between start and end."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first:
        first, last = last, first
    if (last - first).days > limit:
        raise ValueError(f"Range too large (max {limit} days).")
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def pick(names: tuple[str, ...], *sources: Any) -> Any:
    """First non-null value found under any of `names`, across `sources`."""
    for source in sources:
        if not isinstance(source, dict):
            continue
        for name in names:
            value = source.get(name)
            if value is not None:
                return value
    return None


def bucket_mean(rows: list[dict[str, Any]], keys: list[str], points: int) -> list[dict[str, Any]]:
    """Downsample rows to ~points by averaging each numeric key per bucket."""
    if not rows:
        return []
    size = max(1, -(-len(rows) // max(points, 1)))  # ceil division
    out: list[dict[str, Any]] = []
    for i in range(0, len(rows), size):
        chunk = rows[i : i + size]
        row: dict[str, Any] = {}
        for key in keys:
            values = [r[key] for r in chunk if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool)]
            if values:
                row[key] = round(sum(values) / len(values), 2)
        out.append(row)
    return out
