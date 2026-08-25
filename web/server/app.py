"""The HTTP server, and nothing about vaults.

`http.server.ThreadingHTTPServer` and the standard library, on purpose. The
engine is subprocess-bound rather than request-bound — every route spends its
time waiting on `report-maker`, not on the framework — so a framework would buy
routing and middleware this file already has in three hundred lines, at the
price of turning `python3 -m web` into a dependency install. That trade is not
worth making for a tool whose whole engine has no third-party imports.

What lives here is the part of a request that is not about reports:

    socket ──▶ read and cap the body
           ──▶ rate limit by source address        spec 8
           ──▶ open the session from its cookie    spec 9, spec 10
           ──▶ match a route                       routes.TABLE
           ──▶ scrub the answer of server paths    spec (no path in a response)
           ──▶ write it, log one line without the id or the token

Two rules run through all of it.

**One 401 shape.** Missing, malformed, unknown, expired, swept — every reason a
session did not open produces the identical body, because a caller does the
same thing about all of them and a stranger must not be able to tell which it
was.

**No absolute path leaves this process.** Several engine commands print the
vault path inside their own JSON, and a build's stderr is full of them. Every
JSON body is walked on the way out and the server's own roots are removed. Raw
bodies — a PDF, a page image, an HTML bundle — are passed through untouched:
those are the user's artefacts, and rewriting bytes inside one would corrupt
the thing they asked for.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from . import engine, github, routes, security, sessions

# ── the shape of a deployment ────────────────────────────────────────────────

#: Loopback, and it takes a flag and a printed warning to be anything else.
#: Spec requirement 1. The default is not a suggestion about where this is
#: useful; it is the assumption that a server running strangers' Typst source
#: is not on the internet until somebody says so out loud.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

#: A request body ceiling, and the reason the 50 MB disk quota is a real
#: ceiling rather than a floor. `enforce("disk")` refuses the *next* write once
#: the vault is full, so the overshoot is exactly one body — which only holds
#: if a body is bounded. It is bounded here.
MAX_BODY = 2 * 1024 * 1024

#: How much of an over-sized body is read and thrown away before the refusal is
#: written. Without it the server answers 413 and closes while the client is
#: still sending, and the client sees a broken pipe rather than the reason —
#: which is the least useful way to be told a file is too big. Bounded, read in
#: chunks and never kept, so draining costs a constant amount of memory.
DRAIN_LIMIT = 8 * 1024 * 1024

#: Socket-level, not request-level. A build may take the full 65-second engine
#: budget; what this refuses is a client that opens a connection and then sends
#: its headers one byte a minute.
SOCKET_TIMEOUT = 30.0

#: How long a share link lives. Shares are immutable by design, so nothing ever
#: overwrites one and the directory would otherwise grow without bound. Set
#: RM_WEB_SHARE_TTL_HOURS=0 to keep them for ever and watch the disk yourself.
SHARE_TTL_HOURS = 24.0 * 30

_TEXT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".pdf": "application/pdf",
    ".webmanifest": "application/manifest+json",
}


@dataclass(frozen=True)
class Options:
    """Everything a run of this server is. Flags first, then environment."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    root: Path | None = None
    client: Path | None = None
    tls: bool = False
    share_ttl_hours: float = SHARE_TTL_HOURS
    sweep_seconds: float = 600.0


# ── scrubbing ────────────────────────────────────────────────────────────────


def _roots(ctx: routes.Ctx, session: sessions.Session | None) -> list[tuple[str, str]]:
    """The prefixes that must not appear in a response, longest first.

    Longest first because they nest: a session vault sits inside the sessions
    store, which sits inside `RM_WEB_ROOT`. Replacing the outer one first would
    leave the rest of the vault path behind, which is the half that carries the
    session id.

    The engine checkout and the home directory are on the list too. Neither is
    a secret exactly, but a typst error names the file it was compiling and a
    traceback names the file it was raised in, and a response is not the place
    to publish this machine's directory layout.
    """
    pairs: list[tuple[str, str]] = []
    if session is not None:
        vault = str(session.vault)
        pairs += [(vault + os.sep, ""), (vault, "vault")]
    for root in (ctx.store, ctx.root, ctx.shares):
        pairs.append((str(root), "…"))
    try:
        pairs.append((str(engine.locate().parent.parent), "…"))
    except Exception:  # noqa: BLE001 — a missing engine is not a scrubbing failure
        pass
    home = str(Path.home())
    if home and home != os.sep:
        pairs.append((home, "~"))
    # The interpreter's own tree. Every engine traceback that reaches a stdlib
    # frame prints it, and the path spells out the exact Python build — which is
    # the first thing a scanner wants, because it turns "try everything" into a
    # list of CVEs that apply to this box.
    #
    # Both the nominal prefix and the resolved one, and they are routinely
    # different: Homebrew's `python@3.14` prefix is a symlink into `Cellar`, and
    # a traceback prints the target while `sys.prefix` reports the link. Adding
    # only one of the two is adding neither, most of the time.
    for prefix in (sys.base_prefix, sys.prefix):
        if not prefix or prefix == os.sep:
            continue
        pairs.append((str(prefix), "…"))
        try:
            pairs.append((str(Path(prefix).resolve()), "…"))
        except OSError:  # pragma: no cover - an unreadable interpreter tree
            pass
    seen: dict[str, str] = {}
    for prefix, replacement in pairs:
        if prefix and prefix != os.sep and prefix not in seen:
            seen[prefix] = replacement
    return sorted(seen.items(), key=lambda kv: len(kv[0]), reverse=True)


def _scrub(value: Any, pairs: list[tuple[str, str]], secret: str | None) -> Any:
    """Walk a payload and take this machine's layout out of it."""
    if isinstance(value, str):
        for prefix, replacement in pairs:
            if prefix in value:
                value = value.replace(prefix, replacement)
        if secret and secret in value:
            # A last net. The prefixes above remove every path that carries the
            # session id, so reaching this means something built a string we
            # did not anticipate — and the id is the credential.
            value = value.replace(secret, "…")
        return value
    if isinstance(value, dict):
        return {k: _scrub(v, pairs, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, pairs, secret) for v in value]
    return value


# ── translating a failure ────────────────────────────────────────────────────


def _refused(exc: BaseException) -> security.Refused:
    """Every exception this server can produce, as one type.

    `routes` raises `security.Refused` for everything it decides itself, and
    translates the engine's exceptions where it calls them. This is the net for
    the ones that get past — and for anything genuinely unexpected, which
    becomes a 500 that says nothing. A stack trace in a response body is a map
    of the server drawn for whoever asked for it; it goes to stderr, where the
    operator is.
    """
    if isinstance(exc, security.Refused):
        return exc
    if isinstance(exc, engine.Refused):
        return security.Refused(str(exc), code="forbidden", status=403)
    if isinstance(exc, engine.EngineMissing):
        return security.Refused(
            "this server has no report-maker to run",
            code="engine_missing",
            status=500,
            detail=str(exc),
        )
    if isinstance(exc, engine.EngineError):
        return security.Refused(str(exc), code="engine", status=400)
    if isinstance(exc, github.GitHubError):
        return security.Refused(str(exc), code="github", status=400)
    if isinstance(exc, sessions.SessionError):
        return security.Refused(str(exc), code="session", status=500)
    if isinstance(exc, PermissionError):
        return security.Refused("that file cannot be opened", code="denied", status=403)
    if isinstance(exc, FileNotFoundError):
        return security.Refused("no such file in this vault", code="not_found", status=404)
    traceback.print_exc(file=sys.stderr)
    return security.Refused(
        "something went wrong on this server",
        code="internal",
        status=500,
        detail="the details are in the server log, not in this response",
    )


# ── the handler ──────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    """One request. The order of the guards is the whole design.

    Read the body under a cap, then rate limit, then authenticate, then route.
    Nothing expensive happens before the cheap refusals: a flood is answered by
    a token bucket rather than by three subprocesses.
    """

    protocol_version = "HTTP/1.1"
    server_version = "report-maker"
    sys_version = ""
    timeout = SOCKET_TIMEOUT

    ctx: routes.Ctx  # set by `build`

    #: The session this request opened, for the scrubber. Per handler instance,
    #: which is per connection — and a connection serves its requests one after
    #: another on one thread, so there is no sharing to get wrong. `_serve`
    #: clears it first thing, so a keep-alive connection never scrubs one
    #: request's body with the session the previous one held.
    _session: sessions.Session | None = None

    # ── methods ──

    def version_string(self) -> str:
        """The `Server:` header. Just the name — the default appends the Python
        version, which tells a scanner which CVEs to try first."""
        return self.server_version

    def do_GET(self) -> None:
        self._serve("GET")

    def do_HEAD(self) -> None:
        self._serve("HEAD")

    def do_POST(self) -> None:
        self._serve("POST")

    def do_PUT(self) -> None:
        self._serve("PUT")

    def do_DELETE(self) -> None:
        self._serve("DELETE")

    def do_OPTIONS(self) -> None:
        # No CORS headers anywhere in this file, deliberately. The API is for
        # the page this server itself serves; an `Access-Control-Allow-Origin`
        # would be an invitation for another origin to drive a session that a
        # `SameSite=Lax` cookie is specifically arranged to protect.
        self._respond(
            routes.Reply(status=204, body=b"", headers={"Allow": "GET, HEAD, POST, PUT, DELETE"}),
            None,
            "OPTIONS",
        )

    # ── the loop ──

    def _serve(self, method: str) -> None:
        started = time.monotonic()
        parsed = urlsplit(self.path)
        path = parsed.path
        reply: routes.Reply

        # Cleared here and set by `_dispatch` the moment a session opens, rather
        # than taken from `_dispatch`'s return value. A handler that raises never
        # returns one, and the scrubbing that runs afterwards needs the vault
        # prefix and the session id *most* on that path: an engine failure comes
        # back with a traceback in it, and a traceback names the vault, whose
        # path is the session id. Reading it off the return value meant every
        # error body went out unscrubbed — which is to say, with the cookie's
        # value in it, undoing HttpOnly in the one response nobody looks at.
        self._session = None

        try:
            body = self._body()
            self.ctx.limiter.check(self._ip())
            _, reply = self._dispatch(method, path, parsed.query, body)
        except BaseException as exc:  # noqa: BLE001 — every path ends in a response
            refusal = _refused(exc)
            reply = routes.Reply(status=refusal.status, payload=refusal.payload())
            if refusal.retry_after:
                reply.headers["Retry-After"] = str(refusal.retry_after)

        self._respond(reply, self._session, method)
        self._log(method, path, reply.status, time.monotonic() - started)

    def _dispatch(
        self, method: str, path: str, query: str, body: bytes
    ) -> tuple[sessions.Session | None, routes.Reply]:
        found = routes.match(method if method != "HEAD" else "GET", path)
        if found is None:
            return None, self._static(path, method)

        handler, params, needs_session = found
        self._same_site(method)

        sid = sessions.parse_cookie(self.headers.get("Cookie"))
        session = sessions.get(self.ctx.root, sid) if sid else None
        # Published to the response path immediately, before anything can raise:
        # from here on every body — refusal or answer — is scrubbed of this
        # session's vault path and of the id inside it.
        self._session = session
        if session is None and needs_session:
            raise routes.no_session()

        bridge = None
        if session is not None:
            sessions.touch(session)
            routes.load_github(self.ctx, session)
            bridge = routes.Bridge(self.ctx, session)
            params.setdefault("sid", session.id)
        elif sid:
            # `DELETE /api/session` on an id that opens nothing still has to
            # answer the same way, so the raw id is carried through for it and
            # for nothing else.
            params.setdefault("sid", sid)

        request = routes.Request(
            method=method,
            path=path,
            params=params,
            query=parse_qs(query, keep_blank_values=True),
            body=body,
            ctx=self.ctx,
            ip=self._ip(),
            session=session,
            bridge=bridge,
        )
        return session, handler(request)

    # ── reading ──

    def _body(self) -> bytes:
        """The request body, capped, or a refusal.

        `Transfer-Encoding: chunked` is refused rather than decoded. This server
        does not implement chunked decoding, and a body whose length the server
        cannot state before reading it is a body with no cap on it.
        """
        if self.headers.get("Transfer-Encoding", "").strip().lower():
            raise security.Refused(
                "send a request body with a Content-Length",
                code="bad_request",
                status=411,
            )
        raw = self.headers.get("Content-Length")
        if not raw:
            return b""
        try:
            length = int(raw)
        except ValueError as exc:
            raise security.Refused(
                "that Content-Length is not a number", code="bad_request", status=400
            ) from exc
        if length < 0 or length > MAX_BODY:
            self._drain(length)
            raise security.Refused(
                f"that request body is larger than {MAX_BODY // 1024} KB",
                code="too_large",
                status=413,
                detail="a report is edited a file at a time; nothing here takes "
                "an upload",
            )
        return self.rfile.read(length)

    def _drain(self, length: int) -> None:
        """Swallow a body that is about to be refused, up to a point.

        A client that is mid-send when the connection closes gets a broken pipe
        and never reads the 413, so a modest overshoot is read and discarded to
        let the refusal arrive. Past `DRAIN_LIMIT` the connection is closed
        instead: reading gigabytes to be polite about refusing them is the
        denial of service the cap exists to prevent.
        """
        if length < 0 or length > DRAIN_LIMIT:
            self.close_connection = True
            return
        left = length
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                return
            left -= len(chunk)

    def _same_site(self, method: str) -> None:
        """Refuse a state-changing request that another site started.

        The session cookie is `SameSite=Lax`, which already keeps it off a
        cross-site POST. This is the second lock: `Sec-Fetch-Site` is set by the
        browser and cannot be forged by the page, so when it is present and says
        the request came from somewhere else, a write is refused outright.

        Absent is allowed. Not every client sends it, and `curl` sends none of
        it — this file is not the place to decide that the API may only be
        driven by a browser.
        """
        if method in ("GET", "HEAD", "OPTIONS"):
            return
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site and site not in ("same-origin", "none"):
            raise security.Refused(
                "refusing a write that another site started",
                code="cross_site",
                status=403,
            )

    def _ip(self) -> str:
        """The source address, as the socket saw it.

        Deliberately *not* `X-Forwarded-For`. Behind a proxy that header is the
        truth; in front of one it is a string the client typed, and trusting it
        turns the rate limiter off for anyone who reads this file. An operator
        who terminates TLS elsewhere wants their proxy to do the limiting too.
        """
        return self.client_address[0] if self.client_address else "?"

    # ── static ──

    def _static(self, path: str, method: str) -> routes.Reply:
        """The built frontend, behind the same path guard as everything else.

        A static handler is the classic traversal hole, so the URL is taken
        apart the same way a route is — split, decoded once per segment,
        rejoined — and handed to `security.within` against the dist directory.
        A `..` never survives that, and neither does a symlink out of the tree.

        Anything that is not a file and does not look like an asset falls back
        to `index.html`, because the frontend routes on the path and a reload on
        `/report/x` must not 404. `/api` and `/s` never fall back: a mistyped
        API path answering with a page of HTML is how a client ends up parsing
        `<!doctype` as JSON.
        """
        dist = self.ctx.client
        if dist is None:
            raise security.Refused(
                "no frontend is built into this server",
                code="not_found",
                status=404,
                detail="run `npm --prefix web/client run build`, or ask for /api",
            )
        if path.startswith("/api/") or path == "/api" or path.startswith("/s/"):
            raise security.Refused("no such route", code="not_found", status=404)

        relative = "/".join(unquote(segment) for segment in path.strip("/").split("/") if segment)
        target: Path | None = None
        if relative:
            # A refused path is a 404, not a fallback. Answering a `..` with the
            # application shell and a 200 would file a traversal attempt under
            # "user reloaded a page", which is the log line nobody reads.
            candidate = security.within(dist, relative)
            if candidate.is_file():
                target = candidate
        if target is None:
            target = dist / "index.html"
            if not target.is_file():
                raise security.Refused("no such route", code="not_found", status=404)

        suffix = target.suffix.lower()
        kind = _TEXT_TYPES.get(suffix, "application/octet-stream")
        if suffix == ".html":
            return self._page(target)

        raw = target.read_bytes()
        # Vite writes content-hashed filenames under /assets, so those bytes can
        # never change under a given URL and are cached for a year. Anything
        # else keeps its name across a rebuild and must be revalidated.
        immutable = relative.startswith("assets/")
        headers = dict(security.BASE_HEADERS)
        headers["Content-Type"] = kind
        headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if immutable else "no-cache"
        )
        return routes.Reply(body=raw, headers=headers)

    def _page(self, target: Path) -> routes.Reply:
        """`index.html` with a fresh nonce, spec requirement 12.

        The CSP is `script-src 'nonce-…'` and nothing else — not `'self'`,
        because this server's job is serving files a stranger wrote and a
        same-origin URL is therefore not a trusted origin here. The cost is that
        the bundle's own `<script>` has to carry the nonce, and Vite does not
        write one. So it is added on the way out, to every `<script>` and
        `<style>` that has not already got one.

        Never cached. A cached page is a cached nonce, and a nonce that is
        reused is a nonce that is guessable.
        """
        nonce = security.nonce()
        document = target.read_bytes()
        document = document.replace(b"<script", b'<script nonce="%s"' % nonce.encode())
        document = document.replace(b"<style", b'<style nonce="%s"' % nonce.encode())
        headers = security.security_headers(nonce, tls=self.ctx.tls)
        headers["Content-Type"] = "text/html; charset=utf-8"
        headers["Cache-Control"] = "no-store"
        return routes.Reply(body=document, headers=headers)

    # ── writing ──

    def _respond(
        self, reply: routes.Reply, session: sessions.Session | None, method: str
    ) -> None:
        body = reply.body
        headers = dict(reply.headers)
        if body is None:
            payload = _scrub(
                reply.payload,
                _roots(self.ctx, session),
                session.id if session else None,
            )
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json; charset=utf-8")
            headers.setdefault("Cache-Control", "no-store")
            for name, value in security.BASE_HEADERS.items():
                headers.setdefault(name, value)

        # A caller may hand over a whole header set — `share.headers` does, and it
        # counts the bytes of the file on disk. The length of what is actually
        # being written is the only one that can be right, so any other is
        # dropped rather than sent alongside: two Content-Lengths on one
        # response is the shape a request-smuggling proxy bug is made of.
        headers.pop("Content-Length", None)
        headers.pop("content-length", None)

        try:
            self.send_response(reply.status)
            for name, value in headers.items():
                self.send_header(name, value)
            for cookie in reply.cookies:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if method != "HEAD" and reply.status not in (204, 304):
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            # A browser that navigated away mid-build. Nothing to say about it,
            # and nothing to clean up: the engine subprocess is already done.
            self.close_connection = True

    # ── logging ──

    def log_message(self, fmt: str, *args: Any) -> None:
        """Silence the default. Every line this server writes goes through
        `_log`, which knows what must not be in one."""

    def _log(self, method: str, path: str, status: int, seconds: float) -> None:
        """One line per request, and two things deliberately missing.

        The query string is dropped whole — `?code=` on the GitHub callback is
        an authorization code, and a log is a file that gets shipped to a log
        service. A share token is redacted for the same reason: it is the entire
        authorisation for that URL, so a token in a log is a share that anyone
        with the log can read.

        The session id is never here at all; nothing in this line is derived
        from a cookie.
        """
        shown = "/s/…" if path.startswith("/s/") else path
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(
            f"{stamp}  {method:<6} {shown}  {status}  {seconds * 1000:.0f}ms  {self._ip()}",
            file=sys.stderr,
            flush=True,
        )


# ── assembling one ───────────────────────────────────────────────────────────


def context(options: Options) -> routes.Ctx:
    """Everything the server is, resolved once.

    The sessions root is declared to the engine bridge here and nowhere else.
    Until it is, the bridge refuses to spawn anything at all — which is a
    startup error somebody notices in the first minute, rather than a default
    that quietly permits `-C /`.
    """
    root = Path(options.root or os.environ.get("RM_WEB_ROOT") or _scratch()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    store = root / sessions.SESSIONS_DIRNAME
    store.mkdir(parents=True, exist_ok=True, mode=sessions.DIR_MODE)
    shares = root / "shares"
    shares.mkdir(parents=True, exist_ok=True)

    engine.set_sessions_root(store)

    client = options.client
    if client is None:
        env_client = os.environ.get("RM_WEB_CLIENT")
        client = Path(env_client) if env_client else Path(__file__).resolve().parent.parent / "client" / "dist"
    client = Path(client).resolve()

    return routes.Ctx(
        root=root,
        store=store,
        shares=shares,
        client=client if (client / "index.html").is_file() else None,
        tls=options.tls,
        limiter=security.RateLimiter(),
    )


def _scratch() -> Path:
    """A per-run directory when nothing said where to keep sessions.

    Under the system temp root rather than beside the checkout: a session vault
    is a stranger's files with a 24-hour life, and the place for those is the
    place the operating system already sweeps.
    """
    import tempfile

    return Path(tempfile.mkdtemp(prefix="report-maker-web-"))


def build(options: Options) -> tuple[ThreadingHTTPServer, routes.Ctx]:
    """A bound server and its context, not yet serving.

    Split from `serve` so a test can drive the real thing on an ephemeral port
    in the same process — which is the only way the loop in `test_api.py` is
    testing what actually ships rather than a stub of it.
    """
    ctx = context(options)

    class Bound(Handler):
        pass

    Bound.ctx = ctx
    server = ThreadingHTTPServer((options.host, options.port), Bound)
    server.daemon_threads = True
    return server, ctx


def sweeper(ctx: routes.Ctx, options: Options, stop: threading.Event) -> threading.Thread:
    """Spec requirement 9, plus the share directory nobody else sweeps.

    One thread for both, because they are the same job: a session is 24 hours
    of idle and a share is a month of existing, and both end with a directory
    entry going away. Errors are swallowed by `sessions.sweep` itself — on a
    background thread an exception is a thread that quietly stops reclaiming
    disk, which is the failure nobody notices until the volume is full.
    """

    def loop() -> None:
        while not stop.wait(options.sweep_seconds):
            sessions.sweep(ctx.root)
            _sweep_shares(ctx, options.share_ttl_hours)

    thread = threading.Thread(target=loop, name="rm-web-sweeper", daemon=True)
    thread.start()
    return thread


def _sweep_shares(ctx: routes.Ctx, ttl_hours: float) -> int:
    """Drop share bundles past their life. Zero hours means never."""
    if ttl_hours <= 0:
        return 0
    cutoff = time.time() - ttl_hours * 3600
    gone = 0
    try:
        entries = list(os.scandir(ctx.shares))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_file(follow_symlinks=False) and entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)
                gone += 1
        except OSError:
            continue
    return gone


# ── the command line ─────────────────────────────────────────────────────────


def _loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _warn_exposed(options: Options, ctx: routes.Ctx) -> None:
    """Spec requirement 1: say what is being exposed, in words.

    Not "warning: binding to 0.0.0.0". The thing an operator has to weigh is
    what the port *does*, and every line below is a capability this server hands
    to whoever can reach it.
    """
    print(
        f"\n  ⚠  report-maker web is listening on {options.host}:{options.port} — "
        "not just this machine.\n"
        "\n     Anyone who can reach that port can:\n"
        "       · create a vault on this disk and write files into it\n"
        "       · run typst on source they wrote, on this CPU\n"
        "       · make this server fetch a URL of their choosing (`cite`)\n"
        "       · publish a page this server will serve to anyone, with no login\n"
        "\n     Quotas and the SSRF pre-flight are on, `template install` and\n"
        "     diagrams are off, and none of that is the same thing as an\n"
        "     authenticated service. Put it behind TLS and something that knows\n"
        "     who your users are before it is reachable from the internet.\n",
        file=sys.stderr,
        flush=True,
    )
    if not options.tls:
        print(
            "     RM_WEB_SECURE_COOKIE is not set, so the session cookie has no\n"
            "     Secure flag and will travel over plain HTTP.\n",
            file=sys.stderr,
            flush=True,
        )


def parse(argv: list[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(
        prog="python3 -m web",
        description="report-maker over HTTP: a vault per session, no account.",
        epilog="Every question about a vault is a `report-maker` subprocess. "
        "Nothing here parses a report.",
    )
    parser.add_argument("--host", default=os.environ.get("RM_WEB_HOST", DEFAULT_HOST),
                        help=f"interface to bind (default {DEFAULT_HOST}; anything "
                             "else prints a warning naming what is exposed)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("RM_WEB_PORT", DEFAULT_PORT)),
                        help=f"port to bind (default {DEFAULT_PORT})")
    parser.add_argument("--root", default=os.environ.get("RM_WEB_ROOT"),
                        help="where sessions and shares live (default: a per-run temp dir)")
    parser.add_argument("--client", default=os.environ.get("RM_WEB_CLIENT"),
                        help="the built frontend (default: web/client/dist)")
    parser.add_argument("--tls", action="store_true",
                        default=_truthy(os.environ.get("RM_WEB_SECURE_COOKIE")),
                        help="this server is reached over HTTPS: set Secure on the "
                             "cookie and send HSTS")
    args = parser.parse_args(argv)
    return Options(
        host=args.host,
        port=args.port,
        root=Path(args.root) if args.root else None,
        client=Path(args.client) if args.client else None,
        tls=bool(args.tls),
        share_ttl_hours=float(os.environ.get("RM_WEB_SHARE_TTL_HOURS", SHARE_TTL_HOURS)),
    )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def main(argv: list[str] | None = None) -> int:
    options = parse(argv)
    try:
        server, ctx = build(options)
    except OSError as exc:
        print(f"report-maker web: cannot bind {options.host}:{options.port} — {exc}",
              file=sys.stderr)
        return 1

    # `RM_WEB_SECURE_COOKIE` is what `sessions.cookie_for` consults when nobody
    # passes `secure=`, so `--tls` is made to mean the same thing there. Set
    # rather than read, because a flag that half-applies is worse than no flag.
    if options.tls:
        os.environ.setdefault("RM_WEB_SECURE_COOKIE", "1")

    stop = threading.Event()
    sweeper(ctx, options, stop)

    print(
        f"report-maker web on http://{options.host}:{options.port}\n"
        f"  engine    {engine.version() or 'not found'}\n"
        f"  diagrams  {'on' if engine.diagrams_enabled() else 'off (RM_WEB_DIAGRAMS=1)'}\n"
        f"  github    {github.status()['mode']}\n"
        f"  frontend  {'built' if ctx.client else 'not built — API only'}",
        file=sys.stderr,
        flush=True,
    )
    if not _loopback(options.host):
        _warn_exposed(options, ctx)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nreport-maker web: stopping", file=sys.stderr)
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
    return 0
