"""GitHub, held at arm's length.

The web build has two products in one server. Try mode hands a stranger a vault
that lives for a day and dies; **GitHub mode** is the other half — connect a
repository, work in it, commit and push back — and it exists because the vault
is a folder and the version control a writer already has is the one they should
keep. The repository is the store. This server persists nothing of its own past
the session record, which is what makes "close the tab" a safe thing to do.

This module is the whole of that: the OAuth dance, the repository listing, a
clone that assumes the repository is hostile, and one delegation to
`report-maker sync`. It holds no logic about vaults. Every question about what a
vault contains is a subprocess, exactly as in `app/src/main/engine.ts` — see
`app/README.md`, "What it is not".

## The token never reaches the browser

Say it twice, because it is the kind of thing a later refactor breaks by
accident. The access token is minted here, kept in the session record on the
server, and used here. It is never put in a response body, never in a cookie,
never in a URL, never in a log line, never in a template. The browser's only
handle on GitHub is its own session cookie; the token sits behind it. A route
that returned `{"token": …}` "just for the client to display" would hand every
XSS in the frontend a repository-write credential, and there is no version of
that which is worth a convenience.

`RM_WEB_GITHUB_TOKEN` is the same rule said for a self-hosted single user: it is
a **server** environment variable. It is never a field in the UI and never read
off a request — see `token_for`, which reads the session first and the
environment second and a request never.

## When GitHub is not configured

`configured()` is False unless both `RM_WEB_GITHUB_CLIENT_ID` and
`RM_WEB_GITHUB_CLIENT_SECRET` are set, and every entry point raises
`GitHubError(NOT_CONFIGURED)` rather than building a redirect to an OAuth
endpoint that will bounce. A dead button that fails halfway through a redirect
chain teaches a user that the product is broken; a sentence saying GitHub is not
configured on this server teaches them to go and configure it.

## The clone assumes the repository is hostile

Because it is a stranger's, on a server running other strangers' work. Every
guard below was already solved once in `engine/install.py`, which fetches a
design from an arbitrary repository, and the reasoning is copied from there
rather than re-derived — including the one that was a live remote-code-execution
bug: `git fetch` parses options *after* its positionals, so a ref of
`--upload-pack=<command>` reached git as an option and **was executed**. A ref
that begins with `-` is refused here for that reason and no other.

`engine/install.py` is deliberately **not imported**. It is engine code, this is
a different trust context, and an import would make a change safe for one
context silently govern the other. The guards are copied; the reasoning is
cited.

On top of install.py's set, this module adds what a multi-tenant server needs
and a desktop install does not:

  - `GIT_CONFIG_NOSYSTEM=1` and a scratch `HOME`, so nothing in the operator's
    own git config — `core.fsmonitor` runs a command, aliases run commands —
    applies to a stranger's repository;
  - `--template=` with no value, so no sample hooks are copied into the clone;
  - `core.hooksPath=/dev/null` written into the *cloned repository's own config*
    afterwards, so every later git run in that vault is covered too. `git clone`
    never fetches the remote's hooks, so this is belt to install.py's braces —
    but the engine's `gitsync` does not disable hooks and is not ours to edit,
    and this closes that gap from the outside;
  - a size ceiling checked on the temp clone, before anything is moved into
    place, because `--depth 1` bounds the history and not the tree.

And the clone lands in a temp directory beside its destination, then moves. A
failed clone must leave no half-vault: a folder holding three files out of a
repository is worse than no folder, because it looks like a vault.

## Pushing, and the credential that makes it possible

`report-maker sync --push` shells out to plain `git`, which has no idea a token
exists. Rather than teach the bridge to carry one — the token would then have to
cross another module and another process boundary — the clone writes a
`credential.helper` into the cloned repository's **own** `.git/config`, pointing
at a file holding the credential.

That file sits in the session directory *beside* the vault and never inside it,
and the refusal in `credential_file` enforces it. This matters more than it
looks: `report-maker sync` stages `.` across the vault, so a credential file one
directory lower would be committed and pushed to the repository it unlocks. It
is 0600, and it dies with the session directory when the sweeper runs.

The helper list is reset with an empty entry before ours is added, so an
operator's global credential helper cannot answer for a stranger's repository.

Nothing here ever force-pushes and nothing here pushes on its own. `sync` takes
`push` from an explicit user action and passes the engine's refusals back
verbatim — `gitsync.py` already refuses to push with no upstream, from a
detached HEAD, or when behind, and each refusal names the command that fixes it.
Rewording those would be this layer inventing an opinion about git.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

# ── configuration ────────────────────────────────────────────────────────────

AUTHORIZE = "https://github.com/login/oauth/authorize"
EXCHANGE = "https://github.com/login/oauth/access_token"
API = "https://api.github.com"

# The only hosts this module will talk to, redirects included. GitHub's own
# endpoints redirect within these two, and a redirect that leaves them is either
# a mistake or somebody steering our token somewhere else.
ALLOWED_HOSTS = frozenset({"github.com", "api.github.com"})

# `repo` covers private repositories, which is the case that matters — a writer
# with a public vault is rare and a writer with a private one is the norm. Scope
# is deliberately not configurable: an operator raising it by environment
# variable would be raising it for every user of the server at once.
SCOPES = "repo"

USER_AGENT = "report-maker-web"

NOT_CONFIGURED = (
    "GitHub is not configured on this server.\n"
    "  Set RM_WEB_GITHUB_CLIENT_ID, RM_WEB_GITHUB_CLIENT_SECRET and "
    "RM_WEB_GITHUB_CALLBACK to turn GitHub mode on, or work in try mode, "
    "which needs no account."
)

# Ceilings. Every one of them exists because the other end of the connection is
# somebody else's server and the other end of the clone is somebody else's
# repository, and neither has agreed to be small or fast.
HTTP_TIMEOUT = 20.0
HTTP_MAX_BYTES = 4 * 1024 * 1024
CLONE_TIMEOUT = 180.0
REPO_PAGE = 100
REPO_MAX_PAGES = 5

# How long an OAuth state is good for. Long enough to sign in and approve on a
# phone, short enough that a state harvested from a browser history is stale.
STATE_TTL = 600.0
STATE_MAX = 4096

# `owner/repo`, and nothing that is not that. GitHub's own rules are narrower
# still, but this is the shape that matters: two segments of ordinary name
# characters, no path, no scheme, no leading dash.
FULL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class GitHubError(RuntimeError):
    """Something the user should be told, in words they can act on."""


def _env(name: str) -> str | None:
    value = (os.environ.get(name) or "").strip()
    return value or None


def client_id() -> str | None:
    return _env("RM_WEB_GITHUB_CLIENT_ID")


def client_secret() -> str | None:
    return _env("RM_WEB_GITHUB_CLIENT_SECRET")


def callback() -> str | None:
    return _env("RM_WEB_GITHUB_CALLBACK")


def configured() -> bool:
    """Whether the OAuth flow can run at all.

    The callback is deliberately *not* required here. GitHub falls back to the
    callback registered on the OAuth app when none is sent, so an operator who
    set two variables out of three has a working flow, and refusing it would be
    this module being stricter than GitHub about GitHub.
    """
    return bool(client_id() and client_secret())


def server_token() -> str | None:
    """The single-user escape hatch, read from the server's environment.

    Never from a request, never from a form, never from a header. A field in the
    UI asking for a personal access token would be this server collecting other
    people's repository credentials, which is a thing to build only if you want
    to be a breach.
    """
    return _env("RM_WEB_GITHUB_TOKEN")


def available() -> bool:
    """Whether GitHub mode can be offered — by OAuth, or by a server token."""
    return configured() or bool(server_token())


def require_configured() -> None:
    if not configured():
        raise GitHubError(NOT_CONFIGURED)


def status() -> dict:
    """What the frontend is allowed to know: whether the button should exist.

    Not the client id, not the callback, and above all not the token. The client
    id is not a secret in an OAuth web flow, but it is also not something the
    page needs — the page asks the server to start the flow and the server
    builds the URL.
    """
    return {
        "configured": configured(),
        "available": available(),
        "mode": "oauth" if configured() else ("token" if server_token() else "off"),
        "reason": None if available() else NOT_CONFIGURED,
    }


# ── the session's half of it ─────────────────────────────────────────────────
#
# The session record belongs to another module. This one needs exactly one slot
# in it and reads it through accessors, so a session that is a dataclass, an
# object with a dict attribute or a plain mapping all work and none of them has
# to know this module exists.


def _slot(session: Any, *, create: bool = False) -> dict | None:
    """The session's `github` mapping — `{token, login, repo, branch}`."""
    if isinstance(session, dict):
        held = session.get("github")
        if held is None and create:
            held = session["github"] = {}
        return held if isinstance(held, dict) else None

    held = getattr(session, "github", None)
    if held is None and create:
        held = {}
        setattr(session, "github", held)
    return held if isinstance(held, dict) else None


def token_for(session: Any) -> str | None:
    """This session's access token: the connected one, or the server's.

    The order is the one a person would expect. A user who signed in owns the
    session, and a self-hosted server's own token is the fallback for the case
    where nobody signed in because there is only ever one of them.
    """
    held = _slot(session)
    token = (held or {}).get("token")
    if isinstance(token, str) and token:
        return token
    return server_token()


def remember(session: Any, token: str, **fields: Any) -> None:
    """Put the token in the session record. This is the only place it is stored,
    and the record never leaves the server."""
    if not token:
        raise GitHubError("refusing to remember an empty token")
    held = _slot(session, create=True)
    if held is None:
        raise GitHubError("this session cannot hold a GitHub connection")
    held["token"] = token
    held.update({k: v for k, v in fields.items() if v is not None})


def forget(session: Any) -> None:
    """Disconnect. The token is dropped rather than blanked, so nothing later
    finds an empty string where it expected a credential."""
    held = _slot(session)
    if held is not None:
        held.pop("token", None)


def connection(session: Any) -> dict:
    """What the browser may be told about the connection.

    The token is excluded by construction — this builds a new dict from the
    three fields that are not secret, rather than copying the record and
    deleting one key. A denylist is a bug waiting for a fourth field.
    """
    held = _slot(session) or {}
    return {
        "connected": bool(token_for(session)),
        "login": held.get("login"),
        "repo": held.get("repo"),
        "branch": held.get("branch"),
    }


# ── the state parameter ──────────────────────────────────────────────────────


class StateStore:
    """OAuth `state`, issued once and accepted once.

    `state` is the entire defence against cross-site request forgery on the
    callback: without it, an attacker walks their own authorization code into
    somebody else's session and the server happily attaches an attacker-owned
    repository — or, the other way about, attaches the victim's repository to a
    session the attacker holds the cookie for.

    Three properties, and all three are load-bearing:

      **required** — a callback with no state is refused, not tolerated. An
      optional CSRF token is not a CSRF token.

      **single-use** — consumed on the first check, so a state recovered from a
      browser history, a referrer header or a shared screenshot is already spent.

      **compared with `secrets.compare_digest`** — the comparison is against a
      value an attacker supplies and can vary at will, which is the textbook
      shape for a timing oracle. `==` on a `str` short-circuits at the first
      differing byte; `compare_digest` does not.

    It lives in memory. A restart drops every in-flight authorization, which
    costs a user one click and costs an attacker a store they cannot outlast.
    """

    def __init__(self, ttl: float = STATE_TTL) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._issued: dict[str, tuple[str, float]] = {}

    def issue(self, session_id: str) -> str:
        if not session_id:
            raise GitHubError("cannot start a GitHub sign-in without a session")
        state = secrets.token_urlsafe(32)
        with self._lock:
            self._sweep()
            if len(self._issued) >= STATE_MAX:
                # A flood of unfinished authorizations is somebody probing, and
                # unbounded growth would be the thing they were probing for.
                raise GitHubError("too many sign-ins in flight; try again shortly")
            self._issued[session_id] = (state, time.monotonic() + self._ttl)
        return state

    def consume(self, session_id: str, state: str | None) -> bool:
        """True exactly once, for the session that asked and the state we gave it."""
        if not session_id or not isinstance(state, str) or not state:
            return False
        with self._lock:
            self._sweep()
            held = self._issued.pop(session_id, None)
        if held is None:
            return False
        expected, expires = held
        if time.monotonic() > expires:
            return False
        return secrets.compare_digest(expected, state)

    def _sweep(self) -> None:
        now = time.monotonic()
        for key in [k for k, (_, exp) in self._issued.items() if exp < now]:
            self._issued.pop(key, None)


STATES = StateStore()


# ── the OAuth web flow ───────────────────────────────────────────────────────


def authorize_url(state: str) -> str:
    """Where to send the browser. The state is required, not defaulted."""
    require_configured()
    if not state:
        raise GitHubError("refusing to start a GitHub sign-in with no state")
    params = {
        "client_id": client_id(),
        "scope": SCOPES,
        "state": state,
        # GitHub's own account picker, rather than silently reusing whichever
        # account the browser is already signed into. On a shared machine that
        # is the difference between connecting your repository and connecting
        # the last person's.
        "allow_signup": "false",
    }
    if callback():
        params["redirect_uri"] = callback()
    return f"{AUTHORIZE}?{urllib.parse.urlencode(params)}"


def exchange(code: str, *, opener: Any = None) -> str:
    """Trade the authorization code for an access token.

    The token is *returned* rather than stored, so the caller decides which
    session owns it — and so nothing here has to know what a session is. What
    the caller must not do is put the return value anywhere a browser can read.

    GitHub answers an invalid code with HTTP 200 and an `error` field, which is
    the shape that catches people: a naive caller checks the status, finds 200,
    and stores the string `None`.
    """
    require_configured()
    if not isinstance(code, str) or not code.strip():
        raise GitHubError("no authorization code in the callback")

    body = urllib.parse.urlencode(
        {
            "client_id": client_id(),
            "client_secret": client_secret(),
            "code": code.strip(),
            **({"redirect_uri": callback()} if callback() else {}),
        }
    ).encode("ascii")

    payload = _json_request(
        EXCHANGE,
        method="POST",
        data=body,
        content_type="application/x-www-form-urlencoded",
        opener=opener,
    )
    if not isinstance(payload, dict):
        raise GitHubError("GitHub returned something that is not a token response")
    if payload.get("error"):
        # GitHub's own words: `bad_verification_code`, `incorrect_client_credentials`.
        # They name the fix better than a paraphrase would.
        detail = payload.get("error_description") or payload["error"]
        raise GitHubError(f"GitHub refused the sign-in: {detail}")
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise GitHubError("GitHub returned no access token")
    return token


def identity(token: str, *, opener: Any = None) -> dict:
    """Who the token belongs to — login and avatar, for the header. No email."""
    payload = _json_request(f"{API}/user", token=token, opener=opener)
    if not isinstance(payload, dict):
        raise GitHubError("GitHub returned something that is not a user")
    return {"login": payload.get("login"), "avatar": payload.get("avatar_url")}


def repos(token: str, *, opener: Any = None) -> list[dict]:
    """The repositories this token can reach, most recently pushed first.

    Five fields and no more. The GitHub API answers with about a hundred per
    repository, and forwarding all of them would put a stranger's description,
    homepage and topics through this server and into a page — user text, every
    field of it, and every field then something the frontend has to remember to
    escape. Narrowing here is cheaper than escaping there.
    """
    if not token:
        raise GitHubError(NOT_CONFIGURED if not available() else "not connected to GitHub")

    out: list[dict] = []
    url: str | None = (
        f"{API}/user/repos?"
        + urllib.parse.urlencode(
            {"per_page": REPO_PAGE, "sort": "pushed", "affiliation": "owner,collaborator,organization_member"}
        )
    )
    for _ in range(REPO_MAX_PAGES):
        if not url:
            break
        payload, headers = _json_request(url, token=token, opener=opener, with_headers=True)
        if not isinstance(payload, list):
            raise GitHubError("GitHub returned something that is not a repository list")
        for row in payload:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "name": row.get("name"),
                    "full_name": row.get("full_name"),
                    "private": bool(row.get("private")),
                    "default_branch": row.get("default_branch"),
                    "pushed_at": row.get("pushed_at"),
                }
            )
        url = _next_link(headers.get("Link") if headers else None)
    return out


def _next_link(link: str | None) -> str | None:
    """The `rel="next"` URL out of a Link header, if it points where we expect.

    The header is a server's instruction about where to go next, so it is read
    as data: a next page that has wandered off api.github.com is not followed,
    and a bearer token is not offered to it.
    """
    if not link:
        return None
    for part in link.split(","):
        section = part.split(";")
        if len(section) < 2:
            continue
        target = section[0].strip()
        if not (target.startswith("<") and target.endswith(">")):
            continue
        if any(bit.strip().lower() in ('rel="next"', "rel=next") for bit in section[1:]):
            url = target[1:-1]
            try:
                _check_host(url)
            except GitHubError:
                return None
            return url
    return None


# ── talking to GitHub ────────────────────────────────────────────────────────


def _check_host(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise GitHubError(f"refusing to send a GitHub token to {url!r}")
    return url


class _PinnedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but only within GitHub.

    An `Authorization` header survives a redirect in urllib, so a redirect off
    the allowed hosts is a token walking out of the building. Refused rather
    than stripped: a request that silently continues unauthenticated returns a
    404 and sends the caller looking for a permissions problem that is not there.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        _check_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_PinnedRedirects)


def _json_request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    token: str | None = None,
    content_type: str | None = None,
    opener: Any = None,
    with_headers: bool = False,
) -> Any:
    """One GitHub call. Bounded in time and in bytes, and never leaks the token
    into an error message — GitHub's error bodies do not contain it, but an
    exception carrying the request object would."""
    _check_host(url)
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if content_type:
        request.add_header("Content-Type", content_type)
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    use = opener or _OPENER
    try:
        with use.open(request, timeout=HTTP_TIMEOUT) as response:
            raw = response.read(HTTP_MAX_BYTES + 1)
            # The message object, not `dict(...)` of it: header names arrive in
            # whatever case the server chose, and only `HTTPMessage.get` is
            # case-insensitive about `Link` versus `link`.
            headers = getattr(response, "headers", None)
    except urllib.error.HTTPError as exc:
        raise GitHubError(_http_message(exc)) from None
    except urllib.error.URLError as exc:
        raise GitHubError(f"cannot reach GitHub: {exc.reason}") from None
    except TimeoutError:
        raise GitHubError("GitHub did not answer in time") from None

    if len(raw) > HTTP_MAX_BYTES:
        raise GitHubError("GitHub's answer was larger than this server will read")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GitHubError("GitHub's answer was not JSON") from None
    return (payload, headers) if with_headers else payload


def _http_message(exc: urllib.error.HTTPError) -> str:
    """GitHub's own explanation, when it gave one. `message` is the field it
    uses for "Bad credentials" and for a rate limit, and both are things the
    user can act on."""
    detail = ""
    try:
        body = json.loads(exc.read(64 * 1024).decode("utf-8"))
        if isinstance(body, dict) and isinstance(body.get("message"), str):
            detail = body["message"]
    except Exception:
        detail = ""
    if exc.code == 401:
        return "GitHub rejected the credential — sign in again."
    if exc.code == 403 and "rate limit" in detail.lower():
        return f"GitHub rate limit: {detail}"
    return f"GitHub returned {exc.code}" + (f": {detail}" if detail else "")


# ── the clone ────────────────────────────────────────────────────────────────
#
# Everything from here to `sync` assumes the repository is a stranger's. The
# guards are `engine/install.py`'s, copied rather than imported — see the module
# docstring for why, and read that file for the argument-injection bug that put
# the `-` check in both places.


def _safe_full_name(full_name: str) -> str:
    """`owner/repo`, or a refusal.

    A leading `-` makes git parse the argument as an *option* rather than a
    repository — `engine/install.py::_safe_remote` — and the URL here is built
    from this value, so the check happens before the string becomes one. The
    regex forbids it along with everything else that is not a name.
    """
    if not isinstance(full_name, str) or not FULL_NAME.match(full_name.strip()):
        raise GitHubError(
            f"bad repository: {full_name!r} — it must read owner/name, "
            "with no scheme and no path"
        )
    return full_name.strip()


def _safe_ref(ref: str | None) -> str | None:
    """A branch that cannot turn into a git option.

    This is `engine/install.py::_safe_ref`, and it is the one that bites. From
    that file, verbatim in substance: `--branch` consumes its value safely, but
    `git fetch` parses options *after* its positionals, so a ref of
    `--upload-pack=<command>` **runs that command** and then reports the ref as
    not found. A ref name cannot legally begin with `-` or hold whitespace or
    control characters anyway, so refusing those costs nothing real.
    """
    if ref is None:
        return None
    if not isinstance(ref, str) or not ref.strip():
        return None
    ref = ref.strip()
    if ref.startswith("-"):
        raise GitHubError(
            f"bad branch: {ref!r} — a branch, tag or commit may not begin with '-'"
        )
    if any(ch.isspace() or ord(ch) < 0x20 for ch in ref):
        raise GitHubError(f"bad branch: {ref!r} — a ref name holds no whitespace")
    return ref


def _redact(text: str, token: str | None) -> str:
    """git's output, with the credential taken out of it.

    The token is not in the command line and not in the URL, so this should
    never fire. It is here because "should never" is exactly the assumption that
    puts a credential in a log file when someone later adds it to the URL for
    five minutes to debug something.
    """
    if token and token in text:
        text = text.replace(token, "«token»")
    return text


def _git_env(scratch: Path) -> dict:
    """The environment a clone runs in: no operator config, no prompts.

    `GIT_CONFIG_NOSYSTEM` and a scratch `HOME` are what a multi-tenant server
    needs and a desktop install does not. The operator's own `~/.gitconfig` can
    set `core.fsmonitor` or an alias — both of which name a program git will run
    — and none of that has any business applying to a stranger's repository.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "HOME": str(scratch),
            "XDG_CONFIG_HOME": str(scratch / "config"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            # Nothing here may ever block waiting for a human at a terminal.
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "GCM_INTERACTIVE": "never",
        }
    )
    return env


# Applied to every invocation. `core.hooksPath` is the one thing that turns a
# clone from a download into an execution, and `protocol.ext.allow=never` closes
# the other execution path — `ext::sh -c "…"` is a remote-code-execution URL
# wearing a URL's clothes. Both are `engine/install.py::_git`'s reasoning, and
# both are said here rather than relied on from a config file, because a default
# that lives in the user's config is a default somebody can have changed.
GIT_HARDENING = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "protocol.ext.allow=never",
    "-c", "protocol.file.allow=never",
    "-c", "credential.helper=",
    "-c", "core.symlinks=false",
)


def _git(args: list[str], *, env: dict, cwd: Path | None = None, timeout: float, token: str | None) -> subprocess.CompletedProcess:
    exe = shutil.which("git")
    if exe is None:
        raise GitHubError("git is not installed — it is what fetches a repository")
    try:
        return subprocess.run(
            [exe, *GIT_HARDENING, *args],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise GitHubError(
            f"git {args[0]} took longer than {int(timeout)}s and was stopped"
        ) from None


def _tree_bytes(root: Path, ceiling: int) -> int:
    """How much disk the clone took, stopping as soon as it is too much.

    Counted rather than trusted: `--depth 1` bounds the *history*, and a
    repository whose tree is a gigabyte of binaries is a perfectly ordinary
    repository that this server has no room for.
    """
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            total += path.stat().st_size
        except OSError:
            continue
        if total > ceiling:
            return total
    return total


def credential_file(creds: Path | str, token: str, *, vault: Path | str) -> Path:
    """The credential git will read, written outside the vault at 0600.

    It holds git's credential-helper answer — two lines, username and password —
    and the cloned repository's own `.git/config` points at it, so the engine's
    `sync --push` authenticates without this module reaching into the bridge's
    process environment.

    The refusal is the important line. `report-maker sync` stages `.` across the
    whole vault, so a credential file one directory lower would be committed and
    pushed *to the repository it unlocks*. Refusing outright beats a comment
    asking the next person to be careful.
    """
    creds = Path(creds).resolve()
    root = Path(vault).resolve()
    if creds == root or root in creds.parents:
        raise GitHubError(
            f"refusing to write a credential inside the vault at {root} — "
            "`sync` would commit it"
        )
    creds.parent.mkdir(parents=True, exist_ok=True)
    # Opened O_CREAT with the mode in the call, so the token is never even
    # briefly readable by anyone but this process's user.
    handle = os.open(creds, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        fh.write(f"username=x-access-token\npassword={token}\n")
    return creds


def _helper(creds: Path) -> str:
    """git's `!<shell>` credential helper, pointed at our file.

    The path is `shlex.quote`d because `RM_WEB_ROOT` is an operator's choice and
    may hold a space. Nothing in it comes from a request.
    """
    return f"!f() {{ cat {shlex.quote(str(creds))}; }}; f"


def _arm_repo(repo: Path, creds: Path, *, env: dict, token: str) -> None:
    """Teach the cloned repository to authenticate, and never to run a hook."""
    _git(["config", "--local", "core.hooksPath", os.devnull],
         env=env, cwd=repo, timeout=15.0, token=token)
    # An empty entry first: git reads the local config after the global one, and
    # an empty value *resets* the helper list. Without it an operator's own
    # credential helper would get first refusal on a stranger's repository.
    for value in ("", _helper(creds)):
        _git(["config", "--local", "--add", "credential.helper", value],
             env=env, cwd=repo, timeout=15.0, token=token)


def clone(
    token: str,
    full_name: str,
    branch: str | None,
    dest: Path | str,
    *,
    max_bytes: int | None = None,
    timeout: float = CLONE_TIMEOUT,
    credentials_at: Path | str | None = None,
) -> None:
    """Shallow-clone `full_name` into `dest`, or leave `dest` exactly as it was.

    `dest` is the session vault. The clone happens in a temp directory *beside*
    it — same filesystem, so the move into place is a rename and not a copy that
    can fail halfway — and only lands once the checkout is complete and inside
    its size ceiling. A failed clone must leave no half-vault: a folder holding
    three files out of a repository looks like a vault and is not one.

    Anything already at `dest` is moved aside for the duration and restored if
    the clone does not finish, so a re-connect that fails does not cost the user
    what they had.
    """
    token = token or ""
    if not token:
        raise GitHubError(NOT_CONFIGURED if not available() else "not connected to GitHub")
    full_name = _safe_full_name(full_name)
    branch = _safe_ref(branch)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    creds = Path(credentials_at) if credentials_at else dest.parent / ".git-credential"

    staging = Path(tempfile.mkdtemp(prefix=".clone-", dir=dest.parent))
    holding: Path | None = None
    had_creds = creds.exists()
    try:
        repo = staging / "repo"
        scratch = staging / "home"
        (scratch / "config").mkdir(parents=True, exist_ok=True)
        env = _git_env(scratch)

        # The host is a literal and stays one. A configurable GitHub host would
        # be the single most useful parameter an attacker could ask this module
        # for, and no legitimate caller needs it.
        #
        # The username in the URL is not a secret and never was; the token
        # arrives through the credential helper below, so it is in no argv, no
        # process listing and no `git remote -v`.
        url = f"https://x-access-token@github.com/{full_name}.git"
        args = [
            "clone",
            "--depth", "1",
            "--single-branch",
            "--no-recurse-submodules",
            # No template, so not one sample hook is copied into the clone.
            "--template=",
            "--quiet",
            *(["--branch", branch] if branch else []),
            "--", url, str(repo),
        ]
        # The credential file has to exist before the clone asks for it, and the
        # repository has to exist before `git config --local` has anywhere to
        # write. So: write the credential, clone with the helper named inline,
        # then persist the same helper into the repository for every later push.
        creds = credential_file(creds, token, vault=dest)
        proc = _git(["-c", f"credential.helper={_helper(creds)}", *args],
                    env=env, timeout=timeout, token=token)
        if proc.returncode != 0:
            detail = _redact((proc.stderr or proc.stdout).strip(), token)
            tail = "\n  ".join(detail.splitlines()[-4:])
            raise GitHubError(f"cannot clone {full_name}:\n  {tail}")

        if not repo.is_dir():
            # git said it worked and there is no checkout. Nothing good follows
            # from moving that into the vault.
            raise GitHubError(f"cannot clone {full_name}: git produced no checkout")

        ceiling = max_bytes if max_bytes and max_bytes > 0 else None
        if ceiling is not None:
            size = _tree_bytes(repo, ceiling)
            if size > ceiling:
                raise GitHubError(
                    f"{full_name} is larger than this server allows "
                    f"({size // (1024 * 1024)} MB against a {ceiling // (1024 * 1024)} MB limit).\n"
                    "  Work on it locally, or connect a smaller repository."
                )

        # Hooks, again, from the inside. `git clone` never fetches the remote's
        # hooks so there should be none — but the engine's `gitsync` does not
        # disable them and is not ours to edit, and every later git run in this
        # vault reads this config.
        _arm_repo(repo, creds, env=env, token=token)

        if dest.exists():
            holding = staging / "previous"
            shutil.move(str(dest), str(holding))
        shutil.move(str(repo), str(dest))
        holding = None
    except Exception:
        if holding is not None and not dest.exists():
            shutil.move(str(holding), str(dest))
        # A credential with no repository behind it is a token sitting on disk
        # for nothing. It would die with the session anyway; there is no reason
        # for it to live that long.
        if not had_creds:
            creds.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ── sync ─────────────────────────────────────────────────────────────────────


def _bridge_run(session: Any, args: list[str]) -> Any:
    """The engine, through the bridge the rest of `web/` shares.

    Imported inside the call rather than at module scope so this module can be
    read, tested and reasoned about without the bridge — and so a missing bridge
    reports itself in one sentence instead of an ImportError at server start.
    """
    from . import bridge  # noqa: PLC0415 — deliberate: see above

    return bridge.run(session, args)


def _result(run: Any) -> tuple[int, str, str]:
    """A bridge result, read without caring which shape it is."""
    if isinstance(run, dict):
        return int(run.get("code", 1)), str(run.get("stdout", "")), str(run.get("stderr", ""))
    return int(getattr(run, "code", 1)), str(getattr(run, "stdout", "")), str(getattr(run, "stderr", ""))


def state(session: Any, *, run: Callable[[Any, list[str]], Any] | None = None) -> dict:
    """What the repository is doing, straight from `sync --status --json`.

    Not computed here, not cached here. `gitsync.state` is cheap by design and
    never raises, and a second answer to "what branch is this" living in the web
    layer is the thing that would drift.
    """
    call = run or _bridge_run
    code, stdout, stderr = _result(call(session, ["sync", "--status", "--json"]))
    if code != 0 and not stdout.strip():
        raise GitHubError((stderr or stdout).strip() or f"sync --status exited {code}")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        raise GitHubError("the engine did not answer `sync --status --json` with JSON") from None
    if not isinstance(payload, dict):
        raise GitHubError("the engine did not answer `sync --status --json` with an object")
    return payload


def sync(
    session: Any,
    message: str | None = None,
    push: bool = False,
    *,
    run: Callable[[Any, list[str]], Any] | None = None,
) -> dict:
    """Commit the vault, and push only when the user asked for it.

    Every rule that makes this safe lives in `engine/gitsync.py`, and that is
    the point: the same guarantees hold from a terminal, from CI, from the
    desktop app and from here. Never `--force`; never without an upstream; never
    from a detached HEAD; never when behind; never a path outside the vault.

    So the only thing this function may add is the `push` flag, and the only
    thing it may do with a refusal is repeat it. `gitsync`'s refusals each name
    the exact command that fixes them — `git push -u origin <branch>`,
    `git pull --rebase` — and a web layer that summarised them into "push
    failed" would be teaching the writer to reach for `--force` unaided, which
    is the outcome that module exists to prevent.

    `push` is never defaulted true and is never inferred from a setting. It is
    an explicit user action, every time.
    """
    call = run or _bridge_run
    args = ["sync", "--json"]
    if message is not None:
        text = str(message).strip()
        if text:
            # No shell is involved — the bridge spawns argv — so a message is
            # just a string. It is passed after `-m` as its own argument, which
            # is also why a message beginning with `-` is harmless here.
            args += ["-m", text]
    if push:
        args.append("--push")

    code, stdout, stderr = _result(call(session, args))
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        # `sync` exits 1 when a push was refused *and still prints its JSON*, so
        # reaching here means something else went wrong — git missing, the vault
        # not a repository. The engine's own sentence is the answer.
        raise GitHubError((stderr or stdout).strip() or f"sync exited {code}") from None

    if not isinstance(payload, dict):
        raise GitHubError("the engine did not answer `sync --json` with an object")
    result = payload.get("sync")
    if isinstance(result, dict) and result.get("refused"):
        # Carried through untouched, and flagged so the route can answer 409
        # rather than 200 with a hidden failure.
        payload["refused"] = result["refused"]
    payload["code"] = code
    return payload
