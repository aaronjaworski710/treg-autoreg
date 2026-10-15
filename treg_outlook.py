#!/usr/bin/env python3
"""treg_outlook.py — treg.to registration through REAL Outlook/Hotmail mailboxes.

Pipeline:
  1. accounts file (OutlookRegister output): email----password----client_id----refresh_token per line
  2. treg /auth/email/start with cloakbrowser TokenFactory (PASS turnstile tokens)
  3. OTP read via Microsoft Graph API (refresh_token -> access_token -> /me/messages)
  4. /auth/email/verify -> org create -> balance check -> bank jsonl

Usage:
  python treg_outlook.py <accounts.txt> [--out out/outlook_bank.jsonl] [--limit N] [--pace 25]

Env:
  TREG_LIB   dir containing treg_farm_v3.py (TokenFactory)   [default: script dir]
  TREG_BASE  treg API base                                    [default: https://treg.to]

No IMAP needed — Graph OAuth only (Thunderbird client_id works for consumer accounts).
"""
import sys, os, json, time, re, random, argparse
import urllib.request, urllib.parse, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("TREG_LIB", HERE))
BASE = os.environ.get("TREG_BASE", "https://treg.to")

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

CODE_RE = re.compile(r"\b(\d{6})\b")

def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)

def http_json(url, data=None, headers=None, method=None, timeout=40):
    hdrs = {"User-Agent": "Mozilla/5.0"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else json.dumps(data).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded" if isinstance(data, dict) else "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method or ("POST" if body else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode() or "null")
            return r.status, (d if isinstance(d, dict) else {"data": d})
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode() or "null")
            return e.code, (d if isinstance(d, dict) else {"data": d})
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"err": str(e)[:120]}

def graph_token(client_id, refresh_token):
    st, d = http_json("https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
                      data={"client_id": client_id, "refresh_token": refresh_token,
                            "grant_type": "refresh_token",
                            "scope": "https://graph.microsoft.com/.default offline_access"})
    if st == 200 and d.get("access_token"):
        return d["access_token"], d.get("refresh_token", refresh_token)
    return None, refresh_token

def graph_code(access_token, since_iso, timeout_s=180, poll=8):
    """Poll Graph /me/messages for the treg OTP sent after since_iso."""
    url = ("https://graph.microsoft.com/v1.0/me/messages"
           "?$top=15&$orderby=receivedDateTime desc&$select=subject,from,body,receivedDateTime")
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st, d = http_json(url, headers={"Authorization": "Bearer " + access_token})
        if st == 200:
            for m in d.get("value", []):
                if m.get("receivedDateTime", "") < since_iso:
                    continue
                sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
                subj = (m.get("subject") or "")
                body = ((m.get("body") or {}).get("content") or "")
                if "treg" in sender.lower() or "treg" in subj.lower() or "verification" in subj.lower():
                    mm = CODE_RE.search(body) or CODE_RE.search(subj)
                    if mm:
                        return mm.group(1)
        elif st in (401, 403):
            return None
        time.sleep(poll)
    return None

def load_accounts(path):
    accs = []
    for ln in open(path, encoding="utf-8", errors="ignore"):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split("----")
        if len(parts) >= 4:
            accs.append({"email": parts[0], "password": parts[1], "client_id": parts[2], "refresh_token": parts[3]})
        elif len(parts) == 2:
            accs.append({"email": parts[0], "password": parts[1], "client_id": "", "refresh_token": ""})
    return accs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("accounts")
    ap.add_argument("--out", default=os.path.join(HERE, "out", "outlook_bank.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pace", type=int, default=25)
    a = ap.parse_args()

    accs = load_accounts(a.accounts)
    if a.limit:
        accs = accs[:a.limit]
    log("accounts: %d" % len(accs))
    if not accs:
        return

    from treg_farm_v3 import TokenFactory, HDRS  # noqa: E402
    tf = TokenFactory()
    tf.launch()

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    done = set()
    if os.path.exists(a.out):
        for ln in open(a.out, encoding="utf-8", errors="ignore"):
            try:
                done.add(json.loads(ln)["email"])
            except Exception:
                pass

    ok = paid = 0
    for i, acc in enumerate(accs, 1):
        em = acc["email"]
        if em in done:
            continue
        rec = {"email": em, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": "start"}
        sess = requests.Session()
        sess.trust_env = False
        sess.headers.update(HDRS)
        try:
            tok = tf.token()
            if not tok:
                rec.update(stage="token", err="no turnstile token")
                raise RuntimeError("notoken")
            r = sess.post(BASE + "/auth/email/start", json={"email": em, "captcha_token": tok}, timeout=40)
            if r.status_code != 200:
                rec.update(stage="start", err="http %d %s" % (r.status_code, r.text[:120]))
                raise RuntimeError("start")
            since = time.gmtime(time.time() - 30)
            since_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", since)
            rec["stage"] = "otp"
            at, new_rt = (None, acc["refresh_token"])
            if acc["refresh_token"]:
                at, new_rt = graph_token(acc["client_id"], acc["refresh_token"])
            if not at:
                rec.update(err="graph token fail (need refresh_token from OutlookRegister)")
                raise RuntimeError("graph")
            code = graph_code(at, since_iso)
            if not code:
                rec.update(err="no otp in 180s")
                raise RuntimeError("otp")
            r = sess.post(BASE + "/auth/email/verify", json={"email": em, "code": code}, timeout=40)
            if r.status_code != 200:
                rec.update(stage="verify", err="http %d %s" % (r.status_code, r.text[:120]))
                raise RuntimeError("verify")
            vd = r.json() if r.text else {}
            if not isinstance(vd, dict):
                vd = {}
            rec.update(stage="org", token=(vd.get("token") or vd.get("access_token") or ""),
                       user_id=vd.get("user_id") or (vd.get("user") or {}).get("id"))
            # create org
            auth = {"Authorization": "Bearer " + rec["token"]} if rec["token"] else {}
            name = "mslab%d" % random.randint(10000, 99999)
            st, d = http_json(BASE + "/orgs", data={"name": name}, headers={**HDRS, **auth})
            if st == 200 and isinstance(d, dict):
                rec["org_id"] = d.get("id") or (d.get("org") or {}).get("id")
                rec["org_name"] = name
            # balance
            if rec.get("org_id") and rec.get("token"):
                st, d = http_json(BASE + "/orgs/%s/balance?limit=2" % rec["org_id"], headers={**HDRS, **auth})
                if st == 200 and isinstance(d, dict):
                    items = d.get("items") or d.get("entries") or []
                    rec["balance"] = d.get("balance", d.get("total"))
                    rec["grant_seen"] = any(("promo" in json.dumps(x).lower() or "$1" in json.dumps(x)) for x in items) if items else None
            rec["stage"] = "done"
            rec["refresh_token"] = new_rt
            ok += 1
            if rec.get("grant_seen"):
                paid += 1
        except RuntimeError as e:
            rec["err"] = rec.get("err") or str(e)
        except Exception as e:
            rec["err"] = "exc " + str(e)[:120]
        with open(a.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log("%d/%d %s stage=%s %s" % (i, len(accs), em, rec.get("stage"), ("OK bal=%s" % rec.get("balance")) if rec.get("stage") == "done" else rec.get("err", "")[:80]))
        time.sleep(a.pace)

    log("DONE ok=%d paid=%d / %d" % (ok, paid, len(accs)))
    try:
        tf.close()
    except Exception:
        pass

if __name__ == "__main__":
    main()
