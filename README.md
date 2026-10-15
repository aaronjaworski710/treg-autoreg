# treg-autoreg

Mass auto-registration toolkit for **[treg.to](https://treg.to/)** — the "OpenRouter for agent tools" (3955 endpoints, 111 providers, one API key).

Each fresh account gets a **$1.00 signup grant** (~1600–2000 tool calls: X/Twitter posts at $0.0005, TikTok profiles at $0.001, web search free). Zero captcha — pure email OTP flow.

> Part of the free-tools series. More finds: [t.me/AlStack](https://t.me/AlStack)

## Mail doors — which mailboxes pay the $1 grant (Oct 2026)

The grant is **not** domain-reputation based (reversed from treg source):
`promo_eligible = turnstile_outcome == "pass"`, paid once per *promo inbox*
(+tags and gmail dots dedupe; disposable domains sit in an env blocklist;
gmail = $0 for alias farms because of the inbox dedupe).

- `PROVIDERS.md` — ranked provider/door matrix (235-domain probe list, verdicts live in `domains.json`)
- `domain_probe3.py` — resumable blocklist prober: cloakbrowser token factory
  (`window.tregCaptcha.token('email_start')`, instant PASS tokens) + 30 req/15 min per-IP pacing
- `domains.json` — machine-readable verdict matrix (regenerate: `python build_domains_json.py`)
- `treg_farm_v3.py` — current farm core (turnstile era) incl. `TokenFactory`
- `treg_outlook.py` — treg registration through real Outlook/Hotmail mailboxes (Graph OTP, no IMAP)
- `OUTLOOK_AUTOREG.md` — Outlook mass-autoreg toolchain (free Arkose press&hold, MIT fork)
- `WP_REGISTER_API.md` — decoded WP Holding (wp.pl / o2.pl / tlen.pl) register API contract
- `gh_oauth_reg.py` / `gh_session.py` — GitHub OAuth door (no mailbox needed)

Turnstile sitekey (public, in page HTML): `0x4AAAAAAFVJ1qUhtZ_qG-N7`.
`/auth/email/start` now requires `captcha_token` — the old "zero captcha" flow below is historical.

**Proven paying doors (live, Oct 15):** own catch-all domains behind ForwardEmail → Gmail IMAP OTP
(`treg_catchall.py`): distinct local-parts each get their own $1 (27/27, then 6/6 in one wave;
7th+ hit the 30-start/15min IP budget → 429). Probe verdict: **141 ACCEPTED / 5 BLOCKED of 146 domains**
(`domains.json`) — the blocklist is tiny; almost any mailbox family pays if you control the OTP.

## Dashboard (control plane)

`dashboard/` — logicc-style dark UI, stdlib-only (`python dashboard/dashboard.py`, default `:8750`):

- env: `TREG_DATA` (dir with `*.jsonl` banks, `*.log` waves and wave scripts), `TREG_DASH_PORT`, `TREG_DASH_HOST`, `TREG_PYTHON`
- auto-discovers banks/logs (no hardcoded names), masks emails/tokens
- panels: per-door stats (orgs/paid/$), masked bank browser, wave start/stop (catchall · voidash · outlook), log tail, live balance probe, gateway start/stop/token-import, mail-doors verdict matrix
- `treg_gw.py` — optional OpenAI-compatible gateway (`:8751`, `X-GW-Key`, rotates org tokens)

```bash
TREG_DATA=/path/to/treg_test python dashboard/dashboard.py   # http://127.0.0.1:8750
```

## How it works (full protocol, reverse-engineered)

```
POST /auth/email/start   {"email": "you+treg1@gmail.com"}        -> {"sent": true}
  OTP: 6-digit code arrives in the email SUBJECT (sender treg.to)
POST /auth/email/verify  {"email", "code"}                        -> {"token"} + Set-Cookie session
POST /orgs               {"name": "myorg123"}   [X-Treg-Token]    -> org created, grant $1 (async ~2s)
GET  /auth/cli-token     [X-Treg-Org: myorg123, SESSION COOKIE]   -> {"token": <org_key>}   <-- usable API key
GET  /orgs/{id}/balance  [X-Treg-Token: org_key]                  -> {"balance_micro": 1000000}
```

All requests carry `X-Treg-Key-Protocol: 1`.

### Pitfalls this toolkit solves

| Pitfall | Fix |
|---|---|
| `GET /auth/cli-token` returns **403** "a bootstrap token cannot mint a Default key" | Send the verify **session cookie only**, NO `X-Treg-Token` header on that request |
| Org names containing `treg` are rejected | Neutral random names (`keynest983`, `gothlab331`, ...) |
| Gmail IMAP `search` returns **stale** results | `m.noop()` before every search |
| Re-issued `start` invalidates the previous code | Filter mails by `Date >= since_ts - 30` |
| Render backend saturates (503 `treg_saturated: true`) | Exponential backoff retry on every call |
| IP budget: 30 OTP starts / 15 min | Pacing 10–22 s between accounts, auto-cool 900 s on 429 |
| Grant is per-**user**, once (DB-locked) | One Gmail `+alias` = one user = one $1 |

## Setup

```bash
# Python 3.10+, only dependency: requests
pip install requests

# 1. create config
cp config.example.json config.json
# 2. edit config.json: put your Gmail address + IMAP app password
#    (Gmail: Security -> 2-Step Verification -> App passwords)
```

No secrets in the repo — `config.json` is gitignored. Env vars `TREG_IMAP_USER` / `TREG_IMAP_PASS` work too.

## Usage

```bash
# register accounts 1..30 (aliases you+treg1 ... you+treg30)
python treg_farm.py 1 30

# re-login accounts that have an org but no pinned key (recovers 403-stuck accounts)
python treg_farm.py recover

# live-check the bank: balances + pinned keys
python verify_bank.py
```

Output:
- `treg_accounts.jsonl` — the bank: `{email, token, org_token, org, org_id, balance_micro, ts}`
- `treg_farm_state.json` — resumable wave state (`registered` / `no_grant` / `failed`)

Auto-stop: 4 consecutive accounts without grant. The wave is resumable — rerun with a new start index any time.

## Using the keys

```bash
# raw HTTP
curl https://treg.to/call/treg.web.search \
  -H "Authorization: Bearer ***" \
  -H "Content-Type: application/json" \
  -d '{"q": "hello world"}'

# official CLI
pip install "tools-registry[proxy]"
treg login --token ***
treg call treg.x.user.posts --data '{"username": "elonmusk", "limit": 3}'
treg balance
```

Prices (live-verified): X user posts **$0.0005**, X search $0.00075, TikTok profile **$0.001**, web search **free** (19 providers), email verify free. Platform margin 0% — you pay provider cost. BYOK keys are unmetered.

## Rate limits (from source)

- 30 OTP starts / 15 min / IP, 5 / email
- 1 signup grant per user (not per IP) — alias farming is the intended scale path
- Backend on Render: expect occasional 503 saturation windows

## Disclaimer

For authorized security research / personal API usage on your own accounts only. Respect treg.to ToS.

---
[t.me/AlStack](https://t.me/AlStack) — free AI tools, APIs, proxies & farming drops
