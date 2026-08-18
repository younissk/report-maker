"""The vault's own history, and the one command that moves work outward.

A vault is a folder of files, so the version control the writer already has is
the version control the writer should keep: `git`, in the vault's own repository,
with commits a person can still read six months later. This module is a thin and
deliberate wrapper over it — enough for the app to show a branch, a dirty count
and one report's history, and for `report-maker sync` to commit and push without
the writer leaving the editor.

`sync` is the only command in the engine that sends anything anywhere. Everything
else reads files and writes files inside a folder the user already owns; this one
can put work on a server and — far worse — can destroy work that was already
there. So it is built as a list of refusals rather than a list of features:

    never `--force`              rewriting a published history is unrecoverable
                                 for everyone who had already pulled it
    never without an upstream    a push with no tracking branch is a guess about
                                 where the work goes, and guessing loses work
    never from a detached HEAD   that commit belongs to no branch, and pushing
                                 it puts it somewhere nobody will look again
    never when behind            a push that could only succeed by overwriting
                                 is precisely the push not to make
    never a path outside         the vault is what we were asked to sync, and
                                 nothing above it is ours to commit

Every refusal names the exact command that fixes it. A refusal that only says no
teaches the writer to reach for `--force` unaided, which is the outcome this
module exists to prevent.

`state` is the exception to all of the above: the app polls it on a timer, so it
has to be cheap and must never raise. A folder that is not a repository is not an
error here, it is a fact — reported as `repo=False`, which is what the app draws
its "initialise git here" explainer from.

Nothing in this module dials out on its own, so `behind` is only as fresh as the
last fetch. The remote's own non-fast-forward rejection is the backstop for that,
and its message is passed through verbatim rather than summarised, because git
already says the true thing about why a push bounced.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config

# Forced on every invocation so the output shape does not depend on the user's
# git config. `core.quotePath=false` keeps non-ASCII filenames readable instead
# of C-escaped; `status.relativePaths=true` makes porcelain paths relative to the
# vault rather than to the repository root, which is what the app displays.
GIT_OPTIONS = ("-c", "core.quotePath=false", "-c", "status.relativePaths=true")

# Where the path starts in each kind of `status --porcelain=v2` line, counted in
# space-separated fields. Ordinary and rename entries carry a fixed run of
# mode/hash columns first; an untracked entry is just `? <path>`.
#
#   1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
#   2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>\t<origPath>
#   u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
#   ? <path>
STATUS_PATH_FIELD = {"1": 8, "2": 9, "u": 10, "?": 1}

# One record per commit, NUL-prefixed so `--name-only`'s file list — which git
# appends after a blank line — cannot be confused with the next record. The
# subject goes last because it is the only field that may contain anything.
LOG_FORMAT = "%x00%H%x1f%h%x1f%an%x1f%aI%x1f%s"
LOG_FIELDS = 5


class GitError(RuntimeError):
    pass


@dataclass
class GitState:
    """What the repository is doing, as of now.

    Every field has a default, because the answer for a folder that is not a
    repository at all is a fully-formed `GitState(repo=False)` rather than an
    exception — see the module docstring.
    """

    repo: bool = False
    branch: str | None = None  # None on a detached HEAD
    upstream: str | None = None
    dirty: list[str] = field(default_factory=list)  # porcelain paths, vault-relative
    ahead: int = 0
    behind: int = 0
    remote: str | None = None


@dataclass
class _Push:
    """The outcome of trying to push.

    `pushed` and `refusal` are separate because they are not opposites: a branch
    already level with its upstream pushed nothing and was refused nothing.
    """

    pushed: bool
    detail: str
    refusal: str | None = None


# ── running git ──────────────────────────────────────────────────────────────


def _run(cfg: Config, *args: str) -> subprocess.CompletedProcess:
    """Run git in the vault. Non-zero is a normal return, not an exception —
    every caller here has something more useful to say than a returncode."""
    try:
        return subprocess.run(
            ["git", "-C", str(cfg.root), *GIT_OPTIONS, *args],
            capture_output=True,
            text=True,
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise GitError(
            "git is not installed — `report-maker sync` keeps the vault's "
            "history with git, and has nothing to fall back on"
        ) from exc


def _ok(result: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitError(f"{what} failed:\n  {detail}" if detail else f"{what} failed")
    return result


def _require_repo(cfg: Config) -> None:
    if _run(cfg, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise GitError(
            f"{cfg.root} is not inside a git repository, so there is no history "
            "to write to.\n"
            f"  Start one with `git -C {cfg.root} init`."
        )


def _relative(cfg: Config, raw: str | Path) -> str:
    """A pathspec for git, proven to be inside the vault.

    This is the load-bearing half of "only ever stage paths inside the vault".
    Paths are resolved before the comparison, so a `..` segment or a symlink
    pointing out of the vault is caught rather than followed.
    """
    path = Path(raw)
    path = path if path.is_absolute() else cfg.root / path
    resolved, root = path.resolve(), cfg.root.resolve()
    if resolved != root and root not in resolved.parents:
        raise GitError(
            f"refusing to stage {raw} — it is outside the vault at {root}.\n"
            "  `report-maker sync` only ever commits the vault it was pointed at."
        )
    return "." if resolved == root else resolved.relative_to(root).as_posix()


def _pathspecs(cfg: Config, paths: Sequence[str | Path] | None) -> list[str]:
    return [_relative(cfg, p) for p in paths] if paths else ["."]


# ── what the repository is doing ─────────────────────────────────────────────


def _parse_status(text: str) -> GitState:
    st = GitState(repo=True)
    for line in text.splitlines():
        if line.startswith("# branch.head "):
            head = line.split(" ", 2)[2]
            # git spells a detached HEAD "(detached)" — a branch cannot be named
            # that, so there is no ambiguity to worry about.
            st.branch = None if head == "(detached)" else head
        elif line.startswith("# branch.upstream "):
            st.upstream = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            for token in line.split(" ", 2)[2].split():
                if token.startswith("+"):
                    st.ahead = int(token[1:])
                elif token.startswith("-"):
                    st.behind = int(token[1:])
        elif line[:1] in STATUS_PATH_FIELD:
            field_index = STATUS_PATH_FIELD[line[0]]
            parts = line.split(" ", field_index)
            if len(parts) > field_index:
                # A rename entry carries `<path>\t<origPath>`; the new path — the
                # one that exists now — comes first.
                st.dirty.append(parts[field_index].split("\t", 1)[0])
    return st


def _remote_for(cfg: Config, branch: str | None) -> str | None:
    """Which remote this branch belongs to, falling back to the obvious one."""
    if branch:
        configured = _run(cfg, "config", "--get", f"branch.{branch}.remote")
        if configured.returncode == 0 and configured.stdout.strip():
            return configured.stdout.strip()
    names = _run(cfg, "remote").stdout.split()
    if "origin" in names:
        return "origin"
    return names[0] if names else None


def state(cfg: Config) -> GitState:
    """Branch, upstream, drift and dirty files — in two git calls, and never an
    exception. The app polls this, so a missing git or a folder that is not a
    repository both report `repo=False` rather than interrupting anything.

    The status is scoped to the vault with a `.` pathspec, so a vault that lives
    inside a larger repository reports its own changes and not the whole tree's.
    Untracked directories stay collapsed (git's default) rather than enumerated:
    an un-ignored `out/` would otherwise make every poll walk thousands of
    generated files.
    """
    try:
        result = _run(cfg, "status", "--porcelain=v2", "--branch", "--", ".")
    except GitError:
        return GitState(repo=False)
    if result.returncode != 0:
        return GitState(repo=False)
    st = _parse_status(result.stdout)
    st.remote = _remote_for(cfg, st.branch)
    return st


# ── committing ───────────────────────────────────────────────────────────────


def default_message(files: int, *, now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    return f"report-maker: {files} file(s) — {stamp}"


def _staged(cfg: Config, specs: Sequence[str]) -> list[str]:
    result = _run(cfg, "diff", "--cached", "--name-only", "--", *specs)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def commit(
    cfg: Config,
    message: str | None = None,
    *,
    paths: Sequence[str | Path] | None = None,
) -> str | None:
    """Stage and commit the vault. Returns the new sha, or None when there was
    nothing to commit — an empty commit says something happened when nothing did.

    `paths` narrows the commit; every entry is checked against the vault first.
    The commit itself carries the same pathspec, which is git's own guarantee
    that nothing else already sitting in the index can ride along.
    """
    _require_repo(cfg)
    specs = _pathspecs(cfg, paths)

    # `add` first so a brand new report folder is included: a pathspec commit on
    # its own only knows about paths git has already heard of.
    _ok(_run(cfg, "add", "--all", "--", *specs), "git add")
    staged = _staged(cfg, specs)
    if not staged:
        return None

    message = message or default_message(len(staged))
    _ok(_run(cfg, "commit", "--message", message, "--", *specs), "git commit")
    return _ok(_run(cfg, "rev-parse", "HEAD"), "git rev-parse").stdout.strip()


# ── pushing ──────────────────────────────────────────────────────────────────


def push_refusal(st: GitState) -> str | None:
    """Why this state must not be pushed, or None when it is safe to.

    Kept apart from `push` so the rules can be read — and tested — without a
    remote in sight, and so the app can grey its own push button out for the
    same reasons and with the same words.
    """
    if not st.repo:
        return (
            "not a git repository — there is nothing to push, and nowhere to "
            "push it from.\n  Start one with `git init`."
        )
    if st.branch is None:
        return (
            "HEAD is detached, so these commits belong to no branch and a push "
            "would strand them.\n"
            "  Put them on one first: `git switch -c <branch>`, then "
            "`git push -u origin <branch>`."
        )
    if st.upstream is None:
        return (
            f"no upstream for {st.branch} — set one with "
            f"`git push -u {st.remote or 'origin'} {st.branch}`.\n"
            "  Pushing without one means guessing where the work should go."
        )
    if st.behind:
        plural = "" if st.behind == 1 else "s"
        return (
            f"{st.branch} is {st.behind} commit{plural} behind {st.upstream} — "
            "not pushing.\n"
            "  Bring them in first with `git pull --rebase`, then sync again."
        )
    return None


def _tracking(cfg: Config, st: GitState) -> tuple[str, str]:
    """The remote and the full ref this branch pushes to.

    Read from `branch.<name>.remote` / `.merge` rather than assembled from the
    branch name, because a local branch may well track a remote branch called
    something else, and pushing to the wrong ref is a way to lose work quietly.
    """
    remote = _run(cfg, "config", "--get", f"branch.{st.branch}.remote").stdout.strip()
    merge = _run(cfg, "config", "--get", f"branch.{st.branch}.merge").stdout.strip()
    return remote or st.remote or "origin", merge or f"refs/heads/{st.branch}"


def _push(cfg: Config, st: GitState) -> _Push:
    refusal = push_refusal(st)
    if refusal:
        return _Push(pushed=False, detail=refusal, refusal=refusal)
    if st.ahead == 0:
        level = f"nothing to push — {st.branch} is level with {st.upstream}"
        return _Push(pushed=False, detail=level)

    remote, ref = _tracking(cfg, st)
    # An explicit source:destination refspec, and no other flag. Nothing in this
    # module may ever add `--force`, `--force-with-lease` or `--delete`: none of
    # them can be made safe to run unattended on somebody else's behalf.
    result = _run(cfg, "push", remote, f"refs/heads/{st.branch}:{ref}")
    if result.returncode != 0:
        # git's own words. It knows things we do not — a rejected fast-forward,
        # a missing credential, a protected branch — and it says them precisely.
        detail = (result.stderr or result.stdout).strip()
        return _Push(
            pushed=False,
            detail=f"push to {st.upstream} was rejected:\n{detail}",
            refusal=detail or "push failed",
        )
    plural = "" if st.ahead == 1 else "s"
    sent = f"pushed {st.ahead} commit{plural} to {st.upstream}"
    return _Push(pushed=True, detail=sent)


def push(cfg: Config) -> str:
    """Push the current branch to its upstream, or say why that would be unsafe.

    A refusal is returned rather than raised: it is an answer, not a crash, and
    the answer always contains the command that makes the push possible.
    """
    return _push(cfg, state(cfg)).detail


# ── sync ─────────────────────────────────────────────────────────────────────


def sync(
    cfg: Config,
    *,
    message: str | None = None,
    do_push: bool = False,
    paths: Sequence[str | Path] | None = None,
) -> dict:
    """Commit the vault, and push only when asked to.

    Without `do_push` this touches no remote at all — it does not even look one
    up. That is deliberate: the common case is a writer saving work locally, and
    a command that quietly reaches the network when it was not asked to is a
    command people stop trusting.
    """
    sha = commit(cfg, message, paths=paths)
    lines = [
        f"committed {sha[:9]}"
        if sha
        else "nothing to commit — the vault matches its last commit"
    ]

    pushed, refused = False, None
    if do_push:
        # Re-read the state: the commit we just made is the thing being pushed,
        # so `ahead` from before it would be short by one.
        outcome = _push(cfg, state(cfg))
        pushed, refused = outcome.pushed, outcome.refusal
        lines.append(outcome.detail)

    return {
        "committed": sha,
        "pushed": pushed,
        "detail": "\n".join(lines),
        "refused": refused,
    }


# ── history ──────────────────────────────────────────────────────────────────


def _strip(name: str, prefix: str) -> str:
    """A repository-root path, said from inside the vault."""
    return name[len(prefix) :] if prefix and name.startswith(prefix) else name


def log(cfg: Config, path: str | Path, limit: int = 50) -> list[dict]:
    """The commits that touched `path`, newest first.

    This is what the app's version timeline is drawn from, so `files` lists only
    the paths inside the pathspec — for a report folder that is exactly the
    question being asked, which of this report's files did that commit change.
    A merge commit shows no files, because git shows no diff for one by default.

    Those paths are made vault-relative, the same as `GitState.dirty`: `git log`
    always speaks from the repository root, and for a vault nested inside a
    larger repository that is a prefix the app has no use for.
    """
    _require_repo(cfg)
    spec = _relative(cfg, path)
    prefix = _run(cfg, "rev-parse", "--show-prefix").stdout.strip()
    result = _run(
        cfg,
        "log",
        f"--max-count={max(0, int(limit))}",
        f"--format={LOG_FORMAT}",
        "--name-only",
        "--",
        spec,
    )
    if result.returncode != 0:
        # An unknown path is not an error — it is a report with no history yet.
        return []

    rows: list[dict] = []
    for record in result.stdout.split("\0"):
        if not record.strip():
            continue
        head, _, rest = record.partition("\n")
        parts = head.split("\x1f", LOG_FIELDS - 1)
        if len(parts) < LOG_FIELDS:
            continue
        sha, short, author, date, subject = parts
        rows.append(
            {
                "sha": sha,
                "short": short,
                "subject": subject,
                "author": author,
                "date": date,
                "files": [
                    _strip(line, prefix) for line in rest.splitlines() if line.strip()
                ],
            }
        )
    return rows


def show(cfg: Config, rev: str, path: str | Path) -> str:
    """A vault file as it was at `rev`.

    Offered here rather than left to each caller because the path arithmetic is
    the part that goes wrong: `git show` takes a path from the *repository* root,
    while everything in the engine speaks in vault-relative paths, and a vault is
    very often one folder inside a larger repository.
    """
    _require_repo(cfg)
    rel = _relative(cfg, path)
    prefix = _run(cfg, "rev-parse", "--show-prefix").stdout.strip()
    inside = "" if rel == "." else rel
    result = _run(cfg, "show", f"{rev}:{prefix}{inside}")
    if result.returncode != 0:
        raise GitError(
            f"cannot read {rel} at {rev}:\n  {(result.stderr or '').strip()}\n"
            f"  `git -C {cfg.root} log --oneline -- {rel}` lists the revisions "
            "that have it."
        )
    return result.stdout


# ── output ───────────────────────────────────────────────────────────────────


def to_json(
    st: GitState,
    *,
    result: Mapping | None = None,
    log_rows: Sequence[Mapping] | None = None,
) -> dict:
    """The state at the top level, because that is the shape the app's `GitState`
    type expects; a sync result or a log listing rides along beside it when the
    command that produced them was asked for JSON."""
    payload = asdict(st)
    if result is not None:
        payload["sync"] = dict(result)
    if log_rows is not None:
        payload["log"] = [dict(row) for row in log_rows]
    return payload


def report_state(cfg: Config, st: GitState) -> int:
    """Print the state. Always exit 0 — none of this is a failure."""
    if not st.repo:
        print(f"  {cfg.root} is not inside a git repository")
        print(f"    start one with `git -C {cfg.root} init`")
        return 0

    where = st.branch or "HEAD (detached)"
    print(f"  branch    {where}")
    print(f"  upstream  {st.upstream or 'none'}")
    if st.upstream:
        print(f"  drift     {st.ahead} ahead, {st.behind} behind")
    if st.dirty:
        print(f"  dirty     {len(st.dirty)} path(s)")
        for path in st.dirty[:20]:
            print(f"              {path}")
        if len(st.dirty) > 20:
            print(f"              … and {len(st.dirty) - 20} more")
    else:
        print("  dirty     nothing — the vault matches its last commit")
    return 0


def report_sync(cfg: Config, result: Mapping) -> int:
    """Print what sync did. Exit 1 when a push was refused, so a script notices
    that the work is still only local."""
    for line in str(result.get("detail", "")).splitlines():
        print(f"  {line}")
    return 1 if result.get("refused") else 0


def report_log(cfg: Config, rows: Sequence[Mapping]) -> int:
    if not rows:
        print("  no commits touch that path yet")
        return 0
    for row in rows:
        print(f"  {row['short']}  {str(row['date'])[:10]}  {row['author']}")
        print(f"            {row['subject']}")
        for name in row.get("files", ()):
            print(f"              {name}")
    return 0
