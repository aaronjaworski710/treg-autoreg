# treg-autoreg

Mass auto-registration toolkit for **[treg.to](https://treg.to/)** — the "OpenRouter for agent tools" (3955 endpoints, 111 providers, one API key).

Each fresh account gets a **$1.00 signup grant** (~1600–2000 tool calls: X/Twitter posts at $0.0005, TikTok profiles at $0.001, web search free). Zero captcha — pure email OTP flow.

> Part of the free-tools series. More finds: [t.me/AlStack](https://t.me/AlStack)

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
