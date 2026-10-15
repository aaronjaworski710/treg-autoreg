# TREG GATEWAY — local OpenAI-style proxy over treg.to org-token bank.
# Rotates bank tokens, quarantines dead ones, hot-reloads banks every 60s.
# Port 8751. Local key: gw_key.txt (auto-generated).
import json, os, random, re, threading, time, glob, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import urllib.request, urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BASE)
TREG = os.environ.get("TREG_DATA") or os.path.join(REPO, "data")
PORT = int(os.environ.get("TREG_GW_PORT", "8751"))
GW_KEY_FILE = os.path.join(BASE, "gw_key.txt")
IMPORTED = os.path.join(BASE, "imported_bank.jsonl")


def _banks():
    """Auto-discover bank jsonl files (env TREG_DATA). Imported bank last."""
    return sorted(glob.glob(os.path.join(TREG, "*.jsonl"))) + [IMPORTED]


BANKS = _banks()
UPSTREAM = "https://treg.to"

_lock = threading.Lock()
_pool = []          # [{token, org_id, src, fails, quarantined}]
_idx = 0
_last_load = 0.0


def gw_key():
    if os.path.exists(GW_KEY_FILE):
        return open(GW_KEY_FILE).read().strip()
    k = "treg-gw-" + "".join(random.choice("abcdef0123456789") for _ in range(24))
    with open(GW_KEY_FILE, "w") as f:
        f.write(k)
    return k


def load_pool():
    global _pool, _last_load
    seen = set()
    pool = []
    for path in _banks():
        try:
            for l in open(path, encoding="utf-8", errors="ignore"):
                l = l.strip()
                if not l:
                    continue
                try:
                    r = json.loads(l)
                except Exception:
                    # raw token line in imported file
                    if re.match(r"^[A-Za-z0-9_\-\.]{20,}$", l):
                        r = {"org_token": l}
                    else:
                        continue
                tok = r.get("org_token") or r.get("token")
                if not tok or tok in seen:
                    continue
                seen.add(tok)
                pool.append({"token": tok, "org_id": r.get("org_id"),
                             "src": os.path.basename(path), "fails": 0, "q": False})
        except FileNotFoundError:
            pass
    with _lock:
        _pool[:] = pool
        _last_load = time.time()


def reload_thread():
    while True:
        time.sleep(60)
        try:
            load_pool()
        except Exception:
            pass


def next_tok():
    global _idx
    with _lock:
        live = [p for p in _pool if not p["q"]]
        if not live:
            return None
        p = live[_idx % len(live)]
        _idx += 1
        return p


def quarantine(p):
    with _lock:
        p["fails"] += 1
        if p["fails"] >= 2:
            p["q"] = True


def upstream(method, path, tok, body=None, params=None, timeout=120):
    url = UPSTREAM + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Treg-Token", tok)
    req.add_header("X-Treg-Key-Protocol", "1")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 599, json.dumps({"error": str(e)[:200]}).encode()


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

    def _auth(self):
        k = self.headers.get("Authorization", "").replace("Bearer ", "") or self.headers.get("X-GW-Key", "")
        return k == gw_key()

    def _proxy(self, method):
        if not self._auth():
            return self._send(401, {"error": "bad gw key"})
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
        tries = 0
        while tries < 6:
            p = next_tok()
            if not p:
                return self._send(503, {"error": "pool empty / all quarantined"})
            st, raw = upstream(method, u.path, p["token"], body, params)
            if st in (401, 402, 403):
                quarantine(p)
                tries += 1
                continue
            self._send(st, raw)
            return
        self._send(503, {"error": "no live tokens after 6 tries"})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/gw/status":
            if not self._auth():
                return self._send(401, {"error": "bad gw key"})
            with _lock:
                live = sum(1 for p in _pool if not p["q"])
            return self._send(200, {"pool": len(_pool), "live": live,
                                    "quarantined": len(_pool) - live,
                                    "key": gw_key(), "port": PORT})
        if u.path.startswith("/call/"):
            return self._proxy("GET")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/gw/img":
            if not self._auth():
                return self._send(401, {"error": "bad gw key"})
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            return self._forward("/call/replicate.image-gen.flux-schnell",
                                 {"prompt": body.get("prompt", ""), **{k: v for k, v in body.items() if k != "prompt"}})
        if u.path == "/gw/video":
            if not self._auth():
                return self._send(401, {"error": "bad gw key"})
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            return self._forward("/call/minimax.video-gen.from_text",
                                 {"prompt": body.get("prompt", ""), **{k: v for k, v in body.items() if k != "prompt"}})
        if u.path.startswith("/call/"):
            return self._proxy("POST")
        self._send(404, {"error": "not found"})

    def _forward(self, path, body):
        tries = 0
        while tries < 6:
            p = next_tok()
            if not p:
                return self._send(503, {"error": "pool empty"})
            st, raw = upstream("POST", path, p["token"], body)
            if st in (401, 402, 403):
                quarantine(p)
                tries += 1
                continue
            self._send(st, raw)
            return
        self._send(503, {"error": "no live tokens"})


if __name__ == "__main__":
    load_pool()
    threading.Thread(target=reload_thread, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print("TREG GW on http://127.0.0.1:%d key=%s pool=%d" % (PORT, gw_key(), len(_pool)), flush=True)
    srv.serve_forever()
