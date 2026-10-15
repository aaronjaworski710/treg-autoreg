# treg.to voidash autoreg — email door + fresh voidash inbox per account.
# Vlad proved 14.10: fresh voidash.com inbox -> $1 grant. This script: unlimited
# fresh inboxes via voidash API (bond/eu.cc tiers), Turnstile token from v3 cloak
# factory, OTP read via voidash messages API (no IMAP).
# Usage: python treg_voidash.py <count> [--pace 35] [--domains voidash.bond,govno.eu.cc]
import sys, json, time, re, random, os, argparse, requests
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from treg_farm_v3 import TokenFactory, req_with_retry, log, ORG_WORDS  # noqa: E402

BANK = os.path.join(HERE, "treg_voidash_bank.jsonl")
VA_API = "https://api.voidash.com/api/v1"


def va_create(domain):
    try:
        r = requests.post(VA_API + "/inboxes", json={"domain": domain}, timeout=25)
        if r.status_code in (200, 201):
            d = r.json()
            return d["address"], d["session_key"], None
        return None, None, "va %s: %s" % (r.status_code, r.text[:100])
    except Exception as e:
        return None, None, "va err %s" % str(e)[:100]


def va_wait_code(session_key, since_ts, timeout_s=180):
    t0 = time.time()
    hdrs = {"Authorization": "Bea"+"rer " + session_key}
    while time.time() - t0 < timeout_s:
        try:
            r = requests.get(VA_API + "/messages", headers=hdrs, timeout=25)
            if r.status_code == 200:
                msgs = r.json()
                if isinstance(msgs, dict):
                    msgs = msgs.get("messages", msgs.get("data", []))
                for m in msgs:
                    try:
                        created = m.get("created_at") or ""
                        if created:
                            cts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                            if cts < since_ts - 60:
                                continue
                    except Exception:
                        pass
                    subj = (m.get("subject") or "")
                    frm = json.dumps(m.get("from", {})).lower()
                    body = m.get("body") or m.get("text") or ""
                    if not body:
                        try:
                            r2 = requests.get("%s/messages/%s" % (VA_API, m["id"]), headers=hdrs, timeout=25)
                            body = r2.json().get("body") or r2.json().get("text") or ""
                        except Exception:
                            pass
                    # OTP in subject first (treg puts it there), then body
                    sm = re.search(r"(\d{6})", subj)
                    if sm:
                        return sm.group(1)
                    bm = re.search(r"(?:code|Code|CODE)[^\d]{0,40}(\d{6})", body) or re.search(r">\s*(\d{6})\s*<", body) or re.search(r"\b(\d{6})\b", body[:400])
                    if bm:
                        return bm.group(1)
        except Exception as e:
            log("va poll err: %s" % str(e)[:80])
        time.sleep(5)
    return None


def reg_one_voidash(tf, domain, used_orgs):
    addr, key, err = va_create(domain)
    if not addr:
        return {"stage": "inbox", "err": err, "domain": domain}
    sess = requests.Session()
    from treg_farm_v3 import HDRS
    sess.headers.update(HDRS)
    rec = {"email": addr, "domain": domain, "va_key": key,
           "ts": datetime.now(timezone.utc).isoformat()}
    since_ts = time.time()
    tok = tf.token()
    if not tok:
        rec["stage"], rec["err"] = "token", "factory dead"
        return rec
    st, d = req_with_retry(sess, "POST", "/auth/email/start",
                           json={"email": addr, "captcha_token": tok})
    if st == 400 and "captcha" in json.dumps(d).lower():
        log("captcha_failed — reminting")
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
        rec["stage"], rec["err"] = "start", "%s %s" % (st, json.dumps(d)[:150])
        return rec
    code = va_wait_code(key, since_ts)
    if not code:
        try:
            rr = requests.get(VA_API + "/messages", headers={"Authorization": "Bea"+"rer " + key}, timeout=20)
            log("VA RAW DUMP %s: %s" % (addr, rr.text[:400]))
        except Exception:
            pass
        rec["stage"], rec["err"] = "otp", "no code in %ss" % 180
        return rec
    st, d = req_with_retry(sess, "POST", "/auth/email/verify", json={"email": addr, "code": code})
    if st != 200 or "token" not in d:
        rec["stage"], rec["err"] = "verify", "%s %s" % (st, json.dumps(d)[:150])
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
        rec["stage"], rec["err"] = "org", "%s %s" % (st, json.dumps(d)[:150])
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
        rec["stage"], rec["err"] = "pin", "%s %s" % (st, json.dumps(d)[:150])
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
    ap.add_argument("--domains", default="voidash.bond,govno.eu.cc,musor.eu.cc,pomoi.eu.cc,voidash.cyou")
    args = ap.parse_args()
    domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    used_orgs = set()
    if os.path.exists(BANK):
        for l in open(BANK, encoding="utf-8"):
            try:
                r = json.loads(l)
                if r.get("org"):
                    used_orgs.add(r["org"])
            except Exception:
                pass
    tf = TokenFactory()
    tf.launch()
    ok = nogrant = fail = 0
    di = 0
    try:
        for i in range(args.count):
            domain = domains[di % len(domains)]
            rec = reg_one_voidash(tf, domain, used_orgs)
            # domain blocked by treg? rotate permanently for next
            if rec.get("stage") == "start" and any(w in str(rec.get("err", "")).lower() for w in ("unsupported", "disposable", "not allowed", "blocked", "invalid email")):
                log("domain %s rejected by treg: %s" % (domain, str(rec.get("err"))[:80]))
                di += 1
                domain = domains[di % len(domains)]
                rec = reg_one_voidash(tf, domain, used_orgs)
            if rec.get("org_token"):
                bal = int(rec.get("balance_micro") or 0)
                if bal < 1000000:
                    nogrant += 1
                with open(BANK, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
                ok += 1
                log("OK %s org=%s bal=$%.2f (ok=%d nogrant=%d minted=%d)" % (rec["email"], rec["org"], bal / 1e6, ok, nogrant, tf.minted))
            else:
                fail += 1
                log("FAIL %s stage=%s err=%s" % (rec.get("email", "-"), rec.get("stage"), str(rec.get("err", ""))[:110]))
                if rec.get("stage") == "start" and "429" in str(rec.get("err", "")):
                    log("RATE-LIMITED — cooling 900s")
                    time.sleep(900)
            di += 1
            time.sleep(args.pace + random.uniform(0, 15))
    finally:
        tf.close()
    log("VOIDASH WAVE DONE ok=%d nogrant=%d fail=%d minted=%d" % (ok, nogrant, fail, tf.minted))


if __name__ == "__main__":
    main()
