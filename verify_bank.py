# Live-check the bank: for each account in treg_accounts.jsonl verify org_token + balance via API.
# Usage: python verify_bank.py [--json out.json]
import json, os, sys, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(HERE, "treg_accounts.jsonl")
BASE = "https://treg.to"
HDRS = {"X-Treg-Key-Protocol": "1", "Content-Type": "application/json"}

def main():
    out_json = None
    if "--json" in sys.argv:
        out_json = sys.argv[sys.argv.index("--json") + 1]
    rows = [json.loads(l) for l in open(BANK, encoding="utf-8")] if os.path.exists(BANK) else []
    if not rows:
        print("bank empty")
        return
    live, dead = [], []
    total = 0
    for r in rows:
        tok = r.get("org_token")
        if not tok:
            dead.append({**r, "why": "no org_token"})
            continue
        ok = False
        for att in range(4):
            try:
                resp = requests.get(BASE + "/orgs", headers={**HDRS, "X-Treg-Token": tok}, timeout=30)
                if resp.status_code == 503 and "saturated" in resp.text:
                    time.sleep(6 + att * 5); continue
                if resp.status_code == 200:
                    orgs = resp.json()
                    orgs = orgs if isinstance(orgs, list) else orgs.get("orgs", orgs.get("items", []))
                    oid = r.get("org_id")
                    if not oid:
                        for o in orgs:
                            if isinstance(o, dict) and (o.get("slug") or o.get("name")) == r.get("org"):
                                oid = o.get("org_id") or o.get("id")
                    bal = None
                    if oid:
                        b = requests.get(f"{BASE}/orgs/{oid}/balance?limit=1",
                                         headers={**HDRS, "X-Treg-Token": tok}, timeout=30)
                        if b.status_code == 200:
                            bal = b.json().get("balance_micro", b.json().get("balance"))
                    live.append({"email": r["email"], "org": r.get("org"), "org_id": oid,
                                 "balance_micro": bal, "org_token": tok})
                    if bal: total += int(bal)
                    ok = True
                else:
                    dead.append({**r, "why": f"HTTP {resp.status_code}"})
                    ok = True
                break
            except Exception as e:
                if att == 3:
                    dead.append({**r, "why": str(e)[:100]})
                    ok = True
                time.sleep(4)
        if not ok:
            dead.append({**r, "why": "retries exhausted"})
        time.sleep(0.6)
    print(f"LIVE={len(live)} DEAD={len(dead)} TOTAL=${total/1e6:.2f}")
    for x in live:
        print(f"  OK  {x['email']} org={x['org']} bal=${int(x['balance_micro'] or 0)/1e6:.3f}")
    for x in dead:
        print(f"  RIP {x['email']} {x.get('why','')[:80]}")
    if out_json:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump({"live": live, "dead": dead, "total_micro": total}, f, indent=1)
        print(f"saved -> {out_json}")

if __name__ == "__main__":
    main()
