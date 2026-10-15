# -*- coding: utf-8 -*-
"""gh_session — GitHub HTTP session (password + TOTP login) and OAuth authorize.

Pure stdlib + pyotp. No browser. Optional proxy via HTTPS_PROXY env var.
Vendored & sanitized for public release (original: private regfarm tooling).
"""
import re
import time
import html as htmlmod
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar
import os

try:
    import pyotp
except ImportError:
    pyotp = None

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
GH = 'https://github.com'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def form_fields(fragment):
    """Extract hidden/input fields from an HTML form fragment."""
    out = {}
    for m in re.finditer(r'<input[^>]*>', fragment):
        tag = m.group(0)
        nm = re.search(r'name="([^"]+)"', tag)
        vl = re.search(r'value="([^"]*)"', tag)
        if nm:
            out[nm.group(1)] = htmlmod.unescape(vl.group(1)) if vl else ''
    return out


def find_form(html, action_hint):
    for m in re.finditer(r'<form[^>]*>.*?</form>', html, re.S):
        f = m.group(0)
        if action_hint in f:
            return f
    return None


class GHSession:
    def __init__(self, proxy=None):
        self.jar = http.cookiejar.CookieJar()
        proxy = proxy or os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
        handlers = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({'https': proxy, 'http': proxy}))
        handlers += [NoRedirect, urllib.request.HTTPCookieProcessor(self.jar)]
        self.op = urllib.request.build_opener(*handlers)
        self.op.addheaders = [('User-Agent', UA),
                              ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'),
                              ('Accept-Language', 'en-US,en;q=0.9'),
                              ('Origin', GH), ('Referer', GH + '/')]
        self.detail = []

    def _req(self, url, data=None, timeout=30):
        r = urllib.request.Request(url, data=data)
        try:
            with self.op.open(r, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read().decode('utf-8', 'ignore')
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read().decode('utf-8', 'ignore')
        except Exception as e:
            return -1, {}, '%s: %s' % (type(e).__name__, str(e)[:120])

    def get(self, url):
        return self._req(url)

    def post_form(self, url, fields):
        return self._req(url, urllib.parse.urlencode(fields).encode())

    # ---- login ----
    def login(self, login, password, totp):
        st, h, body = self.get(GH + '/login')
        if st != 200:
            return False, 'login page http=%s %s' % (st, body[:120])
        form = find_form(body, '/session')
        if not form:
            return False, 'no /session form on login page'
        fields = form_fields(form)
        fields['login'] = login
        fields['password'] = password
        st, h, body = self.post_form(GH + '/session', fields)
        self.detail.append('session http=%s loc=%s' % (st, h.get('Location', '')[:80]))
        loc = h.get('Location', '')
        if 'verified-device' in loc or 'device-verification' in loc or 'verify' in loc:
            return False, 'device-verification required: %s' % loc
        if st in (301, 302) and ('two-factor' in loc or '2fa' in loc):
            return self._totp(totp, loc)
        if st in (301, 302) and loc.rstrip('/').endswith('github.com'):
            return self._check_login(login)
        if 'two-factor' in body or 'Two-factor' in body:
            return self._totp(totp, '/sessions/two-factor')
        return False, 'login unexpected http=%s loc=%s body=%s' % (st, loc[:100], body[:200])

    def _totp(self, totp, loc):
        if pyotp is None:
            return False, 'pyotp not installed (pip install pyotp)'
        page = loc if loc.startswith('http') else GH + loc
        st, h, body = self.get(page)
        for _ in range(3):
            if st in (301, 302) and h.get('Location'):
                nloc = h['Location']
                page = nloc if nloc.startswith('http') else GH + nloc
                st, h, body = self.get(page)
            else:
                break
        for _attempt in range(3):
            form = find_form(body, 'two-factor')
            if not form:
                return False, 'no 2fa form http=%s %s' % (st, body[:150])
            am = re.search(r'action="([^"]+)"', form)
            post_url = (am.group(1) if am else page)
            if not post_url.startswith('http'):
                post_url = GH + post_url
            fields = form_fields(form)
            fields['app_otp'] = pyotp.TOTP(totp).now()
            st2, h2, body2 = self.post_form(post_url, fields)
            self.detail.append('2fa http=%s loc=%s' % (st2, h2.get('Location', '')[:80]))
            loc2 = h2.get('Location', '')
            if st2 in (301, 302):
                if 'two-factor' in loc2:
                    time.sleep(2)  # code expired near TOTP window edge — retry fresh
                    st, h, body = self.get(page)
                    continue
                return self._check_login(loc2 or None)
            if 'Incorrect' in body2 or 'incorrect' in body2 or st2 == 422:
                time.sleep(2)
                st, h, body = self.get(page)
                continue
            return self._check_login(None)
        return False, 'totp: 3 failed attempts'

    def _check_login(self, redirect=None):
        if redirect:
            self.get(redirect if redirect.startswith('http') else GH + redirect)
        for c in self.jar:
            if c.name == 'logged_in' and c.value in ('yes', 'true'):
                return True, 'logged in (cookie)'
        st, h, body = self.get(GH + '/settings/profile')
        if st == 200 and ('authenticity' in body or 'Settings' in body):
            return True, 'logged in (settings)'
        st, h, body = self.get(GH + '/')
        if 'logged-in' in body or 'Sign out' in body or 'sign_out' in body:
            return True, 'logged in (home)'
        return False, 'not logged in: settings http=%s' % st

    # ---- OAuth authorize ----
    def oauth_code(self, full_url):
        """Follow a full /login/oauth/authorize URL (client_id, state, redirect_uri,
        scope all included). Returns (code, error). Handles: direct redirect with
        code, meta-refresh page with code (already-authorized app), consent form."""
        st, h, body = self.get(full_url)
        self.detail.append('authorize http=%s loc=%s' % (st, h.get('Location', '')[:80]))
        loc = h.get('Location', '')
        if st in (301, 302):
            code = self._code_from(loc)
            if code:
                return code, None
            if 'verified-device' in loc or 'device-verification' in loc:
                return None, 'device-verification: %s' % loc
            return None, 'authorize redirect without code: %s' % loc[:150]
        if st != 200:
            return None, 'authorize http=%s %s' % (st, body[:200])
        m = re.search(r'url=[^"?&]+\?code=([a-f0-9]+)', body)
        if m:
            return m.group(1), None
        m = re.search(r'[?&]code=([a-f0-9]{16,})', body)
        if m:
            return m.group(1), None
        tok = re.search(r'name="authenticity_token"[^>]*value="([^"]+)"', body)
        if not tok:
            tok = re.search(r'value="([^"]+)"[^>]*name="authenticity_token"', body)
        if not tok:
            return None, 'consent page without authenticity_token (len=%d)' % len(body)
        fields = {'authorize': '1', 'authenticity_token': tok.group(1)}
        st2, h2, body2 = self.post_form(full_url, fields)
        self.detail.append('consent http=%s loc=%s' % (st2, h2.get('Location', '')[:80]))
        loc2 = h2.get('Location', '')
        if st2 in (301, 302):
            code = self._code_from(loc2)
            if code:
                return code, None
            return None, 'consent redirect without code: %s' % loc2[:150]
        m = re.search(r'url=[^"?&]+\?code=([a-f0-9]+)', body2)
        if m:
            return m.group(1), None
        m = re.search(r'[?&]code=([a-f0-9]{16,})', body2)
        if m:
            return m.group(1), None
        return None, 'consent http=%s len=%d: %s' % (st2, len(body2), body2[:200])

    @staticmethod
    def _code_from(loc):
        if not loc:
            return None
        q = urllib.parse.urlparse(loc).query
        return urllib.parse.parse_qs(q).get('code', [None])[0]
