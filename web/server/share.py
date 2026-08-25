"""A public link to one report, with its evidence attached.

This is the reason to put the tool on the web at all. A PDF says what we
concluded; `report-maker html` writes the thing nobody else can produce — the
built pages on one tab and, on the other, one card per source, so every `@key`
in the prose is one keypress from the archived copy of the page as it was on the
date it was cited, sha256 and all. Sending someone that file is sending them the
report *and* the standing to check it.

`publish` runs `all --html` through the bridge, copies the single self-contained
bundle to `shares/<token>.html`, and hands back the token. `GET /s/<token>`
serves it with no session and no auth. That route is the only public surface on
this server, so everything below is about keeping it narrow.

## A share is immutable

Re-publishing mints a **new** token. It never rewrites the file behind an
existing one. A link a person was sent must not change under them: the whole
claim of the bundle is "this is what the report said and this is what the
sources said on that date", and a URL whose contents can be swapped afterwards
makes that claim worth nothing. It is the same principle as the snapshot
archive, which rotates `<key>.<date>.html` aside rather than overwriting — an
archive you are willing to overwrite is not an archive, and neither is a share.

The cost is old shares accumulating, which is a sweeper's problem and a cheap
one. The alternative is a broken promise, which is not.

## The share knows nothing about the session

No cookie is set on the share route, no session id appears in the URL or the
file, and `Share.path` — which exists because a caller has to copy a file — is
**server-side only**. Serialising it into a response would publish the layout of
`RM_WEB_ROOT` and, with it, the shape of every other session's storage. The
route returns `{url, token}` and nothing else.

`Referrer-Policy: no-referrer` is not decoration on that. The bundle is full of
links to the cited sources, and a reader clicking one would otherwise hand the
share token — the only secret protecting the link — to whichever site was
audited. That is the exact party who should not have it.

## "Self-contained" is verified, not assumed

`engine/html.py` inlines the page images as `data:` URIs and writes the
stylesheet and the script into the document, precisely so the bundle works from
a USB stick and from a machine with no network. This module does not take that
on trust. Before a byte is published, `references` parses the file and refuses
it if anything loads from a host: a `src`, a `<link>`, a `url()` in the CSS, an
`@import`, an `<iframe>`, a `<base>`.

The reason is privacy, not correctness. The user is about to send this link to a
client. A "self-contained" file carrying one remote image is a beacon that tells
a third party the name of everyone who opened the report and when — and it would
do it silently, because the page would look exactly right. A bundle that phones
home is a leak in the deliverable, so it is refused at publish time rather than
patched at serve time.

The parse catches passive loads. Active ones — a `fetch()` written into the
script — are caught by the Content-Security-Policy the share is served with,
which is `default-src 'none'` with an explicit sha256 for each inline script and
style. Two mechanisms, because either alone has a shape the other does not:
a parser cannot read intent out of JavaScript, and a CSP cannot stop a browser
that ignores it.

## The token is validated before the filesystem is

`get` checks the token against its exact charset **first**, then builds a path,
then confirms that path is inside `shares/`. Order matters: `../../etc/passwd`
must be refused as a malformed token, not resolved and then judged. A check that
happens after `Path(shares_dir) / token` is a check that has already lost.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

# `secrets.token_urlsafe(24)` is 32 characters of base64url — 192 bits, which is
# the point: the token *is* the access control on a public route, so guessing it
# has to be off the table rather than merely unlikely.
TOKEN_BYTES = 24
TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

# A report id is a path of folder names — `clients/acme/2026-01-14-pricing`. It
# reaches the CLI as a positional argument, so a leading `-` would be parsed as
# an option; `..` and an absolute path would be a build aimed somewhere else.
REPORT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")

# The engine announces each bundle it writes as `  → out/<id>.html (NNN KB)`.
# Read rather than reconstructed: `vault.out` is configurable in
# `report-maker.toml`, and a web layer that assumed `out/` would be quietly
# wrong in any vault that renamed it — and quietly wrong is the worst kind.
WROTE = re.compile(r"→\s+(\S+\.html)\s+\(")

# How big a bundle this server is willing to hold open in memory to check it.
# The page images are inlined, so a long report is genuinely a few megabytes; a
# hundred is a report that should be a PDF attachment.
MAX_BUNDLE = 64 * 1024 * 1024


class ShareError(RuntimeError):
    """Something the user should be told before their client sees a link."""


@dataclass(frozen=True)
class Share:
    """One published bundle.

    `path` is where the file went, and it is **server-side only** — see the
    module docstring. `url` is what the browser is given.
    """

    token: str
    report: str
    created: str
    path: Path

    @property
    def url(self) -> str:
        return f"/s/{self.token}"

    def to_json(self) -> dict:
        """What may cross to the browser: the link and what it points at.

        Built as a new dict rather than `asdict()` minus a key, for the same
        reason `github.connection` is: a denylist is a bug waiting for a fifth
        field.
        """
        return {"url": self.url, "token": self.token, "report": self.report, "created": self.created}


# ── what may be published ────────────────────────────────────────────────────


def _report_id(report_id: str) -> str:
    """A report id, or a refusal — and never a repair.

    Only a trailing slash is forgiven, because a user copying a folder path
    brings one along. A *leading* slash is not: stripping it would turn
    `/etc/passwd` into `etc/passwd` and hand the engine a target the caller
    never asked for. Normalising a malformed input into a well-formed one is how
    a validator becomes an attacker's assistant.
    """
    if not isinstance(report_id, str):
        report_id = ""
    candidate = report_id.strip().rstrip("/")
    if not REPORT_ID.match(candidate):
        raise ShareError(
            f"bad report: {report_id!r} — a report id is a path of folder names, "
            "like clients/acme/2026-01-14-pricing"
        )
    return candidate


def _vault(session: Any) -> Path:
    """The session's vault. Every path this module touches is proved to be
    inside it, so getting this wrong is getting everything wrong."""
    root = session.get("vault") if isinstance(session, dict) else getattr(session, "vault", None)
    if not root:
        raise ShareError("this session has no vault")
    return Path(root)


def _within(root: Path, path: Path) -> Path:
    """Refuse any path outside `root`.

    `app/src/main/tree.ts::within`, said in Python. Both sides are resolved
    first, so a `..` segment or a symlink pointing out of the tree is caught
    rather than followed — which is the whole of the difference between a
    containment check and a string comparison that looks like one.
    """
    root, target = root.resolve(), path.resolve()
    if target != root and root not in target.parents:
        raise ShareError(f"refusing to touch {target}: outside {root}")
    return target


def _bridge_run(session: Any, args: list[str]) -> Any:
    """The engine, through the bridge the rest of `web/` shares.

    Imported inside the call so this module can be read and tested without the
    bridge, and so a missing one reports itself in a sentence rather than as an
    ImportError at server start.
    """
    from . import bridge  # noqa: PLC0415 — deliberate: see above

    # No timeout is named here. The per-command wall clock is a session quota
    # and the bridge owns it; a second ceiling in this module would be a second
    # answer to "how long may a build take".
    return bridge.run(session, args)


def _result(run: Any) -> tuple[int, str, str]:
    if isinstance(run, dict):
        return int(run.get("code", 1)), str(run.get("stdout", "")), str(run.get("stderr", ""))
    return int(getattr(run, "code", 1)), str(getattr(run, "stdout", "")), str(getattr(run, "stderr", ""))


def _bundle(vault: Path, report_id: str, stdout: str) -> Path:
    """Where the engine says it put the bundle, proved to be inside the vault."""
    slug = report_id.rsplit("/", 1)[-1]
    written = [vault / raw for raw in WROTE.findall(stdout)]
    exact = [p for p in written if p.stem == slug]
    for candidate in (*exact, *written):
        try:
            found = _within(vault, candidate)
        except ShareError:
            continue
        if found.is_file():
            return found
    raise ShareError(
        f"the build produced no HTML bundle for {report_id}.\n"
        "  `report-maker html` writes it; check the build output for why it did not."
    )


# ── publishing ───────────────────────────────────────────────────────────────


def publish(
    session: Any,
    report_id: str,
    shares_dir: Path | str,
    *,
    run: Callable[..., Any] | None = None,
    allow_findings: bool = False,
) -> Share:
    """Build one report with its evidence bundle, and mint a link to it.

    The build is `all <id> --html`, and **not** `--warn-only`. That is a
    decision, not an oversight. Publishing is the outward-facing act — the point
    at which a report stops being a draft on somebody's screen and becomes a
    thing a client reads — and the citation rule is the entire product claim
    behind it. `CLAUDE.md` names reaching for `--warn-only` outside a genuine
    work-in-progress as the way a true statement about a vault becomes a false
    one, and a share is the last place to make that trade.

    Nothing is lost by it. A report that declares `status: "draft"` already
    reports its errors as warnings and exits 0, so an unfinished report shares
    fine and says on its own face that it is unfinished. What is refused is a
    report claiming to be finished while `check` disagrees, and the fix for that
    is the report.

    `allow_findings` exists for a caller holding an explicit, informed "share it
    anyway" from the user. It is never a default and never a setting.
    """
    report_id = _report_id(report_id)
    vault = _vault(session)
    shares = Path(shares_dir)
    call = run or _bridge_run

    code, stdout, stderr = _result(call(session, ["all", report_id, "--html"]))
    if code != 0 and not allow_findings:
        detail = (stderr or stdout).strip()
        raise ShareError(
            f"{report_id} does not pass `check`, so it is not ready to send.\n"
            "  Fix the findings, or mark the report `status: \"draft\"` if it is "
            "still being written.\n\n" + detail
        )

    bundle = _bundle(vault, report_id, stdout)
    raw = bundle.read_bytes()
    if len(raw) > MAX_BUNDLE:
        raise ShareError(
            f"the bundle for {report_id} is {len(raw) // (1024 * 1024)} MB, "
            "which is more than this server will publish"
        )

    found = references(raw)
    if found:
        raise ShareError(
            "refusing to publish: this bundle is not self-contained.\n  "
            + "\n  ".join(found[:8])
            + "\n\n  A report bundle loads nothing from the network by design — a "
            "single remote image would tell a third party the name of everyone "
            "who opened your link, and it would do it silently."
        )

    token = secrets.token_urlsafe(TOKEN_BYTES)
    created = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    shares.mkdir(parents=True, exist_ok=True)
    target = _within(shares, shares / f"{token}.html")

    # Written beside the destination and renamed into place, so a reader can
    # never fetch a half-copied bundle: on one filesystem, `os.replace` is
    # atomic, and there is no window in which the token resolves to a truncated
    # file.
    handle, tmp = tempfile.mkstemp(prefix=".share-", suffix=".html", dir=str(shares))
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(raw)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise

    # A sidecar, so a listing or a sweeper can answer "what is this and when"
    # without opening a multi-megabyte document. It carries the report id and
    # the date — no session id, no vault path, nothing that would tie the link
    # back to whoever made it.
    _within(shares, shares / f"{token}.json").write_text(
        json.dumps({"report": report_id, "created": created}, indent=2) + "\n",
        encoding="utf-8",
    )

    return Share(token=token, report=report_id, created=created, path=target)


# ── serving ──────────────────────────────────────────────────────────────────


def get(shares_dir: Path | str, token: str) -> Path | None:
    """The bundle behind a token, or None.

    The token is checked against its charset **before** it is joined to a path.
    That ordering is the guard: `../../etc/passwd` has to be refused as a
    malformed token, not resolved and then judged, because by the time a
    resolution has happened the interesting question — did this string ever get
    to name a file — has already been answered wrongly.

    Containment is then checked anyway, and a symlink is refused. Belt and
    braces, on the one route that answers to the whole internet.
    """
    if not isinstance(token, str) or not TOKEN.match(token):
        return None

    shares = Path(shares_dir)
    candidate = shares / f"{token}.html"
    try:
        target = _within(shares, candidate)
    except ShareError:
        return None
    # `is_file` follows a symlink, so it is asked *after* the link itself is
    # ruled out: a symlink inside shares/ pointing at a session vault would
    # otherwise pass containment and serve somebody else's work.
    if candidate.is_symlink() or not target.is_file():
        return None
    return target


def meta(shares_dir: Path | str, token: str) -> dict | None:
    """The sidecar for a token — report id and date, or None."""
    if not isinstance(token, str) or not TOKEN.match(token):
        return None
    shares = Path(shares_dir)
    try:
        side = _within(shares, shares / f"{token}.json")
    except ShareError:
        return None
    try:
        payload = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _filename(report_id: str) -> str:
    """A download name that is the report's own, and cannot be a path.

    The slug is already a folder name, but it arrives here from a sidecar file
    and a header value is not the place to find that out — so it is filtered to
    a known charset rather than trusted, and never carries the group folders,
    which would put the vault's filing structure into a public header.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", report_id.rsplit("/", 1)[-1]).strip("-.")
    return f"{slug or 'report'}.html"


def headers(path: Path, *, report_id: str | None = None, max_age: int = 3600) -> dict[str, str]:
    """Everything the public route must send, and why each one is there.

    `Content-Security-Policy` is `default-src 'none'` with an explicit sha256
    for each inline script and style the document actually contains. Hashes
    rather than a nonce, because a nonce means rewriting the file on the way out
    and the file is the artefact — its sha is the thing a reader may one day
    want to compare. Hashes rather than `'unsafe-inline'`, because a bundle is
    built out of a stranger's prose and a fetched page's text, and "the engine
    escapes it" is a claim to defend in depth rather than to rest on.

    `Referrer-Policy: no-referrer` is the load-bearing one. The bundle links to
    every source it cites; without this header a reader clicking one hands the
    share token — the only secret protecting a public URL — to the site being
    audited.

    The caller must add no `Set-Cookie` to this response. A share carries no
    session, which is what makes it safe to forward.
    """
    raw = path.read_bytes()
    scripts, styles = inline_hashes(raw)
    policy = "; ".join(
        [
            "default-src 'none'",
            # The pages are inlined as data: URIs and nothing else is.
            "img-src data:",
            "font-src data:",
            "style-src " + (" ".join(f"'{h}'" for h in sorted(styles)) or "'none'"),
            "script-src " + (" ".join(f"'{h}'" for h in sorted(scripts)) or "'none'"),
            # Nothing in the bundle submits, frames, or reinterprets a relative
            # URL, so all three are refused outright.
            "form-action 'none'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "connect-src 'none'",
        ]
    )
    return {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": str(len(raw)),
        "Content-Disposition": f'inline; filename="{_filename(report_id or "report")}"',
        "Content-Security-Policy": policy,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        # A share never changes, so it is cacheable — but not for a year. A user
        # who takes a link down should not be arguing with a CDN about it.
        "Cache-Control": f"public, max-age={max_age}",
    }


# ── is it really self-contained? ─────────────────────────────────────────────
#
# Parsed with `html.parser`, not matched with a regular expression. The document
# quotes archived web pages, so the text of a source may legitimately contain
# the characters `src="http://…"`, escaped — and a regex over the whole file
# would refuse a perfectly good bundle because of a sentence in a quotation.
# `HTMLParser` knows the difference between markup and text, and knows that a
# `<script>` body is neither, which is exactly the distinction being asked about.

# Attributes whose value the browser *fetches* while rendering. `href` is absent
# on purpose: on an `<a>` it is a link the reader chooses to follow, and every
# citation in the bundle is one. `<link href>` is handled separately, because
# there it is a subresource.
LOADING = frozenset({"src", "srcset", "poster", "data", "manifest", "background", "ping"})

# Tags that pull in a document of their own no matter how they are spelled.
EMBEDDING = frozenset({"iframe", "frame", "object", "embed", "portal"})

CSS_URL = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.IGNORECASE)
CSS_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?['\"]([^'\"]+)['\"]", re.IGNORECASE)


def _is_local(value: str) -> bool:
    """Whether a reference resolves without touching the network.

    A `data:` URI carries its own bytes, and a bare fragment stays on the page.
    Everything else — an absolute URL, a protocol-relative `//host/…`, and a
    plain relative path too — is refused. The relative path is not a privacy
    problem, but it is a broken bundle: the file is meant to work from an email
    attachment, and a missing sibling is a hole in the evidence.
    """
    text = value.strip()
    if not text or text.startswith("#"):
        return True
    lowered = text.lower()
    return lowered.startswith("data:") or lowered in ("about:blank",)


class _References(HTMLParser):
    """Every place this document would reach out from."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []
        self._css: list[str] = []
        self._in_style = False

    # ── markup ──
    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "style":
            self._in_style = True
        if tag == "base":
            # A `<base>` changes what every relative URL in the document means.
            # There is no benign reason for one here.
            self.found.append("<base> rewrites every relative URL in the document")
        for name, value in attrs:
            name = (name or "").lower()
            if value is None:
                continue
            if name == "style":
                self._css.append(value)
                continue
            fetched = name in LOADING or (name == "href" and tag in ("link", *EMBEDDING))
            if tag in EMBEDDING and name in ("src", "data", "href"):
                fetched = True
            if not fetched:
                continue
            for part in (value.split(",") if name == "srcset" else [value]):
                ref = part.strip().split(" ")[0] if name == "srcset" else part
                if not _is_local(ref):
                    self.found.append(f"<{tag} {name}=…> loads {_shorten(ref)}")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() == "style":
            self._in_style = False

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style":
            self._in_style = False

    # ── stylesheet text ──
    def handle_data(self, data: str) -> None:
        if self._in_style:
            self._css.append(data)

    def finish(self) -> list[str]:
        for block in self._css:
            for _, ref in CSS_URL.findall(block):
                if not _is_local(ref):
                    self.found.append(f"CSS url() loads {_shorten(ref)}")
            for ref in CSS_IMPORT.findall(block):
                if not _is_local(ref):
                    self.found.append(f"CSS @import loads {_shorten(ref)}")
        # Deduplicated, keeping the order they appear in: a bundle with the same
        # tracking pixel on forty pages should say so once.
        return list(dict.fromkeys(self.found))


def _shorten(ref: str) -> str:
    ref = ref.strip().replace("\n", " ")
    return ref if len(ref) <= 120 else ref[:117] + "…"


def references(html: bytes | str) -> list[str]:
    """Everything in this document that would be fetched from somewhere else.

    Empty is the only publishable answer. A non-empty list is the sentence the
    user gets, so each entry names the tag, the attribute and the target.
    """
    text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    parser = _References()
    parser.feed(text)
    parser.close()
    return parser.finish()


# ── the hashes the policy is built from ──────────────────────────────────────


class _Inline(HTMLParser):
    """The exact bytes inside every `<script>` and `<style>`.

    Exact matters: a CSP hash is computed over the element's content character
    for character, so anything that trims, re-encodes or unescapes on the way
    past produces a policy the browser will reject — and a rejected policy on a
    page nobody tests is a blank share link. `HTMLParser` hands script and style
    bodies through raw for precisely this reason: inside them there is no markup
    and no entity to convert.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.styles: list[str] = []
        self._kind: str | None = None
        self._buffer: list[str] = []
        self._external = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in ("script", "style"):
            self._kind = tag
            self._buffer = []
            # A `<script src=…>` has no body to hash — and `references` has
            # already refused the document if that src went anywhere.
            self._external = any((n or "").lower() == "src" for n, _ in attrs)

    def handle_data(self, data: str) -> None:
        if self._kind:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag != self._kind:
            return
        body = "".join(self._buffer)
        if body and not self._external:
            (self.scripts if tag == "script" else self.styles).append(body)
        self._kind, self._buffer, self._external = None, [], False


def inline_hashes(html: bytes | str) -> tuple[set[str], set[str]]:
    """`sha256-…` for every inline script and style, ready for a CSP.

    Returned as sets: `engine/html.py` writes two scripts and one stylesheet
    today, and the count is not this module's business to know.
    """
    text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    parser = _Inline()
    parser.feed(text)
    parser.close()

    def digest(bodies: list[str]) -> set[str]:
        return {
            "sha256-" + base64.b64encode(hashlib.sha256(b.encode("utf-8")).digest()).decode("ascii")
            for b in bodies
        }

    return digest(parser.scripts), digest(parser.styles)
