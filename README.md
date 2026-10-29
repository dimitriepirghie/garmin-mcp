# garmin-mcp

A read-only [MCP](https://modelcontextprotocol.io) server that gives Claude
access to your own Garmin Connect data: sleep, HRV, heart rate, stress, Body
Battery, activities with full time series, training metrics, body composition,
devices, gear and more.

It talks to Garmin Connect directly with your own login token (via
[`garminconnect`](https://github.com/cyberjunky/python-garminconnect)), so
there are no third-party services and no call limits. It runs locally over
stdio, or 24/7 on a Raspberry Pi as a remote connector you can use from
Claude on any device.

- **261 tools**: 17 curated tools with compact, chart-ready output, plus one
  tool per garminconnect `get_*` method, generated from the library at startup
- **Read-only**: nothing can change or delete data in your Garmin account
- **Remote-ready**: Streamable HTTP, bearer-token auth, Tailscale Funnel,
  hardened systemd service
- **Works with `mcp` 1.x and 2.x**, Python 3.12+

---

## Contents

1. [How it works](#how-it-works)
2. [Requirements](#requirements)
3. [Quick start: local (stdio)](#quick-start-local-stdio)
4. [Run 24/7 on a Raspberry Pi](#run-247-on-a-raspberry-pi)
5. [Add it to Claude as a remote connector](#add-it-to-claude-as-a-remote-connector)
6. [Configuration](#configuration)
7. [Tools](#tools)
8. [Security model](#security-model)
9. [Troubleshooting](#troubleshooting)
10. [Development](#development)
11. [Limitations](#limitations)
12. [Credits](#credits)

---

## How it works

```
Local (stdio)
  Claude Desktop / Claude Code ──stdio──> garmin_mcp.py ──HTTPS──> Garmin Connect

Remote (Raspberry Pi)
  Claude (web, desktop, mobile)
        │  requests come from Anthropic's cloud
        ▼
  https://<pi>.<tailnet>.ts.net          Tailscale Funnel (public HTTPS, no port forwarding)
        │
        ▼
  http://127.0.0.1:8000/mcp              garmin-mcp.service on the Pi
        │  checks Authorization: Bearer <token>
        ▼
  Garmin Connect                         from your home IP
```

`login.py` logs in once (email, password, MFA) and saves a session token.
The server only ever loads and refreshes that token; it never logs in with a
password, so restarts can't trigger Garmin's login rate limit.

Running at home is deliberate: Garmin challenges logins from cloud datacenter
IPs, and a residential IP avoids that.

## Requirements

- Python **3.12+** (`garminconnect` requires it)
- [uv](https://docs.astral.sh/uv/)
- A Garmin Connect account
- For the Pi: Raspberry Pi OS **Trixie** (ships Python 3.13; Bookworm's 3.11 is
  too old) and a [Tailscale](https://tailscale.com) account

Dependencies: `mcp>=1.2.0`, `garminconnect>=0.3.20`, `uvicorn`, `anyio`.
`garth` is **not** used: garminconnect 0.3.x authenticates on its own and
rejects garth-format tokens.

## Quick start: local (stdio)

Windows PowerShell shown; on macOS/Linux use the same `uv` commands.

```powershell
# 1. Install uv (then reopen PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Install dependencies
cd $HOME\repo\garmin-mcp
uv sync

# 3. Log in once: email, password, MFA code if asked
uv run login.py
```

The token is saved to `.garmintokens\` (git-ignored). The password is never
stored.

**Register with Claude Code:**

```powershell
claude mcp add garmin -- uv --directory $HOME\repo\garmin-mcp run garmin_mcp.py
```

**Register with Claude Desktop:** edit `%APPDATA%\Claude\claude_desktop_config.json`,
then restart Claude Desktop:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uv",
      "args": ["--directory", "C:\\Users\\<you>\\repo\\garmin-mcp", "run", "garmin_mcp.py"]
    }
  }
}
```

A local stdio server only works in the app on that computer. To use Claude on
your phone too, run it remotely (next section).

## Run 24/7 on a Raspberry Pi

**[PI_SETUP.md](PI_SETUP.md)** has every command in order, with a test after
each stage. In short:

1. Flash Raspberry Pi OS Trixie, update, install build tools
   (`build-essential libffi-dev python3-dev pkg-config`): `cffi` has no armv7
   wheel and compiles on the Pi
2. Install Tailscale and uv
3. Create a `garmin-mcp` system user and `/var/lib/garmin-mcp`
4. Copy the project to `/opt/garmin-mcp`, run `uv sync --python /usr/bin/python3`
5. Log in once over SSH as the service user
6. Generate a bearer token into `/etc/garmin-mcp.env`
7. Install and start [`deploy/garmin-mcp.service`](deploy/garmin-mcp.service)
8. `sudo tailscale funnel --bg 8000`

Tested on a Raspberry Pi 2 Model B (armv7l, 1 GB RAM). Every dependency except
`cffi` has a prebuilt armv7 wheel for Python 3.13.

## Add it to Claude as a remote connector

Claude connects to remote connectors from Anthropic's cloud, so the server must
be reachable on the public internet. A plain Tailscale (tailnet-only) address
won't work; Funnel provides the public HTTPS address.

In Claude: **Customize → Connectors → Add custom connector**

| Field | Value |
|---|---|
| Name | anything, e.g. `Garmin` |
| URL | `https://<pi>.<tailnet>.ts.net/mcp` |
| Authentication | **No sign-in** (the "Sign in" options mean OAuth, which this server doesn't use) |
| Request headers | `authorization` = `Bearer <token>` (type `Bearer`, a space, then the token) |

Print the token on the Pi with:

```bash
sudo sed -n 's/^MCP_BEARER_TOKEN=//p' /etc/garmin-mcp.env
```

The connector belongs to your Claude account, so it also appears in the
Claude mobile and desktop apps. Turn it on per chat under **+ → Connectors**.

**No "Request headers" section in the dialog?** That feature is in beta and not
on every account. Set `MCP_PATH_TOKEN=1` in `/etc/garmin-mcp.env`, restart, and
use the URL `https://<pi>.<tailnet>.ts.net/<token>/mcp` with No sign-in. The
token is stripped before routing and logging, but it stays visible in your
connector settings.

Authentication settings can't be edited after adding a connector; to change
the header, remove the connector and add it again. The URL can be edited.

## Configuration

All settings are environment variables. On the Pi they go in
`/etc/garmin-mcp.env` (root-only, mode 600), or in the service file.

| Variable | Default | Purpose |
|---|---|---|
| `GARMIN_TOKEN_DIR` | `./.garmintokens` | Where `login.py` saves the token and the server reads it. Must be writable: garminconnect rewrites it when it refreshes the session |
| `GARMIN_TOOL_GROUPS` | `all` | Comma list of tool groups to load (see [Choosing groups](#choosing-groups)). `core` is always on |
| `GARMIN_MAX_RESULT_CHARS` | `150000` | Responses larger than this are sampled |
| `MCP_BEARER_TOKEN` | — | **Required in HTTP mode**, at least 32 characters. Generate with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` |
| `MCP_HOST` | `127.0.0.1` | Bind address. Keep it on localhost behind Funnel |
| `MCP_PORT` | `8000` | Bind port |
| `MCP_PATH_TOKEN` | off | `1` also accepts the token as the first path segment (`/<token>/mcp`) |

Command line:

```bash
uv run garmin_mcp.py          # stdio
uv run garmin_mcp.py --http   # Streamable HTTP with bearer auth
uv run garmin_mcp.py --list   # print registered tools by group, then exit
```

## Tools

Every tool only reads. `uv run garmin_mcp.py --list` prints the full list.

### Curated tools (group `core`, always on)

Compact output, date ranges and chart-ready series. Claude should reach for
these first.

| Tool | What it returns |
|---|---|
| `sleep_range(start_date, end_date)` | **Main sleep tool.** Sleep, HRV, resting HR and SpO2 night by night over any range (max 400 days), plus summary stats |
| `sleep_day(day)` | One night, compact |
| `hrv_day(day)` | HRV with Garmin's personal baseline |
| `day_overview(day)` | One call: sleep, HRV, readiness, steps, calories, HR, stress, Body Battery, activities |
| `activities(limit, start)` | Recent activities, newest first (ids for the activity tools) |
| `activities_by_date(start_date, end_date, activity_type)` | Activities in a date range, optionally one type (`cycling`, `running`, …) |
| `activity_details(activity_id, points, metrics)` | Time series inside one activity: heart rate, speed, elevation, power, cadence, temperature, downsampled for charts. `available_metrics` lists everything the device recorded |
| `activity_laps(activity_id)` | Per-lap distance, time, HR, speed, elevation, power |
| `activity_hr_zones(activity_id)` | Time in each heart-rate zone |
| `heart_rate_day(day, points)` | All-day heart-rate curve plus resting/min/max |
| `body_battery(start_date, end_date)` | Body Battery over a range |
| `stress_day(day)` | All-day stress |
| `daily_stats(day)` | Full daily summary |
| `training_status(day)` | VO2max, training load, recovery |
| `training_readiness(day)` | Readiness score and factors |
| `garmin_methods(contains)` / `garmin_get(method, args)` | List and call any read-only garminconnect method by name, even when its group is switched off |

Units follow Garmin: distance in metres, duration in seconds, speed in m/s,
timestamps in epoch milliseconds.

### Catalog tools (one per garminconnect `get_*` method)

Generated from the installed garminconnect at startup, so names, parameters
and types always match the library. Tool name = method name
(`get_activity_weather`, `get_max_metrics`, …).

| Group | Covers |
|---|---|
| `sleep` | Raw sleep record, naps, sleep stats by day / week / overall |
| `health` | Daily, weekly and monthly stats; heart rate, resting HR, HRV range, stress, Body Battery events, SpO2, respiration, hydration, intensity minutes, steps, floors, calories, blood pressure, ECG, glucose, health snapshots, jet lag, acclimation |
| `body` | Weight, weigh-ins, body composition |
| `activities` | Single activity, splits, typed splits, weather, segments, map / polyline, power curve and zones, swim, rounds, exercise sets, records, fastest splits, weekly stats |
| `training` | VO2max / max metrics, endurance and hill score, race predictions, lactate threshold, FTP, cycling ability, running economy and tolerance, stamina, training load and balance, fitness age, personal records, HR and power zones, 1RM, lifetime totals |
| `workouts_calendar` | Workouts, scheduled workouts, training plans, calendar, upcoming events |
| `profile` | User profile, settings, unit system, goals, privacy |
| `devices` | Devices, settings, alarms, sensors, solar, sync |
| `gear` | Gear list, stats, activities per gear |
| `badges_challenges` | Badges, challenges |
| `courses` | Courses |
| `nutrition` | Food log, meals, nutrition status |
| `womens_health` | Menstrual cycle, pregnancy |
| `golf` | Scorecards, shots, club stats |

### Choosing groups

All groups load by default: 261 tools, about 117 KB of tool definitions that
Claude receives with every chat. If Claude picks tools poorly or chats feel
slow, trim the set:

```bash
GARMIN_TOOL_GROUPS=sleep,health,body,activities,training   # 165 tools, ~78 KB
GARMIN_TOOL_GROUPS=core                                     # 17 tools, ~10 KB
```

`core` is always included, and `garmin_get` still reaches every method. After
changing groups, restart the server and reconnect the connector so Claude
reloads the tool list.

## Security model

The remote endpoint is public, and it serves personal health data.

- **Bearer token on every request**, compared in constant time. The server
  refuses to start without one of at least 32 characters.
- **Open paths reveal nothing**: `/healthz` returns `ok`; `/.well-known/*`
  returns `404` (Claude probes these for OAuth, and a `401` there makes the
  connector fail with "could not sign in").
- **Read-only**: only `get_*` methods are exposed or callable; `garmin_get`
  refuses anything else. Tools carry MCP read-only annotations.
- **Localhost bind**: the server listens on `127.0.0.1`; only Funnel reaches
  it. No router port forwarding.
- **Secrets on disk**: the bearer token is in a root-only file; the Garmin
  token is in a mode-700 directory owned by a dedicated system user.
- **systemd sandbox**: non-root user, `ProtectSystem=strict`, `ProtectHome`,
  `NoNewPrivileges`, restricted address families, 400 MB memory cap.
- **DNS-rebinding protection is off in HTTP mode**: it only allows localhost
  Host headers and would reject Funnel traffic. It guards browsers reaching
  unauthenticated local servers; this one rejects every request without the
  token.

Expect automated scanners in the log (requests for `/cpanel`, `/.env`,
`/.well-known/security.txt`, …) within minutes of going public. They get
`401`/`404` and nothing else.

To rotate the token: generate a new one into `/etc/garmin-mcp.env`, restart,
then remove and re-add the connector.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `login.py` fails with **429** | Garmin rate-limits SSO logins. Wait 30–60 minutes and try **once**; repeated attempts extend the block |
| `uv sync` fails building **`cffi`**: `ffi.h: No such file` | Missing build deps: `sudo apt install build-essential libffi-dev python3-dev pkg-config`, then `uv sync` again |
| Python version error on the Pi | Bookworm ships 3.11; flash Raspberry Pi OS **Trixie** (3.13) |
| Server won't start: `MCP_BEARER_TOKEN must be set` | Create `/etc/garmin-mcp.env` (PI_SETUP step 8) |
| Service crash-loops: `cannot import name ... from 'garmin_server' (unknown location)` | The service user can't read the copied files (scp from Windows), or `garmin_server/` is incomplete. `sudo chmod -R a+rX /opt/garmin-mcp`; check with `sudo -u garmin-mcp .venv/bin/python garmin_mcp.py --list` |
| Service status / logs | `systemctl status garmin-mcp --no-pager`, `journalctl -u garmin-mcp -n 50 --no-pager` |
| Tool error **No token found** | Run the one-time login as the service user (PI_SETUP step 7) |
| Connector: **could not sign in** | Choose **No sign-in**. Log shows `/.well-known/... 401`? You're on an old version; update |
| Connector: **couldn't reach**; log shows `POST / 404` | Old version without `/` → `/mcp`; update, or add `/mcp` to the connector URL |
| Log shows `POST /mcp 401` from `160.79.x.x` (Claude) | Header value wrong: it must be `Bearer <token>` with the space |
| No requests in the log at all | URL doesn't match `tailscale funnel status`, or Funnel is off |
| New tools don't appear in Claude | Disconnect and reconnect the connector, then start a new chat |
| Tool returns `_note: Large response…` | Response was sampled; use a shorter range or a curated tool |
| Garmin API errors after a Garmin update | `uv sync --upgrade`; garminconnect usually catches up within days |

Watch live requests from Claude on the Pi:

```bash
journalctl -u garmin-mcp -f -n 0 | grep --line-buffered 160.79
```

`160.79.x.x` is Anthropic. Drop the `grep` to see everything, including
tracebacks.

## Development

### Layout

| Path | Contents |
|---|---|
| `garmin_mcp.py` | Entry point: stdio, `--http`, `--list` |
| `login.py` | One-time interactive Garmin login (handles MFA) |
| `garmin_server/core.py` | MCP server object, Garmin client, `@tool` decorator (worker thread, read-only annotations, errors visible to the model), groups, response sampling, helpers |
| `garmin_server/curated.py` | Curated tools |
| `garmin_server/catalog.py` | Generated catalog tools, group rules, `garmin_methods` / `garmin_get` |
| `garmin_server/http.py` | Streamable HTTP app, bearer auth middleware |
| `deploy/garmin-mcp.service` | systemd unit |
| `tests/test_server.py` | Offline tests with a fake Garmin client |
| `PI_SETUP.md` | Raspberry Pi deployment guide |

### Tests

No Garmin account or network needed:

```bash
uv run --with pytest pytest -q
```

They cover catalog coverage, groups, read-only annotations, parameter
mapping, response sampling, `garmin_get` refusing non-read methods, the
curated tools, error visibility, the event loop staying free during Garmin
calls, and every auth path.

### Design notes

- **Catalog generation**: `catalog.py` walks `Garmin.get_*`, builds a typed
  signature from each method's type hints and registers it as a tool. A
  garminconnect upgrade adds new methods automatically. Methods a curated
  tool covers are skipped (`_COVERED`). Parameters starting with `_` are
  renamed (pydantic rejects them) and mapped back on the call.
- **Groups** are first-match substring rules in `_GROUP_RULES`; a test fails
  if any method lands in `other`.
- **Threading**: each tool runs on a worker thread through a capacity limiter
  of 1. Garmin calls are serialized (shared HTTP session, slow Pi), but the
  event loop stays free for auth checks and `/healthz`.
- **Errors**: exceptions are re-raised as MCP `ToolError` with the message, so
  the model sees *why* a call failed (mcp 2.x otherwise shows only "Error
  executing tool"). Tracebacks go to the log.
- **mcp 1.x / 2.x**: imports, `streamable_http_app` options and annotation
  field names are handled for both.
- **Stateless HTTP + JSON responses**: no sessions to expire, so restarts
  don't break the connector.

### Adding a curated tool

Write a plain synchronous function in `curated.py`, decorate it with
`@tool("core")`, give it a docstring with an `Args:` section (Claude reads
it), and wrap large raw responses in `shrink(...)`. Add a test with a
`FakeGarmin` method.

## Limitations

- The Garmin Connect API is unofficial. Fields move between versions; the
  sleep parser already looks in two places for most fields.
- Activity metric names vary by device; `activity_details` reports what was
  recorded under `available_metrics`.
- Garmin calls are serialized: a long `sleep_range` delays other tool calls.
- A tool returning a list sends one content block per item (MCP behaviour).
- Some catalog methods only return data for devices or features you have
  (golf, ECG, glucose, solar, …); others return empty results.

## Credits

- [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
  does all the talking to Garmin Connect.
- [taxuspt/garmin_mcp](https://github.com/taxuspt/garmin_mcp) inspired tool
  groups, read-only annotations and running Garmin calls off the event loop.
- [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk).

No license file yet: all rights reserved by default. Add one (for example MIT)
before publishing the repository.
