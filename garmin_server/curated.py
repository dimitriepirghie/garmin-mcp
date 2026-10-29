"""Hand-written tools: compact output, date ranges, charts. Group "core",
always on. These cover the common questions; catalog.py covers the rest."""
from __future__ import annotations

from typing import Any

from .core import api, bucket_mean, days, pick, shrink, tool

# ------------------------------------------------------------------ sleep


def _sleep_row(day: str) -> dict[str, Any]:
    """Compact sleep record for one night. Empty dict when no data exists.

    Garmin moves these fields between the response root and dailySleepDTO
    depending on account and API version, so each one is looked up in both.
    HRV and resting heart rate fall back to their dedicated endpoints.
    """
    raw = api().get_sleep_data(day) or {}
    dto = raw.get("dailySleepDTO") or {}
    total = pick(("sleepTimeSeconds",), dto, raw)
    if not total:
        return {}

    scores = dto.get("sleepScores") or raw.get("sleepScores") or {}
    overall = scores.get("overall") or {} if isinstance(scores, dict) else {}
    deep = pick(("deepSleepSeconds",), dto, raw) or 0
    rem = pick(("remSleepSeconds",), dto, raw) or 0

    hrv = pick(("avgOvernightHrv", "averageHrv", "hrvAverage"), dto, raw)
    if hrv is None:
        try:
            summary = (api().get_hrv_data(day) or {}).get("hrvSummary") or {}
            hrv = summary.get("lastNightAvg")
        except Exception:
            pass

    rhr = pick(("restingHeartRate", "restingHeartRateValue"), dto, raw)
    if rhr is None:
        try:
            rhr_raw = api().get_rhr_day(day) or {}
            metrics = (rhr_raw.get("allMetrics") or {}).get("metricsMap") or {}
            series = metrics.get("WELLNESS_RESTING_HEART_RATE") or []
            rhr = series[0].get("value") if series else None
        except Exception:
            pass

    return {
        "date": day,
        "hours": round(total / 3600, 2),
        "score": overall.get("value"),
        "deep_pct": round(deep / total * 100, 1),
        "rem_pct": round(rem / total * 100, 1),
        "awake_min": round((pick(("awakeSleepSeconds",), dto, raw) or 0) / 60),
        "hrv": hrv,
        "rhr": rhr,
        "spo2_avg": pick(("averageSpO2Value", "averageSpo2Value"), dto, raw),
        "spo2_low": pick(("lowestSpO2Value", "lowestSpo2Value"), dto, raw),
        "stress_avg": pick(("avgSleepStress",), dto, raw),
        "restless": pick(("restlessMomentsCount",), raw, dto),
    }


@tool("core")
def sleep_range(start_date: str, end_date: str) -> dict[str, Any]:
    """Night-by-night sleep, HRV, resting heart rate and SpO2 over a date range.

    The main sleep tool: one call covers whole months.

    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD, inclusive
    """
    nights: list[dict[str, Any]] = []
    missing: list[str] = []
    for day in days(start_date, end_date):
        try:
            row = _sleep_row(day)
        except Exception as exc:
            missing.append(f"{day}: {exc}")
            continue
        if row:
            nights.append(row)
        else:
            missing.append(day)

    hrv = [n["hrv"] for n in nights if n.get("hrv")]
    rhr = [n["rhr"] for n in nights if n.get("rhr")]
    summary: dict[str, Any] = {}
    if hrv:
        summary |= {"hrv_mean": round(sum(hrv) / len(hrv), 1), "hrv_min": min(hrv), "hrv_max": max(hrv)}
    if rhr:
        summary |= {"rhr_mean": round(sum(rhr) / len(rhr), 1), "rhr_min": min(rhr), "rhr_max": max(rhr)}
    return {"n": len(nights), "summary": summary, "nights": nights, "missing": missing}


@tool("core")
def sleep_day(day: str) -> dict[str, Any]:
    """Compact sleep record for a single night (get_sleep_data has the full raw record).

    Args:
        day: YYYY-MM-DD
    """
    return _sleep_row(day) or {"date": day, "note": "no data"}


@tool("core")
def hrv_day(day: str) -> dict[str, Any]:
    """HRV summary including the personal baseline Garmin computes.

    Args:
        day: YYYY-MM-DD
    """
    summary = (api().get_hrv_data(day) or {}).get("hrvSummary") or {}
    baseline = summary.get("baseline") or {}
    return {
        "date": day,
        "last_night_avg": summary.get("lastNightAvg"),
        "last_night_5min_high": summary.get("lastNight5MinHigh"),
        "weekly_avg": summary.get("weeklyAvg"),
        "status": summary.get("status"),
        "baseline_low_upper": baseline.get("lowUpper"),
        "baseline_balanced_low": baseline.get("balancedLow"),
        "baseline_balanced_upper": baseline.get("balancedUpper"),
    }


# -------------------------------------------------------------- daily view

_STAT_FIELDS = (
    "totalSteps", "dailyStepGoal", "totalDistanceMeters", "totalKilocalories",
    "activeKilocalories", "restingHeartRate", "minHeartRate", "maxHeartRate",
    "averageStressLevel", "maxStressLevel", "bodyBatteryHighestValue",
    "bodyBatteryLowestValue", "bodyBatteryMostRecentValue", "bodyBatteryChargedValue",
    "bodyBatteryDrainedValue", "floorsAscended", "moderateIntensityMinutes",
    "vigorousIntensityMinutes", "averageSpo2", "lowestSpo2", "avgWakingRespirationValue",
)


@tool("core")
def day_overview(day: str) -> dict[str, Any]:
    """One-call summary of a day: sleep, HRV, readiness, steps, calories,
    heart rate, stress, Body Battery, intensity minutes and activities.

    Each part is fetched separately; a part that fails is listed in "errors".

    Args:
        day: YYYY-MM-DD
    """
    out: dict[str, Any] = {"date": day}
    errors: dict[str, str] = {}

    def part(key: str, fn: Any) -> None:
        try:
            out[key] = fn()
        except Exception as exc:  # one missing endpoint should not sink the rest
            errors[key] = str(exc)[:200]

    def stats() -> dict[str, Any]:
        raw = api().get_stats(day) or {}
        return {k: raw[k] for k in _STAT_FIELDS if raw.get(k) is not None}

    def readiness() -> Any:
        raw = api().get_training_readiness(day) or []
        first = raw[0] if isinstance(raw, list) and raw else raw
        if not isinstance(first, dict):
            return None
        return {k: first.get(k) for k in ("score", "level", "feedbackShort", "recoveryTime") if first.get(k) is not None}

    part("sleep", lambda: _sleep_row(day) or None)
    part("hrv", lambda: ((api().get_hrv_data(day) or {}).get("hrvSummary") or {}).get("lastNightAvg"))
    part("training_readiness", readiness)
    part("stats", stats)
    part("activities", lambda: [_activity_row(a) for a in api().get_activities_by_date(day, day) or []])
    if errors:
        out["errors"] = errors
    return out


# ------------------------------------------------------------- activities


def _activity_row(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": a.get("activityId"),
        "name": a.get("activityName"),
        "type": (a.get("activityType") or {}).get("typeKey"),
        "start": a.get("startTimeLocal"),
        "km": round((a.get("distance") or 0) / 1000, 2),
        "minutes": round((a.get("duration") or 0) / 60, 1),
        "hr_avg": a.get("averageHR"),
        "hr_max": a.get("maxHR"),
        "calories": a.get("calories"),
        "elev_gain_m": a.get("elevationGain"),
    }


@tool("core")
def activities(limit: int = 30, start: int = 0) -> list[dict[str, Any]]:
    """Recent activities, newest first. Use the id with the activity tools.

    Args:
        limit: how many to return (max 100)
        start: offset for paging
    """
    return [_activity_row(a) for a in api().get_activities(start, min(limit, 100)) or []]


@tool("core")
def activities_by_date(start_date: str, end_date: str, activity_type: str | None = None) -> list[dict[str, Any]]:
    """Activities between two dates, e.g. to compare all rides of a season.

    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD, inclusive
        activity_type: optional Garmin type key, e.g. "cycling", "running"
            (get_activity_types lists them)
    """
    return [_activity_row(a) for a in api().get_activities_by_date(start_date, end_date, activity_type) or []]


# Shown first when present; names vary by device, so anything else the
# activity recorded is listed under "available_metrics".
_PREFERRED_METRICS = (
    "sumDuration", "sumDistance", "directHeartRate", "directSpeed", "directElevation",
    "directPower", "directBikeCadence", "directRunCadence", "directAirTemperature",
)


@tool("core")
def activity_details(activity_id: str, points: int = 200, metrics: list[str] | None = None) -> dict[str, Any]:
    """Time series for one activity: heart rate, speed, elevation, power,
    cadence, temperature over the course of the activity, downsampled for charts.

    sumDuration is seconds since start, sumDistance is metres, directSpeed m/s.
    Each point is the average of the raw samples in its bucket.

    Args:
        activity_id: id from activities / activities_by_date
        points: how many points to return (default 200, max 1000)
        metrics: metric names to include; default is the common ones.
            See "available_metrics" in the result for what was recorded.
    """
    raw = api().get_activity_details(activity_id, maxchart=2000, maxpoly=0) or {}
    index_to_key: dict[int, str] = {}
    for d in raw.get("metricDescriptors") or []:
        key, index = d.get("key"), d.get("metricsIndex")
        if isinstance(key, str) and isinstance(index, int) and not isinstance(index, bool) and index >= 0:
            index_to_key[index] = key
    rows = [
        {key: m[i] for i, key in index_to_key.items() if i < len(m)}
        for m in ((s.get("metrics") or []) for s in raw.get("activityDetailMetrics") or [])
    ]
    available = sorted(set(index_to_key.values()))
    if not rows:
        return {"activity_id": activity_id, "note": "no time series", "response_keys": sorted(raw)}

    wanted = metrics or [k for k in _PREFERRED_METRICS if k in available] or available
    keys = [k for k in wanted if k in available]
    return {
        "activity_id": activity_id,
        "raw_samples": len(rows),
        "metrics": keys,
        "available_metrics": available,
        "series": bucket_mean(rows, keys, min(max(points, 10), 1000)),
    }


_LAP_FIELDS = (
    "startTimeGMT", "distance", "duration", "movingDuration", "averageHR", "maxHR",
    "averageSpeed", "maxSpeed", "elevationGain", "elevationLoss", "averagePower",
    "averageBikeCadence", "averageRunCadence", "calories",
)


@tool("core")
def activity_laps(activity_id: str) -> list[dict[str, Any]]:
    """Laps / auto-laps of one activity with HR, speed, elevation per lap.

    distance is metres, duration seconds, speed m/s.

    Args:
        activity_id: id from activities / activities_by_date
    """
    laps = (api().get_activity_laps(activity_id) or {}).get("lapDTOs") or []
    out = []
    for n, lap in enumerate(laps, 1):
        row = {k: lap[k] for k in _LAP_FIELDS if lap.get(k) is not None}
        out.append({"lap": n, **row} if row else {"lap": n, **lap})
    return out


@tool("core")
def activity_hr_zones(activity_id: str) -> Any:
    """Time spent in each heart-rate zone during one activity.

    Args:
        activity_id: id from activities / activities_by_date
    """
    return shrink(api().get_activity_hr_in_timezones(activity_id))


# ---------------------------------------------------------- daily health


@tool("core")
def heart_rate_day(day: str, points: int = 96) -> dict[str, Any]:
    """All-day heart rate: resting/min/max plus a downsampled curve
    (default 96 points = one per 15 minutes). ts is epoch milliseconds.

    Args:
        day: YYYY-MM-DD
        points: how many points in the curve (max 720)
    """
    raw = api().get_heart_rates(day) or {}
    values = [
        {"ts": v[0], "bpm": v[1]}
        for v in raw.get("heartRateValues") or []
        if isinstance(v, (list, tuple)) and len(v) >= 2 and v[1] is not None
    ]
    curve = bucket_mean(values, ["ts", "bpm"], min(max(points, 10), 720))
    for row in curve:
        if "ts" in row:
            row["ts"] = int(row["ts"])
    return {
        "date": day,
        "resting": raw.get("restingHeartRate"),
        "min": raw.get("minHeartRate"),
        "max": raw.get("maxHeartRate"),
        "curve": curve,
    }


@tool("core")
def body_battery(start_date: str, end_date: str) -> Any:
    """Body Battery levels and events over a date range.

    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
    """
    return shrink(api().get_body_battery(start_date, end_date))


@tool("core")
def stress_day(day: str) -> Any:
    """All-day stress data.

    Args:
        day: YYYY-MM-DD
    """
    return shrink(api().get_stress_data(day))


@tool("core")
def daily_stats(day: str) -> Any:
    """Full daily summary: steps, calories, heart rate, stress, Body Battery,
    intensity minutes, floors, SpO2, respiration.

    Args:
        day: YYYY-MM-DD
    """
    return shrink(api().get_stats(day))


@tool("core")
def training_status(day: str) -> Any:
    """Training status: VO2max, training load, load focus, recovery.

    Args:
        day: YYYY-MM-DD
    """
    return shrink(api().get_training_status(day))


@tool("core")
def training_readiness(day: str) -> Any:
    """Training readiness score and its contributing factors for a day.

    Args:
        day: YYYY-MM-DD
    """
    return shrink(api().get_training_readiness(day))
