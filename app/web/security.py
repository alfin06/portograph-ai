"""Security layer for the web dashboard. Every request passes these checks in order:

1. Host check: only hostnames you expect (localhost, or WEB_ALLOWED_HOSTS). Stops DNS
   rebinding, where a website you visit points its own name at 127.0.0.1 to read the
   dashboard from your browser.
2. Cross-site check (Fetch Metadata): another site may link to the dashboard, but may
   not load it in the background, embed it, or submit forms to it.
3. Same-origin check on every POST (CSRF), also for browsers without Fetch Metadata.
4. Password (HTTP Basic) when WEB_PASSWORD is set, with a lockout after repeated
   wrong guesses.
5. Size limit on request bodies.
Every response carries strict headers: a Content-Security-Policy that allows only our
own scripts, no framing (clickjacking), no MIME sniffing, no referrer, no caching of
pages with your data.
"""
import base64
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from core import config

SYMBOL_RE = re.compile(r"^[A-Za-z0-9.^=\-]{1,15}$")
NEXT_RE = re.compile(r"^/[A-Za-z0-9/_\-.]*$")      # local paths only, no query, no //, no \
MAX_BODY = 64 * 1024
MAX_MSG = 200

AUTH_MAX_FAILURES = 10
AUTH_WINDOW_S = 15 * 60
AUTH_LOCKOUT_S = 15 * 60

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",                 # no inline scripts, no eval
    "style-src 'self' 'unsafe-inline'",  # style="width: 40%" on bars; scripts stay strict
    "img-src 'self' data:",
    "connect-src 'self'",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def valid_symbol(s: str | None) -> bool:
    return bool(s) and bool(SYMBOL_RE.match(s))


def safe_next(url: str | None, default: str = "/") -> str:
    """Where to send the browser after a form: our own pages only (no open redirect)."""
    return url if url and NEXT_RE.match(url) and not url.startswith("//") else default


def allowed_hosts(extra: list[str] | None = None) -> set[str] | None:
    """Host names the dashboard answers to; None means any (a password protects it).
    Without a password only loopback names are accepted, whatever WEB_HOST says."""
    hosts = {"127.0.0.1", "localhost", "::1", *config.WEB_ALLOWED_HOSTS, *(extra or [])}
    if config.WEB_PASSWORD and not config.WEB_ALLOWED_HOSTS and not extra:
        return None
    return hosts


def host_ok(host_header: str | None, hosts: set[str] | None) -> bool:
    if hosts is None:
        return True
    if not host_header:
        return False
    h = host_header.strip().lower()
    if h.startswith("["):                        # [::1]:8000
        h = h[1:h.find("]")] if "]" in h else h
    elif h.count(":") == 1:
        h = h.split(":")[0]
    return h in hosts


def cross_site_blocked(method: str, headers) -> bool:
    """Fetch Metadata: only same-origin requests and your own direct visits (typing the
    address, a bookmark, clicking a link to a page) get through."""
    site = headers.get("sec-fetch-site")
    if site is None or site in ("same-origin", "none"):
        return False
    navigate = headers.get("sec-fetch-mode") == "navigate" and headers.get("sec-fetch-dest") in (None, "document")
    return not (method == "GET" and navigate)


def same_origin(headers) -> bool:
    """For POSTs: the request must come from one of the dashboard's own pages."""
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site == "same-origin"
    source = headers.get("origin") or headers.get("referer")
    if not source:
        return False
    return urlsplit(source).netloc == headers.get("host")


class AuthGuard:
    """HTTP Basic password check with a per-client lockout after repeated failures."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._fails: dict[str, deque] = defaultdict(deque)
        self._locked: dict[str, float] = {}
        self._lock = threading.Lock()

    def locked(self, client: str) -> bool:
        with self._lock:
            until = self._locked.get(client)
            if until and until > self._clock():
                return True
            self._locked.pop(client, None)
            return False

    def check(self, client: str, header: str | None) -> bool:
        if not config.WEB_PASSWORD:
            return True
        ok = _password_matches(header)
        with self._lock:
            if ok:
                self._fails.pop(client, None)
                return True
            if header:   # a browser's first request has no header yet; that's not a guess
                now = self._clock()
                q = self._fails[client]
                q.append(now)
                while q and q[0] < now - AUTH_WINDOW_S:
                    q.popleft()
                if len(q) >= AUTH_MAX_FAILURES:
                    self._locked[client] = now + AUTH_LOCKOUT_S
                    q.clear()
        return False


def _password_matches(header: str | None) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        _, _, password = base64.b64decode(header[6:], validate=True).decode().partition(":")
    except Exception:
        return False
    return secrets.compare_digest(password.encode(), config.WEB_PASSWORD.encode())
