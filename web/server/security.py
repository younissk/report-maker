"""The boundary, in one file.

The desktop app trusts its own renderer; this server does not. `web/` hands a
stranger a shell over a folder on our disk and a command that fetches URLs, so
every refusal that used to be a nicety in `app/` is load-bearing here. This
module holds all of them, together, because a guard scattered across the
handlers is a guard with a hole in it: the one route somebody added last week
that resolved a path itself.

Five boundaries live here, and each is written to fail closed.

    path containment     a path from a request is resolved and confirmed to sit
                         inside that session's vault before anything opens it
    the SSRF pre-flight  a URL from a request is resolved and every address it
                         resolves to is vetted before anything connects
    quotas               disk, wall clock, commands and reports, per session
    rate limiting        requests and session creations, per source address
    response headers     escaping, and a CSP with nothing external in it

None of it knows anything about vaults. A vault is the engine's subject, and
`web/` answers every question about one by shelling out to `report-maker`
(see `app/README.md`, "What it is not"). What this file knows about is bytes,
paths, addresses and sockets — the things the operating system will do for
anybody who asks, whether or not they should have.

## The refusal codes

Every refusal carries a stable `code` so the API can render a message and a
test can assert on the reason rather than on the wording:

    path_outside     the resolved path is not inside the vault
    path_absolute    an absolute path was sent where a vault-relative one goes
    path_parent      the path contains `..`
    path_symlink     a component of the path is a symlink leaving the vault
    path_nul         a NUL byte, which no filesystem call should ever see
    url_scheme       not http or https
    url_credentials  a `user:pass@` in the URL
    url_host         no host to resolve
    url_dns          the name does not resolve
    url_blocked      it resolves to an address this server will not connect to
    url_metadata     it resolves to a cloud instance metadata endpoint
    url_unpinned     a connection was attempted to a host nobody vetted
    quota            a per-session limit was reached
    rate_limited     too many requests from one source address

## What this module does not defend against

Stated plainly, because a guard whose limits are undocumented gets trusted past
them:

**Time of check to time of use.** `within` answers about the filesystem as it
was at the moment it was called. A caller that holds the returned path across a
slow operation, or that resolves a second path from it, is back outside the
guarantee. Open the file immediately, and never re-join anything onto the
result.

**The engine re-resolves.** `check_url` vets a URL and returns the addresses it
vetted, but `report-maker cite <report> <url>` does its own DNS lookup inside
`engine/snapshot.py`, which is not ours to edit. Between our lookup and the
engine's, a hostile name server can answer differently — the classic DNS
rebind. `safe_opener` closes that window for fetches this layer makes itself;
for the engine's own fetch it does not, and that is a `needs` item on the
engine rather than something this file can fix by trying harder.

**A single write can overshoot the disk quota.** The check is a floor, not a
ceiling: it refuses the *next* write once the vault is already at the limit, so
the overshoot is bounded by the size of one request body. That bound is the
API's to enforce, not this module's.

**Buckets and command history live in memory.** A restart resets both. That is
acceptable here because a restart also loses the sessions themselves — the
default `RM_WEB_ROOT` is a per-run temp directory — so there is nothing left to
protect the quota of. It would stop being acceptable the moment sessions
outlive the process, and that is the moment to reach for a store.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import html
import http.client
import ipaddress
import os
import re
import secrets
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Protocol
from urllib.parse import urlsplit

# ── refusals ─────────────────────────────────────────────────────────────────


class Refused(Exception):
    """Something a stranger asked for that this server will not do.

    Every guard in this file raises one of these rather than returning a
    boolean, because a boolean is a value a caller can forget to look at and an
    exception is not. The three fields are exactly the API's error envelope, so
    a handler catches `Refused` once at the top and never has to decide what a
    given failure means.
    """

    code = "refused"
    status = 403

    def __init__(
        self,
        message: str,
        *,
        detail: str = "",
        code: str | None = None,
        status: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.retry_after = retry_after
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status

    def payload(self) -> dict:
        """The response body, shaped as the spec's error envelope."""
        return {
            "error": {"code": self.code, "message": self.message, "detail": self.detail}
        }


class Forbidden(Refused):
    """A path or a URL this server refuses to touch. 403."""

    code = "forbidden"
    status = 403


class QuotaExceeded(Refused):
    """A per-session limit reached. 429, naming the limit and its value."""

    code = "quota"
    status = 429

    def __init__(
        self,
        message: str,
        *,
        limit: str,
        value: int,
        used: int | None = None,
        detail: str = "",
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message, detail=detail, retry_after=retry_after)
        self.limit = limit
        self.value = value
        self.used = used

    def payload(self) -> dict:
        body = super().payload()
        body["error"]["limit"] = self.limit
        body["error"]["value"] = self.value
        if self.used is not None:
            body["error"]["used"] = self.used
        return body


class RateLimited(Refused):
    """Too many requests from one source address. 429, with a Retry-After."""

    code = "rate_limited"
    status = 429


# ── path containment ─────────────────────────────────────────────────────────
#
# This is `app/src/main/tree.ts`'s `within`, carried across with its reasoning
# and then made stricter, because the threat is different. In the desktop app
# the caller is our own renderer and the guard catches a bug; here the caller is
# whoever sent the request and the guard is the only thing between them and the
# rest of the disk. So the app's two steps — resolve, then confirm the prefix —
# are kept, and three refusals are added in front of them:
#
#   - an absolute path is refused rather than quietly re-rooted,
#   - `..` is refused outright rather than normalised away,
#   - every component is checked for a symlink that leaves the vault.
#
# The last is not redundant with `resolve()`, which follows symlinks and would
# land outside and be caught by the prefix check anyway. It is here to say the
# true thing in the error — "a symlink in this path leaves the vault" rather
# than "this path is somewhere else" — and because two independent checks means
# a mistake in either one does not open the boundary on its own.

# Enough of the requested path to be recognisable in an error, with nothing in
# it that could break a log line or a terminal.
_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f]")


def _shown(text: str, limit: int = 120) -> str:
    """User input, made safe to put in an error message.

    Errors from this module are read by the person who sent the request, so the
    path they asked for belongs in them. Control characters do not, and neither
    does the whole of a path somebody made a megabyte long.
    """
    clean = _UNPRINTABLE.sub("�", text)
    return clean if len(clean) <= limit else clean[:limit] + "…"


def within(vault: Path, candidate: str) -> Path:
    """Resolve `candidate` against `vault`, or refuse.

    `candidate` is a vault-relative path exactly as it arrived in the request,
    already percent-decoded by the caller and never decoded again afterwards —
    a second decode is how `%252e%252e` becomes `..` one layer too late.

    The returned path is absolute and resolved, and it is the *only* path the
    caller may use. Joining anything onto it afterwards puts the caller back
    outside this guarantee.

    Messages never name the vault or any absolute path on this machine. The
    person who sent the request already knows what they asked for; where we
    keep it is not theirs to learn from an error.
    """
    text = os.fspath(candidate)

    if "\x00" in text:
        raise Forbidden(
            "refusing that path: it contains a NUL byte",
            code="path_nul",
            detail="a NUL byte in a path is never a filename, only an attempt "
            "to truncate one",
        )

    requested = _shown(text)
    relative = Path(text)

    if relative.is_absolute():
        raise Forbidden(
            f'refusing "{requested}": paths are relative to the report vault',
            code="path_absolute",
            detail="send a path relative to the vault, with no leading /",
        )

    # `~` is never expanded here, and must not be expanded by any caller. The
    # refusal is what keeps a stray `expanduser()` downstream from mattering.
    if relative.parts and relative.parts[0].startswith("~"):
        raise Forbidden(
            f'refusing "{requested}": paths are relative to the report vault',
            code="path_absolute",
            detail="a leading ~ is not expanded here",
        )

    if ".." in relative.parts:
        raise Forbidden(
            f'refusing "{requested}": a path may not contain ".."',
            code="path_parent",
            detail="every file this server will open is inside the vault, so "
            "nothing ever needs to walk upward",
        )

    # Both sides are resolved before they are compared. On macOS the session
    # root is usually under /var, which is itself a symlink to /private/var, so
    # a raw string comparison would refuse every legitimate path on the
    # developer's own machine.
    root = Path(vault).resolve()

    here = root
    for part in relative.parts:
        here = here / part
        if here.is_symlink() and not _inside(root, here.resolve()):
            raise Forbidden(
                f'refusing "{requested}": a symlink in that path leaves the vault',
                code="path_symlink",
                detail="a link is followed by whatever opens the file, so a "
                "link out of the vault is a path out of the vault",
            )

    target = (root / relative).resolve()
    if not _inside(root, target):
        raise Forbidden(
            f'refusing "{requested}": it is outside the report vault',
            code="path_outside",
        )

    return target


def _inside(root: Path, target: Path) -> bool:
    """Is `target` the root itself or something under it?

    Compared as paths rather than as strings. A string prefix test says yes to
    `/sessions/abc-evil` for a root of `/sessions/abc`, which is the oldest bug
    in this family.
    """
    return target == root or root in target.parents


def contains(vault: Path, candidate: str) -> bool:
    """`within` as a question. For a caller that wants to skip, not to fail."""
    try:
        within(vault, candidate)
    except Forbidden:
        return False
    return True


# ── the SSRF pre-flight ──────────────────────────────────────────────────────
#
# `report-maker cite <report> <url>` fetches a page and archives it. On a
# writer's laptop that is a feature; on a server it is a request to make an
# HTTP call from inside our network, chosen by a stranger, with the response
# handed back to them. That is the whole shape of server-side request forgery,
# and the thing it reaches for first is the cloud metadata endpoint, where a
# single unauthenticated GET returns credentials for the machine.
#
# So the guard is not a deny-list of hostnames — those are trivially bypassed
# by an A record — it is a check on every address the name actually resolves
# to, using `ipaddress`'s own predicates rather than ranges retyped by hand.
# A retyped range is a range with a typo in it.

# Named explicitly so the refusal can say *why*, which is the message that
# teaches. Every one of these also trips a predicate below — 169.254.169.254 is
# link-local, fd00:ec2::254 is in fc00::/7, 100.100.100.100 is in the carrier
# range, 192.0.0.192 is IETF-reserved — so the list adds no coverage at all.
# It adds the sentence a reader needs to see.
METADATA = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),  # AWS, GCP, Azure, DigitalOcean
        ipaddress.ip_address("fd00:ec2::254"),  # AWS, over IPv6
        ipaddress.ip_address("169.254.170.2"),  # AWS ECS task credentials
        ipaddress.ip_address("100.100.100.100"),  # Alibaba Cloud
        ipaddress.ip_address("192.0.0.192"),  # Oracle Cloud
    }
)

# Most damning first, because the first one that trips is the one named in the
# message. `is_private` is true for loopback and link-local too, so a plain
# loopback answer would otherwise be reported as "private" — true, and useless.
_ADDRESS_CLASSES = (
    ("loopback", "is_loopback"),
    ("link-local", "is_link_local"),
    ("unspecified", "is_unspecified"),
    ("multicast", "is_multicast"),
    ("private", "is_private"),
    ("reserved", "is_reserved"),
)

WEB_SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class ResolvedTarget:
    """A URL that passed the pre-flight, and the addresses it passed *as*.

    `addresses` is the point of this type. A caller that takes the URL and
    resolves the name again has thrown away the only thing the check produced:
    between the two lookups a hostile name server can return a different
    answer, and the second answer is the one that gets connected to. Hand this
    object to `safe_opener`, which connects to these addresses and no others.
    """

    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]

    @property
    def address(self) -> str:
        """The address a connection should be pinned to."""
        return self.addresses[0]


def _embedded(ip: ipaddress._BaseAddress) -> Iterator[ipaddress._BaseAddress]:
    """The address itself, plus any IPv4 address hiding inside it.

    `::ffff:127.0.0.1` is loopback to every network stack on earth and answers
    `False` to `IPv6Address.is_loopback`, because as an IPv6 address it is not
    in `::1/128`. The same is true of a 6to4 or Teredo address wrapping an RFC
    1918 target. Each wrapper is unwrapped and the address inside is judged on
    its own, or the predicates below are checking the envelope.
    """
    yield ip
    if ip.version != 6:
        return
    for wrapped in (ip.ipv4_mapped, ip.sixtofour):
        if wrapped is not None:
            yield wrapped
    if ip.teredo is not None:
        server, client = ip.teredo
        yield server
        yield client


# A host worth handing to `inet_aton` at all: nothing but the characters an
# IPv4 literal can be spelled with, in any of its bases. A real domain name
# reaches this and is rejected by the parser anyway, but the filter keeps the
# common case from paying for a doomed call.
_MAYBE_LITERAL = re.compile(r"^[0-9a-fA-FxX.]+$")


def _literal_forms(host: str) -> Iterator[ipaddress._BaseAddress]:
    """Every address a network stack might read this *hostname* as.

    `getaddrinfo` is not the only parser in the chain, and the parsers do not
    agree. On macOS `getaddrinfo("0177.0.0.1")` answers 177.0.0.1 — it drops
    the leading zero — while `inet_aton`, and glibc, and curl, read the same
    string as octal and answer 127.0.0.1. A guard that consulted only the first
    parser would approve a URL that the thing doing the fetching resolves to
    loopback, which is the entire attack in one line and needs no hostile name
    server to arrange.

    So the host is judged as *both*: whatever the resolver says (in `check_url`)
    and whatever the permissive BSD parse says (here). Anything that is
    non-routable under either reading is refused. The cost is that a hostname
    which is nothing but digits and dots — never a real domain — may be judged
    as the address it spells, which is the answer we want anyway.
    """
    if not _MAYBE_LITERAL.match(host):
        return
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        # Already written the one way every parser reads the same. The resolver
        # pass below will judge it and say "resolves to", which is the true
        # sentence; saying "is a spelling of 169.254.169.254" about the string
        # `169.254.169.254` would be an explanation that explains nothing.
        return
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return
    try:
        yield ipaddress.ip_address(packed)
    except ValueError:  # pragma: no cover - inet_aton always returns four bytes
        return


def _classify(ip: ipaddress._BaseAddress) -> str | None:
    """How this address is described in a refusal, or None if it is public.

    The phrase names the address that actually tripped a predicate, which for a
    wrapped address is not the one in the URL — "a loopback address (127.0.0.1,
    wrapped inside ::ffff:127.0.0.1)" is the sentence that explains itself.
    """
    for wrapped in _embedded(ip):
        name = _class_of(wrapped)
        if name is None:
            continue
        if wrapped is ip:
            return f"a {name} address"
        return f"a {name} address ({wrapped}, wrapped inside {ip})"
    return None


def _class_of(ip: ipaddress._BaseAddress) -> str | None:
    """The class of one address, unwrapped, or None if it is public."""
    for name, predicate in _ADDRESS_CLASSES:
        if getattr(ip, predicate):
            return name
    # The catch-all, and the reason this is not a hand-written list of ranges.
    # `is_private` does not cover every address that cannot be routed on the
    # public internet — 100.64.0.0/10, the carrier-grade NAT range a home
    # router sits behind, answers False to it on current Pythons — and each
    # such gap is a way back into somebody's own network. "Not globally
    # reachable" is the property actually wanted, and the stdlib maintains it
    # against the IANA registries so that this file does not have to.
    if not ip.is_global:
        return "non-routable"
    return None


def check_url(url: str, *, what: str = "fetch") -> ResolvedTarget:
    """Vet a user-supplied URL before anything connects to it.

    Refuses, in order: a scheme that is not http or https; credentials in the
    URL; a missing host; a name that does not resolve; a name that resolves to
    a cloud metadata endpoint; and a name *any* of whose addresses is loopback,
    link-local, private, multicast, reserved or unspecified.

    "Any" rather than "the first" is deliberate. A name with two A records —
    one public, one 127.0.0.1 — is not half safe. Whichever the resolver hands
    the connection is the one that matters, and we do not get to choose.

    Returns the vetted addresses so the caller can pin them. **A caller that
    resolves the hostname again has defeated this guard**: the check and the
    connection must share one lookup, or a name server that answers differently
    the second time — a DNS rebind — walks straight through. `safe_opener`
    exists so that pinning is the easy path for a fetch this server makes, and
    `ResolvedTarget.address` travels to the engine as `--pinned-address` for the
    fetches it makes on our behalf. Either way the rule is the same one: an
    address that came out of this function, never a name looked up again.
    """
    shown = _shown(url)
    split = urlsplit(url)
    scheme = split.scheme.lower()

    if scheme not in WEB_SCHEMES:
        raise Forbidden(
            f"refusing to {what} {shown}: only http and https are fetched",
            code="url_scheme",
            detail=f"{scheme or 'that scheme'} can read this server's own disk "
            "or its own services; http and https cannot",
        )

    if split.username or split.password:
        raise Forbidden(
            f"refusing to {what} {shown}: the URL carries credentials",
            code="url_credentials",
            detail="a user:pass@ in a URL sends a secret to whatever host "
            "follows the @, which is rarely the host a reader saw",
        )

    host = (split.hostname or "").strip()
    if not host:
        raise Forbidden(
            f"refusing to {what} {shown}: there is no host in that URL",
            code="url_host",
        )

    try:
        port = split.port or DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise Forbidden(
            f"refusing to {what} {shown}: that is not a port number",
            code="url_host",
        ) from exc

    # Before the resolver is consulted at all: if the host is *itself* a
    # spelling of an address, judge that spelling too. See `_literal_forms` —
    # two parsers in the same chain disagreeing is a bypass, not a curiosity.
    for literal_ip in _literal_forms(host):
        if literal_ip in METADATA:
            raise Forbidden(
                f"refusing to {what} {shown}: {host} is a spelling of "
                f"{literal_ip}, the cloud instance metadata endpoint",
                code="url_metadata",
                detail="that address answers unauthenticated requests with "
                "credentials for this machine, so fetching it on somebody "
                "else's behalf would hand them those credentials",
            )
        kind = _classify(literal_ip)
        if kind is not None:
            raise Forbidden(
                f"refusing to {what} {shown}: {host} is a spelling of "
                f"{literal_ip}, {kind}",
                code="url_blocked",
                detail="an address written in octal or hex is still that "
                "address, and the library doing the fetching may read it "
                "differently from the one doing the checking",
            )

    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise Forbidden(
            f"could not {what} {shown}: {host} does not resolve",
            code="url_dns",
            status=400,
            detail=str(exc),
        ) from exc

    addresses: list[str] = []
    for answer in answers:
        # An IPv6 answer may arrive scoped — `fe80::1%lo0` — which
        # `ip_address` will not parse. The scope is not part of the judgement.
        literal = answer[4][0].split("%")[0]
        try:
            ip = ipaddress.ip_address(literal)
        except ValueError as exc:  # pragma: no cover - a resolver malfunction
            raise Forbidden(
                f"refusing to {what} {shown}: {host} resolved to something "
                "that is not an address",
                code="url_blocked",
                detail=str(exc),
            ) from exc

        for wrapped in _embedded(ip):
            if wrapped in METADATA:
                raise Forbidden(
                    f"refusing to {what} {shown}: {host} resolves to {ip}, the "
                    "cloud instance metadata endpoint",
                    code="url_metadata",
                    detail="that address answers unauthenticated requests with "
                    "credentials for this machine, so fetching it on somebody "
                    "else's behalf would hand them those credentials",
                )

        kind = _classify(ip)
        if kind is not None:
            raise Forbidden(
                f"refusing to {what} {shown}: {host} resolves to {ip}, {kind}",
                code="url_blocked",
                detail="this server fetches from the public internet only — "
                "its own machine and its own network are not sources",
            )

        if literal not in addresses:
            addresses.append(literal)

    if not addresses:
        raise Forbidden(
            f"could not {what} {shown}: {host} resolves to nothing",
            code="url_dns",
            status=400,
        )

    return ResolvedTarget(
        url=url, scheme=scheme, host=host.lower(), port=port,
        addresses=tuple(addresses),
    )


def check_redirect(url: str) -> ResolvedTarget:
    """The same check, applied to a redirect target before it is followed.

    A public host that answers `302 Location: http://169.254.169.254/` is the
    same attack as naming that address directly, one hop later, and it does not
    even need a hostile name server. Every hop is vetted or none of it is.
    """
    return check_url(url, what="follow a redirect to")


def _pinned(pins: dict[tuple[str, int], str], host: str, port: int) -> str:
    """The vetted address for a host, or a refusal.

    Fail closed: a connection to a host nobody put in this map is a connection
    nobody checked, and the only safe answer is no.
    """
    address = pins.get((host.lower(), port))
    if address is None:
        raise Forbidden(
            f"refusing to connect to {_shown(host)}: it was never vetted",
            code="url_unpinned",
        )
    return address


def safe_opener(
    target: ResolvedTarget, *, timeout: float = 20.0
) -> urllib.request.OpenerDirector:
    """A urllib opener that can only reach addresses this module vetted.

    Three things make it different from `urllib.request.build_opener()`:

    **It connects to the pinned address**, not to the hostname. The TCP
    connection goes to the literal address `check_url` approved, while the Host
    header and the TLS SNI/certificate check keep using the real hostname — so
    the name still has to match the certificate, and a second DNS answer never
    gets a vote. This is the half a naive guard is missing.

    **It has no handler for anything but http and https.** `build_opener`
    installs `FileHandler`, `FTPHandler` and `DataHandler` by default, and some
    versions of urllib's own redirect handler will follow a hop to `ftp://`
    without complaint. Here a redirect to `file:///etc/passwd` reaches no
    handler that could open it, and `UnknownHandler` — the one default kept —
    turns that into a refusal instead of a `None`.

    **It has no `ProxyHandler`.** A proxy taken from the environment would
    resolve the hostname itself at the far end, which is pinning undone — the
    connection would go to the proxy and the proxy would go wherever the name
    said.

    Every redirect is re-vetted by `check_redirect` before it is followed, and
    the address it vetted is added to the pin map, so hop two is pinned exactly
    like hop one.
    """
    pins: dict[tuple[str, int], str] = {(target.host, target.port): target.address}

    class _Plain(http.client.HTTPConnection):
        def connect(self) -> None:
            if self._tunnel_host:
                raise Forbidden(
                    "refusing to connect through a proxy",
                    code="url_unpinned",
                    detail="a proxy resolves the hostname itself, which undoes "
                    "the pin",
                )
            self.sock = socket.create_connection(
                (_pinned(pins, self.host, self.port), self.port),
                self.timeout,
                self.source_address,
            )

    class _Secure(http.client.HTTPSConnection):
        def connect(self) -> None:
            if self._tunnel_host:
                raise Forbidden(
                    "refusing to connect through a proxy",
                    code="url_unpinned",
                    detail="a proxy resolves the hostname itself, which undoes "
                    "the pin",
                )
            raw = socket.create_connection(
                (_pinned(pins, self.host, self.port), self.port),
                self.timeout,
                self.source_address,
            )
            # server_hostname is the *name*, never the pinned address: the
            # certificate must still be issued for the host the user asked for.
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)

    class _PlainHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(_Plain, req)

    class _SecureHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_Secure, req, context=self._context)

    class _VettedRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            vetted = check_redirect(newurl)
            pins[(vetted.host, vetted.port)] = vetted.address
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    class _Timed(urllib.request.OpenerDirector):
        """An opener whose default timeout is a real number.

        `OpenerDirector.open` defaults to "no timeout", and a fetch with no
        timeout is a socket this server holds open for as long as a hostile
        host cares to keep it — which is the cheapest denial of service there
        is against a server that fetches on request.
        """

        def open(self, fullurl, data=None, timeout=None):
            return super().open(
                fullurl, data, timeout if timeout is not None else self.timeout
            )

    opener = _Timed()
    for handler in (
        _PlainHandler(),
        _SecureHandler(),
        _VettedRedirects(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.HTTPDefaultErrorHandler(),
        # Refuses every other scheme *loudly*. Without it, an opener with no
        # handler for `file:` does not raise — `OpenerDirector.open` runs out
        # of handlers and returns None, and the caller gets an AttributeError
        # somewhere else entirely instead of a refusal.
        urllib.request.UnknownHandler(),
    ):
        opener.add_handler(handler)
    opener.addheaders = [("User-Agent", USER_AGENT)]
    opener.timeout = timeout
    return opener


# The engine's own string, copied rather than imported: `engine/snapshot.py` is
# not ours to edit and importing across that line would make this module depend
# on the thing it is guarding. The two must match, because the whole value of
# `trace` is that the hop *we* walk is the hop the engine will walk, and a site
# that serves a different page to a different agent breaks that.
USER_AGENT = "report-maker/1.0 (+https://github.com/younissk/report-maker)"


def trace(
    target: ResolvedTarget,
    *,
    timeout: float = 15.0,
    what: str = "cite",
    opener: urllib.request.OpenerDirector | None = None,
) -> str:
    """Walk the redirect chain with every hop vetted, and say where it lands.

    Spec requirement 4's second sentence — "re-check after redirects" — is not
    something `check_url` can do on its own. `check_url` judges the URL that was
    typed; a host that passes it and then answers `302 Location:
    http://169.254.169.254/latest/meta-data/` has moved the fetch to the
    metadata endpoint without a hostile name server, without DNS, and without
    anything the first check could have seen. And `engine/snapshot.py` follows
    redirects checking only the *scheme*, so the hop lands.

    So the chain is walked here first, through `safe_opener`, which vets and
    pins every hop. What comes back is the URL the chain actually ends at, and
    it is that URL — already terminal, already vetted — that goes to the engine.

    **What this cannot do on its own is bind the engine's fetch to what was
    checked.** The engine opens the terminal URL a second time — its own
    lookup, its own connection — so a name server willing to answer twice
    differently, or a host willing to answer one request with a page and the
    next with a redirect, had one hop nobody watched. That is closed at the
    other end rather than here: `cite` and `verify` take `--pinned-address`, and
    the caller of this function passes the literal that the check *after* the
    trace resolved. Connect where it was checked, keep the hostname for `Host`,
    SNI and the certificate, and refuse a hop off that origin. So this walk is
    now the first half of a pair, not a lone mitigation — see `routes.cite`,
    which does the passing.

    Fails closed. A chain this server cannot walk is a chain nobody vetted, and
    handing it to the engine anyway would make the guard something an attacker
    can switch off by refusing our request.
    """
    director = opener if opener is not None else safe_opener(target, timeout=timeout)
    request = urllib.request.Request(
        target.url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    try:
        response = director.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        # An error status is the answer, not a failure to reach one: a 404 on a
        # page somebody cites is a finding, and the engine will record it. What
        # matters here is only where the chain stopped.
        #
        # A 3xx arriving *here* is different. It means urllib would not follow
        # the hop — a loop, too many of them, or a `Location:` on a scheme it
        # refuses — so the chain has an end this server never saw. Returning the
        # last URL we did see would hand the engine a starting point and let it
        # walk the rest unwatched, which is the guard doing the opposite of its
        # job.
        with contextlib.closing(error):
            if 300 <= error.code < 400:
                raise Forbidden(
                    f"refusing to {what} {_shown(target.url)}: its redirects "
                    "do not end anywhere this server could check",
                    code="url_redirect",
                    detail=f"{error.code} {error.reason} at "
                    f"{_shown(error.geturl() or target.url)}",
                ) from error
            return _final(error.geturl() or target.url, target, what)
    except Refused:
        # A hop this module judged. Its own sentence already names the address
        # and why, which is the message worth showing.
        raise
    except Exception as exc:  # noqa: BLE001 — urllib raises from several layers
        raise Forbidden(
            f"could not {what} {_shown(target.url)}: this server could not "
            "follow it far enough to check where it leads",
            code="url_unreachable",
            status=400,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    # The body is deliberately not read. This is a reconnaissance request: the
    # engine is about to fetch the page properly, and pulling megabytes twice
    # would make every citation cost double for nothing.
    with contextlib.closing(response):
        return _final(response.geturl() or target.url, target, what)


def _final(url: str, target: ResolvedTarget, what: str) -> str:
    """The URL a chain landed on, vetted once more before it is handed onward.

    Every hop *between* two of urllib's handlers went through
    `check_redirect`, and the one place that does not is the end: a `Location:`
    on a scheme urllib will not follow never reaches `redirect_request` at all,
    it surfaces as an `HTTPError` whose `geturl()` is `file:///etc/passwd`. A
    `trace` that returned that string would be handing its caller the exact
    thing it exists to refuse, on the strength of never having connected to it.

    So the answer is checked like any other URL before it becomes one. The
    caller is about to give it to something that fetches.
    """
    if url == target.url:
        return url
    check_url(url, what=f"{what} the page")
    return url


# ── quotas ───────────────────────────────────────────────────────────────────
#
# The limits are per session and they are small, because a session is a
# stranger with a text editor and a build command. They exist to bound what one
# session can spend, not to be generous: somebody who needs more than this
# should be running the engine on their own machine, where it is free.
#
# Counting reports is deliberately *not* done by walking the vault. What a
# report is — a dated folder under reports/ with a main.typ in it — is the
# engine's definition, and re-deciding it here would be the second answer to a
# question `report-maker list --json` already answers. So the count arrives
# from the caller, which got it from the engine.


@dataclass(frozen=True)
class Quota:
    """The ceilings, per session. The numbers are the spec's."""

    disk_bytes: int = 50 * 1024 * 1024
    wall_seconds: int = 60
    commands_per_hour: int = 200
    reports: int = 20


@dataclass
class Usage:
    """What one session has spent so far.

    `commands` is a list of timestamps rather than a counter because the limit
    is a rolling hour: a counter would have to be reset on some boundary, and a
    session that spent its whole allowance at 10:59 would get another whole
    allowance at 11:00.

    In memory only. The session record on disk carries the *limits* (it is the
    `quota` field of `session.json`); the history is worth nothing after a
    restart that has already dropped the vault it belonged to.
    """

    commands: list[float] = field(default_factory=list)
    reports: int = 0
    disk_bytes: int = 0


class Session(Protocol):
    """What `enforce` needs a session to be.

    Structural, not inherited: the session record belongs to another module, and
    this file has no business dictating its shape beyond the three fields it
    actually reads.
    """

    vault: Path
    quota: Quota
    usage: Usage


WINDOW_SECONDS = 3600


def dir_size(path: Path, *, stop_at: int | None = None) -> int:
    """Apparent bytes under `path`, symlinks not followed.

    Apparent size rather than blocks: it is the number a person recognises, and
    it is what the folder would weigh if it were zipped and handed over, which
    is the operation this whole vault design is built around.

    Symlinks are counted as themselves and never traversed. Following them
    would let a session inflate — or, with a loop, hang — its own disk reading
    with a file it does not own; `within` already refuses to write through one.

    `stop_at` returns early once the total has passed a threshold, so the check
    before a write does not walk a huge tree to learn something it knew a
    thousand files ago.
    """
    total = 0
    stack = [Path(path)]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            # A directory that vanished or cannot be read is not a reason to
            # refuse the write; it is zero bytes we cannot see.
            continue
        for entry in entries:
            try:
                total += entry.stat(follow_symlinks=False).st_size
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
            except OSError:
                continue
            if stop_at is not None and total >= stop_at:
                return total
    return total


def enforce(
    session: Session,
    kind: str,
    *,
    count: int | None = None,
    now: float | None = None,
) -> None:
    """Refuse the next unit of work if this session has spent its allowance.

    `kind` is one of:

        disk      before writing a file. Measures the vault.
        command   before spawning the engine. **Records the command too** —
                  checking and consuming are one operation on purpose, because
                  a gap between them is a gap two threads can both walk through.
        report    before `report-maker new`. `count` is the current number of
                  reports, from `list --json`; without it the session's own
                  tally is used.

    There is no `kind` for the wall clock. Sixty seconds is not something to
    check before the fact — it is `subprocess.run(..., timeout=quota.wall_seconds)`
    at the moment of the spawn, and the runner owns that.
    """
    quota = session.quota
    usage = session.usage
    moment = time.monotonic() if now is None else now

    if kind == "disk":
        limit = quota.disk_bytes
        used = dir_size(session.vault, stop_at=limit)
        usage.disk_bytes = used
        if used >= limit:
            raise QuotaExceeded(
                f"this session has used its {limit // (1024 * 1024)} MB of disk",
                limit="disk_bytes",
                value=limit,
                used=used,
                detail="delete a report, or run the engine locally where "
                "nothing is metered",
            )
        return

    if kind == "command":
        limit = quota.commands_per_hour
        cutoff = moment - WINDOW_SECONDS
        usage.commands = [stamp for stamp in usage.commands if stamp > cutoff]
        if len(usage.commands) >= limit:
            wait = int(usage.commands[0] + WINDOW_SECONDS - moment) + 1
            raise QuotaExceeded(
                f"this session has run its {limit} engine commands for the hour",
                limit="commands_per_hour",
                value=limit,
                used=len(usage.commands),
                retry_after=max(wait, 1),
                detail="the allowance is a rolling hour, so the oldest command "
                "falls out of it shortly",
            )
        usage.commands.append(moment)
        return

    if kind == "report":
        limit = quota.reports
        held = usage.reports if count is None else count
        if held >= limit:
            raise QuotaExceeded(
                f"this session already holds its {limit} reports",
                limit="reports",
                value=limit,
                used=held,
                detail="delete a report, or connect a GitHub repository, where "
                "the repo is the store and this limit does not apply",
            )
        return

    raise ValueError(f"unknown quota kind: {kind!r}")


# ── rate limiting ────────────────────────────────────────────────────────────


@dataclass
class _Bucket:
    tokens: float
    stamp: float


class RateLimiter:
    """A token bucket per source address, in memory.

    Two buckets, because the two things being limited are not alike. Sixty
    requests a minute is what an editor typing into an autosave does; five
    session creations an hour is what a person does never, and a script does
    constantly — each session creation costs a directory, a starter and a
    subprocess.

    A token bucket rather than a fixed window: it refills continuously, so a
    client that goes quiet for ten seconds gets ten requests back rather than
    waiting for a boundary, and a client that saves its whole minute for one
    burst still cannot exceed the burst size.

    **In memory, with no external store.** A restart resets every bucket, and
    that is accepted: the sessions the buckets were protecting live in a
    per-run temp directory and do not survive the restart either. Multiple
    processes would each keep their own buckets and the effective limit would
    multiply, so this server runs as one process — which
    `ThreadingHTTPServer` already assumes.

    Thread-safe by one lock over both maps. The critical section is a
    subtraction; contention is not the problem here, a wrong answer is.
    """

    #: Below this many buckets nothing is swept; above it, idle ones go.
    MAX_KEYS = 10_000

    def __init__(
        self,
        *,
        requests_per_minute: int = 60,
        sessions_per_hour: int = 5,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int | None = None,
    ) -> None:
        self.limits = {
            # (capacity, tokens per second)
            "request": (float(requests_per_minute), requests_per_minute / 60.0),
            "session": (float(sessions_per_hour), sessions_per_hour / 3600.0),
        }
        self._clock = clock
        self._max_keys = self.MAX_KEYS if max_keys is None else max_keys
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = Lock()

    def check(self, ip: str, kind: str = "request") -> None:
        """Spend one token for this address, or raise `RateLimited`."""
        wait = self._take(ip, kind)
        if wait <= 0:
            return
        seconds = max(int(wait) + 1, 1)
        noun = "requests" if kind == "request" else "new sessions"
        raise RateLimited(
            f"too many {noun} from this address — try again in {seconds}s",
            retry_after=seconds,
            detail="the limit is per source address and refills continuously",
        )

    def allow(self, ip: str, kind: str = "request") -> bool:
        """`check` as a question."""
        return self._take(ip, kind) <= 0

    def _take(self, ip: str, kind: str) -> float:
        """Spend a token. Returns 0 when spent, or the seconds until one exists.

        A refused request takes nothing. Charging for a refusal turns a client
        that retries into a client that can never recover, which is how a rate
        limiter becomes a denial of service against the honest.
        """
        capacity, refill = self.limits[kind]
        key = (kind, self.key(ip))
        with self._lock:
            now = self._clock()
            bucket = self._buckets.get(key)
            if bucket is None:
                self._sweep(now)
                bucket = self._buckets[key] = _Bucket(tokens=capacity, stamp=now)
            else:
                bucket.tokens = min(
                    capacity, bucket.tokens + (now - bucket.stamp) * refill
                )
                bucket.stamp = now
            if bucket.tokens < 1.0:
                return (1.0 - bucket.tokens) / refill
            bucket.tokens -= 1.0
            return 0.0

    def _sweep(self, now: float) -> None:
        """Drop buckets that owe nothing, once the map gets large.

        Without this, a flood from forged or rotating source addresses fills
        memory with buckets — the rate limiter becoming the resource being
        exhausted. A bucket that has refilled to capacity holds no state worth
        keeping: recreating it gives exactly the same answer.
        """
        if len(self._buckets) <= self._max_keys:
            return
        for key, bucket in list(self._buckets.items()):
            capacity, refill = self.limits[key[0]]
            if bucket.tokens + (now - bucket.stamp) * refill >= capacity:
                del self._buckets[key]
        if len(self._buckets) > self._max_keys:
            oldest = sorted(self._buckets.items(), key=lambda kv: kv[1].stamp)
            for key, _ in oldest[: len(self._buckets) - self._max_keys]:
                del self._buckets[key]

    @staticmethod
    def key(ip: str) -> str:
        """The bucket an address belongs to.

        IPv6 is grouped by /64. A single host is routinely handed that much
        space, so limiting by the full address limits nothing at all — the same
        client simply uses its next address.
        """
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if parsed.version == 6:
            return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
        return str(parsed)


# ── escaping ─────────────────────────────────────────────────────────────────


def esc(text: object) -> str:
    """User text, safe to place in HTML.

    Report titles, source titles, group names and commit messages are all typed
    by somebody and all end up on a page. `html.escape` with `quote=True`
    covers text nodes and quoted attribute values — both quote characters, so
    `title='…'` is as safe as `title="…"`.

    It does **not** make text safe inside a `<script>`, inside a `<style>`, in
    an unquoted attribute, or in a `href`/`src` where the value could begin
    `javascript:`. Those are different escapes, and a helper that pretended
    otherwise would be worse than none.
    """
    return html.escape("" if text is None else str(text), quote=True)


_INLINE = {
    "script": re.compile(
        r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\s*>", re.I | re.S
    ),
    "style": re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.I | re.S),
}


def inline_hashes(document: str, tag: str = "script") -> tuple[str, ...]:
    """`'sha256-…'` sources for the inline blocks in a document.

    This exists for one page: `GET /s/<token>`, which serves the self-contained
    bundle `report-maker html` writes. That bundle carries its own inline
    `<script>` and `<style>` — it has to, being one file — and it has no hook
    for a nonce, and `engine/` is not ours to edit. Hashing what is actually
    there keeps the share page's CSP as strict as the app's without either
    weakening it to `'unsafe-inline'` or shipping the page with its tabs and
    its evidence popovers dead.

    Hash what will be served, at the moment of serving. A hash computed from a
    different copy of the file is a page that silently stops working.
    """
    return tuple(
        "'sha256-" + base64.b64encode(
            hashlib.sha256(block.encode("utf-8")).digest()
        ).decode("ascii") + "'"
        for block in _INLINE[tag].findall(document)
    )


# ── response headers ─────────────────────────────────────────────────────────


def nonce() -> str:
    """A fresh CSP nonce. One per response, never reused, never guessable."""
    return secrets.token_urlsafe(18)


def _csp(directives: dict[str, str]) -> str:
    """Join directives, allowing the valueless ones (upgrade-insecure-requests)."""
    return "; ".join(
        f"{name} {value}".strip() for name, value in directives.items()
    )


BASE_HEADERS = {
    # The server declares the type; the browser does not get to guess. Without
    # this, a stranger's uploaded file served as text/plain can be sniffed into
    # script by a browser trying to be helpful.
    "X-Content-Type-Options": "nosniff",
    # A vault path or a share token must never travel in a Referer to whatever
    # a report links to.
    "Referrer-Policy": "no-referrer",
    # frame-ancestors is the modern rule; this is the same statement for
    # anything that still only understands the old one.
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    # Nothing here needs a camera, a microphone or a location.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


def security_headers(
    nonce: str,
    *,
    tls: bool = False,
    dynamic_imports: bool = False,
) -> dict[str, str]:
    """The headers every served page carries.

    The policy starts at `default-src 'none'` and adds back only what the app
    actually loads, all of it same-origin. **No external host appears anywhere
    in it** — not a CDN, not a font host, not an analytics domain — so a
    successful injection has nowhere to send what it stole.

    Script is by nonce alone, and deliberately not `'self'`. `'self'` would be
    the easy answer and it is the wrong one *on this server specifically*: this
    server's whole job is serving files a stranger wrote, so a same-origin URL
    is not a trusted origin here. The cost is that every `<script>` tag —
    including the bundle's — must carry `nonce="…"`, and that a code-split
    build's dynamic chunks will not load, since a chunk fetched by the loader
    carries no nonce. Ship one chunk, or pass `dynamic_imports=True` to add
    `'strict-dynamic'` and accept that the bundle may then pull in whatever it
    likes.

    `style-src` is `'self' 'unsafe-inline'`, and that is a considered
    concession rather than an oversight. The frontend the spec asks for —
    CodeMirror 6 and Radix — cannot run without inline style: CodeMirror
    positions its cursor and its selection layer with `style` attributes and
    mounts its theme as an injected `<style>`, Radix positions every overlay the
    same way, and the page viewer sizes a page image in real pixels. Under a
    nonce-only `style-src` all of that is blocked and the editor renders as an
    empty box — observed, not predicted.

    What this costs is bounded, and it is not the boundary that matters here.
    `script-src` stays nonce-only, so an injection still cannot execute; this
    server has no login form, no password field and no payment field to be
    restyled into; and every string the app renders goes through React as a text
    node. Inline *style* is the weakest of the three inline surfaces, and it is
    the only one being reopened.
    """
    script = f"'nonce-{nonce}'"
    if dynamic_imports:
        script += " 'strict-dynamic'"

    directives = {
        "default-src": "'none'",
        "script-src": script,
        # See the docstring: CodeMirror and Radix both require it, and
        # `script-src` above is where the containment actually lives.
        "style-src": "'self' 'unsafe-inline'",
        # blob: is how the PDF reaches the viewer; data: is how a small inline
        # icon does. Neither can reach off this origin.
        "img-src": "'self' blob: data:",
        "font-src": "'self'",
        "connect-src": "'self'",
        "media-src": "'self'",
        "object-src": "'none'",
        "frame-src": "'none'",
        "frame-ancestors": "'none'",
        "base-uri": "'none'",
        "form-action": "'self'",
    }
    if tls:
        directives["upgrade-insecure-requests"] = ""

    headers = dict(BASE_HEADERS)
    headers["Content-Security-Policy"] = _csp(directives)
    if tls:
        headers["Strict-Transport-Security"] = "max-age=31536000"
    return headers


def share_headers(
    *,
    script_hashes: tuple[str, ...] = (),
    style_hashes: tuple[str, ...] = (),
    tls: bool = False,
) -> dict[str, str]:
    """Headers for `GET /s/<token>`, the public share page.

    A different policy from the app's, because it is a different page: it has
    no session, no API to call, and it is one self-contained file the engine
    wrote. So `connect-src` is `'none'` — nothing on that page has any business
    talking to this server — and its inline blocks are allowed by hash rather
    than by nonce, since the engine emits them and has no nonce to emit.

    With no hashes passed, script is refused outright. That is the honest
    default: the bundle's script drives its tabs and its citation popovers, so
    the page degrades rather than breaking, and a caller that wants the
    interactive version has to say which bytes it is trusting.
    """
    directives = {
        "default-src": "'none'",
        "script-src": " ".join(script_hashes) if script_hashes else "'none'",
        "style-src": " ".join(style_hashes) if style_hashes else "'none'",
        "img-src": "'self' data:",
        "font-src": "'self' data:",
        "connect-src": "'none'",
        "object-src": "'none'",
        "frame-src": "'none'",
        "frame-ancestors": "'none'",
        "base-uri": "'none'",
        "form-action": "'none'",
    }
    headers = dict(BASE_HEADERS)
    headers["Content-Security-Policy"] = _csp(directives)
    if tls:
        headers["Strict-Transport-Security"] = "max-age=31536000"
    return headers
