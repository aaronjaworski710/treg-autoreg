#!/usr/bin/env python3
"""build_domains_json.py — generate domains.json (machine-readable mail-door matrix)
from probe_state.json (domain_probe3.py verdicts).

Usage:  python build_domains_json.py
Output: domains.json  { "generated": ts, "total": N, "accepted": [...], "blocked": [...], "other": {...},
                        "domains": { domain: {verdict, ts, note} } }
"""
import json, time, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("PROBE_STATE", os.path.join(HERE, "probe_state.json"))
OUT = os.environ.get("DOMAINS_JSON", os.path.join(HERE, "domains.json"))

# auto-registration feasibility notes per family (manual knowledge, keyed by suffix/domain)
FAMILY = {
    "outlook.com": "outlook-autoreg", "hotmail.com": "outlook-autoreg", "live.com": "outlook-autoreg",
    "proton.me": "proton-signup", "protonmail.com": "proton-signup", "pm.me": "proton-signup",
    "wp.pl": "wp-onelogin", "o2.pl": "wp-onelogin", "tlen.pl": "wp-onelogin", "poczta.fm": "wp-onelogin",
    "mail.ru": "mailru-signup", "inbox.ru": "mailru-signup", "list.ru": "mailru-signup", "bk.ru": "mailru-signup",
    "gmail.com": "$0-promo-dedup", "t-online.de": "accepted-but-$0-observed",
    "mail.tm": "blocklisted", "tempmail.lol": "blocklisted",
}

def main():
    st = json.load(open(STATE, encoding="utf-8"))
    doms = st.get("domains", st)
    accepted, blocked, other = [], [], {}
    out = {}
    for d, v in sorted(doms.items()):
        verdict = v.get("verdict") if isinstance(v, dict) else v
        note = v.get("note") if isinstance(v, dict) else None
        ts = v.get("ts") if isinstance(v, dict) else None
        rec = {"verdict": verdict}
        if ts: rec["ts"] = ts
        if note: rec["note"] = note
        if d in FAMILY: rec["reg_path"] = FAMILY[d]
        out[d] = rec
        if verdict == "ACCEPTED": accepted.append(d)
        elif verdict == "BLOCKED": blocked.append(d)
        else: other[d] = verdict
    doc = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "treg.to /auth/email/start probe (domain_probe3.py, TokenFactory PASS tokens)",
        "total": len(out),
        "accepted_count": len(accepted),
        "blocked_count": len(blocked),
        "accepted": accepted,
        "blocked": blocked,
        "other": other,
        "domains": out,
    }
    json.dump(doc, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("domains.json written:", OUT, "| total", len(out), "| accepted", len(accepted), "| blocked", len(blocked), "| other", len(other))

if __name__ == "__main__":
    main()
