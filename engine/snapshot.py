"""Archived evidence.

A citation points at a page, and a page is not a stable thing. It gets reworded,
repriced, paywalled, redirected and deleted, usually without a trace, and usually
between the day a report is written and the day somebody disputes it. A `@key`
that resolves to a dead link proves nothing.

So when a source is cited, its bytes are kept:

    reports/<id>/snapshots/<key>.html    the response body, verbatim
    reports/<id>/snapshots/<key>.txt     the same page as plain text
    reports/<id>/snapshots/<key>.json    the record, with the sha256

They live *with* the report rather than in a vault-wide cache, so moving the
report folder moves the evidence with it, a `git mv` keeps the history, and a
report handed to somebody else arrives complete. The verbatim bytes are what you
show when the claim is challenged; the extracted text is what `check` reads to
prove a quoted sentence really appears on the page; the record is what `verify`
compares against to notice the page has changed underneath us.

Two constraints shape the fetching. The first is that **only http and https are
ever fetched** — a `url:` in a bibliography is untrusted input, and a vault that
could write `file:///etc/passwd` into a source would turn `report-maker cite`
into a file-exfiltration tool. That check is a security boundary, not a
convenience, and it is applied to the URL we were given *and* to wherever the
redirects landed. The second is that fetching must be injectable, so every test
in this repository runs with the network unplugged.

**The scheme check alone was never sufficient, and saying why is the point of
this paragraph.** It answers "what protocol will we speak", and the question a
caller who vetted a URL actually needs answered is "which machine will we speak
it to". Those come apart in two places, both of them ordinary:

- **The name is resolved again.** Somebody — `report-maker cite` called from a
  server, a wrapper that checks a URL before handing it over — looks the host
  up, judges every address it answers with, and passes the URL on. This module
  then calls `getaddrinfo` a second time, and a name server willing to answer
  the two lookups differently has just chosen the address for a fetch that was
  approved against a different one. That is a DNS rebind, and no amount of
  checking the *string* catches it.
- **A redirect is followed on its scheme.** `302 Location:
  http://169.254.169.254/latest/meta-data/` is `http`, so it passed, and the
  cloud metadata endpoint was archived into `snapshots/` where whoever asked
  for the citation reads it straight back. No hostile resolver, no timing, one
  header.

So `http_fetch` takes `pinned=` and `on_redirect=`. A pinned fetch connects to
the literal address the caller vetted while keeping the hostname for the `Host`
header, the TLS SNI and the certificate check — nothing about verification is
relaxed to make that work, the stdlib is simply handed a socket already opened
somewhere approved — and it refuses a redirect that leaves the origin it was
pinned for. `on_redirect` sees every hop before it is followed and aborts the
fetch by raising. Both default to off: a person citing a URL at a terminal is
the caller *and* the vetter, and gets exactly the fetch this module has always
made.
"""

from __future__ import annotations

import codecs
import contextlib
import datetime as dt
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from .workspace import Report

# Politeness, and traceability: a site owner reading their logs can find out what
# this is and who wrote it, rather than seeing an anonymous scraper.
USER_AGENT = "report-maker/1.0 (+https://github.com/younissk/report-maker)"

# Enough for any document worth citing, small enough that a mislabelled video
# cannot exhaust memory.
MAX_BYTES = 8 * 1024 * 1024

# The security boundary. Everything else — file, ftp, data, gopher — is refused.
WEB_SCHEMES = frozenset({"http", "https"})

HTML_TYPES = frozenset({"text/html", "application/xhtml+xml", "application/xhtml"})

# Text we can read but that is not markup. Anything outside this set and the HTML
# types is stored as bytes with no text rendition at all.
PLAIN_TYPES = frozenset({"application/json", "application/xml", "application/ld+json"})

# Page furniture. It is on every page of a site, it is not what the citation is
# about, and leaving it in makes two unrelated pages look 60% similar to `verify`.
# `title` is in here because it belongs to the record, not to the body — a quote
# check should not match text that never appeared on the page.
DROP = frozenset(
    {
        "script",
        "style",
        "nav",
        "footer",
        "header",
        "aside",
        "noscript",
        "template",
        "title",
    }
)

# Tags that end a line of prose. Without them the whole page collapses into one
# run-on paragraph and a quote that spans two list items can never be matched.
BREAKS = frozenset(
    """address article blockquote br dd details div dl dt fieldset figcaption
    figure form h1 h2 h3 h4 h5 h6 hr li main ol p pre section summary table
    tbody td th thead tr ul""".split()
)

CHARSET_IN_TYPE = re.compile(r"charset\s*=\s*\"?([\w.:+-]+)", re.I)
CHARSET_IN_META = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:+-]+)""", re.I)

# A rotated snapshot, kept by `rotate` when a refresh would otherwise overwrite
# evidence: `<key>.2026-08-18.html`.
ROTATED = re.compile(r"\.\d{4}-\d{2}-\d{2}$")


class SnapshotError(RuntimeError):
    pass


@dataclass
class Fetched:
    """One HTTP response, reduced to the parts worth archiving."""

    url: str
    status: int
    content_type: str
    body: bytes
    # Where the redirects actually landed. Defaulted so a test fetcher that does
    # not care about redirects can leave it out; readers use `final_url or url`.
    final_url: str = ""


#: How a URL becomes bytes. Injected everywhere, so no test ever needs a network.
Fetcher = Callable[[str], Fetched]


# ── fetching ─────────────────────────────────────────────────────────────────


def _require_web_scheme(url: str, *, after_redirect: bool = False) -> None:
    scheme = urlsplit(url).scheme.lower()
    if scheme in WEB_SCHEMES:
        return
    where = "a redirect sent us to" if after_redirect else "asked to archive"
    raise SnapshotError(
        f"refusing to fetch {url!r}: {where} the {scheme or 'schemeless'} scheme, "
        "and only http and https are ever fetched.\n"
        "  A URL in a bibliography is untrusted input; archiving one that names a "
        "local path would make citing a file-reading command."
    )


class _WebOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect off http/https *before* it is followed.

    urllib will follow a redirect to `ftp://` without complaint, which would let
    a page choose a host and a protocol the engine never meant to speak. Checking
    the response URL afterwards is too late — by then the connection has been
    made. So the check goes here, where the new location is still just a string.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_web_scheme(newurl, after_redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_WebOnlyRedirects)


# ── pinning ──────────────────────────────────────────────────────────────────
#
# Everything below is off unless a caller asks for it. It exists for the one
# situation the module docstring describes: somebody other than the person at
# the keyboard vetted this URL, and the vetting is only worth anything if the
# connection goes where they looked.


#: What `on_redirect` is handed, and what it may hand back. Raising aborts the
#: fetch. Returning an IP literal pins that hop, so a caller who vets a
#: redirect can also say which address it vetted; returning `None` leaves the
#: hop to the pin map, which refuses it unless it stays on a pinned origin.
Watcher = Callable[[str], str | None]


def _ip_literal(address: str) -> str:
    """A pinned address is a literal, and this is where that is insisted on.

    A hostname here would have to be resolved, and the resolution is the exact
    step pinning exists to remove. Refusing it is not pedantry: `--pinned-address
    metadata.internal` would look like a pin and be a lookup.
    """
    try:
        return str(ipaddress.ip_address(address.strip()))
    except ValueError as exc:
        raise SnapshotError(
            f"{address!r} is not an IP address — a pinned address is the literal "
            "the connection is made to.\n"
            "  A name would have to be resolved here, and that second lookup is "
            "what pinning exists to remove."
        ) from exc


def _origin(url: str) -> tuple[str, int]:
    """The (host, port) a URL connects to: lowercased, with the scheme's default.

    Pins are keyed on this rather than on the URL, because a redirect within one
    site changes the path and connects to the same place — and because `HOST`
    and `host` are one machine.
    """
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as exc:
        raise SnapshotError(f"{url!r} does not name a port number: {exc}") from exc
    host = (parts.hostname or "").strip().lower()
    return host, port or (443 if parts.scheme.lower() == "https" else 80)


def _vet_hop(pins: dict[tuple[str, int], str], newurl: str, watch: Watcher | None) -> None:
    """Show a redirect to the watcher, then hold it to the pin map.

    Order matters. The watcher sees the hop first, so a caller that wants to
    judge an address can add it; what it does not add is refused, because a pin
    that lets an unvetted origin through on the strength of nobody having
    objected is not a pin.
    """
    if watch is not None:
        vetted = watch(newurl)  # raising here aborts the fetch, by contract
        if pins and vetted:
            pins[_origin(newurl)] = _ip_literal(str(vetted))
    if not pins or _origin(newurl) in pins:
        return
    known = ", ".join(f"{host}:{port}" for host, port in sorted(pins))
    raise SnapshotError(
        f"refusing to follow the redirect to {newurl}: this fetch is pinned to "
        f"{known}, and that hop leaves it.\n"
        "  The address was vetted by whoever asked for this fetch; a hop they "
        "never saw is a hop nobody checked, and following it on the strength of "
        "its scheme is how a redirect reaches a private address."
    )


def _guarded_opener(
    pins: dict[tuple[str, int], str], watch: Watcher | None
) -> urllib.request.OpenerDirector:
    """An opener that connects only where it was told, and reports every hop.

    Built by hand rather than with `build_opener`, for two reasons. It installs
    `FileHandler`, `FTPHandler` and `DataHandler` by default, which is a set of
    doors this fetch has no use for; and it installs a `ProxyHandler` taken from
    the environment, which would resolve the hostname at the far end and undo
    the pin without anything looking wrong.
    """

    def create_connection(address, timeout, source_address):
        """`http.client`'s socket factory, replaced with a pinned one.

        Everything downstream of it is the stdlib's own and untouched — the
        `TCP_NODELAY` it sets, and for TLS the `wrap_socket(...,
        server_hostname=self.host)` in `HTTPSConnection.connect`. That is the
        whole trick: the certificate is still verified against the *name* the
        caller asked for, with the default context's `check_hostname` and
        `CERT_REQUIRED` intact. Nothing is disabled to make pinning work.
        """
        host = str(address[0]).strip("[]").lower()
        port = int(address[1])
        pinned = pins.get((host, port))
        if pinned is None:
            raise SnapshotError(
                f"refusing to connect to {host}:{port}: this fetch is pinned, "
                "and that host is not one of the addresses that were vetted.\n"
                "  A pinned fetch connects where the caller checked, or it does "
                "not connect."
            )
        return socket.create_connection((pinned, port), timeout, source_address)

    class _PinnedConnection:
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._create_connection = create_connection

        def connect(self) -> None:
            if self._tunnel_host:
                raise SnapshotError(
                    "refusing to fetch through a proxy: the proxy resolves the "
                    "hostname at its end, which undoes the pin"
                )
            super().connect()

    class _Plain(_PinnedConnection, http.client.HTTPConnection):
        pass

    class _Secure(_PinnedConnection, http.client.HTTPSConnection):
        pass

    class _PlainHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(_Plain, req)

    class _SecureHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_Secure, req, context=self._context)

    class _WatchedRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _require_web_scheme(newurl, after_redirect=True)
            _vet_hop(pins, newurl, watch)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    transport: tuple[urllib.request.BaseHandler, ...] = (
        (_PlainHandler(), _SecureHandler())
        if pins
        else (urllib.request.HTTPHandler(), urllib.request.HTTPSHandler())
    )
    opener = urllib.request.OpenerDirector()
    for handler in (
        *transport,
        _WatchedRedirects(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.HTTPDefaultErrorHandler(),
        # Refuses every other scheme *loudly*. Without it an opener with no
        # handler for `file:` does not raise — `open` runs out of handlers and
        # returns None, and the caller meets an AttributeError somewhere else.
        urllib.request.UnknownHandler(),
    ):
        opener.add_handler(handler)
    return opener


def fetcher(*, pinned: str | None = None, on_redirect: Watcher | None = None) -> Fetcher:
    """A `Fetcher` with a pin and a watcher baked in — or `http_fetch` itself.

    With neither argument this returns the module-level function unchanged, so
    the default path is not merely equivalent to today's, it *is* today's. That
    is the property `report-maker cite <url>` at a terminal depends on.

    With a pin it returns a fetcher that pins each URL it is given to that
    address — and refuses the second *different* origin in one run. A single
    address names one machine, so a `verify` pass reaching two hosts under one
    pin is a caller mistake, and it is better to say so than to connect the
    second host to the first one's address and report the resulting mess as
    evidence drift.
    """
    if pinned is None and on_redirect is None:
        return http_fetch
    address = _ip_literal(pinned) if pinned is not None else None
    bound: dict[tuple[str, int], str] = {}

    def fetch(url: str) -> Fetched:
        if address is not None:
            where = _origin(url)
            if not bound:
                bound[where] = address
            elif where not in bound:
                held = ", ".join(f"{host}:{port}" for host, port in sorted(bound))
                raise SnapshotError(
                    f"refusing to fetch {url}: this run is pinned to {held}, and "
                    "one address cannot stand for two hosts.\n"
                    "  Pin a run that reaches one host, or fetch the rest "
                    "without a pin and vet them another way."
                )
        return http_fetch(url, pinned=address, on_redirect=on_redirect)

    return fetch


def http_fetch(
    url: str,
    timeout: float = 20.0,
    pinned: str | None = None,
    on_redirect: Watcher | None = None,
) -> Fetched:
    """Fetch a URL for archiving. Redirects are followed; schemes are not trusted.

    An HTTP error status comes back as a `Fetched` rather than an exception: a
    404 on a page a report cites is a *finding*, and the body the server sent
    with it is worth keeping. Only a failure to speak to the server at all — DNS,
    TLS, a timeout — raises.

    `pinned` is an IP literal, and it means: connect *there*, and keep `url`'s
    hostname for the `Host` header, the TLS SNI and the certificate check. It is
    for a caller who resolved the name and judged the addresses already — a
    server citing a URL a stranger typed — so that the fetch happens against
    what was judged rather than against whatever the resolver says a second time.
    A pinned fetch also refuses a redirect off the origin it was pinned for,
    because a hop the caller never saw is a hop nobody vetted.

    `on_redirect` is called with every hop before it is followed, and anything it
    raises aborts the fetch. It may return an IP literal to pin that hop.

    Both default to `None`, and with both unset this is the fetch it has always
    been, through the module-level opener.
    """
    _require_web_scheme(url)
    opener = _OPENER
    if pinned is not None or on_redirect is not None:
        opener = _guarded_opener(
            {_origin(url): _ip_literal(pinned)} if pinned is not None else {},
            on_redirect,
        )
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            # Belt and braces: the redirect handler above refuses a non-web hop
            # before it happens, and this catches anything that reached us anyway.
            final = response.geturl() or url
            _require_web_scheme(final, after_redirect=True)
            return Fetched(
                url=url,
                status=int(getattr(response, "status", None) or 200),
                content_type=response.headers.get("Content-Type", "") or "",
                body=_read_capped(response, url),
                final_url=final,
            )
    except urllib.error.HTTPError as error:
        # An HTTPError *is* the response — it holds the connection the server
        # answered on, and nothing hands it back to the caller. Closing it on
        # every path matters because `verify` walks every archived URL in the
        # vault in one pass: a socket leaked per 404 is a run that eventually
        # dies on file descriptors rather than on anything to do with evidence.
        with contextlib.closing(error):
            # urllib reports a redirect it would not follow as an HTTPError naming
            # the location — including the ones it refuses on scheme grounds, which
            # is how a `file://` Location arrives here rather than at the handler.
            _require_web_scheme(error.geturl() or url, after_redirect=True)
            if 300 <= error.code < 400:
                raise SnapshotError(
                    f"could not follow the redirects from {url}: {error.reason}"
                ) from error
            # Otherwise: data, not a crash. The error page is what the URL serves today.
            return Fetched(
                url=url,
                status=int(error.code),
                content_type=(
                    error.headers.get("Content-Type", "") if error.headers else ""
                )
                or "",
                body=_read_capped(error, url),
                final_url=error.geturl() or url,
            )
    except SnapshotError:
        raise
    except urllib.error.URLError as error:
        raise SnapshotError(f"could not reach {url}: {error.reason}") from error
    except (OSError, ValueError) as error:
        raise SnapshotError(f"could not fetch {url}: {error}") from error


def _read_capped(stream, url: str) -> bytes:
    """Read a response body, refusing anything over the cap.

    Truncating would be worse than failing: half a document has a sha256 of its
    own, and `verify` would then compare two halves and call the page stable.
    """
    body = stream.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise SnapshotError(
            f"{url} is larger than {MAX_BYTES // (1024 * 1024)} MiB — refusing to "
            "archive a truncated copy. Cite the document with --no-snapshot, or "
            "link a smaller canonical page."
        )
    return body


# ── decoding ─────────────────────────────────────────────────────────────────


def _mime(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _charset(body: bytes, content_type: str) -> str:
    """The declared encoding: the header first, then the document, then utf-8.

    The header wins because it is what the client actually received; a `<meta
    charset>` is the author's intent and is frequently stale.
    """
    declared = CHARSET_IN_TYPE.search(content_type or "")
    embedded = CHARSET_IN_META.search(body[:4096])
    candidates = [
        declared.group(1) if declared else "",
        embedded.group(1).decode("ascii", "replace") if embedded else "",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            codecs.lookup(candidate)
        except LookupError:
            continue  # a charset nobody implements is not a reason to give up
        return candidate
    return "utf-8"


def _decode(body: bytes, content_type: str) -> str:
    if body[:3] == codecs.BOM_UTF8:
        return body.decode("utf-8-sig", errors="replace")
    return body.decode(_charset(body, content_type), errors="replace")


def _is_html(body: bytes, content_type: str) -> bool:
    mime = _mime(content_type)
    if mime:
        return mime in HTML_TYPES
    head = body[:2048].lower()
    return b"<html" in head or b"<!doctype html" in head


# ── plain text ───────────────────────────────────────────────────────────────


class _TextExtractor(HTMLParser):
    """Markup in, readable prose out.

    Deliberately forgiving: real pages are unbalanced, and a parser that raised
    on the first stray `</div>` would archive nothing. Unknown tags are simply
    passed through as their text.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._dropped: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in DROP:
            self._dropped.append(tag)
        elif tag in BREAKS and not self._dropped:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in BREAKS and not self._dropped:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in DROP:
            if tag in self._dropped:
                # Close anything left open inside it too — an unclosed <div> in a
                # <nav> must not leak the rest of the page into the dropped state.
                last = len(self._dropped) - 1 - self._dropped[::-1].index(tag)
                del self._dropped[last:]
        elif tag in BREAKS and not self._dropped:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._dropped:
            # Newlines inside a text run are source wrapping, not structure. Left
            # in, a sentence broken across two lines of HTML could never be
            # matched against a quote written as one line.
            self.parts.append(re.sub(r"\s+", " ", data))


def _collapse(text: str) -> str:
    """One space between words, one line break per block. No blank lines.

    Structure is kept only to the extent that it separates statements: a list of
    four bullets reads as four lines, so a quote cannot silently span two of
    them, and nothing more is promised.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)  # spaces, tabs, nbsp — but not newlines
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line) + "\n" if any(lines) else ""


def extract_text(html: bytes, content_type: str) -> str:
    """The page as prose, for quote checking. Empty when there is no text to get.

    A PDF is the honest empty case: its bytes are archived and its sha is
    recorded, but the engine has no PDF text layer and will not pretend to. The
    record says so, so a caller can tell "no text" from "not archived".
    """
    if _is_html(html, content_type):
        parser = _TextExtractor()
        parser.feed(_decode(html, content_type))
        parser.close()
        return _collapse("".join(parser.parts))
    mime = _mime(content_type)
    if mime.startswith("text/") or mime in PLAIN_TYPES:
        return _collapse(_decode(html, content_type))
    return ""


# ── metadata ─────────────────────────────────────────────────────────────────


class _MetaExtractor(HTMLParser):
    """Collects the handful of `<head>` facts a bibliography entry is made of."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.times: list[str] = []
        self.json_ld: list[str] = []
        self._in_title = False
        self._in_json_ld = False

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag == "title" and not self.title:
            self._in_title = True
        elif tag == "meta":
            name = (
                values.get("property")
                or values.get("name")
                or values.get("itemprop")
                or ""
            ).strip().lower()
            content = values.get("content", "").strip()
            # First occurrence wins: a page that repeats og:title later is
            # usually a widget, not a correction.
            if name and content and name not in self.meta:
                self.meta[name] = content
        elif tag == "time" and values.get("datetime"):
            self.times.append(values["datetime"].strip())
        elif tag == "script" and _mime(values.get("type", "")) == "application/ld+json":
            self._in_json_ld = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script":
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._in_json_ld:
            self.json_ld.append(data)


def _ld_nodes(data) -> list[Mapping]:
    """Every mapping inside a JSON-LD blob, outermost first.

    Bounded rather than recursive: schema.org graphs nest arbitrarily and some
    are enormous, and nothing worth reading is a hundred levels down.
    """
    out: list[Mapping] = []
    queue = [data]
    while queue and len(out) < 200:
        item = queue.pop(0)
        if isinstance(item, Mapping):
            out.append(item)
            queue.extend(v for v in item.values() if isinstance(v, (dict, list)))
        elif isinstance(item, list):
            queue.extend(item)
    return out


def _ld_person(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return str(value.get("name", "")).strip()
    if isinstance(value, list):
        return ", ".join(part for part in map(_ld_person, value) if part)
    return ""


def _from_json_ld(blobs: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for blob in blobs:
        try:
            data = json.loads(blob)
        except (ValueError, TypeError):
            continue  # a broken embedded blob is not a reason to lose the page
        for node in _ld_nodes(data):
            author = _ld_person(node.get("author"))
            if author and "author" not in found:
                found["author"] = author
            published = node.get("datePublished")
            if published and "published" not in found:
                found["published"] = str(published).strip()
    return found


def extract_meta(html: bytes, content_type: str) -> dict:
    """`{"title","author","site","published"}` — but only the keys we really found.

    Nothing here is inferred. An absent author stays absent rather than becoming
    the domain name, because a bibliography that invents an attribution is worse
    than one that admits it does not know.
    """
    if not _is_html(html, content_type):
        return {}
    parser = _MetaExtractor()
    parser.feed(_decode(html, content_type))
    parser.close()
    linked = _from_json_ld(parser.json_ld)

    candidates = {
        "title": (parser.title, parser.meta.get("og:title")),
        "author": (
            parser.meta.get("author"),
            parser.meta.get("og:article:author"),
            parser.meta.get("article:author"),
            linked.get("author"),
        ),
        "site": (parser.meta.get("og:site_name"),),
        "published": (
            parser.meta.get("article:published_time"),
            parser.times[0] if parser.times else None,
            linked.get("published"),
        ),
    }
    out: dict[str, str] = {}
    for name, sources_for_name in candidates.items():
        for value in sources_for_name:
            cleaned = re.sub(r"\s+", " ", value or "").strip()
            if cleaned:
                out[name] = cleaned
                break
    return out


def sha256_of(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


# ── on disk ──────────────────────────────────────────────────────────────────


def dir_for(report: Report) -> Path:
    return report.folder / "snapshots"


def _filename(key: str) -> str:
    """A citation key as a filename.

    A key may legally contain `:` and `+`, which some filesystems will not take.
    One function does the mapping so reads and writes can never disagree about
    where a snapshot lives.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "-", key)


def raw_path(report: Report, key: str) -> Path:
    return dir_for(report) / f"{_filename(key)}.html"


def text_path(report: Report, key: str) -> Path:
    return dir_for(report) / f"{_filename(key)}.txt"


def record_path(report: Report, key: str) -> Path:
    return dir_for(report) / f"{_filename(key)}.json"


def _now() -> str:
    """The moment of fetching, with its offset.

    Local time rather than UTC, because the first ten characters of this string
    become the accessed date in References and the name of a rotated snapshot.
    Somebody citing a page late on a Tuesday evening in Vienna should not find
    their bibliography claiming they read it on Monday. The offset is carried so
    the instant is still unambiguous.
    """
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def write(report: Report, key: str, fetched: Fetched) -> dict:
    """Archive one response next to the report, and return the record written."""
    folder = dir_for(report)
    folder.mkdir(parents=True, exist_ok=True)

    body = fetched.body
    final = fetched.final_url or fetched.url
    text = extract_text(body, fetched.content_type)
    meta = extract_meta(body, fetched.content_type)

    record = {
        "key": key,
        "url": final,
        "fetched": _now(),
        "sha256": sha256_of(body),
        "content_type": fetched.content_type,
        "status": int(fetched.status),
        "title": meta.get("title", ""),
        "bytes": len(body),
    }

    # Anything a reader would otherwise have to work out from the file sizes.
    notes = []
    if final != fetched.url:
        notes.append(f"redirected from {fetched.url}")
    if not text:
        notes.append(
            f"no text extracted from {_mime(fetched.content_type) or 'an unlabelled body'}"
            " — the bytes are archived, quote checking is not available"
        )
    if record["status"] >= 400:
        notes.append(f"the server answered {record['status']}")
    if notes:
        record["note"] = "; ".join(notes)

    raw_path(report, key).write_bytes(body)
    text_path(report, key).write_text(text, encoding="utf-8")
    record_path(report, key).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    return record


def rotate(report: Report, key: str) -> list[Path]:
    """Move the current snapshot aside, keyed by the date it was taken.

    A refresh must never overwrite evidence — the whole point of the archive is
    that the old bytes are still there when somebody asks what the page said in
    March. Returns the paths now holding the previous copy.
    """
    record = read_record(report, key)
    if record is None:
        return []
    stamp = str(record.get("fetched", ""))[:10] or "undated"
    moved: list[Path] = []
    for path in (raw_path(report, key), text_path(report, key), record_path(report, key)):
        if not path.is_file():
            continue
        target = path.with_name(f"{path.stem}.{stamp}{path.suffix}")
        counter = 2
        while target.exists():
            target = path.with_name(f"{path.stem}.{stamp}-{counter}{path.suffix}")
            counter += 1
        path.rename(target)
        moved.append(target)
    return moved


def read_record(report: Report, key: str) -> dict | None:
    path = record_path(report, key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None  # a corrupt record reads as "not archived", never as a crash
    return data if isinstance(data, dict) else None


def read_text(report: Report, key: str) -> str | None:
    path = text_path(report, key)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def records(report: Report) -> dict[str, dict]:
    """Every current snapshot record for a report, keyed by citation key.

    This is what `sources.rows(..., snapshots=…)` and the HTML export want:
    one read of the folder rather than one `read_record` per entry. Rotated
    copies are skipped — they are history, not the current state.
    """
    folder = dir_for(report)
    if not folder.is_dir():
        return {}
    out: dict[str, dict] = {}
    for path in sorted(folder.glob("*.json")):
        if ROTATED.search(path.stem):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(data, dict) and data.get("key"):
            out[str(data["key"])] = data
    return out
