# treg.to catch-all autoreg — own .loc.cc domains via ForwardEmail -> Gmail IMAP
# Usage: python treg_catchall.py <count> [--pace 35] [--domains vortexio.loc.cc,apexnode.loc.cc,nexuslab.loc.cc]
import argparse, imaplib, email as emaillib, json, os, random, re, string, sys, threading, time
from datetime import datetime, timezone
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from treg_farm_v3 import TokenFactory, req_with_retry, ORG_WORDS, HDRS, log

BANK = os.path.join(os.environ.get("TREG_BANK_DIR", os.path.dirname(os.path.abspath(__file__))), "treg_catchall_bank.jsonl")
GMAIL = os.environ.get("TREG_CATCHALL_GMAIL", "")
APP_PW = os.environ.get("GMAIL_APP_PW", "")
if not GMAIL or not APP_PW:
    sys.exit("set TREG_CATCHALL_GMAIL + GMAIL_APP_PW (gmail address receiving the ForwardEmail catch-all copies)")
PROXY = ""  # set via --proxy / TREG_PROXY; used by TokenFactory browser + API sessions
WORDS1 = ["nova","lumen","ferra","quartz","onyx","delta","astra","vela","orion","pyra","zephyr","koda","rima","solis","tavi","nexa","umbra","cyra","halo","jure"]
WORDS2 = ["works","labs","studio","forge","works2","hub","space","craft","logic","base"]


def rand_addr(domain):
    w = random.choice(WORDS1) + random.choice(WORDS2)
    n = random.randint(10, 999)
    sep = random.choice(["", ".", "_"])
    return "%s%s%d@%s" % (w, sep, n, domain)


def poll_gmail_fwd(target_email, timeout=240, min_ts=None):
    """OTP via forwardemail catch-all -> GMAIL. Searches forwarded copies."""
    min_ts = min_ts or time.time()
    local = target_email.split("@")[0].lower()
    t_end = time.time() + timeout
    while time.time() < t_end:
        try:
            M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            M.login(GMAIL, APP_PW)
            M.select("INBOX")
            typ, data = M.search(None, "ALL")
            ids = data[0].split()
            for i in reversed(ids[-40:]):
                typ, msgd = M.fetch(i, "(RFC822)")
                if typ != "OK":
                    continue
                msg = emaillib.message_from_bytes(msgd[0][1])
                dt = msg.get("Date", "")
                try:
                    ts = emaillib.utils.parsedate_to_datetime(dt).timestamp()
                except Exception:
                    ts = 0
                if ts and ts < min_ts - 30:
                    continue
                to = (msg.get("To", "") + " " + msg.get("Delivered-To", "") + " " + msg.get("X-Original-To", "")).lower()
                if local not in to:
                    continue
                subj = msg.get("Subject", "")
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            try:
                                body = part.get_payload(decode=True).decode("utf-8", "ignore")
                            except Exception:
                                pass
                            break
                else:
                    try:
                        body = msg.get_payload(decode=True).decode("utf-8", "ignore")
                    except Exception:
                        body = str(msg.get_payload())
                m = re.search(r"(\d{6})", subj) or re.search(r"(?:code|Code|CODE)[^\d]{0,40}(\d{6})", body) or re.search(r">\s*(\d{6})\s*<", body) or re.search(r"\b(\d{6})\b", body[:600])
                if m:
                    M.logout()
                    return m.group(1)
            M.logout()
        except Exception as e:
            log("gmail poll err: %s" % str(e)[:80])
        time.sleep(6)
    return None


def reg_one(tf, domain, used_orgs, used_addr):
    addr = rand_addr(domain)
    while addr in used_addr:
        addr = rand_addr(domain)
    used_addr.add(addr)
    sess = requests.Session()
    sess.headers.update(HDRS)
    if PROXY:
        sess.proxies = {"http": PROXY, "https": PROXY}
    rec = {"email": addr, "domain": domain, "door": "catchall",
           "ts": datetime.now(timezone.utc).isoformat()}
    since_ts = time.time()
    tok = tf.token()
    if not tok:
        rec["stage"], rec["err"] = "token", "factory dead"
        return rec
    st, d = req_with_retry(sess, "POST", "/auth/email/start",
                           json={"email": addr, "captcha_token": tok})
    if st == 400 and "captcha" in json.dumps(d).lower():
        try:
            tf.pg.evaluate("()=>{window.tregCaptcha&&window.tregCaptcha.reset&&window.tregCaptcha.reset()}")
        except Exception:
            pass
        time.sleep(3)
        tok = tf.token()
        if tok:
            st, d = req_with_retry(sess, "POST", "/auth/email/start",
                                   json={"email": addr, "captcha_token": tok})
    if st != 200:
        rec["stage"], rec["err"] = "start", "%s %s" % (st, json.dumps(d)[:200])
        return rec
    code = poll_gmail_fwd(addr, timeout=240, min_ts=since_ts)
    if not code:
        rec["stage"], rec["err"] = "otp", "no code in 240s"
        return rec
    st, d = req_with_retry(sess, "POST", "/auth/email/verify", json={"email": addr, "code": code})
    if st != 200 or "token" not in d:
        rec["stage"], rec["err"] = "verify", "%s %s" % (st, json.dumps(d)[:200])
        return rec
    rec["token"] = d["token"]
    org_name = None
    for w in random.sample(ORG_WORDS, len(ORG_WORDS)):
        cand = "%s%d" % (w, random.randint(10000, 99999))
        if cand not in used_orgs:
            org_name = cand
            break
    st, d = req_with_retry(sess, "POST", "/orgs", json={"name": org_name},
                           headers={"X-Treg-Token": rec["token"]})
    if st >= 400:
        rec["stage"], rec["err"] = "org", "%s %s" % (st, json.dumps(d)[:200])
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
        rec["stage"], rec["err"] = "pin", "%s %s" % (st, json.dumps(d)[:200])
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
        st, d = req_with_retry(sess, "GET", "/orgs/%s/balance?limit=3" % rec["org_id"])
        if st == 200:
            rec["balance_micro"] = d.get("balance_micro", d.get("balance"))
        else:
            rec["bal_err"] = "%s %s" % (st, json.dumps(d)[:100])
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("count", type=int)
    ap.add_argument("--pace", type=int, default=35)
    ap.add_argument("--domains", default="vortexio.loc.cc,apexnode.loc.cc,nexuslab.loc.cc")
    ap.add_argument("--proxy", default=os.environ.get("TREG_PROXY", ""),
                    help="http(s) proxy for BOTH token browser and API calls (scale beyond 1-IP rate budget)")
    a = ap.parse_args()
    global PROXY
    PROXY = (a.proxy or "").strip()
    domains = [x.strip() for x in a.domains.split(",") if x.strip()]
    tf = TokenFactory(proxy=PROXY or None)
    tf.launch()
    used_orgs, used_addr = set(), set()
    # preload bank to avoid org-name collisions
    try:
        for l in open(BANK, encoding="utf-8"):
            try:
                r = json.loads(l)
                if r.get("org"):
                    used_orgs.add(str(r["org"]))
                if r.get("email"):
                    used_addr.add(r["email"])
            except Exception:
                pass
    except FileNotFoundError:
        pass
    ok = paid = 0
    for i in range(a.count):
        dom = domains[i % len(domains)]
        try:
            rec = reg_one(tf, dom, used_orgs, used_addr)
        except Exception as e:
            rec = {"stage": "crash", "err": str(e)[:200], "domain": dom,
                   "ts": datetime.now(timezone.utc).isoformat()}
        bal = rec.get("balance_micro") or 0
        with open(BANK, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if rec.get("org_token"):
            ok += 1
        if bal and int(bal) > 0:
            paid += 1
        log("%d/%d %s org=%s bal=$%.2f stage=%s %s" % (
            i + 1, a.count, rec.get("email", "?"), rec.get("org", "-"),
            int(bal) / 1e6, rec.get("stage", "OK"), str(rec.get("err", ""))[:90]))
        if i < a.count - 1:
            time.sleep(a.pace + random.randint(0, 10))
    log("DONE ok=%d paid=%d / %d" % (ok, paid, a.count))
    try:
        tf.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
