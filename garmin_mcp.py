"""MCP server for Garmin Connect. Entry point; the code is in garmin_server/.

No call limits - talks directly to Garmin Connect using your own OAuth token.
Read-only: every tool only fetches data.

    uv run login.py               # once, to authenticate
    uv run garmin_mcp.py          # stdio (Claude Desktop / Claude Code, local)
    uv run garmin_mcp.py --http   # Streamable HTTP with bearer auth (Pi)
    uv run garmin_mcp.py --list   # print the registered tools by group

Environment:
    GARMIN_TOKEN_DIR      default ./.garmintokens next to this file
    GARMIN_TOOL_GROUPS    "all" (default) or a comma list, e.g.
                          "sleep,health,activities,training". "core" (curated
                          tools + garmin_get) is always on. Groups: core, sleep,
                          health, body, activities, training, workouts_calendar,
                          profile, devices, gear, badges_challenges, courses,
                          nutrition, womens_health, golf, other
    GARMIN_MAX_RESULT_CHARS  default 150000; larger results are sampled

HTTP mode:
    MCP_BEARER_TOKEN  required, >= 32 chars. Clients must send
                      "Authorization: Bearer <token>".
    MCP_HOST          default 127.0.0.1 (Tailscale Funnel proxies to it)
    MCP_PORT          default 8000
    MCP_PATH_TOKEN    "1" to also accept the token as /<token>/mcp
"""
import sys
from collections import Counter

from garmin_server import REGISTERED, mcp


def _list() -> None:
    by_group = Counter(REGISTERED.values())
    for group, count in sorted(by_group.items()):
        names = sorted(n for n, g in REGISTERED.items() if g == group)
        print(f"\n[{group}] {count}\n  " + "\n  ".join(names))
    print(f"\n{len(REGISTERED)} tools")


if __name__ == "__main__":
    if "--list" in sys.argv[1:]:
        _list()
    elif "--http" in sys.argv[1:]:
        from garmin_server.http import run_http

        run_http()
    else:
        mcp.run()
