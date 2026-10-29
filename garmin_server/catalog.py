"""One read-only tool per garminconnect `get_*` method, generated from the
library itself so names, parameters and types always match the installed
version. Grouped by area; GARMIN_TOOL_GROUPS selects which groups load.

Plus garmin_methods / garmin_get (group "core", always on): list and call any
read method by name, including ones whose group is switched off.
"""
from __future__ import annotations

import inspect
import typing
from typing import Any

from garminconnect import Garmin

from .core import api, shrink, tool

# Methods a curated tool already covers in a better shape.
_COVERED = {
    "get_activities",          # activities
    "get_activities_by_date",  # activities_by_date
    "get_activity_details",    # activity_details (parsed + downsampled)
    "get_activity_laps",       # activity_laps
    "get_activity_hr_in_timezones",  # activity_hr_zones
    "get_heart_rates",         # heart_rate_day
    "get_hrv_data",            # hrv_day
    "get_body_battery",        # body_battery
    "get_stress_data",         # stress_day
    "get_stats",               # daily_stats
    "get_training_status",     # training_status
    "get_training_readiness",  # training_readiness
}

# First match wins, so the more specific groups come first.
_GROUP_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("womens_health", ("menstrual", "pregnancy")),
    ("golf", ("golf",)),
    ("nutrition", ("nutrition",)),
    ("badges_challenges", ("badge", "challenge")),
    ("gear", ("gear",)),
    ("courses", ("course",)),
    ("devices", ("device", "sensors", "alarm", "solar", "sync_timestamp")),
    ("activities", ("get_activit", "get_last_activity", "get_adjacent_activities",
                    "get_favorite_activities", "get_fastest_splits",
                    "get_personal_records_for_activity", "get_weekly_activity_stats")),
    ("sleep", ("sleep", "get_naps")),
    ("workouts_calendar", ("workout", "calendar", "training_plan", "scheduled",
                           "upcoming_events", "primary_event")),
    ("training", ("training", "readiness", "max_metrics", "endurance", "hill_score",
                  "race_prediction", "lactate", "ftp", "functional_threshold",
                  "cycling_ability", "running_", "stamina", "critical_swim", "power_",
                  "heat_altitude", "one_rep_max", "strength", "heart_rate_zones",
                  "available_metrics", "progress_summary", "lifetime_totals",
                  "personal_record", "fitness_age", "fitnessage", "pace_bands")),
    ("body", ("weight", "weigh", "body_composition")),
    ("health", ("hrv", "stats", "heart_rate", "rhr", "resting_hr", "stress", "body_battery",
                "spo2", "respiration", "hydration", "intensity", "floors", "steps",
                "movement", "calor", "all_day", "lifestyle", "health_snapshot", "ecg",
                "glucose", "blood_pressure", "wellness", "summar", "biometric",
                "jetlag", "acclimation")),
    ("profile", ("user", "usage", "personal_information", "full_name", "unit_system",
                 "privacy", "goals", "timezone", "report_status")),
)

_DATE_PARAMS = {"cdate", "startdate", "enddate", "fordate", "today_date", "current_date",
                "start_date", "end_date"}


def group_of(method: str) -> str:
    for group, needles in _GROUP_RULES:
        if any(n in method for n in needles):
            return group
    return "other"


def read_methods() -> dict[str, Any]:
    """Every public read-only method on the Garmin client (name starts with get_)."""
    return {
        name: getattr(Garmin, name)
        for name in dir(Garmin)
        if name.startswith("get_") and callable(getattr(Garmin, name))
    }


def _param_doc(p: inspect.Parameter, hint: Any) -> str:
    text = p.name.lstrip("_")
    is_str = hint is str or (typing.get_origin(hint) is typing.Union and str in typing.get_args(hint))
    if is_str and (p.name in _DATE_PARAMS or p.name in ("start", "end")):
        text += ": YYYY-MM-DD"
    if p.default is not inspect.Parameter.empty:
        text += f" (default {p.default!r})"
    return text


def _make_tool(method: str, fn: Any, group: str) -> None:
    hints = typing.get_type_hints(fn)
    sig = inspect.signature(fn)
    params: list[inspect.Parameter] = []
    rename: dict[str, str] = {}  # tool param -> library param
    docs: list[str] = []
    for p in sig.parameters.values():
        if p.name == "self":
            continue
        public = p.name.lstrip("_") or p.name  # pydantic rejects leading "_"
        rename[public] = p.name
        hint = hints.get(p.name, Any)
        params.append(
            inspect.Parameter(public, inspect.Parameter.KEYWORD_ONLY, default=p.default, annotation=hint)
        )
        docs.append("    " + _param_doc(p, hint))

    summary = (inspect.getdoc(fn) or method.replace("_", " ")).strip().splitlines()[0]
    description = f"{summary} [Garmin read-only · {group}]"
    if docs:
        description += "\n\nArgs:\n" + "\n".join(docs)

    def impl(**kwargs: Any) -> Any:
        call = {rename[k]: v for k, v in kwargs.items()}
        return shrink(getattr(api(), method)(**call))

    impl.__name__ = method
    impl.__qualname__ = method
    impl.__doc__ = description
    impl.__signature__ = inspect.Signature(params, return_annotation=Any)  # type: ignore[attr-defined]
    tool(group, name=method, description=description)(impl)


def register_catalog() -> None:
    for method, fn in sorted(read_methods().items()):
        if method not in _COVERED:
            _make_tool(method, fn, group_of(method))


register_catalog()


# ------------------------------------------------------ escape hatch (core)


@tool("core")
def garmin_methods(contains: str = "") -> list[str]:
    """List every read-only Garmin Connect method with its parameters and
    group. Callable through garmin_get even when its group is switched off.

    Args:
        contains: optional substring filter, e.g. "activity", "weight", "spo2"
    """
    out = []
    for name, fn in sorted(read_methods().items()):
        if contains.lower() in name.lower():
            params = [str(p) for p in inspect.signature(fn).parameters.values() if p.name != "self"]
            out.append(f"{name}({', '.join(params)})  [{group_of(name)}]")
    return out


@tool("core")
def garmin_get(method: str, args: dict[str, Any] | None = None) -> Any:
    """Call any read-only Garmin Connect method by name (see garmin_methods).

    Only methods starting with get_ are allowed. Large results are sampled.

    Args:
        method: e.g. "get_activity_weather"
        args: keyword arguments, e.g. {"activity_id": "123456"}
    """
    if method not in read_methods():
        raise ValueError(f"Unknown or non-read method {method!r}. Call garmin_methods first.")
    return shrink(getattr(api(), method)(**(args or {})))
