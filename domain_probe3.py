# Probe treg.to /auth/email/start across many mail domains (v3).
# Token = cloakbrowser TokenFactory (window.tregCaptcha.token('email_start'), instant).
# ACCEPTED(200) / BLOCKED(403) / RATE(429->retry) / OTHER. State persisted, resumable.
import json, random, sqlite3, string, sys, time
import requests

sys.path.insert(0, r"C:/Users/User/tmp/treg_test")
from treg_farm_v3 import HDRS, TokenFactory

DOMAINS = [
    # privacy
    "protonmail.com", "pm.me", "tutanota.com", "tutanota.de", "tuta.io",
    "mailfence.com", "startmail.com", "disroot.org", "posteo.de", "mailbox.org",
    "cock.li", "lavabit.com", "hushmail.com", "elude.in",
    # majors (controls)
    "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
    "aol.com", "mail.com", "gmx.com", "zoho.com", "qq.com", "163.com", "126.com",
    "t-online.de", "yandex.com", "live.com", "me.com", "fastmail.com", "runbox.com",
    # PL
    "wp.pl", "o2.pl", "tlen.pl", "interia.pl", "onet.pl", "op.pl", "vp.pl", "buziaczek.pl",
    "poczta.fm", "amorki.pl",
    # UA/RU
    "ukr.net", "meta.ua", "i.ua", "email.ua", "mail.ru", "inbox.ru", "list.ru", "bk.ru",
    "yandex.ru", "rambler.ru", "ua.fm",
    # CZ/SK
    "seznam.cz", "email.cz", "centrum.cz", "post.cz", "azet.sk", "zoznam.sk", "atlas.sk",
    # DE/AT/CH
    "gmx.de", "gmx.at", "gmx.ch", "web.de", "aon.at", "bluewin.ch", "hispeed.ch",
    # baltics
    "inbox.lv", "mail.lv", "one.lt", "delfi.lt", "hot.ee", "mail.ee",
    # IT/FR/GR/HU/BG
    "libero.it", "virgilio.it", "tiscali.it", "tin.it", "alice.it",
    "orange.fr", "laposte.net", "free.fr", "wanadoo.fr", "sfr.fr",
    "in.gr", "otenet.gr", "freemail.hu", "citromail.hu", "vipmail.hu",
    "abv.bg", "mail.bg", "myabv.com",
    "yahoo.fr", "yahoo.de", "yahoo.co.uk", "yahoo.it", "yahoo.es",
    # nordics/nl/be/pt/tr
    "telia.com", "web.nl", "ziggo.nl", "telenet.be", "scarlet.be",
    "sapo.pt", "clix.pt", "mynet.com", "superonline.com",
    # known controls
    "gmail.com", "proton.me", "voidash.bond",
    "guerrillamail.com", "mailinator.com", "tempmail.lol", "mail.tm", "yopmail.com",
]

DB = r"C:/Users/User/Desktop/proxy_bot/proxies.db"
STATE = r"C:/Users/User/tmp/treg_repo/probe_state.json"
state = {}
try:
    state = json.load(open(STATE, encoding="utf-8"))
except Exception:
    state = {}

def save():
    json.dump(state, open(STATE, "w", encoding="utf-8"))

def load_proxies():
    px = []
    try:
        con = sqlite3.connect(DB)
        for (h,) in con.execute("select proxy from pool where alive=1 order by score desc limit 400"):
            if not h.startswith("http"):
                h = "http://" + h
            px.append(h)
        con.close()
    except Exception as e:
        print("proxydb err", e, flush=True)
    return px

def probe(tf, dom, px_pool):
    addr = "".join(random.choice(string.ascii_lowercase) for _ in range(9)) + str(random.randint(10, 99)) + "@" + dom
    for attempt in range(3):
        tok = tf.token()
        if not tok:
            return "NOTOKEN"
        sess = requests.Session()
        sess.trust_env = False
        sess.headers.update(HDRS)
        try:
            r = sess.post("https://treg.to/auth/email/start",
                          json={"email": addr, "captcha_token": tok},
                          timeout=40)
            st = r.status_code
        except Exception:
            st = 0
        if st == 200:
            return "ACCEPTED"
        if st == 403:
            return "BLOCKED"
        if st == 429:
            return "RATE"   # 30 req/15min per IP — outer loop paces; RATE=re-run later
        if st == 0:
            time.sleep(5)
            continue
        return f"HTTP{st}"
    return "NETERR"

def main():
    tf = TokenFactory()
    tf.launch()
    px_pool = load_proxies()
    todo = [d for d in DOMAINS if state.get(d, {}).get("verdict") not in ("ACCEPTED", "BLOCKED")]
    print("proxies:", len(px_pool), "| to probe:", len(todo), flush=True)
    for i, dom in enumerate(todo, 1):
        v = probe(tf, dom, px_pool)
        if v == "RATE":
            print("RATE limit hit — sleeping 16 min", flush=True)
            time.sleep(960)
            v = probe(tf, dom, px_pool)
        state[dom] = {"verdict": v, "ts": time.strftime("%m-%d %H:%M:%S")}
        save()
        print("%3d %-20s %s" % (i, dom, v), flush=True)
        time.sleep(32)   # 30 req/15min per IP -> stay under
    acc = sorted([d for d, v in state.items() if v.get("verdict") == "ACCEPTED"])
    blk = sorted([d for d, v in state.items() if v.get("verdict") == "BLOCKED"])
    oth = {d: v.get("verdict") for d, v in state.items() if v.get("verdict") not in ("ACCEPTED", "BLOCKED")}
    print("=== SUMMARY ===", flush=True)
    print("ACCEPTED(%d): %s" % (len(acc), acc), flush=True)
    print("BLOCKED(%d): %s" % (len(blk), blk), flush=True)
    print("OTHER(%d): %s" % (len(oth), oth), flush=True)
    tf.close()

if __name__ == "__main__":
    main()
