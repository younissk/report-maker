"""Who is holding a vault, and for how long.

The web build has no accounts and no database. A stranger lands on the site, and
a moment later they are editing a real vault — so the only thing the server has
to remember is which browser owns which folder on disk, and when to take it away
again. That is the whole of this module:

    RM_WEB_ROOT/
      sessions/<session-id>/
        vault/          a real vault — report-maker.toml lives here
        session.json    {id, created, last_seen, mode, repo?, branch?, quota}

Nothing here knows what a vault contains. Seeding one is `report-maker init`
followed by `report-maker new`, spawned exactly as a terminal would spawn them,
for the same reason `app/` does it: the engine is the single answer to "what does
this vault contain", and a second implementation is the thing that drifts.

## The id is the whole of the authentication

There is no password to go with it, so the id *is* the credential, and every rule
below follows from that one fact.

`secrets.token_urlsafe(32)` — 256 bits, which is not guessable and is not meant
to be memorable. It travels in an `HttpOnly`, `SameSite=Lax` cookie and nowhere
else: never in a URL (URLs reach the Referer header, the browser history, the
proxy log and the screenshot somebody pastes into a bug report), and never in a
server log. `Session` therefore declares `id` and `token` with `repr=False`, so
an idle `print(session)` in some future handler cannot leak either — the failure
this guards against is not a decision anybody would make, it is a line somebody
adds while debugging and forgets.

For the log line that *should* exist, `Session.label` is a short non-secret handle
minted alongside the id. It identifies a session in a trace without being able to
open one.

A lookup compares the id with `secrets.compare_digest`. Against a network timing
attack on a 256-bit secret that is close to theatre, but it also closes a real and
much duller hole: macOS ships a case-insensitive filesystem, so `sessions/aB…`
and `sessions/Ab…` are the *same directory*, and a plain "does the folder exist"
check would hand a session to an id that is not the one it was issued for. The
comparison is against the id recorded inside `session.json`, which is exact.

An unknown id, a malformed id, an expired one and one that never existed all
return `None` from `get`. They are one case as far as a caller is concerned, and
the HTTP layer owes them one 401 shape — a server that distinguishes "expired"
from "no such session" is a server that confirms guesses.

## The seeded vault is red on purpose

A brand-new scaffold does not pass `check`, and this build does not hide that.
The starter ships invented cover KPIs and a citation to `example.com`; `E012`
exists precisely so a half-written report cannot reach a branded PDF carrying a
fabricated citation. So the first thing a visitor sees is a report with a page of
findings against it, and `Session.starter_findings` is the flag that lets the UI
say why in one line (`STARTER_EXPLAINER`).

That is not a wart to paper over. It is the product's argument, delivered before
anybody has read a word of documentation, and it costs one subprocess to get.
Seeding must never reach for `status: "draft"` or `--warn-only` to make the new
vault look clean: that would trade the demonstration for a lie about the report.

The flag is *measured*, not assumed — `create` reads `check --json` and records
what the engine actually said. If a future engine stops flagging the starter, the
flag goes false and the explainer stops appearing, rather than the UI insisting
on findings that are no longer there.

## What is stored, and what is not

`quota_used` holds only what cannot be recomputed: the timestamps of recent engine
commands, for the rolling 200-an-hour window. How many reports a session has and
how much disk it is using are questions with real answers on disk — `list --json`
and a directory walk — and a counter kept alongside them would be a second answer
that can be wrong.

`to_json` is the browser-facing view and it deliberately drops three fields the
spec's route sketch listed: the session id, the vault path (which contains the
id, and would hand the token straight to JavaScript, defeating `HttpOnly` in the
same breath as setting it) and any GitHub token. See `to_json`.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# ── the shape of the store ───────────────────────────────────────────────────

SESSIONS_DIRNAME = "sessions"
VAULT_DIRNAME = "vault"
RECORD_NAME = "session.json"

MODES = ("try", "github")

# Session directories hold a stranger's work on what may be a shared host, so
# they are the owner's business and nobody else's.
DIR_MODE = 0o700

TTL_HOURS = 24.0

# The id as it is allowed to look before it is permitted anywhere near a path.
# `token_urlsafe(32)` produces 43 characters from this alphabet; the range is
# wide enough to survive a change of length and narrow enough that no id can
# ever contain a separator, a dot, or anything else that walks out of the store.
_WELL_FORMED = re.compile(r"\A[A-Za-z0-9_-]{22,128}\Z")

# ── quotas the record has to carry ───────────────────────────────────────────
#
# The limits themselves belong to whatever enforces them; what lives here is the
# one piece of state enforcement cannot reconstruct from the filesystem.

COMMAND_TIMEOUT_SECONDS = 60.0
COMMANDS_PER_HOUR = 200
DISK_QUOTA_BYTES = 50 * 1024 * 1024

# ── the seed ─────────────────────────────────────────────────────────────────

SEED_TITLE = "Your first report — edit me"

STARTER_EXPLAINER = (
    "This report is red on purpose. It is still the starter's example text — "
    "invented cover numbers and a citation to example.com — and the citation "
    "rule refuses to let a fabrication build. Replace them and the findings go."
)


class SessionError(RuntimeError):
    """A session could not be made or kept. Never raised for a bad id."""


# ── the engine, spawned ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Run:
    """One engine invocation, as it finished."""

    code: int
    stdout: str
    stderr: str
    command: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    def complaint(self) -> str:
        """What to put in front of a person when this run failed."""
        return _why(self)


# A runner is anything with `report-maker`'s shape: a vault, an argv, a deadline,
# and something carrying `code` / `stdout` / `stderr` back. `web.server.engine`'s
# `run` satisfies it as written, which is why `_default` can just call it.
Runner = Callable[[Path, Sequence[str], float], Run]


def _why(done: Run) -> str:
    """What to put in front of a person when a run failed.

    Duck-typed on purpose: the shared bridge's `Run` carries more fields than
    this module's does, and a seed that broke because the two dataclasses are
    not the same class would be an absurd way to fail.
    """
    return (
        getattr(done, "stderr", "") or getattr(done, "stdout", "") or f"exit {done.code}"
    ).strip()


# Environment variables the engine has no business seeing. Typst cannot read the
# environment and cannot reach the shell, which is the sandbox this build leans
# on — but a child process that never receives the OAuth secret cannot leak it
# through any hole anybody finds later, and stripping it costs one dict
# comprehension.
_STRIPPED_ENV_PREFIXES = ("RM_WEB_", "GITHUB_")


def _repo_root() -> Path:
    """The checkout this file was installed from: web/server/… → …/."""
    return Path(__file__).resolve().parents[2]


def _locate() -> list[str] | None:
    """The argv prefix that runs the CLI, in the order a user expects to win.

    The same search `app/src/main/engine.ts` does, for the same reason: the engine
    is not in the vault and the vault is not in the server, so one installation
    serves whatever this process is asked to open.
    """
    explicit = os.environ.get("REPORT_MAKER_BIN")
    if explicit:
        return [explicit]

    python = os.environ.get("PYTHON", "python3")
    roots = [os.environ.get("REPORT_MAKER_ROOT"), str(_repo_root())]
    for root in roots:
        if not root:
            continue
        script = Path(root) / "bin" / "report-maker"
        if script.exists():
            return [python, str(script)]

    installed = shutil.which("report-maker")
    return [installed] if installed else None


def _spawn(vault: Path, args: Sequence[str], timeout: float) -> Run:
    """The fallback runner: one `report-maker -C <vault> …`, and its result.

    Deliberately the narrowest thing that can seed a vault, so this module is
    usable and testable on its own — a store under a scratch directory, with no
    server started and no root declared anywhere.

    **Every argv that reaches here is a constant of this module.** It carries
    none of `web/server/engine.py`'s argument guard, so nothing shaped by a
    request may be routed through it; anything that is goes through the shared
    bridge, which is what `_default` reaches for first.
    """
    prefix = _locate()
    if prefix is None:
        raise SessionError(
            "report-maker was not found. Set REPORT_MAKER_ROOT to the engine "
            "checkout, or put report-maker on PATH."
        )

    argv = [*prefix, "-C", str(vault), *args]
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_STRIPPED_ENV_PREFIXES)
    }
    try:
        done = subprocess.run(  # noqa: S603 — argv, no shell, no user-built words
            argv,
            cwd=str(vault),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return Run(124, "", f"timed out after {timeout:.0f}s", " ".join(args))
    return Run(done.returncode, done.stdout, done.stderr, " ".join(args))


def _default(vault: Path, args: Sequence[str], timeout: float) -> Run:
    """The shared engine bridge when it can answer, the local spawn otherwise.

    There must be exactly one place in `web/` that knows how to spawn the CLI,
    the way `app/` has exactly one — so this prefers `web.server.engine.run`
    rather than waiting to be told about it. A wiring step somebody forgets is a
    second, unguarded spawn path in a server that runs strangers' input, and the
    way to not have one is to not depend on the step.

    It falls back only when the bridge genuinely cannot answer: it is not there
    yet, or no sessions root has been declared and none can be inferred — which
    is the case in a unit test with a scratch store, and is not a case that
    occurs in a running server.
    """
    try:
        from . import engine  # noqa: PLC0415 — optional by design; see above
    except ImportError:
        return _spawn(vault, args, timeout)

    try:
        root = engine.sessions_root()
    except Exception:  # noqa: BLE001 — any refusal means "cannot answer"
        return _spawn(vault, args, timeout)
    if root not in vault.resolve().parents:
        return _spawn(vault, args, timeout)

    return engine.run(vault, args, timeout)


_run: Runner = _default


def use_engine(runner: Runner) -> None:
    """Override what this module spawns with.

    The server does not need to call this — `_default` already finds the shared
    bridge. It exists for a test that wants to watch, or refuse, what seeding
    asks for.
    """
    global _run
    _run = runner


# ── the record ───────────────────────────────────────────────────────────────


@dataclass
class Quota:
    """What a session has spent that the disk cannot be asked about.

    Reports and bytes are not here on purpose: `list --json` and a directory walk
    already answer those, and a counter beside a fact is a second answer that
    drifts from it.
    """

    commands: list[float] = field(default_factory=list)

    def record(self, now: float | None = None) -> None:
        self.commands.append(time.time() if now is None else now)

    def since(self, cutoff: float) -> int:
        """How many engine commands ran after `cutoff`."""
        return sum(1 for stamp in self.commands if stamp >= cutoff)

    def prune(self, cutoff: float) -> None:
        """Forget everything older than the window. Called before every check,
        so the list stays the size of the limit rather than the size of the
        session's life."""
        self.commands = [stamp for stamp in self.commands if stamp >= cutoff]

    def to_json(self, now: float | None = None) -> dict[str, object]:
        now = time.time() if now is None else now
        # A count, not the list: the browser is being told how much of the hour's
        # allowance is gone, and the timestamps are nobody else's business.
        return {
            "commandsThisHour": self.since(now - 3600),
            "commandsPerHour": COMMANDS_PER_HOUR,
            "diskQuotaBytes": DISK_QUOTA_BYTES,
            "commandTimeoutSeconds": COMMAND_TIMEOUT_SECONDS,
        }


@dataclass
class Session:
    """One browser, one vault, until it expires.

    `id`, `vault` and `token` carry `repr=False`: the credential must not appear
    in a traceback, a log line or a debugger's default rendering of this object.
    `vault` is on that list because the path *is* the id with two directory names
    around it — the first draft left it in and the test that prints a session
    caught it, which is the whole reason that test exists. `label` is what a log
    line names a session by.
    """

    id: str = field(repr=False)
    label: str
    created: float
    last_seen: float
    mode: str
    vault: Path = field(repr=False)
    repo: str | None = None
    branch: str | None = None
    quota_used: Quota = field(default_factory=Quota)
    starter_findings: bool = False
    token: str | None = field(default=None, repr=False)

    # ── where this session lives ──

    @property
    def dir(self) -> Path:
        return self.vault.parent

    @property
    def record(self) -> Path:
        return self.dir / RECORD_NAME

    def expired(self, ttl_hours: float = TTL_HOURS, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now - self.last_seen > ttl_hours * 3600

    # ── serialisation ──

    def to_record(self) -> dict[str, object]:
        """The on-disk form. This one keeps everything, secrets included: it sits
        inside a 0o700 directory on the server and is never served."""
        return {
            "id": self.id,
            "label": self.label,
            "created": self.created,
            "last_seen": self.last_seen,
            "mode": self.mode,
            "repo": self.repo,
            "branch": self.branch,
            "quota": {"commands": list(self.quota_used.commands)},
            "starter_findings": self.starter_findings,
            "token": self.token,
        }

    def to_json(self) -> dict[str, object]:
        """The browser-facing form, and what it refuses to say.

        Three fields the API sketch listed are missing, and their absence is the
        point:

        `id` and `vault` — the vault path *contains* the session id, so returning
        either one hands the credential to JavaScript and makes `HttpOnly` a
        decoration. The cookie is how a browser proves which session it holds;
        200-versus-401 on this route is how it finds out whether it holds one.
        Nothing in the UI needs the id, and nothing in the UI needs a server
        filesystem path — every route is addressed by report id.

        `token` — a GitHub token lives in the session record and stops there.
        """
        return {
            "label": self.label,
            "mode": self.mode,
            "created": self.created,
            "lastSeen": self.last_seen,
            "repo": self.repo,
            "branch": self.branch,
            "starterFindings": self.starter_findings,
            "starterExplainer": STARTER_EXPLAINER if self.starter_findings else None,
            "quota": self.quota_used.to_json(),
        }


# ── paths, contained ─────────────────────────────────────────────────────────


def _store(root: Path | str) -> Path:
    return Path(root).resolve() / SESSIONS_DIRNAME


def _session_dir(root: Path | str, sid: str) -> Path | None:
    """Where `sid` would live, or None if it has no business being a path.

    The mirror of `app/src/main/tree.ts`'s `within`, applied one level higher: a
    session id arrives from a cookie, which is to say from a stranger, and it is
    used as a directory name. Two gates, because either alone has a hole. The
    shape check rejects a separator or a dot segment before the string is ever
    joined; resolving and re-testing containment catches the case the first gate
    cannot see, a `sessions/<id>` that is a symlink pointing somewhere else.
    """
    if not isinstance(sid, str) or not _WELL_FORMED.match(sid):
        return None
    store = _store(root)
    target = (store / sid).resolve()
    if target.parent != store:
        return None
    return target


# ── reading and writing the record ───────────────────────────────────────────

# Sessions are touched from request threads, and a record is small enough that
# one lock over all of them is cheaper than being clever. The write is atomic so
# a crash between two handlers cannot leave a half-written record behind.
_lock = threading.Lock()


def _save(session: Session) -> None:
    with _lock:
        tmp = session.record.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(session.to_record(), indent=2), encoding="utf-8")
        os.replace(tmp, session.record)


def _load(directory: Path) -> Session | None:
    """The session in that directory, or None if there is not one there.

    Never raises. A directory with no record, a truncated record, or a record
    from an older build with a field this one does not understand is not an
    error to report — it is a session that cannot be opened, which is the same
    answer as a session that does not exist.
    """
    try:
        raw = json.loads((directory / RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None

    sid = raw.get("id")
    mode = raw.get("mode")
    if not isinstance(sid, str) or mode not in MODES:
        return None

    quota = raw.get("quota") or {}
    stamps = quota.get("commands") if isinstance(quota, dict) else None
    try:
        return Session(
            id=sid,
            label=str(raw.get("label") or ""),
            created=float(raw.get("created", 0.0)),
            last_seen=float(raw.get("last_seen", 0.0)),
            mode=mode,
            vault=directory / VAULT_DIRNAME,
            repo=raw.get("repo"),
            branch=raw.get("branch"),
            quota_used=Quota(
                commands=[float(s) for s in stamps] if isinstance(stamps, list) else []
            ),
            starter_findings=bool(raw.get("starter_findings")),
            token=raw.get("token") if isinstance(raw.get("token"), str) else None,
        )
    except (TypeError, ValueError):
        return None


# ── the lifecycle ────────────────────────────────────────────────────────────


def create(root: Path | str, mode: str = "try") -> Session:
    """A new session, with a vault already in it.

    The record is written *before* the vault is seeded, so a process that dies
    halfway leaves a directory the sweeper knows how to remove rather than an
    orphan nothing owns. If seeding fails the whole directory goes and the caller
    gets the engine's own complaint — a session whose vault is half-built is
    worse than no session, because the failure surfaces later and somewhere else.
    """
    if mode not in MODES:
        raise SessionError(f"unknown session mode: {mode!r}")

    store = _store(root)
    store.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)

    sid = secrets.token_urlsafe(32)
    directory = store / sid
    directory.mkdir(mode=DIR_MODE)
    vault = directory / VAULT_DIRNAME
    vault.mkdir(mode=DIR_MODE)

    now = time.time()
    session = Session(
        id=sid,
        label=secrets.token_urlsafe(6),
        created=now,
        last_seen=now,
        mode=mode,
        vault=vault,
    )
    _save(session)

    if mode == "try":
        try:
            session.starter_findings = _seed(session)
        except Exception:
            # Whatever went wrong — a refused argv, a missing engine, a full
            # disk — the directory goes with it. Broad on purpose: the thing
            # that must not survive is the half-built vault, and enumerating the
            # exceptions the engine bridge may grow is how one gets missed.
            shutil.rmtree(directory, ignore_errors=True)
            raise
        _save(session)

    return session


def _seed(session: Session) -> bool:
    """Make the vault, put one report in it, and find out how red it is.

    Two engine commands and a question, in that order. The title invites the
    edit that the report needs anyway: everything the starter asserts is somebody
    else's invention, and `check` is about to say so line by line.

    Returns whether the engine reported any E012 against it — measured rather
    than assumed, so the UI's explainer can never outlive the thing it explains.
    """
    for args in (["init"], ["new", SEED_TITLE]):
        done = _run(session.vault, args, COMMAND_TIMEOUT_SECONDS)
        if done.code != 0:
            raise SessionError(f"could not seed a vault: {_why(done)}")
    session.quota_used.record()
    session.quota_used.record()

    # `check` exits 1 when it finds errors, which here is the expected answer
    # rather than a failure — the findings are the payload, and stdout carries
    # them whatever the exit code says.
    done = _run(session.vault, ["check", "--json"], COMMAND_TIMEOUT_SECONDS)
    session.quota_used.record()
    try:
        findings = json.loads(done.stdout).get("findings", [])
    except (ValueError, AttributeError):
        return False
    return any(
        f.get("code") == "E012" and f.get("level") == "error"
        for f in findings
        if isinstance(f, dict)
    )


def get(root: Path | str, sid: str, ttl_hours: float = TTL_HOURS) -> Session | None:
    """The session that id opens, or None.

    None covers every way this can fail to produce one — malformed, unknown,
    unreadable, expired — because a caller has one thing to do about all of them
    and the answer a stranger gets must not tell them which it was. `compare_digest`
    is the last gate: the directory may have been found case-insensitively, and
    the id recorded inside it is the only exact statement of what was issued.

    An expired session is reported gone but not removed here. Deleting a vault is
    the sweeper's job, and a read that quietly destroys a folder is a surprise
    waiting for whoever debugs it next.
    """
    directory = _session_dir(root, sid)
    if directory is None:
        return None
    session = _load(directory)
    if session is None:
        return None
    if not secrets.compare_digest(session.id, sid):
        return None
    if session.expired(ttl_hours):
        return None
    return session


def touch(session: Session) -> None:
    """Push the expiry out. Called on every authenticated request, which is what
    makes the TTL an idle timeout rather than a hard cap on a working day."""
    session.last_seen = time.time()
    _save(session)


def destroy(root: Path | str, sid: str) -> None:
    """Remove a session and everything it was holding.

    Silent on an id that opens nothing: this is what `DELETE /session` calls, and
    a delete that reports whether the thing existed is a delete that answers
    questions about other people's sessions. `rmtree` unlinks symlinks rather
    than following them, so a link planted inside the vault deletes the link and
    not its target.
    """
    directory = _session_dir(root, sid)
    if directory is None:
        return
    session = _load(directory)
    if session is None or not secrets.compare_digest(session.id, sid):
        # An unreadable record is still this session's directory, and leaving it
        # behind would be a disk leak with nobody left to claim it.
        if session is None and directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
        return
    shutil.rmtree(directory, ignore_errors=True)


def sweep(root: Path | str, ttl_hours: float = TTL_HOURS) -> int:
    """Remove every session past its TTL. Returns how many went.

    Never raises. It runs on a background thread where an exception is a thread
    that quietly stops sweeping, and a server that stops reclaiming disk fails
    hours later in a way that points at nothing.

    A directory with no readable record is swept on its own mtime. It is not a
    session — nothing can open it — and keeping it forever is how a long-running
    server fills a disk with folders that belong to nobody.
    """
    store = _store(root)
    try:
        entries = list(store.iterdir())
    except OSError:
        return 0

    now = time.time()
    gone = 0
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
            session = _load(entry)
            if session is not None:
                stale = session.expired(ttl_hours, now)
            else:
                stale = now - entry.stat().st_mtime > ttl_hours * 3600
            if stale:
                shutil.rmtree(entry, ignore_errors=True)
                gone += 1
        except OSError:
            continue
    return gone


def sweeper(
    root: Path | str,
    ttl_hours: float = TTL_HOURS,
    interval_seconds: float = 600.0,
    stop: threading.Event | None = None,
) -> threading.Thread:
    """The sweep, on a daemon thread, started.

    Daemon because a sweep is never the reason to keep a process alive; the
    `stop` event is there so a test can shut it down without waiting an interval.
    """
    stop = stop or threading.Event()

    def loop() -> None:
        while not stop.wait(interval_seconds):
            sweep(root, ttl_hours)

    thread = threading.Thread(target=loop, name="rm-web-sweeper", daemon=True)
    thread.start()
    return thread


# ── the cookie ───────────────────────────────────────────────────────────────

COOKIE_NAME = "rm_session"


def cookie_for(
    session: Session,
    *,
    secure: bool | None = None,
    max_age: float = TTL_HOURS * 3600,
) -> str:
    """The `Set-Cookie` value that hands this session to a browser.

    `HttpOnly` because the id is the credential and no script on the page has any
    use for it. `SameSite=Lax` because every state-changing route here is a POST,
    PUT or DELETE and Lax withholds the cookie from all of them cross-site, while
    still letting somebody follow a link to the app and arrive logged in.
    `Path=/` because the API and the page it serves share an origin.

    `Secure` is on when the connection is TLS. It cannot be inferred from inside
    this process when a proxy terminates TLS in front of it, so it is passed in;
    `RM_WEB_SECURE_COOKIE` is the answer for a deployment that always is. The
    default is off, because the default bind is 127.0.0.1 and a `Secure` cookie
    over plain http is simply dropped — a dev server nobody can log into.
    """
    if secure is None:
        secure = os.environ.get("RM_WEB_SECURE_COOKIE", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    parts = [
        f"{COOKIE_NAME}={session.id}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={int(max_age)}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie(*, secure: bool | None = None) -> str:
    """The `Set-Cookie` that takes it back, for `DELETE /session`.

    The attributes have to match the ones it was set with or the browser keeps a
    second cookie of the same name and the session appears to survive its own
    deletion.
    """
    if secure is None:
        secure = os.environ.get("RM_WEB_SECURE_COOKIE", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    parts = [f"{COOKIE_NAME}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def parse_cookie(header: str | None) -> str | None:
    """The session id out of a `Cookie:` header, or None.

    Split by hand rather than through `http.cookies`, which unquotes values and
    has its own opinions about what is legal — neither of which this wants. The
    id is returned only if it is already well formed, so a crafted value is
    rejected here rather than one layer further in, and this function cannot
    raise on any input a stranger can send.
    """
    if not header:
        return None
    for crumb in header.split(";"):
        name, _, value = crumb.partition("=")
        if name.strip() != COOKIE_NAME:
            continue
        candidate = value.strip().strip('"')
        if _WELL_FORMED.match(candidate):
            return candidate
    return None


# ── the disk a session is using ──────────────────────────────────────────────


def disk_bytes(session: Session) -> int:
    """What this session's vault occupies, measured rather than remembered.

    Symlinks are counted as links and never followed: a link is a few bytes, and
    walking through one would both mis-count and read outside the vault.
    """

    def walk(directory: Path) -> int:
        total = 0
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return 0
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    total += walk(Path(entry.path))
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
        return total

    return walk(session.vault)
