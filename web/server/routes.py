"""What each URL means, expressed as engine commands.

This module is the whole of the web layer's knowledge about report-maker, and
the list is deliberately short: which subcommand answers which question, and
which flag makes it print JSON. It holds no idea of what a report *is*, where a
vault keeps its output, or whether a citation is valid — every one of those is
a subprocess away, and a copy kept here would be a second answer that drifts
from the engine's the first time either changes.

    request  ──▶  routes  ──▶  Bridge  ──▶  engine.run  ──spawn──▶  report-maker

`Bridge` is the only way a handler reaches the engine or the disk, and it is
where the meter sits. A handler that wanted to spawn something itself would
have to import `engine` directly, which is exactly the thing to notice in a
review.

Nothing in here touches a socket. Handlers take a `Request` and return a
`Reply`, so the whole route table can be driven from a test with no server
running — which is how `web/tests/test_api.py` exercises the loop.

Two conventions worth stating once:

**Every failure is a `security.Refused`.** It already carries the spec's error
envelope, a status and an optional `Retry-After`, so `app.py` catches one type
at the top and never has to decide what a given failure means. The engine's own
exceptions are translated at the point of the call, where the context that
makes a good message is still in scope.

**Absolute paths never leave this process.** Several engine commands print the
vault path in their JSON — `check --json` opens with it, `brand show --json`
carries it beside the pack — and a build's stderr is full of them. `app.py`
scrubs every JSON body on the way out; this module's job is to make sure the
thing being scrubbed is a payload and not a pre-rendered string.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from . import engine, github, security, sessions, share

# ── the vocabulary ───────────────────────────────────────────────────────────


@dataclass
class Reply:
    """One response, before anything HTTP has happened to it.

    `payload` and `body` are not alternatives with a default — a route sets one
    or the other, and `body is None` is what tells `app.py` to serialise the
    payload. That distinction is load-bearing: a payload is scrubbed of server
    paths on the way out and a body is not, because a body is the user's own
    artefact (a PDF, a page image, their HTML bundle) and rewriting bytes
    inside it would corrupt the thing they asked for.
    """

    status: int = 200
    payload: Any = None
    body: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)
    cookies: list[str] = field(default_factory=list)


@dataclass
class Ctx:
    """What the server is, as far as a handler needs to know.

    Assembled once at startup by `app.py`. Handlers read it; nothing here
    writes to it except `logins`, which is explained where it is declared.
    """

    root: Path
    store: Path
    shares: Path
    client: Path | None
    tls: bool
    limiter: security.RateLimiter
    quota: security.Quota = field(default_factory=security.Quota)
    # GitHub logins, in memory and nowhere else. `session.json` has no field
    # for one and this module does not get to add one; a login is a display
    # nicety, so losing it on restart costs a name in the header and nothing
    # else. The token, which matters, is in the record.
    logins: dict[str, str] = field(default_factory=dict)


@dataclass
class Request:
    """One request, already parsed and already authenticated.

    `params` are the URL's captured segments, decoded exactly once — see
    `match`. `query` is `parse_qs`'s shape and is read through `one()`, because
    a repeated parameter is a thing a stranger can send and "the first one
    wins" beats "a list where a string was expected".
    """

    method: str
    path: str
    params: dict[str, str]
    query: dict[str, list[str]]
    body: bytes
    ctx: Ctx
    ip: str
    session: sessions.Session | None = None
    bridge: "Bridge | None" = None

    def one(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        return values[0] if values else default

    def flag(self, name: str, default: bool = False) -> bool:
        """A query parameter read as a boolean, the way a URL spells one."""
        raw = self.one(name)
        if raw is None:
            return default
        return raw.strip().lower() not in ("", "0", "false", "no", "off")

    def json(self) -> dict:
        """The request body as an object, or a refusal.

        An object specifically. Every route here takes named fields, and a bare
        array or string arriving where an object was expected is a bug in the
        caller worth naming rather than an AttributeError three lines later.
        """
        if not self.body:
            return {}
        try:
            payload = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise security.Refused(
                "that request body is not JSON",
                code="bad_json",
                status=400,
                detail=str(exc),
            ) from exc
        if not isinstance(payload, dict):
            raise security.Refused(
                "that request body is not a JSON object",
                code="bad_json",
                status=400,
            )
        return payload


# ── the meter ────────────────────────────────────────────────────────────────


class Bridge:
    """One session's access to the engine and to its own files, metered.

    Every guard the spec asks for meets here, in the order they have to happen:
    the command allowance is spent *before* a process exists, the disk is
    measured *before* a write, and the vault boundary is proved *before* a path
    is opened. A route that skipped this object would skip all three, which is
    why it is the only thing handlers are given.

    The command tally is the session's own, read from `session.quota_used` and
    written back after every spawn. That round trip is not decoration: the
    session record is re-read from disk on each request, so a count kept only
    in this object would reset the moment the browser sent its next request.
    """

    def __init__(self, ctx: Ctx, session: sessions.Session) -> None:
        self.ctx = ctx
        self.session = session
        self.vault: Path = session.vault
        self.quota = ctx.quota
        # `enforce` rebinds `usage.commands` rather than mutating it, so the
        # list is copied in and copied back rather than shared.
        self.usage = security.Usage(commands=list(session.quota_used.commands))

    # ── spawning ──

    def run(self, args: list[str], timeout: float | None = None) -> engine.Run:
        """One engine command, charged to this session.

        `enforce` checks and records in a single operation, so two request
        threads cannot both find one slot left. The record is persisted
        immediately afterwards rather than at the end of the request: a build
        that times out has still spent its command.
        """
        security.enforce(self, "command", now=time.time())
        self.session.quota_used.commands = self.usage.commands
        sessions.touch(self.session)

        budget = float(self.quota.wall_seconds if timeout is None else timeout)
        try:
            done = engine.run(self.vault, args, timeout=budget)
        except engine.Refused as exc:
            # The bridge's denylist — `template install`, `diagrams`, a `-C` in
            # the arguments. Its sentence already explains itself.
            raise security.Refused(str(exc), code="forbidden", status=403) from exc
        except engine.EngineMissing as exc:
            raise security.Refused(
                "this server has no report-maker to run",
                code="engine_missing",
                status=500,
                detail=str(exc),
            ) from exc

        if done.timed_out:
            raise security.Refused(
                f"that command did not finish within {int(budget)} seconds",
                code="timeout",
                status=504,
                detail=done.message(),
            )
        if done.truncated:
            raise security.Refused(
                "that command printed more output than this server will carry",
                code="too_much_output",
                status=413,
                detail=done.message(),
            )
        return done

    def json(self, args: list[str], timeout: float | None = None) -> Any:
        """A command whose stdout is JSON, however it exited.

        `check` exits 1 when it finds something, and the findings are the
        answer — so the exit code is not consulted and stdout is. A command
        that failed *and* printed nothing parseable is the real failure, and
        that is the one that raises, carrying the engine's own words.
        """
        done = self.run(args, timeout=timeout)
        text = done.stdout.strip()
        if text:
            try:
                return json.loads(text)
            except ValueError:
                pass
        raise security.Refused(
            f"`{args[0] if args else 'report-maker'}` did not answer with JSON",
            code="engine",
            status=502 if done.ok else 400,
            detail=done.message(),
        )

    # ── the disk ──

    def before_write(self) -> None:
        """Spec requirement 7, the disk half. Measured, not counted."""
        security.enforce(self, "disk", now=time.time())

    def within(self, candidate: str) -> Path:
        """Spec requirement 2. The only way a handler gets a path."""
        return security.within(self.vault, candidate)

    def call(self, _session: Any, args: list[str]) -> engine.Run:
        """The `(session, args)` shape `share` and `github` inject.

        Those modules default to a bridge module that does not exist in this
        build, so every call site passes this in. The session argument is
        ignored on purpose — the one this bridge was built for is the only one
        it will ever run a command in, and taking the caller's word for which
        vault to touch is precisely the mistake `-C` containment exists to
        prevent.
        """
        return self.run(args)


# ── small validators ─────────────────────────────────────────────────────────
#
# Everything below refuses rather than sanitises. A sanitiser turns a hostile
# string into a plausible one and hands it onward; a refusal ends the request
# while the caller can still be told what was wrong with it.

# The engine's own shape for a report id, borrowed from `share` so the two
# cannot disagree about what a token in a URL is allowed to be.
_ID = share.REPORT_ID

# `app/src/main/tree.ts`'s EDITABLE, carried across. Everything the editor
# opens; anything else is a file the engine wrote and nobody should be typing
# into over HTTP.
_EDITABLE = re.compile(r"\.(typ|yml|yaml|json|toml|mmd|md|txt|csv)$", re.IGNORECASE)

_MANIFEST_WROTE = re.compile(r"→\s+(\S+manifest\.json)")


def _id(raw: str, what: str = "report") -> str:
    """A report id or a folder of them, or a refusal.

    Refused before it is joined to anything. A path check that runs after a
    string has already been used to build a path has answered the interesting
    question in the wrong order.
    """
    text = (raw or "").strip().strip("/")
    if not text or not _ID.match(text):
        raise security.Refused(
            f"that is not a {what} id",
            code="bad_id",
            status=400,
            detail="a report id is its folder path under reports/, e.g. "
            "clients/acme/2026-08-12-audit",
        )
    return text


def _target(req: Request) -> str | None:
    """`?target=` — one report, one folder of them, or the whole vault."""
    raw = req.one("target")
    return _id(raw, "target") if raw else None


def _argument(value: Any, what: str) -> str:
    """A string bound for an argv, refused if it could read as a flag.

    No shell is involved — the bridge spawns a list — so quoting is not the
    risk. `argparse` is: a title of `--force` is consumed as an option, and the
    report that gets created is not the one that was asked for.
    """
    text = str(value or "").strip()
    if not text:
        raise security.Refused(f"{what} is required", code="missing", status=400)
    if text.startswith("-"):
        raise security.Refused(
            f"{what} may not begin with a dash",
            code="bad_argument",
            status=400,
            detail="the engine would read it as an option rather than a value",
        )
    if "\x00" in text or "\n" in text:
        raise security.Refused(
            f"{what} contains a character that cannot be in one",
            code="bad_argument",
            status=400,
        )
    return text


def _relative(value: Any, what: str) -> str:
    """An argument that becomes a *path* inside the vault, or a refusal.

    `_argument` is about argv — it stops a value being read as a flag. This is
    about the filesystem: `--into` and `--slug` are joined onto `reports/` by
    `engine/scaffold.py`, so `../../../../tmp/escape` is a request to scaffold a
    report outside the vault.

    The engine does refuse it today, but by accident of ordering: it computes
    the project-relative path of `sources.yml` a few lines before it calls
    `mkdir`, and that computation happens to raise. A containment guarantee
    that depends on which line of somebody else's function runs first is not a
    guarantee, and `engine/` is not ours to hold still. So the same three
    refusals `within` makes are made here, before the string reaches an argv.
    """
    text = _argument(value, what)
    if Path(text).is_absolute() or text.startswith("~"):
        raise security.Refused(
            f"{what} is a folder inside the vault, not an absolute path",
            code="bad_argument",
            status=400,
        )
    if ".." in Path(text).parts:
        raise security.Refused(
            f'{what} may not contain ".."',
            code="bad_argument",
            status=400,
            detail="a report is filed under reports/, and nothing above it",
        )
    return text


def _not_found(what: str) -> security.Refused:
    return security.Refused(what, code="not_found", status=404)


# ── the manifest, which is how paths are learned ─────────────────────────────


def _manifest(bridge: Bridge) -> dict:
    """`out/manifest.json`, refreshed and read.

    This is how the web layer learns where anything is. The manifest carries
    vault-relative paths for a report's source, its PDF and its page directory,
    so nothing here has to know that output lands in `out/` — which is
    configurable in `report-maker.toml` and therefore not ours to assume.

    `manifest` prints where it wrote the file, so even that path is the
    engine's answer rather than a guess. It is cheap: no typst, no network,
    one walk of the reports folder.
    """
    done = bridge.run(["manifest"])
    written = _MANIFEST_WROTE.findall(done.stdout)
    if not written:
        raise security.Refused(
            "the engine did not say where it wrote the manifest",
            code="engine",
            status=502,
            detail=done.message(),
        )
    try:
        return json.loads(bridge.within(written[0]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise security.Refused(
            "this vault's manifest could not be read",
            code="engine",
            status=502,
            detail=str(exc),
        ) from exc


def _entry(bridge: Bridge, report_id: str) -> dict:
    """One report's manifest entry, or a 404."""
    for record in _manifest(bridge).get("reports", []):
        if isinstance(record, dict) and record.get("id") == report_id:
            return record
    raise _not_found(f"no report {report_id!r} in this vault")


def _artefact(bridge: Bridge, entry: dict, key: str, what: str) -> Path:
    """A path the manifest named, proved to be inside the vault and to exist."""
    relative = (entry.get("artifacts") or {}).get(key)
    if not relative:
        raise _not_found(f"{entry.get('id')} has no {what} yet — build it first")
    path = bridge.within(str(relative))
    if not path.exists():
        raise _not_found(f"{entry.get('id')} has no {what} yet — build it first")
    return path


# ── session ──────────────────────────────────────────────────────────────────


def session_create(req: Request) -> Reply:
    """A vault, a starter report and a cookie, in about a second.

    The session limit is spent here rather than in `app.py` because this is the
    only route that costs a directory and three subprocesses; every other route
    is charged the ordinary request rate.
    """
    req.ctx.limiter.check(req.ip, "session")
    try:
        session = sessions.create(req.ctx.root, "try")
    except sessions.SessionError as exc:
        raise security.Refused(
            "this server could not make a vault for you",
            code="session",
            status=500,
            detail=str(exc),
        ) from exc
    return Reply(
        status=201,
        payload=session.to_json(),
        cookies=[sessions.cookie_for(session, secure=req.ctx.tls)],
    )


def session_read(req: Request) -> Reply:
    """The session as the browser is allowed to see it.

    `sessions.to_json()` withholds the id and the vault path deliberately, and
    nothing is added back here. What *is* added is the disk figure, because the
    50 MB ceiling is the one quota a person can act on.
    """
    session, bridge = _authed(req)
    body = dict(session.to_json())
    quota = body.get("quota")
    if isinstance(quota, dict):
        quota["diskBytes"] = sessions.disk_bytes(session)
        quota["reports"] = req.ctx.quota.reports
    body["github"] = github.connection(session)
    body["githubStatus"] = github.status()
    return Reply(payload=body)


def session_delete(req: Request) -> Reply:
    """Destroy it, and say the same thing whether or not it existed.

    `sessions.destroy` is silent on an id that opens nothing, so this route
    cannot be used to find out whether somebody else's session is live.
    """
    sid = req.params.get("sid")
    if sid:
        sessions.destroy(req.ctx.root, sid)
    return Reply(
        payload={"ok": True},
        cookies=[sessions.clear_cookie(secure=req.ctx.tls)],
    )


# ── reports ──────────────────────────────────────────────────────────────────


def reports_list(req: Request) -> Reply:
    _, bridge = _authed(req)
    return Reply(payload={"reports": bridge.json(["list", "--json"])})


def reports_create(req: Request) -> Reply:
    """`report-maker new`, with the report ceiling checked against `list`.

    The count comes from the engine and not from a counter kept here. What a
    report *is* is the engine's definition — a folder under `reports/` holding
    a `main.typ` — and a tally beside that definition is a second answer that
    can be wrong.
    """
    _, bridge = _authed(req)
    body = req.json()

    before = bridge.json(["list", "--json"])
    held = before if isinstance(before, list) else []
    security.enforce(bridge, "report", count=len(held), now=time.time())
    bridge.before_write()

    args = ["new", _argument(body.get("title"), "a title")]
    # `group`, `template` and `slug` are joined onto a directory by the engine,
    # so they go through `_relative`; the rest are text that lands in the
    # report's front matter and only has to survive argv.
    for field_name, flag, check in (
        ("group", "--into", _relative),
        ("template", "--template", _relative),
        ("kind", "--kind", _argument),
        ("author", "--author", _argument),
        ("slug", "--slug", _relative),
        ("date", "--date", _argument),
    ):
        value = body.get(field_name)
        if value not in (None, ""):
            args += [flag, check(value, field_name)]

    done = bridge.run(args)
    if not done.ok:
        raise security.Refused(
            "the engine would not scaffold that report",
            code="engine",
            status=400,
            detail=done.message(),
        )

    after = bridge.json(["list", "--json"])
    known = {r.get("id") for r in held if isinstance(r, dict)}
    fresh = [
        r.get("id")
        for r in (after if isinstance(after, list) else [])
        if isinstance(r, dict) and r.get("id") not in known
    ]
    return Reply(
        status=201,
        payload={"created": fresh[0] if fresh else None, "reports": after},
    )


def report_read(req: Request) -> Reply:
    """One report: what the manifest says, plus the files in its folder.

    The file list is the folder walked, which is `app/src/main/tree.ts`'s job
    done in Python and for the same reason — the folder *is* the data model, so
    there is nothing to project it from. Where the folder is comes from the
    manifest's `source.main`, so the `reports/` prefix is never assumed here
    either.
    """
    _, bridge = _authed(req)
    report_id = _id(req.params["id"])
    entry = _entry(bridge, report_id)

    main = (entry.get("source") or {}).get("main")
    files: list[dict] = []
    if main:
        folder = bridge.within(str(main)).parent
        files = _walk(bridge.vault, folder)
    return Reply(payload={"report": entry, "files": files})


_SKIP_DIRS = {".build", "out", "node_modules", ".git", "__pycache__"}


def _walk(vault: Path, folder: Path, depth: int = 0) -> list[dict]:
    """A report folder, flattened to a list of vault-relative files.

    Flat rather than nested: a report folder is three files and three
    subfolders, and a tree shape would be structure for its own sake. The
    generated directories are skipped for the same reason the desktop tree
    skips them — nobody should be editing staged output.
    """
    if depth > 6:
        return []
    found: list[dict] = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name)
    except OSError:
        return []
    for entry in entries:
        if entry.name.startswith(".") and entry.name != ".gitkeep":
            continue
        if entry.is_dir(follow_symlinks=False):
            if entry.name in _SKIP_DIRS:
                continue
            found += _walk(vault, Path(entry.path), depth + 1)
            continue
        if not entry.is_file(follow_symlinks=False):
            # A symlink, a socket, a device node. `within` would refuse to open
            # it later; listing it would only promise something untrue.
            continue
        try:
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        relative = Path(entry.path).relative_to(vault)
        found.append(
            {
                "path": str(relative),
                "name": entry.name,
                "bytes": size,
                "editable": bool(_EDITABLE.search(entry.name)),
            }
        )
    return found


def file_read(req: Request) -> Reply:
    _, bridge = _authed(req)
    _id(req.params["id"])
    path = _file(req, bridge)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise _not_found("no such file in this vault") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise security.Refused(
            "that file is not text this editor can open",
            code="not_text",
            status=415,
            detail=str(exc),
        ) from exc
    return Reply(payload={"path": req.one("path"), "text": text})


def file_write(req: Request) -> Reply:
    """Write one file, inside the vault, of a kind the editor opens.

    Three refusals in front of the write, and the order matters: the disk quota
    is checked before the bytes are considered, the boundary before the name is
    considered, and the extension last. A caller learns which limit it hit
    without any of them having let it past the previous one.
    """
    _, bridge = _authed(req)
    _id(req.params["id"])
    bridge.before_write()
    path = _file(req, bridge)
    if not _EDITABLE.search(path.name):
        raise security.Refused(
            f"refusing to write {path.name}: not a file this editor opens",
            code="not_editable",
            status=415,
            detail="text formats only — .typ, .yml, .json, .toml, .mmd, .md, "
            ".txt, .csv",
        )
    text = req.json().get("text")
    if not isinstance(text, str):
        raise security.Refused(
            "a write needs a `text` string", code="missing", status=400
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return Reply(payload={"path": req.one("path"), "bytes": len(text.encode("utf-8"))})


def _file(req: Request, bridge: Bridge) -> Path:
    raw = req.one("path")
    if not raw:
        raise security.Refused("no `path` given", code="missing", status=400)
    return bridge.within(raw)


def report_build(req: Request) -> Reply:
    """`report-maker all <id>` — stage, build, pages, manifest, check.

    A non-zero exit is returned as 200 with `ok: false`, and that is not
    laziness. `all` ends by running `check`, so a build that fails the citation
    rule is the product working exactly as designed; the findings are the
    answer to the request, not an error in serving it. What does become an
    error status is a timeout (504) — that is the server failing to answer.
    """
    _, bridge = _authed(req)
    report_id = _id(req.params["id"])
    bridge.before_write()
    done = bridge.run(["all", report_id])

    artefacts: dict[str, Any] = {}
    try:
        entry = _entry(bridge, report_id)
        artefacts = {
            "pdf": bool((entry.get("artifacts") or {}).get("pdf")),
            "pages": (entry.get("artifacts") or {}).get("page_count", 0),
            "built": (entry.get("state") or {}).get("built", False),
            "stale": (entry.get("state") or {}).get("stale", True),
        }
    except security.Refused:
        # The build failed early enough that the report has no entry. The run
        # itself is still the useful answer, so it is returned rather than
        # replaced by a complaint about the manifest.
        artefacts = {}
    # Whether diagrams were rendered at all, stated rather than left to be read
    # out of the build log. With them off, `all` still runs its diagrams step
    # and the engine's own skip message reads "Install Node.js, then re-run" —
    # true on a laptop, and misleading on a server where that is the operator's
    # decision and not the writer's. The flag is what lets the page say so.
    return Reply(
        payload={
            **done.as_dict(),
            "artefacts": artefacts,
            "diagrams": engine.diagrams_enabled(),
        }
    )


def report_pdf(req: Request) -> Reply:
    _, bridge = _authed(req)
    entry = _entry(bridge, _id(req.params["id"]))
    path = _artefact(bridge, entry, "pdf", "PDF")
    return Reply(
        body=path.read_bytes(),
        headers={"Content-Type": "application/pdf", "Cache-Control": "no-store"},
    )


def report_pages(req: Request) -> Reply:
    """The page index, with URLs rather than paths.

    This is the route the phone reads. iOS Safari cannot usefully show a PDF in
    a frame, and `pages.json` plus one PNG per page is already what the engine
    writes — so the mobile reader is not a workaround, it is the artefact that
    was there all along.
    """
    _, bridge = _authed(req)
    report_id = _id(req.params["id"])
    entry = _entry(bridge, report_id)
    folder = _artefact(bridge, entry, "pages", "page images")
    try:
        index = json.loads((folder / "pages.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _not_found(f"{report_id} has no page images yet — build it first") from exc
    count = int(index.get("count", 0))
    return Reply(
        payload={
            "count": count,
            "ppi": index.get("ppi"),
            "pages": [
                f"/api/reports/{report_id}/page/{n}" for n in range(1, count + 1)
            ],
        }
    )


def report_page(req: Request) -> Reply:
    _, bridge = _authed(req)
    entry = _entry(bridge, _id(req.params["id"]))
    folder = _artefact(bridge, entry, "pages", "page images")
    raw = req.params.get("n", "")
    if not raw.isdigit() or not 1 <= int(raw) <= 9999:
        raise security.Refused("that is not a page number", code="bad_id", status=400)
    # Built from an integer, so the filename cannot be anything but this shape;
    # `within` proves it anyway, on the principle that the guard is cheap and
    # the day somebody makes this string interpolated is the day it matters.
    page = bridge.within(str((folder / f"page-{int(raw)}.png").relative_to(bridge.vault)))
    if not page.is_file():
        raise _not_found("no such page")
    return Reply(
        body=page.read_bytes(),
        headers={"Content-Type": "image/png", "Cache-Control": "no-store"},
    )


def report_html(req: Request) -> Reply:
    """The self-contained bundle, for the session's own eyes.

    Served under `share`'s header set rather than the app's. It is the same
    artefact `GET /s/<token>` serves, so it gets the same policy: hashes for
    its own inline blocks, `connect-src 'none'`, and nothing external anywhere.
    """
    _, bridge = _authed(req)
    report_id = _id(req.params["id"])
    bridge.before_write()
    done = bridge.run(["html", report_id])
    try:
        bundle = share._bundle(bridge.vault, report_id, done.stdout)
    except share.ShareError as exc:
        raise security.Refused(
            f"no HTML bundle for {report_id}",
            code="engine",
            status=400,
            detail=f"{exc}\n{done.message()}",
        ) from exc
    return Reply(
        body=bundle.read_bytes(),
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
            **share.headers(bundle, report_id=report_id, max_age=0),
        },
    )


# ── the citation rule, and the evidence behind it ────────────────────────────


def check(req: Request) -> Reply:
    _, bridge = _authed(req)
    args = ["check", "--json", "--score"]
    target = _target(req)
    if target:
        args.append(target)
    return Reply(payload=bridge.json(args))


def score(req: Request) -> Reply:
    _, bridge = _authed(req)
    args = ["score", "--json"]
    target = _target(req)
    if target:
        args.append(target)
    return Reply(payload=bridge.json(args))


def sources(req: Request) -> Reply:
    _, bridge = _authed(req)
    report_id = _id(req.params["id"])
    return Reply(payload={"sources": bridge.json(["sources", report_id, "--json"])})


def cite(req: Request) -> Reply:
    """Archive a page and file it in `sources.yml` — after the pre-flight.

    Spec requirement 4. `security.check_url` resolves the name and judges every
    address it answers with, so a URL naming the metadata endpoint, a private
    range or loopback is refused before any process starts.

    That check alone is only half of the requirement. Its second sentence —
    "re-check after redirects" — matters more than the first, because a host
    that passes the pre-flight and then answers `302 Location:
    http://169.254.169.254/latest/meta-data/` needs no hostile name server and
    no DNS trick at all; `engine/snapshot.py` follows redirects checking only
    the *scheme*, so the hop lands and the credentials it returns are archived
    into `snapshots/`, where the person who asked for the citation can read them
    back through `GET /reports/:id/file`. So `security.trace` walks the chain
    first, vetting and pinning every hop, and the engine is handed the URL the
    chain actually ends at rather than the one that was typed.

    The last hop of that used to be open and is not any more. The engine opened
    the URL itself and resolved the name a *second* time, so a name server
    willing to answer the two lookups differently — or simply a host willing to
    answer one request with a page and the next with a `302` — got one fetch
    through a guard that had already approved something else. `cite` now takes
    `--pinned-address`, so the vetted literal goes with the URL: the engine
    connects there, keeps the hostname for `Host`, for TLS SNI and for the
    certificate check, and refuses a redirect that leaves the origin the pin was
    made for. The address handed over is the one the check just resolved, and
    nothing between that resolution and the connection looks the name up again.
    """
    _, bridge = _authed(req)
    report_id = _id(req.params["id"])
    body = req.json()
    url = _argument(body.get("url"), "a url")
    target = security.check_url(url, what="cite")
    # Every hop, not just the first. What comes back is terminal and vetted, and
    # it is what gets archived — a citation should name the page that answered,
    # not the shortener that pointed at it.
    url = security.trace(target, what="cite")
    # Kept, not discarded: `address` is what the engine is told to connect to,
    # and a check whose answer is thrown away is a check the next lookup gets
    # to overrule.
    landed = security.check_url(url, what="cite")

    bridge.before_write()
    # The list before, so the answer can say whether a key was minted or an
    # existing entry kept its own — `cite` is idempotent on a URL, and "already
    # a source" is a different sentence from "filed". Same shape as `report_new`
    # above, and for the same reason: the engine names what it did in prose, and
    # prose is not something to parse.
    held = bridge.json(["sources", report_id, "--json"])
    before = {
        row.get("key")
        for row in (_rows(held))
        if isinstance(row, dict)
    }
    args = ["cite", report_id, url, "--pinned-address", landed.address]
    for name, flag in (("key", "--key"), ("type", "--type")):
        value = body.get(name)
        if value not in (None, ""):
            args += [flag, _argument(value, name)]
    if body.get("snapshot") is False:
        args.append("--no-snapshot")

    done = bridge.run(args)
    if not done.ok:
        raise security.Refused(
            "the engine could not archive that page",
            code="engine",
            status=400,
            detail=done.message(),
        )
    after = bridge.json(["sources", report_id, "--json"])
    rows = _rows(after)
    # The key is the entry that carries the URL that was just archived. Matched
    # on the field rather than read out of the command's own sentence: a lookup
    # in what the engine printed as data, never a parse of what it printed as
    # prose.
    entry = next(
        (row for row in rows if isinstance(row, dict) and row.get("url") == url),
        None,
    )
    key = entry.get("key") if entry else None
    return Reply(
        status=201,
        payload={
            # What was actually cited, which is not always what was sent: a
            # redirect chain is followed here so it can be vetted, and the
            # caller is owed the destination rather than left to wonder.
            "url": url,
            "key": key,
            "title": entry.get("title") if entry else None,
            "created": bool(key) and key not in before,
            "snapshot": entry.get("snapshot") if entry else None,
            "stdout": done.stdout,
            "sources": after,
        },
    )


def _rows(payload) -> list:
    """`sources --json` through the server, whichever envelope it arrived in."""
    if isinstance(payload, dict):
        inner = payload.get("sources")
        return inner if isinstance(inner, list) else []
    return payload if isinstance(payload, list) else []


def verify(req: Request) -> Reply:
    """Evidence drift. Offline unless every URL involved passes the pre-flight.

    `verify` without `--offline` re-fetches every archived source, and in
    GitHub mode those URLs came out of somebody's repository — so it is `cite`
    again, once per entry, with nobody having looked at them. An online run is
    therefore allowed only for a named report, whose `sources.yml` can be read
    first and every URL in it judged. For the whole vault the answer is no,
    because there is no single list to vet.

    The judgement is bound to the fetch the same way `cite` binds it, with one
    condition attached. `--pinned-address` names *one* machine and this pass
    fetches every source in the report, so it is passed only when they all vet
    as the same destination and none of their chains leaves it — the case where
    one address is the truth about the whole run. Otherwise the run goes
    unpinned rather than wrongly pinned: a pin for the wrong host would make a
    live source fail its certificate and be reported as evidence drift, which
    is a false statement about somebody's citations and worse than the window
    it would close.
    """
    _, bridge = _authed(req)
    target = _target(req)
    offline = not req.flag("online")
    # One entry per source: the destination it was vetted as, or None for one
    # this server could not pin. See the note where it is read.
    pins: set[tuple[str, int, str] | None] = set()

    if not offline:
        if not target:
            raise security.Refused(
                "an online verify has to name one report",
                code="forbidden",
                status=403,
                detail="every URL it would fetch is checked first, and there "
                "is no single list of them for a whole vault",
            )
        listed = bridge.json(["sources", target, "--json"])
        for record in listed if isinstance(listed, list) else []:
            url = isinstance(record, dict) and record.get("url")
            if not url:
                continue
            checked = security.check_url(str(url), what="verify")
            # The redirect chain matters here for the same reason it does in
            # `cite`: a `sources.yml` is a file the session can write, so a URL
            # in it is a URL a stranger chose. The one difference is what an
            # *unreachable* source means. In `cite` it is a chain nobody could
            # vet, and the request is refused; here it is the finding — a cited
            # page that no longer answers is exactly what `verify` is run to
            # discover, and refusing the whole pass would hide it.
            try:
                landed = security.check_url(
                    security.trace(checked, what="verify"), what="verify"
                )
            except security.Refused as exc:
                if exc.code != "url_unreachable":
                    raise
                pins.add(None)
                continue
            # `--pinned-address` names one machine, and this pass fetches every
            # source in the report. So it is passed only when they are all the
            # same machine *and* none of them redirects off it — a pin that
            # holds for the fetch the engine will actually make. Anything else
            # adds `None` to the set, and a set with a `None` or a second
            # destination in it pins nothing: a wrong pin would turn a live
            # source into "error" and report a certificate mismatch as evidence
            # drift, which is a false statement about somebody's citations.
            stayed = (landed.host, landed.port) == (checked.host, checked.port)
            pins.add((checked.host, checked.port, checked.address) if stayed else None)

    args = ["verify", "--json"]
    if offline:
        args.append("--offline")
    if len(pins) == 1:
        only = next(iter(pins))
        if only is not None:
            args += ["--pinned-address", only[2]]
    if target:
        args.append(target)
    return Reply(payload=bridge.json(args))


# ── the pad ──────────────────────────────────────────────────────────────────


def todos_read(req: Request) -> Reply:
    _, bridge = _authed(req)
    args = ["todos", "--json"]
    if req.flag("open"):
        args.append("--open")
    target = _target(req)
    if target:
        args.append(target)
    return Reply(payload=bridge.json(args))


def todos_write(req: Request) -> Reply:
    """Add a task, or tick one. The engine rewrites the file; we do not.

    `todos.md` is markdown a person also edits by hand, and the engine already
    knows how to add a line without disturbing the rest of it. Rewriting the
    file here would be a second implementation of a format with one owner.
    """
    _, bridge = _authed(req)
    report_id = _id(req.params["id"])
    body = req.json()
    bridge.before_write()

    if "text" in body:
        args = ["todos", report_id, "--add", _argument(body["text"], "text")]
    elif "line" in body:
        line = body.get("line")
        if not isinstance(line, int) or line < 1:
            raise security.Refused(
                "`line` is a line number", code="bad_argument", status=400
            )
        flag = "--check" if body.get("done", True) else "--uncheck"
        args = ["todos", report_id, flag, str(line)]
        where = body.get("in")
        if where:
            args += ["--in", _argument(where, "in")]
    else:
        raise security.Refused(
            "send either `text` to add a task, or `line` and `done` to tick one",
            code="missing",
            status=400,
        )

    done = bridge.run(args)
    if not done.ok:
        raise security.Refused(
            "the engine would not write that task",
            code="engine",
            status=400,
            detail=done.message(),
        )
    return Reply(payload=bridge.json(["todos", report_id, "--json"]))


def notes_read(req: Request) -> Reply:
    """`notes.md`, or null. A report without one is the ordinary case.

    The engine answers `null` for a report that has never had a note, and that
    is passed through rather than turned into a 404: nothing is missing, the
    writer simply has not written anything down yet.
    """
    _, bridge = _authed(req)
    report_id = _id(req.params["id"])
    return Reply(payload={"notes": bridge.json(["notes", report_id, "--json"])})


def notes_write(req: Request) -> Reply:
    _, bridge = _authed(req)
    report_id = _id(req.params["id"])
    text = req.json().get("text")
    if not isinstance(text, str):
        raise security.Refused("a note needs a `text` string", code="missing", status=400)

    bridge.before_write()
    entry = _entry(bridge, report_id)
    main = (entry.get("source") or {}).get("main")
    if not main:
        raise _not_found(f"no report {report_id!r} in this vault")
    folder = Path(str(main)).parent
    path = bridge.within(str(folder / "notes.md"))
    path.write_text(text, encoding="utf-8")
    return Reply(payload={"bytes": len(text.encode("utf-8"))})


# ── search, designs, brand ───────────────────────────────────────────────────


def find(req: Request) -> Reply:
    _, bridge = _authed(req)
    query = _argument(req.one("q"), "a query")
    args = ["find", query, "--json"]
    for kind in req.query.get("kind", []):
        args += ["--kind", _argument(kind, "kind")]
    limit = req.one("limit")
    if limit and limit.isdigit():
        args += ["--limit", str(min(int(limit), 500))]
    return Reply(payload=bridge.json(args))


def templates(req: Request) -> Reply:
    _, bridge = _authed(req)
    return Reply(payload={"templates": bridge.json(["templates", "--json"])})


def template_install(req: Request) -> Reply:
    """Spec requirement 5, given a door so the answer can be a sentence.

    `template install` clones an arbitrary git repository named in the request.
    The bridge refuses it whatever route asks, and this route exists only so
    that a frontend asking gets 403 and the reason rather than 404 and a
    shrug — a feature that is off should say it is off.
    """
    _authed(req)
    try:
        engine.guard(["template", "install", "https://example.invalid/design.git"])
    except engine.Refused as exc:
        raise security.Refused(str(exc), code="forbidden", status=403) from exc
    raise security.Refused(
        "`template install` is disabled in web mode",
        code="forbidden",
        status=403,
    )


def brand_read(req: Request) -> Reply:
    _, bridge = _authed(req)
    args = ["brand", "show", "--json"]
    pack = req.one("pack")
    if pack:
        args.append(_argument(pack, "pack"))
    return Reply(payload=bridge.json(args))


def brand_write(req: Request) -> Reply:
    """Write `brand.json` and restage, at the path the engine names.

    `brand show --json` reports the pack's own path, vault-relative, so the
    file this writes to is the one the engine reads rather than a convention
    guessed at here. Restaging afterwards is what makes the change visible: the
    designs in `.build/` are generated from the brand, and a brand edited
    without a restage is a change that appears on the next build for no
    apparent reason.
    """
    _, bridge = _authed(req)
    body = req.json()
    values = body.get("brand")
    if not isinstance(values, dict):
        raise security.Refused(
            "send the pack as a `brand` object", code="missing", status=400
        )

    bridge.before_write()
    args = ["brand", "show", "--json"]
    pack = body.get("pack")
    if pack:
        args.append(_argument(pack, "pack"))
    shown = bridge.json(args)
    relative = shown.get("path") if isinstance(shown, dict) else None
    if not relative:
        raise security.Refused(
            "this vault has no writable brand pack",
            code="not_found",
            status=404,
            detail="`brand new` creates one; a built-in pack is not edited in "
            "place",
        )

    path = bridge.within(str(relative))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    staged = bridge.run(["stage"])
    return Reply(payload={"path": str(relative), "staged": staged.as_dict()})


# ── git, and the repository that is the store ────────────────────────────────


def git_state(req: Request) -> Reply:
    session, bridge = _authed(req)
    try:
        return Reply(payload=github.state(session, run=bridge.call))
    except github.GitHubError as exc:
        raise security.Refused(str(exc), code="git", status=400) from exc


def git_sync(req: Request) -> Reply:
    """Commit, and push only because somebody asked in this request.

    Every rule that makes a push safe lives in `engine/gitsync.py` — never
    `--force`, never without an upstream, never from a detached HEAD, never
    when behind — and its refusals name the command that fixes them. They are
    passed through word for word. A web layer that summarised them into "push
    failed" would be teaching the writer to reach for `--force` unaided, which
    is the outcome that module exists to prevent.

    A refusal answers 409 rather than 200: nothing was pushed, and a caller
    that only looked at the status would otherwise think something was.
    """
    session, bridge = _authed(req)
    body = req.json()
    message = body.get("message")
    push = body.get("push") is True
    try:
        result = github.sync(session, message, push, run=bridge.call)
    except github.GitHubError as exc:
        raise security.Refused(str(exc), code="git", status=400) from exc
    return Reply(status=409 if result.get("refused") else 200, payload=result)


def github_status(req: Request) -> Reply:
    """Whether the button should exist. No session, because the answer is
    about this server and not about whoever is asking."""
    return Reply(payload=github.status())


def github_authorize(req: Request) -> Reply:
    """Where to send the browser, with a state bound to this session.

    Returned as a URL rather than a 302 because the caller is `fetch`, not a
    navigation — a redirect here would be followed by the fetch and land the
    OAuth page in a JSON parser.
    """
    session, _ = _authed(req)
    if not github.configured():
        raise security.Refused(
            github.NOT_CONFIGURED, code="github_off", status=503
        )
    state = github.STATES.issue(session.id)
    return Reply(payload={"url": github.authorize_url(state)})


def github_callback(req: Request) -> Reply:
    """GitHub sending the browser back. The state is what makes it ours.

    A callback with no state, or one this session did not issue, is refused
    rather than tolerated: without it, any page on the internet could walk a
    signed-in visitor through an authorization that ends with somebody else's
    token in their session.
    """
    session, _ = _authed(req)
    if not github.configured():
        raise security.Refused(github.NOT_CONFIGURED, code="github_off", status=503)
    if not github.STATES.consume(session.id, req.one("state")):
        raise security.Refused(
            "that GitHub sign-in did not start here",
            code="bad_state",
            status=400,
            detail="start again from the Connect button",
        )
    code = req.one("code")
    if not code:
        raise security.Refused("GitHub sent no code", code="missing", status=400)
    try:
        token = github.exchange(code)
        who = github.identity(token)
    except github.GitHubError as exc:
        raise security.Refused(str(exc), code="github", status=502) from exc

    github.remember(session, token, login=who.get("login"))
    _persist_github(req, session)
    # A browser navigation, so the answer is a page change and not a document.
    return Reply(status=303, headers={"Location": "/?github=connected"})


def github_repos(req: Request) -> Reply:
    session, _ = _authed(req)
    token = github.token_for(session)
    if not token:
        raise security.Refused(
            "this session is not connected to GitHub", code="not_connected", status=401
        )
    try:
        return Reply(payload={"repos": github.repos(token)})
    except github.GitHubError as exc:
        raise security.Refused(str(exc), code="github", status=502) from exc


def github_connect(req: Request) -> Reply:
    """Clone a repository into this session's vault. The repo is the store.

    The clone is given the session's own disk ceiling, so a repository larger
    than the quota is refused on the way in rather than after it has landed.
    Whether the result is a vault is the engine's question — `report-maker.toml`
    or not — and the answer is reported rather than acted on: offering to `init`
    somebody's repository is a change to their repository, and that is theirs
    to ask for.
    """
    session, bridge = _authed(req)
    token = github.token_for(session)
    if not token:
        raise security.Refused(
            "this session is not connected to GitHub", code="not_connected", status=401
        )
    body = req.json()
    repo = _argument(body.get("repo"), "a repository")
    branch = body.get("branch") or None
    try:
        github.clone(
            token,
            repo,
            branch,
            session.vault,
            max_bytes=req.ctx.quota.disk_bytes,
        )
    except github.GitHubError as exc:
        raise security.Refused(str(exc), code="github", status=400) from exc

    session.mode = "github"
    github.remember(session, token, repo=repo, branch=branch)
    _persist_github(req, session)
    return Reply(
        payload={
            "repo": repo,
            "branch": branch,
            "isVault": engine.is_vault(session.vault),
            "session": session.to_json(),
        }
    )


def github_init(req: Request) -> Reply:
    """`report-maker init` on a cloned repository that is not yet a vault.

    Its own route rather than a step inside `connect`, because it writes a file
    into somebody's repository and that is a decision, not a detail.
    """
    session, bridge = _authed(req)
    if engine.is_vault(session.vault):
        return Reply(payload={"isVault": True, "stdout": ""})
    bridge.before_write()
    done = bridge.run(["init"])
    if not done.ok:
        raise security.Refused(
            "the engine would not initialise that folder",
            code="engine",
            status=400,
            detail=done.message(),
        )
    return Reply(payload={"isVault": engine.is_vault(session.vault), "stdout": done.stdout})


def _persist_github(req: Request, session: sessions.Session) -> None:
    """Fold the `github` slot back into the fields `session.json` actually has.

    `github.remember` writes into a mutable `github` mapping it expects the
    session record to carry; `sessions.Session` has no such field and is not
    ours to change. So the three durable values are copied onto the record's
    own `token`, `repo` and `branch`, and the login — which the record has no
    place for — is kept in memory. Losing a display name on restart is a fair
    price for not inventing a field in somebody else's schema.
    """
    slot = getattr(session, "github", None) or {}
    session.token = slot.get("token") or session.token
    session.repo = slot.get("repo") or session.repo
    session.branch = slot.get("branch") or session.branch
    login = slot.get("login")
    if login:
        req.ctx.logins[session.id] = str(login)
    sessions.touch(session)


def load_github(ctx: Ctx, session: sessions.Session) -> None:
    """The other direction, run once per request before any github call."""
    session.github = {  # type: ignore[attr-defined]
        "token": session.token,
        "repo": session.repo,
        "branch": session.branch,
        "login": ctx.logins.get(session.id),
    }


# ── sharing ──────────────────────────────────────────────────────────────────


def share_publish(req: Request) -> Reply:
    """Build the report with its evidence and mint a link to it.

    `share.publish` refuses a report that does not pass `check`, and that
    refusal is passed through rather than worked around. Publishing is the
    outward-facing act; a report that claims to be finished while `check`
    disagrees is exactly the thing the whole tool exists to catch. A report
    that says `status: "draft"` shares fine and says on its own face that it is
    unfinished.
    """
    session, bridge = _authed(req)
    report_id = _id(req.params["id"])
    bridge.before_write()
    try:
        published = share.publish(
            session,
            report_id,
            req.ctx.shares,
            run=bridge.call,
            allow_findings=req.json().get("anyway") is True,
        )
    except share.ShareError as exc:
        raise security.Refused(str(exc), code="not_shareable", status=400) from exc
    return Reply(status=201, payload=published.to_json())


def share_read(req: Request) -> Reply:
    """The one public route. No cookie is read, and none is set.

    The token is the whole of the authorisation — an unguessable capability in
    a URL — which is why nothing else about the request is consulted and why
    the token never appears in a log line. `share.headers` supplies the whole
    header set, hashes and all, verbatim.
    """
    path = share.get(req.ctx.shares, req.params.get("token", ""))
    if path is None:
        raise _not_found("no such share")
    meta = share.meta(req.ctx.shares, req.params["token"]) or {}
    return Reply(
        body=path.read_bytes(),
        headers=share.headers(path, report_id=meta.get("report")),
    )


# ── health ───────────────────────────────────────────────────────────────────


def health(req: Request) -> Reply:
    """Enough for a container probe, and nothing about this machine's layout.

    Deliberately not `engine.health()`: that answers with the absolute path of
    the CLI and of the sessions root, and a health endpoint is not a place to
    publish either. Whether an engine was found is the fact a probe needs.
    """
    version = engine.version()
    return Reply(
        status=200 if version else 503,
        payload={
            "ok": bool(version),
            "version": version,
            "diagrams": engine.diagrams_enabled(),
            "github": github.status()["mode"],
        },
    )


# ── the table ────────────────────────────────────────────────────────────────
#
# Order is significant, and only in one way: a pattern with literal segments
# after its greedy `{id...}` must be listed before the bare one, or
# `/api/reports/a/b/file` is read as a report called "a/b/file". Everything
# else is grouped for reading.

Handler = Callable[[Request], Reply]

# (method, pattern, handler, needs a session)
TABLE: tuple[tuple[str, str, Handler, bool], ...] = (
    ("GET", "/api/health", health, False),
    ("GET", "/api/github/status", github_status, False),

    ("POST", "/api/session", session_create, False),
    ("GET", "/api/session", session_read, True),
    ("DELETE", "/api/session", session_delete, False),

    ("GET", "/api/reports/{id...}/file", file_read, True),
    ("PUT", "/api/reports/{id...}/file", file_write, True),
    ("POST", "/api/reports/{id...}/build", report_build, True),
    ("GET", "/api/reports/{id...}/pdf", report_pdf, True),
    ("GET", "/api/reports/{id...}/pages", report_pages, True),
    ("GET", "/api/reports/{id...}/page/{n}", report_page, True),
    ("GET", "/api/reports/{id...}/html", report_html, True),
    ("GET", "/api/reports", reports_list, True),
    ("POST", "/api/reports", reports_create, True),
    ("GET", "/api/reports/{id...}", report_read, True),

    ("GET", "/api/check", check, True),
    ("GET", "/api/score", score, True),
    ("GET", "/api/verify", verify, True),
    ("POST", "/api/sources/{id...}/cite", cite, True),
    ("GET", "/api/sources/{id...}", sources, True),

    ("GET", "/api/todos", todos_read, True),
    ("POST", "/api/todos/{id...}", todos_write, True),
    ("GET", "/api/notes/{id...}", notes_read, True),
    ("PUT", "/api/notes/{id...}", notes_write, True),

    ("GET", "/api/find", find, True),
    ("POST", "/api/templates/install", template_install, True),
    ("GET", "/api/templates", templates, True),
    ("GET", "/api/brand", brand_read, True),
    ("PUT", "/api/brand", brand_write, True),

    ("GET", "/api/git/state", git_state, True),
    ("POST", "/api/git/sync", git_sync, True),
    ("GET", "/api/github/authorize", github_authorize, True),
    ("GET", "/api/github/callback", github_callback, True),
    ("GET", "/api/github/repos", github_repos, True),
    ("POST", "/api/github/connect", github_connect, True),
    ("POST", "/api/github/init", github_init, True),

    ("POST", "/api/share/{id...}", share_publish, True),
    ("GET", "/s/{token}", share_read, False),
)

_PATTERNS = tuple((method, tuple(p.strip("/").split("/")), fn, auth) for method, p, fn, auth in TABLE)


def match(method: str, path: str) -> tuple[Handler, dict[str, str], bool] | None:
    """Find the handler for a path, or None.

    Segments are percent-decoded **one at a time and exactly once**, after the
    split. Decoding the whole path first is how `%2F` becomes a separator that
    was never sent, and decoding twice is how `%252e%252e` becomes `..` one
    layer too late; doing it here, once, means no handler downstream is ever
    tempted to do it again.
    """
    parts = [unquote(segment) for segment in path.strip("/").split("/") if segment]
    allowed = False
    for want, pattern, handler, auth in _PATTERNS:
        params = _bind(pattern, parts)
        if params is None:
            continue
        if want != method:
            allowed = True
            continue
        return handler, params, auth
    if allowed:
        raise security.Refused(
            f"{method} is not how that is asked for", code="bad_method", status=405
        )
    return None


def _bind(pattern: tuple[str, ...], parts: list[str]) -> dict[str, str] | None:
    """Match one pattern. `{name}` takes a segment; `{name...}` takes a run."""
    greedy = next((i for i, s in enumerate(pattern) if s.endswith("...}")), None)
    if greedy is None:
        if len(pattern) != len(parts):
            return None
        found: dict[str, str] = {}
        for want, got in zip(pattern, parts):
            if want.startswith("{"):
                found[want[1:-1]] = got
            elif want != got:
                return None
        return found

    tail = len(pattern) - greedy - 1
    # The greedy segment must take at least one, so a pattern of n segments
    # needs at least n path segments.
    if len(parts) < len(pattern):
        return None
    found = {}
    for want, got in zip(pattern[:greedy], parts[:greedy]):
        if want.startswith("{"):
            found[want[1:-1]] = got
        elif want != got:
            return None
    if tail:
        for want, got in zip(pattern[greedy + 1 :], parts[len(parts) - tail :]):
            if want.startswith("{"):
                found[want[1:-1]] = got
            elif want != got:
                return None
    found[pattern[greedy][1:-4]] = "/".join(parts[greedy : len(parts) - tail])
    return found


def _authed(req: Request) -> tuple[sessions.Session, Bridge]:
    """The session and its bridge, or the same 401 every other cause gets.

    `app.py` has already refused an unauthenticated request to a route marked
    as needing one, so reaching here without a session is a routing mistake
    rather than a stranger — but it still answers 401 rather than raising an
    AttributeError, because a mistake that becomes a 500 tells an attacker more
    than a mistake that becomes a 401.
    """
    if req.session is None or req.bridge is None:
        raise no_session()
    return req.session, req.bridge


def no_session() -> security.Refused:
    """One shape for every reason a session did not open.

    Malformed, unknown, tampered with, expired, swept — the caller does the
    same thing about all of them, and a stranger must not be able to tell which
    it was. `sessions.get` already collapses them into `None`; this keeps the
    collapse intact all the way out to the wire.
    """
    return security.Refused(
        "no session — start one first",
        code="no_session",
        status=401,
        detail="POST /api/session hands out a vault; nothing else needs an account",
    )
