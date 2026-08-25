"""GitHub mode, and the four ways it must fail.

Nothing here touches the network. What needs proving is not that GitHub answers
— it does — but that this server behaves when GitHub is *not* configured, when a
callback arrives with the wrong state, when a branch name is an option in
disguise, and when a clone dies halfway. Those are the cases a live test would
never reach and the ones that matter.

The dash-ref test is the one to read first. `engine/install.py` carries a
comment about a ref beginning with `-` reaching `git fetch` as an option and
being executed; this asserts the same guard here, and asserts it fires before
git is spawned at all.

    python3 -m unittest discover -s web/tests -t .
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from web.server import github  # noqa: E402

ENV_KEYS = (
    "RM_WEB_GITHUB_CLIENT_ID",
    "RM_WEB_GITHUB_CLIENT_SECRET",
    "RM_WEB_GITHUB_CALLBACK",
    "RM_WEB_GITHUB_TOKEN",
)


class EnvCase(unittest.TestCase):
    """A test that owns the GitHub environment for its duration."""

    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def configure(self) -> None:
        os.environ["RM_WEB_GITHUB_CLIENT_ID"] = "Iv1.testclient"
        os.environ["RM_WEB_GITHUB_CLIENT_SECRET"] = "s3cr3t"
        os.environ["RM_WEB_GITHUB_CALLBACK"] = "https://reports.example/api/github/callback"


# ── unconfigured ─────────────────────────────────────────────────────────────


class TestUnconfigured(EnvCase):
    """With no client id and secret, GitHub mode is off and says so.

    The failure being designed against is a button that redirects into an OAuth
    flow which bounces off GitHub with `client_id is required` — the user reads
    that as "this product is broken" rather than "this server was not set up".
    """

    def test_configured_is_false(self) -> None:
        self.assertFalse(github.configured())
        self.assertFalse(github.available())

    def test_half_configured_is_still_off(self) -> None:
        os.environ["RM_WEB_GITHUB_CLIENT_ID"] = "Iv1.testclient"
        self.assertFalse(github.configured())

    def test_empty_string_is_not_configuration(self) -> None:
        os.environ["RM_WEB_GITHUB_CLIENT_ID"] = "   "
        os.environ["RM_WEB_GITHUB_CLIENT_SECRET"] = ""
        self.assertFalse(github.configured())

    def test_status_carries_the_reason(self) -> None:
        payload = github.status()
        self.assertEqual(payload["mode"], "off")
        self.assertIn("not configured on this server", payload["reason"])

    def test_authorize_url_refuses(self) -> None:
        with self.assertRaises(github.GitHubError) as caught:
            github.authorize_url("some-state")
        self.assertIn("not configured on this server", str(caught.exception))

    def test_exchange_refuses(self) -> None:
        with self.assertRaises(github.GitHubError) as caught:
            github.exchange("code")
        self.assertIn("not configured on this server", str(caught.exception))

    def test_server_token_still_offers_github_mode(self) -> None:
        """A self-hosted single user needs no OAuth app — and the token is read
        from the server's environment, never from a request."""
        os.environ["RM_WEB_GITHUB_TOKEN"] = "ghp_serverside"
        self.assertFalse(github.configured())
        self.assertTrue(github.available())
        self.assertEqual(github.status()["mode"], "token")
        self.assertEqual(github.token_for({}), "ghp_serverside")

    def test_configured_flow_builds_a_url(self) -> None:
        self.configure()
        url = github.authorize_url("st4te")
        self.assertTrue(url.startswith("https://github.com/login/oauth/authorize?"))
        self.assertIn("state=st4te", url)
        self.assertIn("client_id=Iv1.testclient", url)
        # The secret has no business in a URL the browser is about to follow.
        self.assertNotIn("s3cr3t", url)

    def test_authorize_url_requires_a_state(self) -> None:
        self.configure()
        with self.assertRaises(github.GitHubError):
            github.authorize_url("")


# ── the state parameter ──────────────────────────────────────────────────────


class TestState(unittest.TestCase):
    """`state` is required, single-use, and compared with `compare_digest`."""

    def setUp(self) -> None:
        self.states = github.StateStore()

    def test_the_right_state_is_accepted_once(self) -> None:
        state = self.states.issue("session-a")
        self.assertTrue(self.states.consume("session-a", state))
        # Single use: a state recovered from a browser history is already spent.
        self.assertFalse(self.states.consume("session-a", state))

    def test_a_mismatch_is_refused(self) -> None:
        self.states.issue("session-a")
        self.assertFalse(self.states.consume("session-a", "not-the-state"))

    def test_a_missing_state_is_refused(self) -> None:
        self.states.issue("session-a")
        for value in (None, "", 0, [], {}):
            self.assertFalse(self.states.consume("session-a", value))  # type: ignore[arg-type]

    def test_another_session_cannot_spend_it(self) -> None:
        """The whole point: an attacker's authorization code must not land in
        somebody else's session."""
        state = self.states.issue("session-a")
        self.assertFalse(self.states.consume("session-b", state))

    def test_an_expired_state_is_refused(self) -> None:
        short = github.StateStore(ttl=-1.0)
        state = short.issue("session-a")
        self.assertFalse(short.consume("session-a", state))

    def test_a_flood_is_bounded(self) -> None:
        for index in range(github.STATE_MAX):
            self.states.issue(f"session-{index}")
        with self.assertRaises(github.GitHubError):
            self.states.issue("one-too-many")

    def test_no_session_no_state(self) -> None:
        with self.assertRaises(github.GitHubError):
            self.states.issue("")


# ── the token stays on the server ────────────────────────────────────────────


class TestTokenContainment(EnvCase):
    def test_connection_never_carries_the_token(self) -> None:
        session: dict = {}
        github.remember(session, "ghp_verysecret", login="writer", repo="acme/reports")
        payload = github.connection(session)
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["login"], "writer")
        self.assertNotIn("token", payload)
        self.assertNotIn("ghp_verysecret", repr(payload))

    def test_forget_disconnects(self) -> None:
        session: dict = {}
        github.remember(session, "ghp_verysecret")
        github.forget(session)
        self.assertIsNone(github.token_for(session))
        self.assertFalse(github.connection(session)["connected"])

    def test_an_empty_token_is_not_remembered(self) -> None:
        with self.assertRaises(github.GitHubError):
            github.remember({}, "")

    def test_an_object_session_works_too(self) -> None:
        class Record:
            pass

        session = Record()
        github.remember(session, "ghp_object")
        self.assertEqual(github.token_for(session), "ghp_object")
        self.assertNotIn("token", github.connection(session))


# ── nothing walks the token off github.com ───────────────────────────────────


class TestHostPinning(unittest.TestCase):
    def test_only_github_over_https(self) -> None:
        for bad in (
            "http://github.com/login",
            "https://github.com.evil.test/login",
            "https://raw.githubusercontent.com/x",
            "https://127.0.0.1/login",
            "file:///etc/passwd",
        ):
            with self.assertRaises(github.GitHubError):
                github._check_host(bad)

    def test_the_real_hosts_pass(self) -> None:
        github._check_host("https://api.github.com/user/repos?page=2")
        github._check_host("https://github.com/login/oauth/access_token")

    def test_a_next_link_off_host_is_not_followed(self) -> None:
        header = '<https://evil.test/user/repos?page=2>; rel="next"'
        self.assertIsNone(github._next_link(header))

    def test_a_next_link_on_host_is_followed(self) -> None:
        header = '<https://api.github.com/user/repos?page=2>; rel="next", <https://api.github.com/user/repos?page=9>; rel="last"'
        self.assertEqual(github._next_link(header), "https://api.github.com/user/repos?page=2")

    def test_no_link_header_ends_the_walk(self) -> None:
        self.assertIsNone(github._next_link(None))
        self.assertIsNone(github._next_link('<https://api.github.com/x>; rel="last"'))


# ── the clone guards ─────────────────────────────────────────────────────────


class TestCloneGuards(EnvCase):
    """A branch and a repository name are values, never options.

    `engine/install.py::_safe_ref` records why: `git fetch` parses options after
    its positionals, so a ref of `--upload-pack=<command>` reached git as an
    option and *was executed*. The guard is copied here rather than imported —
    a different trust context — so it is tested here too.
    """

    def setUp(self) -> None:
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="rm-web-clone-"))
        self.vault = self.tmp / "vault"
        self.spawned: list[list[str]] = []
        self._real_git = github._git
        github._git = self._record  # type: ignore[assignment]

    def tearDown(self) -> None:
        github._git = self._real_git  # type: ignore[assignment]
        super().tearDown()

    def _record(self, args, *, env, cwd=None, timeout, token):
        """A git that records what it was asked and leaves a plausible checkout."""
        self.spawned.append(list(args))
        if "clone" in args:
            Path(args[-1]).mkdir(parents=True, exist_ok=True)
            (Path(args[-1]) / "report-maker.toml").write_text("cloned\n", encoding="utf-8")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def test_a_ref_beginning_with_a_dash_is_refused(self) -> None:
        with self.assertRaises(github.GitHubError) as caught:
            github.clone("ghp_x", "acme/reports", "--upload-pack=touch /tmp/pwned", self.vault)
        self.assertIn("may not begin with '-'", str(caught.exception))
        # Refused before git existed in the picture, not after.
        self.assertEqual(self.spawned, [])
        self.assertFalse(self.vault.exists())

    def test_a_ref_with_whitespace_is_refused(self) -> None:
        with self.assertRaises(github.GitHubError):
            github.clone("ghp_x", "acme/reports", "main --upload-pack=sh", self.vault)
        self.assertEqual(self.spawned, [])

    def test_a_ref_with_a_control_character_is_refused(self) -> None:
        with self.assertRaises(github.GitHubError):
            github.clone("ghp_x", "acme/reports", "main\nrm -rf /", self.vault)
        self.assertEqual(self.spawned, [])

    def test_a_repository_that_is_not_owner_slash_name_is_refused(self) -> None:
        for bad in (
            "-oProxyCommand=sh",
            "../../etc",
            "acme/reports/../../..",
            "https://evil.test/acme/reports",
            "ext::sh -c whoami",
            "acme",
            "",
        ):
            with self.subTest(repo=bad):
                with self.assertRaises(github.GitHubError):
                    github.clone("ghp_x", bad, "main", self.vault)
        self.assertEqual(self.spawned, [])

    def test_no_token_is_refused_before_git(self) -> None:
        with self.assertRaises(github.GitHubError):
            github.clone("", "acme/reports", "main", self.vault)
        self.assertEqual(self.spawned, [])

    def test_the_hardening_is_on_every_invocation(self) -> None:
        """Hooks off and `ext::` off, said explicitly rather than relied on from
        whatever git config the machine happens to have."""
        flags = " ".join(github.GIT_HARDENING)
        self.assertIn("core.hooksPath=/dev/null", flags)
        self.assertIn("protocol.ext.allow=never", flags)

    def test_a_failed_clone_leaves_no_half_vault(self) -> None:
        def fails(args, *, env, cwd=None, timeout, token):
            self.spawned.append(list(args))
            if args and args[0] == "clone" or "clone" in args:
                return subprocess.CompletedProcess(args, 128, "", "fatal: repository not found")
            return subprocess.CompletedProcess(args, 0, "", "")

        github._git = fails  # type: ignore[assignment]
        self.vault.mkdir(parents=True)
        (self.vault / "report-maker.toml").write_text("keep me\n", encoding="utf-8")

        with self.assertRaises(github.GitHubError) as caught:
            github.clone("ghp_x", "acme/reports", "main", self.vault)
        self.assertIn("cannot clone acme/reports", str(caught.exception))

        # What was there is still there, byte for byte, and nothing else is.
        self.assertEqual((self.vault / "report-maker.toml").read_text(), "keep me\n")
        leftovers = [p.name for p in self.vault.parent.iterdir() if p.name.startswith(".clone-")]
        self.assertEqual(leftovers, [])
        # And no token left sitting on disk for a repository that never landed.
        self.assertFalse((self.vault.parent / ".git-credential").exists())

    def test_the_clone_never_puts_the_token_in_a_command_line(self) -> None:
        """The token reaches git through a credential helper, so it is in no
        argv and therefore in no `ps` listing on a shared machine."""
        github.clone("ghp_verysecret", "acme/reports", "main", self.vault)
        self.assertTrue(self.spawned, "expected git to have been invoked")
        for args in self.spawned:
            self.assertNotIn("ghp_verysecret", " ".join(args))

    def test_the_checkout_lands_and_the_credential_does_not(self) -> None:
        github.clone("ghp_verysecret", "acme/reports", "main", self.vault)
        self.assertEqual((self.vault / "report-maker.toml").read_text(), "cloned\n")
        # The credential sits beside the vault, never inside it — `sync` stages
        # `.` across the vault and would push it to the repository it unlocks.
        inside = [p.name for p in self.vault.rglob("*") if "credential" in p.name]
        self.assertEqual(inside, [])
        self.assertTrue((self.vault.parent / ".git-credential").is_file())
        # And the clone left no staging directory behind.
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith(".clone-")], [])

    def test_hooks_are_disabled_in_the_cloned_repository_too(self) -> None:
        """`git clone` never fetches the remote's hooks, but the engine's
        `gitsync` does not disable them and is not ours to edit. This closes it
        from the outside, for every later git run in that vault."""
        github.clone("ghp_x", "acme/reports", None, self.vault)
        configured = [a for a in self.spawned if a[:3] == ["config", "--local", "core.hooksPath"]]
        self.assertEqual(len(configured), 1)


class TestCredentialFile(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="rm-web-cred-"))
        self.vault = self.tmp / "vault"
        self.vault.mkdir()

    def test_it_is_never_written_inside_the_vault(self) -> None:
        """`report-maker sync` stages `.` across the vault. A credential one
        directory lower would be committed and pushed to the repository it
        unlocks."""
        with self.assertRaises(github.GitHubError) as caught:
            github.credential_file(self.vault / "deep" / ".git-credential", "ghp_x", vault=self.vault)
        self.assertIn("`sync` would commit it", str(caught.exception))
        self.assertFalse((self.vault / "deep").exists())

    def test_it_is_owner_readable_only(self) -> None:
        path = github.credential_file(self.tmp / ".git-credential", "ghp_x", vault=self.vault)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertIn("password=ghp_x", path.read_text(encoding="utf-8"))


# ── sync ─────────────────────────────────────────────────────────────────────


class Run:
    """What the bridge hands back."""

    def __init__(self, code: int, stdout: str = "", stderr: str = "") -> None:
        self.code, self.stdout, self.stderr = code, stdout, stderr


class TestSync(unittest.TestCase):
    """The engine owns every rule; this layer owns one flag and repeats the
    refusals word for word."""

    def setUp(self) -> None:
        self.calls: list[list[str]] = []

    def _run(self, payload: str, code: int = 0):
        def run(session, args):
            self.calls.append(list(args))
            return Run(code, payload)

        return run

    def test_it_never_pushes_unless_asked(self) -> None:
        github.sync({}, "a message", run=self._run('{"repo": true, "sync": {"committed": "abc"}}'))
        self.assertEqual(self.calls, [["sync", "--json", "-m", "a message"]])
        self.assertNotIn("--push", self.calls[0])

    def test_push_is_an_explicit_flag(self) -> None:
        github.sync({}, None, True, run=self._run('{"repo": true, "sync": {}}'))
        self.assertEqual(self.calls, [["sync", "--json", "--push"]])

    def test_it_never_forces(self) -> None:
        github.sync({}, "m", True, run=self._run('{"sync": {}}'))
        joined = " ".join(self.calls[0])
        for forbidden in ("--force", "--force-with-lease", "--delete", "-f"):
            self.assertNotIn(forbidden, joined)

    def test_a_refusal_is_surfaced_verbatim(self) -> None:
        """`gitsync`'s refusals each name the command that fixes them. A web
        layer that summarised them would teach people to reach for --force."""
        refusal = (
            "no upstream for main — set one with `git push -u origin main`.\n"
            "  Pushing without one means guessing where the work should go."
        )
        payload = json.dumps(
            {"repo": True, "sync": {"committed": None, "pushed": False, "refused": refusal}}
        )
        result = github.sync({}, None, True, run=self._run(payload, code=1))
        self.assertEqual(result["refused"], refusal)
        self.assertEqual(result["sync"]["refused"], refusal)
        self.assertIn("git push -u origin main", result["refused"])

    def test_a_blank_message_is_left_to_the_engine(self) -> None:
        """`gitsync.default_message` already knows what a commit is called."""
        github.sync({}, "   ", run=self._run('{"sync": {}}'))
        self.assertEqual(self.calls[0], ["sync", "--json"])

    def test_a_non_json_answer_becomes_the_engine_s_own_sentence(self) -> None:
        def run(session, args):
            return Run(1, "", "git is not installed — `report-maker sync` keeps the vault's history with git")

        with self.assertRaises(github.GitHubError) as caught:
            github.sync({}, None, run=run)
        self.assertIn("git is not installed", str(caught.exception))

    def test_state_reads_the_engine(self) -> None:
        state = github.state({}, run=self._run('{"repo": true, "branch": "main", "behind": 2}'))
        self.assertEqual(self.calls, [["sync", "--status", "--json"]])
        self.assertEqual(state["branch"], "main")


class TestErrorsNeverLeakTheToken(unittest.TestCase):
    def test_git_output_is_redacted(self) -> None:
        noisy = "fatal: could not read Username for 'https://x:ghp_secret@github.com'"
        self.assertNotIn("ghp_secret", github._redact(noisy, "ghp_secret"))

    def test_an_http_error_reports_github_s_words(self) -> None:
        import io

        exc = urllib.error.HTTPError(
            "https://api.github.com/user", 401, "Unauthorized", {},
            io.BytesIO(b'{"message": "Bad credentials"}'),
        )
        try:
            self.assertEqual(github._http_message(exc), "GitHub rejected the credential — sign in again.")
        finally:
            exc.close()

    def test_github_s_own_message_survives(self) -> None:
        """A rate limit says when it lifts. A paraphrase would not."""
        import io

        exc = urllib.error.HTTPError(
            "https://api.github.com/user/repos", 403, "Forbidden", {},
            io.BytesIO(b'{"message": "API rate limit exceeded for user"}'),
        )
        try:
            self.assertIn("rate limit exceeded", github._http_message(exc))
        finally:
            exc.close()


if __name__ == "__main__":
    unittest.main()
