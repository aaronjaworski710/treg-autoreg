# treg.to mass autoreg — email OTP via Gmail alias + IMAP, org create, Default-key pin
# Usage: python treg_farm.py <start_n> <count>   (aliases <imap_user>+<prefix><n>@gmail.com)
#        python treg_farm.py recover             (re-login accounts missing org_token)
# Config: config.json (see config.example.json) or env TREG_IMAP_USER / TREG_IMAP_PASS
# Output: treg_accounts.jsonl {email, token, org_token, org, org_id, balance_micro, ts}
import sys, json, time, re, random, os, imaplib, email.utils
import requests
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "treg_accounts.jsonl")
STATE = os.path.join(HERE, "treg_farm_state.json")

def load_cfg():
    cfg = {}
    p = os.path.join(HERE, "config.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["imap_user"] = cfg.get("imap_user") or os.environ.get("TREG_IMAP_USER", "")
    cfg["imap_pass"] = cfg.get("imap_pass") or os.environ.get("TREG_IMAP_PASS", "")
    if not cfg["imap_user"] or not cfg["imap_pass"]:
        sys.exit("ERROR: set imap_user/imap_pass in config.json or TREG_IMAP_USER/TREG_IMAP_PASS env")
    cfg.setdefault("imap_host", "imap.gmail.com")
    cfg.setdefault("alias_prefix", "treg")
    cfg.setdefault("base_url", "https://treg.to")
    cfg.setdefault("email_domain", cfg["imap_user"].split("@", 1)[1])
    cfg.setdefault("email_base", cfg["imap_user"].split("@", 1)[0])
    return cfg

CFG = load_cfg()
BASE = CFG["base_url"]
HDRS = {"Content-Type": "application/json", "X-Treg-Key-Protocol": "1"}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def alias(n):
    return f"{CFG['email_base']}+{CFG['alias_prefix']}{n}@{CFG['email_domain']}"

def req_with_retry(sess, method, path, retries=6, **kw):
    url = BASE + path
    for att in range(retries):
        try:
            r = sess.request(method, url, timeout=35, **kw)
            if r.status_code == 503 and "saturated" in r.text:
                time.sleep(8 + att * 7 + random.uniform(0, 5))
                continue
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"raw": r.text[:200]}
        except Exception as e:
            if att == retries - 1:
                return 0, {"err": str(e)[:150]}
            time.sleep(5 + att * 4)
    return 503, {"err": "saturated-exhausted"}

def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {"registered": [], "no_grant": [], "failed": []}

def save_state(st):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)

def imap_code(want_email, since_ts, timeout_s=120):
    """6-digit code (in email SUBJECT) for want_email; Date >= since_ts-30 guard."""
    deadline = time.time() + timeout_s
    m = None
    while time.time() < deadline:
        try:
            if m is None:
                m = imaplib.IMAP4_SSL(CFG["imap_host"])
                m.login(CFG["imap_user"], CFG["imap_pass"])
                m.select("INBOX", readonly=True)
            m.noop()  # CRITICAL: Gmail IMAP search results are stale without noop
            typ, data = m.search(None, "FROM", "treg.to")
            ids = data[0].split()
            for i in reversed(ids[-8:]):
                typ, msg = m.fetch(i, "(BODY.PEEK[HEADER.FIELDS (TO SUBJECT DATE)])")
                hdr = msg[0][1].decode("utf-8", "ignore")
                if want_email.lower() not in hdr.lower():
                    continue
                dm = re.search(r"Date: (.+)", hdr)
                if dm:
                    try:
                        dts = email.utils.parsedate_to_datetime(dm.group(1).strip()).timestamp()
                        if dts < since_ts - 30:
                            continue
                    except Exception:
                        pass
                subj = re.search(r"Subject: (\d{6})", hdr)
                if subj:
                    try: m.logout()
                    except Exception: pass
                    return subj.group(1)
        except Exception as e:
            log(f"imap err {e}")
            try:
                if m: m.logout()
            except Exception: pass
            m = None
        time.sleep(5)
    try:
        if m: m.logout()
    except Exception: pass
    return None

ORG_WORDS = ["reformlab", "vladwork", "gothlab", "stackyard", "promptden", "datahive",
             "keynest", "loopdock", "nodegarden", "byteshed", "orbitkit", "fluxden"]

def login_and_pin(email, want_org=None, org_name=None, used_orgs=None):
    """OTP login -> create org -> pin Default key (cookie-only!) -> balance."""
    sess = requests.Session()
    sess.headers.update(HDRS)
    rec = {"email": email, "ts": datetime.now(timezone.utc).isoformat()}
    since_ts = time.time()
    st, d = req_with_retry(sess, "POST", "/auth/email/start", json={"email": email})
    if st != 200:
        rec["stage"], rec["err"] = "start", f"{st} {json.dumps(d)[:150]}"
        return rec
    code = imap_code(email, since_ts)
    if not code:
        rec["stage"], rec["err"] = "imap", "no code"
        return rec
    st, d = req_with_retry(sess, "POST", "/auth/email/verify", json={"email": email, "code": code})
    if st != 200 or "token" not in d:
        rec["stage"], rec["err"] = "verify", f"{st} {json.dumps(d)[:150]}"
        return rec
    rec["token"] = d["token"]
    # org: existing (recover) or fresh random name ('treg' substring is RESERVED server-side)
    if want_org:
        org_name = want_org
    else:
        if not org_name:
            for w in random.sample(ORG_WORDS, len(ORG_WORDS)):
                cand = f"{w}{random.randint(100, 999)}"
                if used_orgs is None or cand not in used_orgs:
                    org_name = cand
                    break
        st, d = req_with_retry(sess, "POST", "/orgs", json={"name": org_name},
                               headers={"X-Treg-Token": rec["token"]})
        if st >= 400:
            rec["stage"], rec["err"] = "org", f"{st} {json.dumps(d)[:150]}"
            return rec
        if used_orgs is not None:
            used_orgs.add(org_name)
    rec["org"] = org_name
    time.sleep(1.5)
    # PIN: /auth/cli-token with SESSION COOKIE ONLY — sending the bootstrap token in
    # X-Treg-Token trips server-side 403 "a bootstrap token cannot mint a Default key".
    st, d = req_with_retry(sess, "GET", "/auth/cli-token", headers={"X-Treg-Org": org_name})
    if st == 200 and d.get("token"):
        rec["org_token"] = d["token"]
    else:
        rec["stage"], rec["err"] = "pin", f"{st} {json.dumps(d)[:150]}"
        return rec
    sess.headers["X-Treg-Token"] = rec["org_token"]
    st, d = req_with_retry(sess, "GET", "/orgs")
    org_id = None
    if st == 200:
        orgs = d if isinstance(d, list) else d.get("orgs", d.get("items", []))
        for o in orgs:
            if isinstance(o, dict) and (o.get("slug") or o.get("name")) == org_name:
                org_id = o.get("org_id") or o.get("id")
    rec["org_id"] = org_id
    if org_id:
        st, d = req_with_retry(sess, "GET", f"/orgs/{org_id}/balance?limit=3")
        if st == 200:
            rec["balance_micro"] = d.get("balance_micro", d.get("balance"))
        else:
            rec["bal_err"] = f"{st} {json.dumps(d)[:100]}"
    return rec

def merge_account(st, rec):
    st["registered"] = [r for r in st["registered"] if r.get("email") != rec["email"]]
    st["no_grant"] = [r for r in st["no_grant"] if r.get("email") != rec["email"]]
    if rec.get("balance_micro") and int(rec["balance_micro"]) > 0:
        st["registered"].append(rec)
    else:
        st["no_grant"].append(rec)
    save_state(st)
    bank = {}
    for src in (st["registered"], st["no_grant"]):
        for r in src:
            if r.get("token"):
                bank[r["email"]] = r
    with open(OUT, "w", encoding="utf-8") as f:
        for r in bank.values():
            f.write(json.dumps(r) + "\n")

def recover():
    st = load_state()
    stuck = [r for r in st.get("no_grant", []) if r.get("org") and not r.get("org_token")]
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            r = json.loads(l)
            if r.get("org") and not r.get("org_token") and not any(x["email"] == r["email"] for x in stuck):
                stuck.append(r)
    log(f"recover: {len(stuck)} stuck accounts")
    for r in stuck:
        rec = login_and_pin(r["email"], want_org=r["org"])
        if rec.get("org_token"):
            merge_account(st, rec)
            log(f"RECOVERED {r['email']} org={r['org']} bal={rec.get('balance_micro')}")
        else:
            log(f"RECOVER-FAIL {r['email']} {rec.get('stage')} {str(rec.get('err',''))[:100]}")
        time.sleep(random.uniform(10, 20))

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "recover":
        recover()
        return
    start_n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    st = load_state()
    done_emails = {r.get("email") for r in st["registered"]}
    used_orgs = {r.get("org") for r in st["registered"] + st["no_grant"] if r.get("org")}
    ok, nogrant, fail = 0, 0, 0
    consecutive_nogrant = 0
    for n in range(start_n, start_n + count):
        email = alias(n)
        if email in done_emails:
            continue
        rec = login_and_pin(email, org_name=None, used_orgs=used_orgs)
        if rec.get("org_token"):
            merge_account(st, rec)
            bal = rec.get("balance_micro")
            if bal and int(bal) > 0:
                ok += 1
                consecutive_nogrant = 0
                log(f"OK n={n} org={rec['org']} bal=${int(bal)/1e6:.2f} (ok={ok})")
            else:
                nogrant += 1
                consecutive_nogrant += 1
                log(f"NO-GRANT n={n} org={rec.get('org')} bal={bal} err={rec.get('bal_err','')} (nogrant={nogrant})")
        else:
            st["failed"].append({k: rec.get(k) for k in ("email", "stage", "err", "ts")})
            fail += 1
            save_state(st)
            log(f"FAIL n={n} stage={rec.get('stage')} err={str(rec.get('err',''))[:120]}")
            if rec.get("stage") == "start" and ("429" in str(rec.get("err", "")) or "rate" in str(rec.get("err", "")).lower()):
                log("RATE-LIMITED — cooling 900s (OTP IP window: 30 starts / 15 min)")
                time.sleep(900)
        if consecutive_nogrant >= 4:
            log("4 consecutive no-grant — stopping wave")
            break
        time.sleep(random.uniform(10, 22))
    total_bal = sum(int(r.get("balance_micro") or 0) for r in st["registered"])
    log(f"WAVE DONE ok={ok} nogrant={nogrant} fail={fail} | accounts_with_grant={len(st['registered'])} total=${total_bal/1e6:.2f}")

if __name__ == "__main__":
    main()
