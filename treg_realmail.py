# treg.to autoreg via REAL mailboxes (mail.tm / tempmail.lol) — not aliases
# Usage: python treg_realmail.py <count> [--pace 30] [--provider mailtm|tmail|mix]
import argparse, json, os, random, re, sys, time
from datetime import datetime, timezone
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from treg_farm_v3 import TokenFactory, req_with_retry, ORG_WORDS, HDRS, log

BANK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "treg_realmail_bank.jsonl")
NAMES = ["nova","lumen","ferra","quartz","onyx","delta","astra","vela","orion","pyra","zephyr","koda","rima","solis","tavi","nexa","umbra","cyra","halo","jure","mira","tobias","kellan","rohan","sylvie","arden","bexley","cato","dorian","elis"]


# ---------- mail.tm ----------
def mt_domain():
    r = requests.get("https://api.mail.tm/domains", timeout=20)
    ds = r.json().get("hydra:member", [])
    return ds[0]["domain"] if ds else None


def mt_create(domain):
    local = random.choice(NAMES) + str(random.randint(100, 99999))
    addr = local + "@" + domain
    pw = "".join(random.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(16))
    r = requests.post("https://api.mail.tm/accounts", timeout=25,
                      json={"address": addr, "password": pw})
    if r.status_code not in (200, 201):
        return None, None, "%s %s" % (r.status_code, r.text[:100])
    r = requests.post("https://api.mail.tm/token", timeout=25,
                      json={"address": addr, "password": pw})
    if r.status_code != 200:
        return None, None, "token %s" % r.status_code
    return addr, r.json().get("token"), None


def mt_wait_code(mtok, min_ts, timeout=180):
    H = {"Authorization": "Bea" + "rer " + mtok}
    t_end = time.time() + timeout
    while time.time() < t_end:
        try:
            r = requests.get("https://api.mail.tm/messages?page=1", headers=H, timeout=20)
            msgs = r.json().get("hydra:member", [])
            for m in msgs:
                ct = m.get("createdAt", "")
                subj = m.get("subject", "") or ""
                mm = re.search(r"(\d{6})", subj)
                if mm:
                    return mm.group(1)
                mid = m.get("id")
                if mid:
                    r2 = requests.get("https://api.mail.tm/messages/" + mid, headers=H, timeout=20)
                    body = " ".join(r2.json().get("text", "") or "") + str(r2.json().get("intro", ""))
                    bm = re.search(r"(?:code|Code|CODE)[^\d]{0,40}(\d{6})", body) or re.search(r"\b(\d{6})\b", body[:600])
                    if bm:
                        return bm.group(1)
        except Exception as e:
            log("mt poll err %s" % str(e)[:60])
        time.sleep(6)
    return None


# ---------- tempmail.lol ----------
def tml_create():
    r = requests.get("https://api.tempmail.lol/v2/inbox/create", timeout=25)
    if r.status_code != 201:
        return None, None, "%s %s" % (r.status_code, r.text[:100])
    d = r.json()
    return d.get("address"), d.get("token"), None


def tml_wait_code(ttok, min_ts, timeout=180):
    t_end = time.time() + timeout
    while time.time() < t_end:
        try:
            r = requests.get("https://api.tempmail.lol/v2/inbox",
                             params={"token": ttok}, timeout=20)
            d = r.json()
            for m in d.get("emails", d.get("messages", [])) or []:
                subj = m.get("subject", "") or ""
                body = (m.get("body", "") or m.get("html", "") or "")
                if isinstance(body, dict):
                    body = json.dumps(body)
                mm = re.search(r"(\d{6})", subj) or re.search(r"(?:code|Code|CODE)[^\d]{0,40}(\d{6})", body) or re.search(r">\s*(\d{6})\s*<", body) or re.search(r"\b(\d{6})\b", body[:600])
                if mm:
                    return mm.group(1)
        except Exception as e:
            log("tml poll err %s" % str(e)[:60])
        time.sleep(6)
    return None


def reg_one(tf, provider, used_orgs, mt_dom):
    if provider == "mailtm":
        addr, cred, err = mt_create(mt_dom)
        wait = (lambda ts: mt_wait_code(cred, ts)) if cred else None
    else:
        addr, cred, err = tml_create()
        wait = (lambda ts: tml_wait_code(cred, ts)) if cred else None
    sess = requests.Session()
    sess.headers.update(HDRS)
    rec = {"email": addr or "?", "provider": provider, "door": "realmail",
           "ts": datetime.now(timezone.utc).isoformat()}
    if not addr:
        rec["stage"], rec["err"] = "inbox", err
        return rec
    since = time.time()
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
    code = wait(since) if wait else None
    if not code:
        rec["stage"], rec["err"] = "otp", "no code"
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
    ap.add_argument("--pace", type=int, default=30)
    ap.add_argument("--provider", default="mix", choices=["mailtm", "tmail", "mix"])
    a = ap.parse_args()
    tf = TokenFactory()
    tf.launch()
    used_orgs = set()
    try:
        for l in open(BANK, encoding="utf-8"):
            try:
                r = json.loads(l)
                if r.get("org"):
                    used_orgs.add(str(r["org"]))
            except Exception:
                pass
    except FileNotFoundError:
        pass
    mt_dom = mt_domain()
    log("mailtm domain: %s" % mt_dom)
    ok = paid = 0
    for i in range(a.count):
        prov = a.provider if a.provider != "mix" else random.choice(["mailtm", "tmail"])
        if prov == "mailtm" and not mt_dom:
            prov = "tmail"
        try:
            rec = reg_one(tf, prov, used_orgs, mt_dom)
        except Exception as e:
            rec = {"stage": "crash", "err": str(e)[:200], "provider": prov,
                   "ts": datetime.now(timezone.utc).isoformat()}
        bal = rec.get("balance_micro") or 0
        with open(BANK, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if rec.get("org_token"):
            ok += 1
        if bal and int(bal) > 0:
            paid += 1
        log("%d/%d [%s] %s org=%s bal=$%.2f stage=%s %s" % (
            i + 1, a.count, prov, rec.get("email", "?"), rec.get("org", "-"),
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
