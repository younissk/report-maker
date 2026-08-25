"""The bridge to the engine.

The web layer runs no logic of its own. Every question about a vault — what
reports exist, whether the citation rule holds, what a build produced — is
answered by shelling out to `report-maker`, exactly as `app/src/main/engine.ts`
does for the desktop shell and exactly as a terminal would:

    HTTP request  ──▶  route  ──spawn──▶  report-maker -C <session vault> …

That is the whole reason the engine is headless, and it is what keeps the web
version from becoming a second, drifting answer to "what does this vault
contain". Nothing in this module parses a report, evaluates a rule, or computes
something a CLI command already prints.

What is different here, and the reason this file is three times the size of the
desktop one, is *who* is on the other end. The app spawns commands on behalf of
the person sitting at the machine, inside a vault they already own. This server
spawns them on behalf of a stranger, over the network, inside a vault the server
handed out a minute ago. So every call goes through four refusals before a
process is created:

    argv, never a string       a report id is user text, and a shell would read
                               `; rm -rf ~` in it as punctuation
    the vault is a session      `-C` is ours to fill in and no caller may
                               override it — one crafted argument would
                               otherwise point the engine at `/`
    a denied command stays denied   `template install` clones arbitrary git
                               repositories and `diagrams` drives a headless
                               Chrome; the denial lives here rather than in a
                               route, because a route can forget
    nothing runs unbounded     every call has a deadline and an output budget,
                               and both are enforced by killing the process
                               *group* — typst, git and node are grandchildren,
                               and a child killed alone leaves them running

The interpreter trap, and why it is not one. `bin/report-maker` begins with
`#!/usr/bin/env python3`, and a server whose first `python3` is 3.9 has no
`tomllib` for the engine to import. The script self-heals: it re-execs into a
newer interpreter before importing anything. Verified on this machine from a
minimal environment —

    env -i PATH=/usr/bin:/bin ./bin/report-maker --version   →  report-maker 0.1.0
    env -i PATH=/usr/bin:/bin ./bin/report-maker -C … list --json  →  real JSON

— with `python3 -V` on that same PATH reporting 3.9.6. So this module execs the
script directly and lets it sort its own interpreter out. The one case the
shebang cannot cover is a checkout without the execute bit, and that falls back
to running the script under *this* server's interpreter, which is 3.11+ by the
same requirement that lets this file exist.

The residual diagram hole, stated plainly. `diagrams` is refused below, but
`all` calls `diagrams.build()` itself, so a session vault containing a `.mmd`
file drives mermaid from inside a build that this module has no way to see into.
The engine degrades correctly when node is absent — `ensure_cli` raises, `all`
prints "skipped: …" and carries on — so the deployment answer today is to leave
npm off the server's PATH unless `RM_WEB_DIAGRAMS=1`. The real answer is an
engine flag, and engine/ is not ours to edit; it is a `needs` item.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from json import JSONDecodeError
from json import loads as json_loads
from pathlib import Path
from typing import Any

# Spec requirement 7: sixty seconds of wall clock per engine command. A build
# that needs longer than this is a build the writer should be told about, not
# one the server should keep a worker on.
DEFAULT_TIMEOUT = 60.0

# How long a killed process group gets between SIGTERM and SIGKILL. typst may be
# midway through writing a PDF; five seconds lets it stop cleanly, and the worst
# case a caller must plan for is therefore `timeout + GRACE`.
GRACE = 5.0

# The most output one command may produce before it is killed. The largest
# legitimate stdout in this engine is a `--json` listing over a whole vault, and
# a session vault is capped at 50 MB of disk — so this is far above anything
# real and far below a memory problem on a shared server.
MAX_OUTPUT = 8 * 1024 * 1024

# Both markers are load-bearing: routes match on them to choose 504 over 500,
# and the tests match on them to prove a kill actually happened.
TIMEOUT_MARKER = "report-maker: timed out"
TRUNCATED_MARKER = "report-maker: output truncated"

MISSING = (
    "report-maker was not found. Set RM_WEB_ENGINE to the engine's "
    "bin/report-maker, or put report-maker on PATH."
)


class EngineError(RuntimeError):
    """A command that did not produce an answer.

    The message is the engine's own stderr wherever there is one. That is not
    politeness — the engine's refusals are written to be read by the person who
    triggered them ("never --force: rewriting a published history is…"), and a
    web layer that replaced them with "command failed" would be throwing away
    the only part of the response worth showing.
    """

    def __init__(self, message: str, run: "Run | None" = None) -> None:
        super().__init__(message)
        self.run = run


class EngineMissing(EngineError):
    """No `report-maker` anywhere. A deployment fault, not a request fault."""


class Refused(EngineError):
    """An argv this bridge will not spawn.

    Separate from `EngineError` because it is answered differently: a refusal is
    a 403 with a reason a human can act on, never a 500. Nothing was run.
    """


# ── what a run was ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Run:
    """One engine invocation, finished.

    `command` is a display string and nothing else. It is `shlex.join`ed so a
    log line can be pasted into a terminal by a developer, and it is never fed
    back to a shell by this module or any caller — `argv` is the truth, and it
    is kept alongside precisely so that nobody has to re-split `command` to
    recover it.

    `timed_out` is not derivable from `code`. A command killed at the deadline
    exits with a signal, and so does a command that segfaulted on its own; only
    the caller of `wait()` knows which happened, so it is recorded rather than
    guessed.
    """

    code: int
    stdout: str
    stderr: str
    command: str
    duration: float
    argv: tuple[str, ...] = ()
    timed_out: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0

    def as_dict(self) -> dict[str, Any]:
        """The shape a route returns. `argv` is deliberately absent: it carries
        absolute server paths, and a response body is not a place for those."""
        return {
            "ok": self.ok,
            "code": self.code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration": round(self.duration, 3),
            "timed_out": self.timed_out,
        }

    def message(self) -> str:
        """The most useful line to show a human: the engine's own words."""
        return (self.stderr or self.stdout or f"exit {self.code}").strip()


# ── the sessions root, and why nothing runs without it ───────────────────────
#
# `-C` decides which folder the engine treats as a vault, and this server's
# whole containment story rests on that folder being a session's own. So the
# root is state this module keeps, and a vault argument is checked against it on
# every single call rather than trusted because a route already checked.
#
# It fails closed. A server that has not declared its sessions root cannot spawn
# anything, which is a startup error somebody notices in the first minute —
# unlike the alternative, a default that quietly permits `-C /`.

_root: Path | None = None
_root_lock = threading.Lock()


def set_sessions_root(path: str | os.PathLike[str]) -> Path:
    """Declare where session vaults live. Called once, at startup, by whichever
    module owns the store. Idempotent, so a re-entrant start is harmless."""
    global _root
    resolved = Path(path).resolve()
    with _root_lock:
        _root = resolved
    return resolved


def sessions_root() -> Path:
    """The declared root, or the one `RM_WEB_ROOT` implies.

    Reading the environment here is a convenience for `python3 -m web` and for
    tests, not a second source of truth: `set_sessions_root` always wins, and
    with neither the answer is a refusal.
    """
    with _root_lock:
        if _root is not None:
            return _root
    env_root = os.environ.get("RM_WEB_ROOT")
    if env_root:
        return (Path(env_root) / "sessions").resolve()
    raise EngineError(
        "no sessions root: call engine.set_sessions_root(...) at startup, or "
        "set RM_WEB_ROOT. Refusing to run the engine without one."
    )


def _vault(vault: str | os.PathLike[str]) -> Path:
    """The vault this command may touch, or a refusal.

    Resolve *first*, then compare. That order is the whole point: `resolve()`
    follows every symlink in the path, so a symlink planted inside a session
    that points at `/etc` is judged by where it lands rather than by where it
    sits. Mirrors `app/src/main/tree.ts`'s `within`, hardened for a caller who
    is not the person at the keyboard.

    The root itself is not a vault. `sessions/` holds session folders, and a
    `-C` pointing at it would let one request build another session's reports.
    """
    root = sessions_root()
    target = Path(vault).resolve()
    if target == root or root not in target.parents:
        raise Refused(
            f"refusing -C {target}: it is not inside the sessions root {root}"
        )
    if not target.is_dir():
        raise Refused(f"refusing -C {target}: not a directory")
    return target


# ── the denylist ─────────────────────────────────────────────────────────────
#
# Two engine commands do things a public server must not do on a stranger's
# word, and one flag would undo the containment above. All three are refused
# here rather than in the routes that call them, because a denial that lives in
# a route is a denial the next route forgets. Nothing gets spawned before this
# runs, so a refusal costs no process.

DIAGRAMS_ENV = "RM_WEB_DIAGRAMS"

_TEMPLATE_NETWORK = {
    # Clones an arbitrary git repository named in the request. Spec requirement 5.
    "install": (
        "`template install` is disabled in web mode: it clones an arbitrary git "
        "repository into the vault. Install designs on a machine you own."
    ),
    # The same mechanism, one step removed — it re-fetches the URLs recorded in
    # the vault, and in GitHub mode those records arrived from the user's repo.
    # Spec requirement 5 names `install`; `update` is the identical hole with a
    # different door, so it is shut too.
    "update": (
        "`template update` is disabled in web mode: it re-fetches designs from "
        "git repositories recorded in the vault."
    ),
}

_DIAGRAMS_DENIED = (
    "diagrams are off in web mode: rendering one drives a headless Chrome per "
    f"diagram. Set {DIAGRAMS_ENV}=1 on the server to enable them."
)


def diagrams_enabled() -> bool:
    """Spec requirement 6: off unless the operator says otherwise, read live so
    a test — or an operator — can flip it without a restart."""
    return os.environ.get(DIAGRAMS_ENV, "") not in ("", "0", "false", "no")


def _head(args: Sequence[str]) -> list[str]:
    """The positional tokens that name the command, ignoring what follows them.

    `report-maker template install <url>` is a command; a report id that happens
    to read `template` is not. Stopping at the first flag keeps the match on the
    subcommand shape rather than on any token anywhere in the line.
    """
    head: list[str] = []
    for arg in args:
        if arg.startswith("-"):
            break
        head.append(arg)
    return head


def guard(args: Sequence[str]) -> None:
    """Refuse an argv this bridge will not spawn. Raises `Refused`, or returns.

    Public because it is worth calling early — a route that knows a request is
    going to be refused can say so without building a session vault first — and
    because a guard nobody can test in isolation is a guard nobody trusts.
    """
    for arg in args:
        if not isinstance(arg, str):
            raise Refused(f"refusing a non-string argument: {arg!r}")
        if "\x00" in arg:
            raise Refused("refusing an argument containing a NUL byte")
        # `-C`, `--vault`, `-C/tmp`, `--vault=/tmp`: every spelling of "work in
        # a different folder". This module fills `-C` in and nobody overrides it.
        if arg == "-C" or arg.startswith("-C") or arg == "--vault" or arg.startswith("--vault="):
            raise Refused(
                f"refusing {arg!r}: the vault is set by the session, not by the request"
            )

    head = _head(args)
    if not head:
        return

    if head[0] == "template" and len(head) > 1 and head[1] in _TEMPLATE_NETWORK:
        raise Refused(_TEMPLATE_NETWORK[head[1]])

    if head[0] == "diagrams" and not diagrams_enabled():
        # `diagrams --prepare` renders nothing and would be safe to allow, but a
        # security boundary with a carve-out in it is a boundary a reader has to
        # reason about. The whole subcommand stays shut.
        raise Refused(_DIAGRAMS_DENIED)


# ── finding the engine ───────────────────────────────────────────────────────

ENGINE_ENV = "RM_WEB_ENGINE"

_located: Path | None = None
_located_lock = threading.Lock()


def locate(refresh: bool = False) -> Path:
    """Where `report-maker` is, in the order a deployment expects to win.

    An explicit override first, because an operator who set one has already
    answered this question. Then the repository this file was installed from —
    `web/` is a sibling of `engine/`, so a checkout needs no configuration at
    all. Then PATH, for an install that put the CLI somewhere standard.

    The answer is cached and announced once on stderr. Which binary is running
    is the first thing to check when a deployment behaves unlike a laptop, and a
    log line at startup is cheaper than working it out from a stack trace later.
    """
    global _located
    with _located_lock:
        if _located is not None and not refresh:
            return _located

        override = os.environ.get(ENGINE_ENV)
        if override:
            path = Path(override).expanduser().resolve()
            # A directory is accepted because `RM_WEB_ENGINE=/srv/report-maker`
            # is what somebody will type; the script inside is what we want.
            if path.is_dir():
                path = path / "bin" / "report-maker"
            if not path.is_file():
                # An override that silently does not apply is the worst outcome
                # available: the server runs, on the wrong engine, and says so
                # nowhere. Fail loudly instead.
                raise EngineMissing(f"{ENGINE_ENV}={override} is not a report-maker script")
            found = path
        else:
            repo = Path(__file__).resolve().parents[2] / "bin" / "report-maker"
            on_path = shutil.which("report-maker")
            if repo.is_file():
                found = repo
            elif on_path:
                found = Path(on_path)
            else:
                raise EngineMissing(MISSING)

        print(f"engine: {found}", file=sys.stderr, flush=True)
        _located = found
        return found


def describe() -> str:
    """What a health endpoint says about the installation. Never raises — "not
    found" is an answer, and a health check that 500s tells you less."""
    try:
        return str(locate())
    except EngineMissing as exc:
        return f"not found ({exc})"


def _argv(script: Path, args: Sequence[str]) -> list[str]:
    """The command line, as a list, always.

    Executed directly when the execute bit is set, so the script's own shebang
    logic runs and moves itself to a Python new enough for `tomllib`. Without
    the bit — a checkout from a zip, a volume mounted `noexec` — it is run under
    this interpreter instead, which is already 3.11+ because this file imports
    under it.
    """
    if os.access(script, os.X_OK):
        return [str(script), *args]
    return [sys.executable, str(script), *args]


# ── the environment a command runs in ────────────────────────────────────────

# Names whose values must never cross into a subprocess by accident. The engine
# needs none of them, and a `sync` that shells out to git inherits whatever is
# here — so the OAuth secret stays on this side of the boundary unless a caller
# passes it deliberately, per call, for a push that actually needs it.
_SECRET_HINTS = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "API_KEY")


def _env(extra: Mapping[str, str] | None) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        # Web-layer configuration is not the engine's business, and the GitHub
        # client secret lives in it.
        if not name.startswith("RM_WEB_")
        and not any(hint in name.upper() for hint in _SECRET_HINTS)
    }
    env.update(
        {
            # The engine prints progress from Python. Down a pipe that is block
            # buffered, which would make `stream()` deliver a whole build in one
            # lump at the end — the opposite of what it exists for.
            "PYTHONUNBUFFERED": "1",
            # Nothing on this server may ever block on a credential prompt: a
            # process waiting on stdin that will never arrive is a worker held
            # until the deadline, which is a denial of service with extra steps.
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
        }
    )
    if not diagrams_enabled():
        env["PATH"] = _without_node(env.get("PATH", ""))
    if extra:
        env.update(extra)
    return env


# The executables mermaid needs, and the only thing in the engine that wants a
# JavaScript runtime at all. `report-maker doctor` reports npm as "only needed
# for mermaid diagrams", which is the whole list.
_NODE_TOOLS = ("npm", "npx", "node")


def _without_node(path: str) -> str:
    """`PATH` with every directory that holds a node executable removed.

    Spec requirement 6 says diagrams are off unless the operator turns them on,
    and `guard` refuses `report-maker diagrams`. That is not the whole surface:
    `report-maker all` renders diagrams as its second step, so a stranger who
    writes seventeen bytes of mermaid into `diagrams/attack.mmd` and presses
    Build gets an `npm install` of a hundred and ninety packages and a headless
    Chrome per render — on a server where the banner says diagrams are off. It
    also gets past the disk quota, which refuses the next *write* and has no
    say over what a command it already approved does afterwards: measured here,
    460 MB into a vault with a 50 MB ceiling.

    The engine cannot be told to skip the step — `all` has no `--no-diagrams`,
    and `engine/` is not ours to edit — but it already knows how to do without.
    `diagrams.ensure_cli` raises `DiagramError` when npm is absent, `cmd_all`
    catches it and prints "skipped", and the build carries on to typst. So the
    honest way to say "not on this server" is to run the command on a machine
    that, as far as it can tell, has no Node on it.

    Directory granularity rather than a shim, because `PATH` is what
    `shutil.which` reads and what a `#!/usr/bin/env node` shebang reads, and
    both have to come up empty — the second is what stops a `node_modules/.bin`
    that arrived inside a cloned repository from running anyway. Nothing else
    in the engine wants node, so nothing else notices; git, typst and python
    keep their directories unless they happen to share one with npm, and a
    directory holding both is a Node distribution rather than a system bindir.
    """
    kept = []
    for entry in path.split(os.pathsep):
        if not entry:
            continue
        directory = Path(entry)
        if any((directory / tool).exists() for tool in _NODE_TOOLS):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


# ── running one ──────────────────────────────────────────────────────────────


def _terminate(proc: subprocess.Popen, hard: bool = False) -> None:
    """Kill the process *group*, not the child.

    `report-maker` is a Python script that spawns typst, git and sometimes node;
    killing the script alone orphans those, and an orphaned typst on a shared
    server is exactly the runaway the deadline exists to prevent. `Popen` is
    given `start_new_session=True`, so the child leads its own group and one
    `killpg` reaches everything it started.

    SIGTERM first unless `hard`, so a half-written PDF gets a chance to be
    cleaned up; SIGKILL after `GRACE` for anything that ignored it.
    """
    if proc.poll() is not None:
        return
    try:
        group = os.getpgid(proc.pid) if hasattr(os, "getpgid") else None
    except ProcessLookupError:
        return

    def signal_group(sig: int) -> None:
        try:
            if group is not None and hasattr(os, "killpg"):
                os.killpg(group, sig)
            else:  # pragma: no cover - Windows has no process groups here
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass

    if not hard:
        signal_group(signal.SIGTERM)
        try:
            proc.wait(timeout=GRACE)
            return
        except subprocess.TimeoutExpired:
            pass

    signal_group(signal.SIGKILL)
    try:
        proc.wait(timeout=GRACE)
    except subprocess.TimeoutExpired:  # pragma: no cover - unkillable, D state
        pass


class _Drain:
    """One pipe, read to exhaustion on a thread, with a budget.

    Threads rather than `communicate()` because the budget is the point: a
    command printing without end would otherwise be bounded only by the
    deadline, and sixty seconds of output into a list is a memory problem on a
    server that runs strangers' input. When the budget is spent the process
    group is killed immediately — there is nothing useful left to wait for.
    """

    def __init__(self, pipe, on_overflow) -> None:
        self.pipe = pipe
        self.chunks: list[bytes] = []
        self.size = 0
        self.overflowed = False
        self._on_overflow = on_overflow
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            while True:
                chunk = self.pipe.read(65536)
                if not chunk:
                    return
                if self.size < MAX_OUTPUT:
                    self.chunks.append(chunk)
                    self.size += len(chunk)
                elif not self.overflowed:
                    self.overflowed = True
                    self._on_overflow()
        except (ValueError, OSError):  # pipe closed under us
            return
        finally:
            try:
                self.pipe.close()
            except OSError:
                pass

    def text(self) -> str:
        # `errors="replace"` for the same reason gitsync uses it: a report can
        # contain any bytes at all, and a decode error here would turn somebody's
        # filename into a 500.
        return b"".join(self.chunks).decode("utf-8", errors="replace")


def _spawn(
    args: Sequence[str],
    cwd: str | os.PathLike[str],
    timeout: float,
    env: Mapping[str, str] | None,
) -> Run:
    """Spawn a final argv and wait for it, bounded in time and in output.

    Private, and it guards nothing: by the time a caller reaches here the argv
    has had `-C` prepended, and `guard` refuses `-C` on principle. The guard
    belongs to the *caller's* arguments, which is where the stranger's text is,
    and running it twice over two different lists would only teach the reader
    that it means two different things.
    """
    started = time.monotonic()
    try:
        script = locate()
    except EngineMissing as exc:
        # 127 is what a shell reports for "command not found", and a Run is what
        # every caller here is written to handle. A missing engine is a
        # deployment fault, but it should still arrive as a response.
        return Run(127, "", str(exc), "report-maker", time.monotonic() - started, ())

    full = _argv(script, args)
    display = shlex.join(full)

    try:
        proc = subprocess.Popen(
            full,
            cwd=str(cwd),
            env=_env(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,  # nothing here is interactive, ever
            start_new_session=True,  # its own process group — see _terminate
        )
    except OSError as exc:
        return Run(127, "", f"{exc}", display, time.monotonic() - started, tuple(full))

    over = threading.Event()

    def on_overflow() -> None:
        over.set()
        _terminate(proc, hard=True)

    out = _Drain(proc.stdout, on_overflow)
    err = _Drain(proc.stderr, on_overflow)

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate(proc)
    finally:
        # The pipes close when every writer in the group is gone, so joining
        # after the wait is what guarantees the partial output is complete.
        out.thread.join(timeout=GRACE)
        err.thread.join(timeout=GRACE)

    stdout, stderr = out.text(), err.text()
    truncated = out.overflowed or err.overflowed
    if timed_out:
        stderr = (
            f"{stderr}\n{TIMEOUT_MARKER} after {timeout:g}s — the process group "
            "was killed. Nothing it was writing can be assumed complete."
        ).lstrip("\n")
    if truncated:
        stderr = (
            f"{stderr}\n{TRUNCATED_MARKER} at {MAX_OUTPUT // (1024 * 1024)} MiB "
            "and the command was stopped."
        ).lstrip("\n")

    return Run(
        code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
        command=display,
        duration=time.monotonic() - started,
        argv=tuple(full),
        timed_out=timed_out,
        truncated=truncated,
    )


def exec_engine(
    args: Sequence[str],
    cwd: str | os.PathLike[str],
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> Run:
    """One engine command with no vault — `--version`, `doctor` about the box.

    The narrow door. Everything that touches a stranger's vault goes through
    `run()`, which is the only function here that fills in `-C`.
    """
    guard(args)
    return _spawn(args, cwd=cwd, timeout=timeout, env=env)


def run(
    vault: str | os.PathLike[str],
    args: Sequence[str],
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> Run:
    """One engine command, in one session's vault.

    The vault is both the `-C` and the working directory, which is belt and
    braces on purpose: commands that take no target resolve the nearest vault
    above the cwd, so the two agreeing means there is no arrangement of
    arguments under which the engine looks somewhere else.
    """
    root = _vault(vault)
    guard(args)
    return _spawn(["-C", str(root), *args], cwd=root, timeout=timeout, env=env)


def json(
    vault: str | os.PathLike[str],
    args: Sequence[str],
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """A run whose stdout is JSON — `list --json`, `check --json`, `score --json`.

    A non-zero exit raises with the engine's own stderr, unedited. That matters
    more here than anywhere else in this file: `check` failing is not a server
    error, it is the product working, and the findings it printed are the
    response. Callers turn this into a 4xx with the text intact.
    """
    result = run(vault, args, timeout=timeout)
    if result.code != 0:
        raise EngineError(result.message(), result)
    try:
        return json_loads(result.stdout)
    except (JSONDecodeError, ValueError) as exc:
        # Not "invalid JSON": the useful half is what the engine said instead,
        # which is usually a warning it printed on the way to a valid answer or
        # a refusal it wrote to stdout.
        detail = result.stderr.strip() or result.stdout.strip()[:400]
        raise EngineError(f"{args[0] if args else 'command'}: {exc}\n{detail}", result) from exc


def version() -> str | None:
    """The engine's version, or None when it cannot say.

    `--version` exits during argument parsing, before any vault is resolved, so
    it is the one command here that needs no session — which is why it runs
    through `exec_engine` with a cwd that is merely required to exist.

    None rather than a raise, and None rather than a usage line: an engine
    predating the flag answers with argparse's usage on stderr and exit 2, and
    printing that where a version belongs is worse than admitting ignorance.
    """
    result = exec_engine(["--version"], cwd=Path(os.sep), timeout=15)
    if result.code != 0:
        return None
    line = (result.stdout or result.stderr).strip().splitlines()
    if not line:
        return None
    return line[0].strip().removeprefix("report-maker").strip() or None


# ── streaming one ────────────────────────────────────────────────────────────


def stream(
    vault: str | os.PathLike[str],
    args: Sequence[str],
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> Iterator[str]:
    """The same command, line by line, while it runs.

    For a build the UI shows live. `all` prints its phases — stage, diagrams,
    build, pages, manifest, check — and a writer watching those knows what is
    happening in a way a spinner cannot tell them.

    stderr is merged into stdout rather than kept apart. Two pipes read from one
    thread deadlock, and for a progress view the interleaving *is* the
    information: the typst error belongs where it happened, between the phase
    that caused it and the one that followed.

    Two things are guaranteed regardless of how the consumer behaves. A watchdog
    kills the group at the deadline, so a silent command cannot hold the reader
    on a blocking `readline` for ever. And the `finally` kills whatever is still
    alive — a browser that closed the connection mid-build abandons this
    generator, and the process it started must not outlive it.

    Not a generator itself. A generator function runs none of its body until the
    first `next()`, which would defer every refusal below to a point where the
    response has already begun — and a 403 written into the middle of a build
    log is not a 403. The checks happen on the call; the streaming happens after.
    """
    root = _vault(vault)
    guard(args)
    script = locate()
    return _stream(_argv(script, ["-C", str(root), *args]), root, timeout, env)


def _stream(
    full: Sequence[str],
    root: Path,
    timeout: float,
    env: Mapping[str, str] | None,
) -> Iterator[str]:
    """The half of `stream` that may safely be deferred to the first `next()`."""
    proc = subprocess.Popen(
        full,
        cwd=str(root),
        env=_env(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    expired = threading.Event()

    def on_deadline() -> None:
        expired.set()
        _terminate(proc)

    watchdog = threading.Timer(timeout, on_deadline)
    watchdog.daemon = True
    watchdog.start()

    sent = 0
    pipe = proc.stdout
    try:
        for raw in pipe or ():
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            sent += len(raw)
            if sent > MAX_OUTPUT:
                _terminate(proc, hard=True)
                yield f"{TRUNCATED_MARKER} at {MAX_OUTPUT // (1024 * 1024)} MiB."
                return
            yield line
        if expired.is_set():
            yield f"{TIMEOUT_MARKER} after {timeout:g}s — the process group was killed."
    finally:
        watchdog.cancel()
        _terminate(proc, hard=True)
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


# ── conveniences that are still subprocesses ─────────────────────────────────


def is_vault(path: str | os.PathLike[str]) -> bool:
    """A vault is any folder holding `report-maker.toml` — nothing more.

    The one fact about a vault this file states without asking the engine,
    because it is the definition rather than a computation, and because the
    caller needs it *before* a command can be run in that folder at all.
    """
    return (Path(path) / "report-maker.toml").is_file()


@dataclass
class Health:
    """What the server can say about its own installation."""

    engine: str
    version: str | None
    root: str | None
    diagrams: bool
    fields: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "version": self.version,
            "sessions_root": self.root,
            "diagrams": self.diagrams,
            **self.fields,
        }


def health() -> Health:
    """Never raises. A health endpoint that fails to answer has answered."""
    try:
        root: str | None = str(sessions_root())
    except EngineError:
        root = None
    return Health(
        engine=describe(),
        version=version(),
        root=root,
        diagrams=diagrams_enabled(),
    )
