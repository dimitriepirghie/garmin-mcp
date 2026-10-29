"""Offline tests: a fake Garmin client stands in for the real one, so no
account, token or network is needed.

    uv run --with pytest pytest -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("GARMIN_TOOL_GROUPS", "all")

from garmin_server import REGISTERED, core, mcp  # noqa: E402
from garmin_server.catalog import group_of, read_methods  # noqa: E402
from garmin_server.http import BearerAuth  # noqa: E402

TOKEN = "t" * 43


class FakeGarmin:
    """Answers the handful of methods the tests call."""

    def get_activity_details(self, activity_id: str, maxchart: int = 2000, maxpoly: int = 4000) -> dict:
        desc = [
            {"metricsIndex": 0, "key": "sumDuration"},
            {"metricsIndex": 1, "key": "directHeartRate"},
            {"metricsIndex": 2, "key": "directAirTemperature"},
        ]
        rows = [{"metrics": [i, 120 + i % 30, 80.0]} for i in range(3000)]
        return {"metricDescriptors": desc, "activityDetailMetrics": rows}

    def get_activity_laps(self, activity_id: str) -> dict:
        return {"lapDTOs": [{"distance": 5000.0, "duration": 900.0, "averageHR": 135, "junk": 1}]}

    def get_race_predictions(self, startdate=None, enddate=None, _type=None) -> dict:
        return {"called_with": [startdate, enddate, _type]}

    def get_all_day_stress(self, cdate: str) -> dict:
        return {"stressValuesArray": [[i, i % 100] for i in range(20000)]}

    def get_activity_weather(self, activity_id: str) -> dict:
        time.sleep(0.5)
        return {"temp": 12, "id": activity_id}

    def get_heart_rates(self, cdate: str) -> dict:
        values = [[1761696000000 + i * 120000, 60 + i % 40] for i in range(720)]
        return {"restingHeartRate": 52, "maxHeartRate": 160, "heartRateValues": values + [[1, None]]}

    def get_sleep_data(self, day: str) -> dict:
        return {"dailySleepDTO": {"sleepTimeSeconds": 27000, "deepSleepSeconds": 5400, "remSleepSeconds": 6000}}

    def get_hrv_data(self, day: str) -> dict:
        return {"hrvSummary": {"lastNightAvg": 61}}

    def get_rhr_day(self, day: str) -> dict:
        raise RuntimeError("rhr endpoint down")

    def get_stats(self, day: str) -> dict:
        return {"totalSteps": 9000, "restingHeartRate": 50, "junk": 1}

    def get_training_readiness(self, day: str) -> list:
        return [{"score": 77, "level": "HIGH"}]

    def get_activities_by_date(self, start, end, activitytype=None) -> list:
        return [{"activityId": 1, "activityName": "Gravel", "distance": 80000, "duration": 14400, "averageHR": 136}]


@pytest.fixture(autouse=True)
def fake_client():
    core._client = FakeGarmin()
    yield
    core._client = None


def call(name: str, args: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Call a tool through the MCP server; returns (is_error, text)."""

    async def run() -> tuple[bool, str]:
        try:
            result = await mcp.call_tool(name, args or {})
        except Exception as exc:  # mcp 1.x raises ToolError
            return True, str(exc)
        if isinstance(result, tuple):
            result = result[0]
        is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
        content = getattr(result, "content", result)
        return is_error, "".join(getattr(c, "text", "") for c in content)

    return asyncio.run(run())


def tools() -> dict[str, Any]:
    listed = asyncio.run(mcp.list_tools())
    listed = getattr(listed, "tools", listed)
    return {t.name: t for t in listed}


def schema(tool: Any) -> dict:
    return getattr(tool, "inputSchema", None) or tool.input_schema


# ------------------------------------------------------------------ catalog


def test_every_read_method_is_reachable():
    names = set(tools())
    covered_by_curated = {
        "get_activities", "get_activities_by_date", "get_activity_details", "get_activity_laps",
        "get_activity_hr_in_timezones", "get_heart_rates", "get_hrv_data", "get_body_battery",
        "get_stress_data", "get_stats", "get_training_status", "get_training_readiness",
    }
    missing = set(read_methods()) - names - covered_by_curated
    assert not missing


def test_no_method_falls_into_other_group():
    assert not [m for m in read_methods() if group_of(m) == "other"]


def test_all_tools_are_read_only():
    for tool in tools().values():
        ann = tool.annotations
        assert getattr(ann, "read_only_hint", None) or getattr(ann, "readOnlyHint", None), tool.name


def test_underscore_param_is_renamed_and_mapped_back():
    assert "type" in schema(tools()["get_race_predictions"])["properties"]
    err, text = call("get_race_predictions", {"type": "RUNNING", "startdate": "2026-01-01"})
    assert not err and json.loads(text)["called_with"] == ["2026-01-01", None, "RUNNING"]


def test_required_params_come_from_the_library():
    assert schema(tools()["get_activity_weather"]).get("required") == ["activity_id"]


def test_large_response_is_sampled():
    err, text = call("get_all_day_stress", {"cdate": "2026-10-28"})
    data = json.loads(text)
    assert not err and len(data["data"]["stressValuesArray"]) == core.LIST_SAMPLE
    assert data["_sampled"] == [f"stressValuesArray: 20000 -> {core.LIST_SAMPLE}"]


def test_garmin_get_rejects_non_read_methods():
    for method in ("delete_activity", "login", "logout", "__init__"):
        err, text = call("garmin_get", {"method": method})
        assert err and "non-read" in text


def test_garmin_get_calls_read_methods():
    err, text = call("garmin_get", {"method": "get_activity_weather", "args": {"activity_id": "5"}})
    assert not err and json.loads(text) == {"temp": 12, "id": "5"}


# ------------------------------------------------------------------ curated


def test_activity_details_downsamples_and_lists_metrics():
    err, text = call("activity_details", {"activity_id": "1", "points": 50})
    data = json.loads(text)
    assert not err and len(data["series"]) == 50
    assert "directAirTemperature" in data["available_metrics"]


def test_activity_laps_keeps_known_fields():
    # MCP sends a returned list as one content block per item.
    err, text = call("activity_laps", {"activity_id": "1"})
    assert json.loads(text) == {"lap": 1, "distance": 5000.0, "duration": 900.0, "averageHR": 135}


def test_heart_rate_day_curve():
    data = json.loads(call("heart_rate_day", {"day": "2026-10-28", "points": 96})[1])
    assert data["resting"] == 52 and 80 <= len(data["curve"]) <= 96


def test_day_overview_survives_a_failing_part():
    err, text = call("day_overview", {"day": "2026-10-28"})
    data = json.loads(text)
    assert not err
    assert data["sleep"]["hours"] == 7.5 and data["hrv"] == 61
    assert data["training_readiness"]["score"] == 77
    assert data["stats"] == {"totalSteps": 9000, "restingHeartRate": 50}


def test_errors_reach_the_model():
    core._client = None
    saved, core.TOKEN_DIR = core.TOKEN_DIR, "/nonexistent"
    try:
        err, text = call("get_activity_weather", {"activity_id": "1"})
    finally:
        core.TOKEN_DIR = saved
    assert err and "No token found" in text


def test_garmin_calls_do_not_block_the_event_loop():
    async def run() -> int:
        ticks = 0
        start = time.monotonic()

        async def ticker() -> None:
            nonlocal ticks
            while time.monotonic() - start < 0.4:
                await asyncio.sleep(0.05)
                ticks += 1

        await asyncio.gather(mcp.call_tool("get_activity_weather", {"activity_id": "7"}), ticker())
        return ticks

    assert asyncio.run(run()) >= 5


# --------------------------------------------------------------------- auth


def auth(path: str, headers: list | None = None, path_token: bool = False) -> tuple[int, str]:
    seen: dict[str, str] = {}

    async def inner(scope, receive, send):
        seen["path"] = scope["path"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"inner"})

    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "path": path, "raw_path": path.encode(), "headers": headers or []}
    asyncio.run(BearerAuth(inner, TOKEN, path_token)(scope, receive, send))
    return sent[0]["status"], seen.get("path", "")


BEARER = [(b"authorization", f"Bearer {TOKEN}".encode())]


def test_auth_rejects_missing_and_wrong_token():
    assert auth("/mcp")[0] == 401
    assert auth("/mcp", [(b"authorization", b"Bearer wrong")])[0] == 401


def test_auth_accepts_bearer_any_case():
    assert auth("/mcp", BEARER) == (200, "/mcp")
    assert auth("/mcp", [(b"authorization", f"bearer {TOKEN}".encode())]) == (200, "/mcp")


def test_open_paths():
    assert auth("/healthz")[0] == 200
    assert auth("/.well-known/oauth-protected-resource")[0] == 404  # 401 breaks Claude connectors


def test_root_is_served_as_mcp():
    assert auth("/", BEARER) == (200, "/mcp")
    assert auth("/")[0] == 401


def test_path_token_only_when_enabled():
    assert auth(f"/{TOKEN}/mcp")[0] == 401
    assert auth(f"/{TOKEN}/mcp", path_token=True) == (200, "/mcp")
    assert auth("/wrong/mcp", path_token=True)[0] == 401
