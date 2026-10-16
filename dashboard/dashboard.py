#!/usr/bin/env python3
"""TREG FARM — control plane (logicc-style dark UI). Stdlib only (+psutil optional).

Env:
  TREG_DATA       dir with banks/logs/wave scripts   [default: ./data next to repo]
  TREG_DASH_PORT  listen port                        [default: 8750]
  TREG_DASH_HOST  listen host                        [default: 127.0.0.1]

Endpoints:
  GET  /            UI (index.html)
  GET  /api/stats   bank totals + running loops
  GET  /api/bank?door=X&limit=N   masked bank rows
  GET  /api/log?f=X&n=100         log tail
  GET  /api/domains               mail-door verdict matrix (domains.json)
  GET  /api/probe                 live balance probe with last org token
  GET  /api/gw                    gateway status
  POST /api/wave {door,action,count}   start/stop reg waves
  POST /api/gw/start|stop|import
"""
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TREG = os.environ.get("TREG_DATA") or os.path.join(REPO, "data")
PORT = int(os.environ.get("TREG_DASH_PORT", "8750"))
HOST = os.environ.get("TREG_DASH_HOST", "127.0.0.1")
PY = os.environ.get("TREG_PYTHON", sys.executable)

os.makedirs(TREG, exist_ok=True)

# waves: door -> (script in TREG, default count)
WAVES = {
    "catchall": ("treg_catchall.py", 10),
    "voidash": ("treg_voidash.py", 10),
    "outlook": ("treg_outlook.py", 10),
}
PROC_MARKS = {
    "catchall": "treg_catchall.py",
    "voidash": "treg_voidash.py",
    "outlook": "treg_outlook.py",
    "probe": "domain_probe3.py",
    "gh_wave": "gh_oauth_reg.py",
}


def mask_email(e):
    if not e or "@" not in e:
        return e or ""
    l, d = e.split("@", 1)
    return (l[:3] if len(l) > 3 else l[:1]) + "***@" + d


def mask_tok(t):
    if not t:
        return ""
    t = str(t)
    return t[:8] + "..." + t[-4:] if len(t) > 16 else "***"


def read_bank(path, limit=100000):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for l in f:
                l = l.strip()
                if not l:
                    continue
                try:
                    rows.append(json.loads(l))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return rows[-limit:]


def find_banks():
    """Auto-discover bank jsonl files in TREG_DATA (name -> path)."""
    out = {}
    for p in sorted(glob.glob(os.path.join(TREG, "*.jsonl"))):
        stem = os.path.basename(p)[:-6]
        stem = stem.replace("treg_", "").replace("_bank", "").replace("_accounts", "")
        out[stem] = p
    return out


def find_logs():
    """Auto-discover logs in TREG_DATA + repo root."""
    out = {}
    for d in (TREG, REPO):
        for p in sorted(glob.glob(os.path.join(d, "*.log"))):
            out[os.path.basename(p)] = p
    return out


def _tok(r):
    return r.get("org_token") or r.get("token") or ""


def _live_cutoff():
    """UTC ISO prefix for 'registered within last 90 min' (treg revokes grants ~1-1.5h after signup)."""
    import datetime as _dt
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M")


def door_stats():
    out = {}
    total_orgs = total_paid = 0
    total_usd = 0.0
    for door, path in find_banks().items():
        rows = read_bank(path)
        orgs = [r for r in rows if _tok(r)]
        paid = [r for r in orgs if int(r.get("balance_micro") or 0) > 0]
        usd = sum(int(r.get("balance_micro") or 0) for r in paid) / 1e6
        stages = {}
        for r in rows:
            if not _tok(r):
                s = r.get("stage", "?")
                stages[s] = stages.get(s, 0) + 1
        last_ts = rows[-1].get("ts", "") if rows else ""
        live = [r for r in paid if (r.get("ts") or "") >= _live_cutoff()]
        out[door] = {"total": len(rows), "orgs": len(orgs), "paid": len(paid),
                     "usd": round(usd, 2), "fails": stages, "last": last_ts,
                     "live_now": len(live),
                     "live_usd": round(sum(int(r.get("balance_micro") or 0) for r in live) / 1e6, 2)}
        total_orgs += len(orgs)
        total_paid += len(paid)
        total_usd += usd
    out["_totals"] = {"orgs": total_orgs, "paid": total_paid, "usd": round(total_usd, 2)}
    return out


def _procs():
    try:
        import psutil
        for p in psutil.process_iter(["pid", "cmdline"]):
            if p.info["pid"] == os.getpid():
                continue
            try:
                yield p, " ".join(os.path.basename(a) for a in (p.info["cmdline"] or []))
            except Exception:
                pass
    except ImportError:
        pass


def running_loops():
    res = {k: False for k in PROC_MARKS}
    for _p, base in _procs():
        for k, mark in PROC_MARKS.items():
            if mark in base:
                res[k] = True
    return res


def tail_log(path, n=100):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            sz = f.tell()
            f.seek(max(0, sz - 200000))
            data = f.read().decode("utf-8", "ignore")
        lines = [l for l in data.splitlines() if l.strip()]
        return "\n".join(lines[-n:])
    except FileNotFoundError:
        return "(no log)"


def wave_action(door, action, count=None):
    if door not in WAVES:
        return {"ok": False, "err": "unknown door (have: %s)" % ",".join(WAVES)}
    script, dcount = WAVES[door]
    spath = os.path.join(TREG, script)
    if action == "start":
        if not os.path.exists(spath):
            return {"ok": False, "err": "script not found: %s (set TREG_DATA)" % spath}
        if running_loops().get(door):
            return {"ok": False, "err": "already running"}
        env = dict(os.environ)
        env.update({"PYTHONPATH": "", "PYTHONIOENCODING": "utf-8"})
        env.setdefault("HOME", os.path.expanduser("~"))
        env.setdefault("USERPROFILE", os.path.expanduser("~"))
        if os.name == "nt":
            env.setdefault("PROCESSOR_ARCHITECTURE", "AMD64")
        cmd = [PY, "-u", script, str(count or dcount)]
        lf = open(os.path.join(TREG, "wave_%s.log" % door), "ab")
        flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0
        if os.name == "nt":
            flags |= 0x01000000
        p = subprocess.Popen(cmd, cwd=TREG, env=env, creationflags=flags,
                             stdout=lf, stderr=lf, stdin=subprocess.DEVNULL)
        return {"ok": True, "pid": p.pid, "log": "wave_%s.log" % door}
    elif action == "stop":
        killed = 0
        mark = PROC_MARKS.get(door, WAVES[door][0])
        for p, base in _procs():
            try:
                if mark in base:
                    p.kill()
                    killed += 1
            except Exception:
                pass
        return {"ok": True, "killed": killed}
    return {"ok": False, "err": "bad action"}


def domains_matrix():
    for cand in (os.path.join(REPO, "domains.json"), os.path.join(TREG, "domains.json")):
        if os.path.exists(cand):
            try:
                return json.load(open(cand, encoding="utf-8"))
            except Exception as e:
                return {"err": str(e)[:100]}
    return {"err": "domains.json not found (run build_domains_json.py)"}


def api_probe():
    out = {"balance": None, "note": ""}
    banks = find_banks()
    rows = []
    for key in ("catchall", "voidash", "realmail"):
        for name, path in banks.items():
            if key in name:
                rows = read_bank(path)
                break
        if rows:
            break
    if not rows:
        for path in banks.values():
            rows = read_bank(path)
            if rows:
                break
    tok = oid = None
    for r in reversed(rows):
        if _tok(r):
            tok, oid = _tok(r), r.get("org_id")
            break
    if not tok:
        out["note"] = "no org token in banks"
        return out
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://treg.to/orgs/%s/balance?limit=2" % oid,
            headers={"X-Treg-Token": tok, "X-Treg-Key-Protocol": "1"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            out["balance"] = json.loads(resp.read().decode())
            out["balance"]["token_masked"] = mask_tok(tok)
    except Exception as e:
        out["note"] = "probe err: %s" % str(e)[:120]
    return out


GW_PID = os.path.join(HERE, "gw.pid")


def gw_running():
    try:
        pid = int(open(GW_PID).read().strip())
        import psutil
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != "zombie"
    except Exception:
        return False


def gw_start():
    if gw_running():
        return {"ok": True, "note": "already running"}
    env = dict(os.environ)
    env.update({"PYTHONPATH": "", "PYTHONIOENCODING": "utf-8"})
    lf = open(os.path.join(HERE, "gw.log"), "ab")
    flags = (0x00000008 | 0x00000200 | 0x01000000) if os.name == "nt" else 0
    p = subprocess.Popen([PY, os.path.join(HERE, "treg_gw.py")], cwd=HERE, env=env,
                         creationflags=flags, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL)
    with open(GW_PID, "w") as f:
        f.write(str(p.pid))
    return {"ok": True, "pid": p.pid}


def gw_stop():
    try:
        import psutil
        pid = int(open(GW_PID).read().strip())
        psutil.Process(pid).kill()
        os.remove(GW_PID)
        return {"ok": True, "killed": pid}
    except Exception as e:
        return {"ok": False, "err": str(e)[:100]}


def gw_import(tok):
    import urllib.request, urllib.error
    tok = (tok or "").strip()
    if len(tok) < 20:
        return {"ok": False, "err": "token too short"}
    req = urllib.request.Request("https://treg.to/orgs",
                                 headers={"X-Treg-Token": tok, "X-Treg-Key-Protocol": "1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            d = json.loads(resp.read().decode())
        orgs = d if isinstance(d, list) else d.get("orgs", d.get("items", []))
        org = orgs[0] if orgs else {}
        oid = org.get("org_id") or org.get("id")
        usd = None
        if oid:
            req2 = urllib.request.Request("https://treg.to/orgs/%s/balance?limit=1" % oid,
                                          headers={"X-Treg-Token": tok, "X-Treg-Key-Protocol": "1"})
            with urllib.request.urlopen(req2, timeout=20) as resp2:
                b = json.loads(resp2.read().decode())
                usd = round(int(b.get("balance_micro") or 0) / 1e6, 4)
        rec = {"org_token": tok, "org_id": oid, "org": org.get("slug") or org.get("name"),
               "door": "imported", "ts": datetime.now(timezone.utc).isoformat(),
               "balance_micro": int((usd or 0) * 1e6)}
        with open(os.path.join(TREG, "imported_bank.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return {"ok": True, "org": rec["org"], "usd": usd}
    except urllib.error.HTTPError as e:
        return {"ok": False, "err": "treg said %s" % e.code}
    except Exception as e:
        return {"ok": False, "err": str(e)[:120]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, {"err": "index.html missing"})
        elif u.path == "/api/stats":
            self._send(200, {"doors": door_stats(), "loops": running_loops(),
                             "data_dir": TREG})
        elif u.path == "/api/bank":
            door = (q.get("door") or [""])[0]
            limit = int((q.get("limit") or ["200"])[0])
            banks = find_banks()
            path = banks.get(door, "")
            if not path:
                for name, p in banks.items():
                    if door and door in name:
                        path = p
                        break
            rows = read_bank(path, limit)[-limit:][::-1]
            safe = []
            for r in rows:
                safe.append({"email": mask_email(r.get("email", "")),
                             "door": r.get("door", door), "domain": r.get("domain", ""),
                             "org": r.get("org", ""), "org_id": r.get("org_id", ""),
                             "token": mask_tok(_tok(r)),
                             "bal_usd": round(int(r.get("balance_micro") or 0) / 1e6, 4),
                             "stage": r.get("stage", "OK"), "err": str(r.get("err", ""))[:80],
                             "ts": r.get("ts", "")})
            self._send(200, {"rows": safe, "count": len(safe), "doors": sorted(banks)})
        elif u.path == "/api/log":
            f = (q.get("f") or [""])[0]
            logs = find_logs()
            path = logs.get(f, "")
            if not path:
                self._send(200, {"text": "(no log) available: %s" % ", ".join(sorted(logs))},
                           "text/plain; charset=utf-8")
                return
            self._send(200, {"text": tail_log(path, int((q.get("n") or ["100"])[0]))},
                       "text/plain; charset=utf-8")
        elif u.path == "/api/domains":
            self._send(200, domains_matrix())
        elif u.path == "/api/probe":
            self._send(200, api_probe())
        elif u.path == "/api/gw":
            self._send(200, {"running": gw_running()})
        else:
            self._send(404, {"err": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if u.path == "/api/wave":
            self._send(200, wave_action(body.get("door", ""), body.get("action", ""),
                                        body.get("count")))
        elif u.path == "/api/gw/start":
            self._send(200, gw_start())
        elif u.path == "/api/gw/stop":
            self._send(200, gw_stop())
        elif u.path == "/api/gw/import":
            self._send(200, gw_import(body.get("token", "")))
        else:
            self._send(404, {"err": "not found"})


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print("TREG DASH on http://%s:%d | data=%s" % (HOST, PORT, TREG), flush=True)
    srv.serve_forever()
