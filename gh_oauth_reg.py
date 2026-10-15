# -*- coding: utf-8 -*-
"""gh_oauth_reg — treg.to registration via the GitHub OAuth door.

The email+OTP door no longer pays the signup grant (Turnstile outcome must be
"PASS" server-side; headless tokens are not classified PASS, so email signups
are created with promo_eligible=False). The GitHub OAuth door provisions the
user with promo_eligible defaulting to True -> $1 signup grant per fresh inbox.

Flow per account:
  1. GET {base}/auth/github          -> 302 to github.com/login/oauth/authorize
                                          (client_id, state) + treg_oauth_state cookie
  2. GH password+TOTP login, follow the FULL authorize URL (redirect_uri matters)
     -> code (direct redirect / meta-refresh / consent POST)
  3. GET {base}/auth/github/callback?code&state with the state cookie -> treg_session
  4. POST {base}/orgs {"name": ...}  -> org_id + slug
  5. GET  {base}/auth/cli-token (X-Treg-Org header) -> team token
  6. GET  {base}/orgs/{id}/balance (X-Treg-Token + X-Treg-Key-Protocol: 1)

Usage:  python gh_oauth_reg.py <count>
Config: config.json (see config.example.json) or env TREG_* / GH_POOL.
Pool:   JSON array of {"login","password","totp","email"} GitHub accounts.

Anti-fraud notes (why some accounts get $0):
  - SignupPromoClaim is unique per normalized inbox: "+tag" stripped for every
    domain, dots stripped for gmail. One grant per mailbox, forever. Retrying
    with a second org does NOT pay again (claim already consumed).
  - So: gmail alias/dot farms are dead for this promo; only genuinely fresh
    inboxes pay. Observed hit rate on a fresh pool: 69/69; on a pool with
    previously-touched inboxes: ~50-70%.
"""
import sys
import os
import re
import json
import time
import random
import string
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gh_session import GHSession  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def _cfg():
    cfg = {}
    p = os.path.join(HERE, 'config.json')
    if os.path.exists(p):
        cfg = json.load(open(p, encoding='utf-8'))
    return cfg


CFG = _cfg()
B = os.environ.get('TREG_BASE', CFG.get('base', 'https://treg.to'))
POOL = os.environ.get('GH_POOL', CFG.get('gh_pool', 'gh_accounts.json'))
if not os.path.isabs(POOL):
    POOL = os.path.join(HERE, POOL)
STATE_F = os.path.join(HERE, os.environ.get('GH_STATE', CFG.get('state', 'gh_reg_state.json')))
BANK_F = os.path.join(HERE, CFG.get('bank', 'treg_gh_bank.jsonl'))
PACE = CFG.get('pace', [8, 15])

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


def load_state():
    try:
        return json.load(open(STATE_F))
    except Exception:
        return {'idx': 0}


def save_state(st):
    with open(STATE_F, 'w') as f:
        json.dump(st, f)


def opener_noredir():
    class NoRedir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, rq, fp, code, msg, headers, newurl):
            return None
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(NoRedir, urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [('User-Agent', UA)]
    return op, jar


def req(op, url, data=None, method=None, timeout=40, ctype=None, extra=None):
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header('User-Agent', UA)
    r.add_header('Accept', '*/*')
    if ctype:
        r.add_header('Content-Type', ctype)
    if extra:
        for k, v in extra.items():
            r.add_header(k, v)
    try:
        with op.open(r, timeout=timeout) as resp:
            return resp.status, resp.read()[:400000]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:4000]
    except Exception as e:
        return -1, ('%s: %s' % (type(e).__name__, str(e)[:120])).encode()


def rndname(pfx='studio'):
    return pfx + ''.join(random.choices(string.ascii_lowercase + string.digits, k=7))


def run(gh_acc):
    op, jar = opener_noredir()
    # step 1: OAuth start -> authorize URL + state
    try:
        with op.open(B + '/auth/github', timeout=30) as resp:
            return None, 'unexpected %s on /auth/github' % resp.status
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307):
            return None, 'start http=%s' % e.code
        loc = e.headers.get('Location', '')
    m = re.search(r'client_id=([^&]+)', loc)
    ms = re.search(r'state=([^&]+)', loc)
    if not m or not ms:
        return None, 'no client_id/state in redirect: %s' % loc[:120]
    state = ms.group(1)
    state_cookie = ''
    for c in jar:
        if c.name == 'treg_oauth_state':
            state_cookie = c.value

    # step 2: GitHub login + authorize (FULL url — redirect_uri is required)
    sess = GHSession()
    ok, err = sess.login(gh_acc['login'], gh_acc['password'], gh_acc.get('totp', ''))
    if not ok:
        return None, 'gh login failed: %s' % str(err)[:120]
    code, err = sess.oauth_code(loc)
    if not code:
        return None, 'gh authorize failed: %s' % str(err)[:200]

    # step 3: callback with the state cookie -> treg session
    cb = B + '/auth/github/callback?code=%s&state=%s' % (
        urllib.parse.quote(code), urllib.parse.quote(state))
    st, body = req(op, cb, timeout=40, extra={'Cookie': 'treg_oauth_state=' + state_cookie})
    if st not in (200, 302, 303, 307):
        return None, 'callback http=%s %s' % (st, body[:200])
    sess_cookie = ''
    for c in jar:
        if 'session' in c.name or 'treg' in c.name:
            sess_cookie += '%s=%s; ' % (c.name, c.value)
    if not sess_cookie.strip():
        return None, 'no session cookie after callback; body=%s' % body[:200].decode('utf-8', 'ignore')

    # step 4: create org (claim fires here)
    name = rndname()
    st, body = req(op, B + '/orgs', data=json.dumps({'name': name}).encode(),
                   method='POST', timeout=30, ctype='application/json',
                   extra={'Cookie': sess_cookie})
    org_id = slug = None
    try:
        pj = json.loads(body)
        org_id = pj.get('org_id') or (pj.get('org') or {}).get('id')
        slug = pj.get('org') if isinstance(pj.get('org'), str) else pj.get('slug')
        if isinstance(pj.get('org'), dict):
            org_id = org_id or pj['org'].get('id')
            slug = slug or pj['org'].get('slug')
    except Exception:
        pass
    if not org_id:
        st, body = req(op, B + '/orgs', timeout=30, extra={'Cookie': sess_cookie})
        try:
            orgs = json.loads(body)
            if isinstance(orgs, dict):
                orgs = orgs.get('orgs') or orgs.get('data') or []
            if orgs:
                org_id = orgs[0].get('org_id') or orgs[0].get('id')
                slug = orgs[0].get('org') or orgs[0].get('slug')
        except Exception:
            pass

    # step 5: team token
    st, body = req(op, B + '/auth/cli-token', timeout=30,
                   extra={'Cookie': sess_cookie, 'X-Treg-Org': str(slug or org_id or '')})
    try:
        tok = json.loads(body).get('token', '')
    except Exception:
        tok = ''
    if not tok:
        return {'email': gh_acc.get('email'), 'gh_login': gh_acc['login'],
                'org': slug, 'org_id': org_id, 'cookie': sess_cookie.strip()}, \
            'no cli token: %s' % body[:150]

    # step 6: balance (X-Treg-Token auth; Bearer is rejected with 401)
    bal = -1.0
    if org_id:
        st2, b2 = req(op, '%s/orgs/%s/balance?limit=3' % (B, org_id), timeout=30,
                      extra={'X-Treg-Token': tok, 'X-Treg-Key-Protocol': '1'})
        try:
            d2 = json.loads(b2)
            bal = d2.get('balance_micro', d2.get('balance', -1)) / 1e6
        except Exception:
            pass
    rec = {'ts': time.time(), 'email': gh_acc.get('email'), 'gh_login': gh_acc['login'],
           'org': slug, 'org_id': org_id, 'token': tok, 'balance_usd': bal,
           'via': 'github-oauth'}
    with open(BANK_F, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return rec, 'bal=$%.2f' % bal


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    pool = json.load(open(POOL, encoding='utf-8'))
    st = load_state()
    done = 0
    for i in range(n):
        idx = st['idx']
        if idx >= len(pool):
            print('pool exhausted')
            break
        acc = pool[idx]
        st['idx'] = idx + 1
        save_state(st)
        print('=== acc %d/%d gh=%s' % (i + 1, n, acc['login']), flush=True)
        rec, msg = run(acc)
        print('RESULT: %s' % msg, flush=True)
        if rec and rec.get('balance_usd', 0) > 0:
            done += 1
        time.sleep(random.uniform(*PACE))
    print('GRANT>0: %d/%d' % (done, n))


if __name__ == '__main__':
    main()
