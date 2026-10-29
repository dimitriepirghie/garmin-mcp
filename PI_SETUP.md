# Garmin MCP on a Raspberry Pi 2 (24/7, remote connector)

Target: Raspberry Pi 2 Model B (armv7l, 1 GB), Raspberry Pi OS **Trixie**
(Python 3.13). The server runs as a systemd service, listens on
`127.0.0.1:8000` only, and is published over HTTPS by **Tailscale Funnel**.
No router port forwarding.

```
Claude (Anthropic cloud) ──HTTPS──> https://<pi>.<tailnet>.ts.net  (Tailscale Funnel)
                                          │
                                          └─> http://127.0.0.1:8000/mcp  (garmin-mcp.service)
                                                 checks Authorization: Bearer <token>
```

Why Funnel and not plain Tailscale: Claude's remote connectors connect from
Anthropic's cloud, and the server "must be reachable over the public internet
from Anthropic's IP ranges". A tailnet-only address is unreachable for them.
Funnel gives a public HTTPS URL without opening a port; the bearer token is
what keeps it private.

Placeholders used below: `<pi-user>` = your login on the Pi, `<pi-ip>` = its LAN
address, `<pi-host>` = its Funnel hostname (`<name>.<tailnet>.ts.net`).

---

## 1. OS and build tools (on the Pi, over SSH)

```bash
sudo apt update && sudo apt full-upgrade -y && sudo reboot
# reconnect, then:
grep VERSION_CODENAME /etc/os-release   # trixie
python3 --version                       # 3.13.x
sudo apt install -y build-essential libffi-dev python3-dev pkg-config curl
```

The build tools are needed because `cffi` (a dependency of `curl_cffi`) has no
armv7 wheel on PyPI and compiles from source. Everything else has armv7 wheels
for Python 3.13, including `curl_cffi`, `pydantic-core`, `cryptography` and
`rpds-py`.

## 2. Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up          # open the printed URL and approve the Pi
tailscale status           # the Pi should be listed
```

## 3. uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv --version
```

(armv7 Linux is a Tier 2 uv platform.)

## 4. Service user and folders

```bash
sudo useradd --system --home-dir /var/lib/garmin-mcp --shell /usr/sbin/nologin garmin-mcp
sudo install -d -o garmin-mcp -g garmin-mcp -m 700 /var/lib/garmin-mcp
sudo install -d -o "$USER" -g "$USER" -m 755 /opt/garmin-mcp
```

- `/opt/garmin-mcp`: code and venv, owned by you, read-only for the service.
- `/var/lib/garmin-mcp/tokens`: Garmin token, owned by `garmin-mcp`, mode 700.
  The service must be able to write it: garminconnect rewrites the token file
  each time it refreshes the session.

## 5. Copy the project (PowerShell on the laptop)

```powershell
cd $HOME\repo\garmin-mcp
scp -r garmin_mcp.py login.py pyproject.toml garmin_server deploy\garmin-mcp.service <pi-user>@<pi-ip>:/opt/garmin-mcp/
```

Do not copy `.venv\` (Windows binaries) or `.garmintokens\`; the Pi logs in on
its own in step 7.

Then, on the Pi, make the files readable for the service user. scp from
Windows can create them readable only by you, and the service then fails with
`ImportError: cannot import name ... from 'garmin_server' (unknown location)`:

```bash
sudo chmod -R a+rX /opt/garmin-mcp
```

## 6. Install dependencies (on the Pi)

```bash
cd /opt/garmin-mcp
uv sync --python /usr/bin/python3
.venv/bin/python -c "import garminconnect, mcp, curl_cffi, uvicorn; print('ok')"
```

`--python /usr/bin/python3` uses the system 3.13 instead of downloading one.
Expect the `cffi` compile to take a few minutes on a Pi 2.

## 7. One-time Garmin login (on the Pi)

```bash
sudo -u garmin-mcp env GARMIN_TOKEN_DIR=/var/lib/garmin-mcp/tokens \
  /opt/garmin-mcp/.venv/bin/python /opt/garmin-mcp/login.py
```

Enter email, password and the MFA code when asked. It runs **once**. If it
prints a 429, wait 30-60 minutes and run it once more, not in a loop.

```bash
sudo ls -l /var/lib/garmin-mcp/tokens    # garmin_tokens.json, -rw-------
```

The service itself never logs in with a password. It only loads and refreshes
this token, so restarts and crash loops cannot trigger SSO rate limits.

## 8. Bearer token

```bash
sudo install -m 600 -o root -g root /dev/null /etc/garmin-mcp.env
echo "MCP_BEARER_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  | sudo tee /etc/garmin-mcp.env > /dev/null
sudo cat /etc/garmin-mcp.env     # copy the value for step 11
```

The server refuses to start if the token is missing or shorter than 32
characters. The file is root-only; systemd reads it before switching to the
`garmin-mcp` user.

## 9. systemd service

```bash
sudo cp /opt/garmin-mcp/garmin-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now garmin-mcp
systemctl status garmin-mcp --no-pager
```

Starts at boot, restarts 10 s after a failure, runs as `garmin-mcp` with a
hardened sandbox and a 400 MB memory cap.

Test locally on the Pi:

```bash
TOKEN=$(sudo sed -n 's/^MCP_BEARER_TOKEN=//p' /etc/garmin-mcp.env)
curl -s http://127.0.0.1:8000/healthz; echo                                   # ok
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/mcp    # 401
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"sleep_day\",\"arguments\":{\"day\":\"$(date -d yesterday +%F)\"}}}"
```

The last command should return last night's sleep. Errors appear in
`journalctl -u garmin-mcp -n 50`. With mcp 2.x the client sees only "Error
executing tool …", and the full traceback is in the journal.

## 10. Tailscale Funnel

```bash
sudo tailscale funnel --bg 8000
tailscale funnel status          # shows https://<pi-host>
```

Funnel needs MagicDNS, HTTPS certificates and the `funnel` node attribute on
your tailnet. If the command says Funnel isn't enabled, follow the link it
prints, or enable them in the admin console (DNS page: MagicDNS + HTTPS
Certificates) and add this to the policy file:

```json
"nodeAttrs": [{ "target": ["autogroup:member"], "attr": ["funnel"] }]
```

`--bg` keeps Funnel running across reboots and Tailscale restarts. To turn it
off: `sudo tailscale funnel reset`.

Test from the laptop (PowerShell; the first request may take a while while
the certificate is issued):

```powershell
curl.exe -s https://<pi-host>/healthz                                    # ok
curl.exe -s -o NUL -w "%{http_code}" -X POST https://<pi-host>/mcp       # 401
```

## 11. Add it in Claude

**Customize → Connectors → Add custom connector**

| Field | Value |
|---|---|
| Name | `Garmin` |
| URL | `https://<pi-host>/mcp` |
| Authentication | **No sign-in** |
| Request headers | header `authorization`, value `Bearer <token>` (include `Bearer` and the space), Required |

Claude sends the value exactly as typed and adds no prefix. Header values
can't be viewed or edited after saving; to change one, remove the connector
and add it again.

**If your dialog has no "Request headers" section:** that feature is in beta
and isn't enabled for every account. Use the path-token fallback instead:

```bash
echo "MCP_PATH_TOKEN=1" | sudo tee -a /etc/garmin-mcp.env > /dev/null
sudo systemctl restart garmin-mcp
```

Then add the connector with URL `https://<pi-host>/<token>/mcp` and
**No sign-in**. The server strips the token from the path before routing and
logging. The trade-off: the URL, token included, stays visible in your
connector settings.

Once it works, you can remove the local `garmin` entry from
`claude_desktop_config.json` so the tools don't show up twice.

---

## Operations

| Task | Command (on the Pi) |
|---|---|
| Logs | `journalctl -u garmin-mcp -f` |
| Restart | `sudo systemctl restart garmin-mcp` |
| Update deps | `cd /opt/garmin-mcp && uv sync --upgrade && sudo systemctl restart garmin-mcp` |
| Update code | Rerun the `scp -r` line from step 5, then `sudo chmod -R a+rX /opt/garmin-mcp`, `uv sync --python /usr/bin/python3`, restart |
| List tools | `cd /opt/garmin-mcp && .venv/bin/python garmin_mcp.py --list` |
| Trim tool groups | Add e.g. `GARMIN_TOOL_GROUPS=sleep,health,body,activities,training` to `/etc/garmin-mcp.env`, restart, then reconnect the connector |
| Re-login (token rejected) | Rerun step 7 once, then restart |
| Rotate bearer token | Rerun step 8, restart, then remove and re-add the connector |
| Funnel status / off | `tailscale funnel status` / `sudo tailscale funnel reset` |

## Notes

- Garmin calls run one at a time on a worker thread. A long `sleep_range`
  delays other tool calls but never blocks auth checks or `/healthz`. Keep
  ranges to a few months per call.
- All tools are read-only (see README "Tools"). Tool errors, such as a missing
  token or a Garmin 404, reach Claude as text; tracebacks go to the journal.
- `/.well-known/*` returns 404 on purpose: Claude probes it for OAuth, and a
  401 there makes the connector fail with "could not sign in".
- HTTP mode is stateless JSON (`stateless_http`, `json_response`): no
  long-lived sessions, so a restart doesn't break the connector.
- DNS-rebinding protection is off in HTTP mode, because it only allows
  localhost Host headers and would reject Funnel traffic. It guards against
  browsers reaching unauthenticated local servers; this server rejects every
  request without the token.
- stdio mode is unchanged: `uv run garmin_mcp.py` on the laptop still works.
