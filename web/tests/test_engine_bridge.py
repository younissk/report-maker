"""The subprocess bridge, tested as the boundary it is.

Every test here spawns a real process. Mocking `subprocess` would test that this
module calls the functions it calls, which is the one thing nobody doubts — the
questions worth answering are whether a shell ever sees an argument, whether a
killed command really takes typst's grandchild with it, and whether a crafted
`-C` can walk out of the sessions root. None of those can be answered without an
operating system in the loop.

The engine under test is usually a *fake* `report-maker`: a script that echoes
its argv, hangs, floods or fails on demand, reached through `RM_WEB_ENGINE`. That
is not a shortcut. A test that shelled out to the real engine to prove a timeout
would be measuring typst, and a test that proved argv safety by running `list`
would pass just as well if the arguments had been mangled beyond recognition.
The fake makes the argument itself the assertion. Two tests do use the real
engine, and they are the two where realness is the point: that `--version`
answers, and that `bin/report-maker` recovers from a stale `python3`.

    python3 -m unittest discover -s web/tests
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from json import loads as json_loads
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from web.server import engine  # noqa: E402

# The fake engine. `-C <vault>` is prepended by the bridge, so it is stripped
# here before the mode is read — which also means every test proves in passing
# that the bridge put it there.
FAKE = '''#!{python}
import json
import os
import pathlib
import subprocess
import sys
import time

args = sys.argv[1:]
vault = None
if args[:1] == ["-C"]:
    vault, args = args[1], args[2:]
mode = args[0] if args else ""
rest = args[1:]

if mode == "echo-argv":
    print(json.dumps({{"vault": vault, "args": args}}))
elif mode == "echo-env":
    print(json.dumps(dict(os.environ)))
elif mode == "echo-cwd":
    print(json.dumps({{"cwd": os.getcwd()}}))
elif mode == "fail":
    print("half an answer")
    print("E999 sources.yml:12 the engine's own words", file=sys.stderr)
    sys.exit(3)
elif mode == "not-json":
    print("stage\\ndiagrams\\n")
    print("W007 a warning the engine printed", file=sys.stderr)
elif mode == "hang":
    # A grandchild that outlives its parent unless the whole *group* is killed.
    pidfile, marker = rest[0], rest[1]
    child = subprocess.Popen([
        sys.executable, "-c",
        "import sys,time,pathlib;time.sleep(30);"
        "pathlib.Path(sys.argv[1]).write_text('the grandchild survived')",
        marker,
    ])
    pathlib.Path(pidfile).write_text(str(child.pid))
    print("started", flush=True)
    time.sleep(30)
elif mode == "flood":
    line = "x" * 4096 + "\\n"
    while True:
        sys.stdout.write(line)
elif mode == "lines":
    for n in range(int(rest[0])):
        print(f"line {{n}}", flush=True)
        time.sleep(0.02)
elif mode == "diagrams" or mode == "template":
    print(json.dumps({{"ran": args}}))
    pathlib.Path(os.environ["FAKE_RAN"]).write_text(" ".join(args))
else:
    print(f"unknown mode {{mode!r}}", file=sys.stderr)
    sys.exit(2)
'''


def quietly(action):
    """Run something that announces the located engine, without the noise."""
    with redirect_stderr(io.StringIO()):
        return action()


class BridgeTest(unittest.TestCase):
    """A sessions root, a session vault inside it, and a fake engine."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "sessions"
        self.vault = self.root / "s-abc" / "vault"
        self.vault.mkdir(parents=True)
        (self.vault / "report-maker.toml").write_text("[vault]\n", encoding="utf-8")

        self.fake = self.base / "fake-report-maker"
        self.fake.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
        self.fake.chmod(0o755)

        self.env = dict(os.environ)
        os.environ["RM_WEB_ENGINE"] = str(self.fake)
        os.environ.pop("RM_WEB_DIAGRAMS", None)
        os.environ["FAKE_RAN"] = str(self.base / "ran.txt")
        engine.set_sessions_root(self.root)
        quietly(lambda: engine.locate(refresh=True))

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env)
        engine.set_sessions_root(self.base)  # no test may inherit another's root
        quietly(lambda: engine.locate(refresh=True))
        self.tmp.cleanup()

    def echo(self, *args: str) -> dict:
        result = engine.run(self.vault, ["echo-argv", *args])
        self.assertEqual(result.code, 0, result.stderr)
        return json_loads(result.stdout)

    def ran(self) -> bool:
        """Whether the fake engine was reached at all. A refusal must not."""
        return (self.base / "ran.txt").exists()


# ── argv is a list, and stays one ────────────────────────────────────────────


class ArgvIsNeverAShell(BridgeTest):
    def test_shell_metacharacters_arrive_verbatim(self) -> None:
        """The one property everything else rests on.

        A report id comes from a stranger. If any layer between here and `execve`
        joined the arguments into a string, `; touch …` would be punctuation
        rather than text — so the test asserts both halves: the token survived
        intact, *and* the command it would have been never ran.
        """
        bomb = self.base / "pwned"
        hostile = [
            f"; touch {bomb}",
            f"$(touch {bomb})",
            f"`touch {bomb}`",
            f"&& touch {bomb}",
            f"| touch {bomb}",
            "a b\tc\nd",
            "--not-a-flag=$HOME",
            "'quoted'",
            '"double"',
            "\\backslash",
            "*",
        ]
        got = self.echo(*hostile)
        self.assertEqual(got["args"][1:], hostile)
        self.assertFalse(bomb.exists(), "a shell interpreted an argument")

    def test_the_display_command_is_not_the_argv(self) -> None:
        """`command` is for a log line, `argv` is the truth. They must not be
        confusable: quoting the display string is what stops a reader (or a
        future caller) pasting it somewhere it would be re-split."""
        result = engine.run(self.vault, ["echo-argv", "a b; rm -rf /"])
        self.assertIn("'a b; rm -rf /'", result.command)
        self.assertIn("a b; rm -rf /", result.argv)

    def test_the_vault_is_the_cwd_as_well_as_the_C(self) -> None:
        """Commands with no target resolve the nearest vault above the working
        directory, so the two have to agree or `-C` is not the only answer."""
        got = self.echo()
        self.assertEqual(got["vault"], str(self.vault))
        cwd = json_loads(engine.run(self.vault, ["echo-cwd"]).stdout)["cwd"]
        self.assertEqual(Path(cwd).resolve(), self.vault)


# ── the deadline ─────────────────────────────────────────────────────────────


class Deadlines(BridgeTest):
    def hang(self, timeout: float = 1.0) -> tuple[engine.Run, Path, int]:
        pidfile = self.base / "grandchild.pid"
        marker = self.base / "survived"
        result = engine.run(
            self.vault, ["hang", str(pidfile), str(marker)], timeout=timeout
        )
        self.assertTrue(pidfile.exists(), "the fake never started its grandchild")
        return result, marker, int(pidfile.read_text())

    def test_a_timeout_kills_and_says_so(self) -> None:
        result, _, _ = self.hang()
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.code, 0)
        self.assertIn(engine.TIMEOUT_MARKER, result.stderr)
        # Bounded by the deadline plus the grace it allows for a clean stop.
        self.assertLess(result.duration, 1.0 + engine.GRACE)

    def test_the_kill_reaches_the_grandchild(self) -> None:
        """typst, git and node are grandchildren of `report-maker`. Killing the
        child alone would orphan them, which is the runaway a deadline exists to
        prevent — so the process *group* is what gets signalled."""
        _, marker, pid = self.hang()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail(f"grandchild {pid} outlived the kill")
        self.assertFalse(marker.exists())

    def test_what_was_captured_before_the_deadline_survives(self) -> None:
        """A timed-out build has usually printed the phase it died in, and that
        line is the whole diagnosis. Losing it would make every timeout look
        identical."""
        result, _, _ = self.hang()
        self.assertIn("started", result.stdout)

    def test_an_unbounded_flood_is_stopped(self) -> None:
        """Sixty seconds of output into a list is a memory problem, not a
        timeout. The budget kills rather than waits."""
        result = engine.run(self.vault, ["flood"], timeout=30)
        self.assertTrue(result.truncated)
        self.assertIn(engine.TRUNCATED_MARKER, result.stderr)
        self.assertLessEqual(len(result.stdout), engine.MAX_OUTPUT + 65536)
        self.assertLess(result.duration, 30)


# ── failure, reported in the engine's own words ──────────────────────────────


class Failures(BridgeTest):
    def test_a_non_zero_exit_raises_with_stderr_intact(self) -> None:
        with self.assertRaises(engine.EngineError) as caught:
            engine.json(self.vault, ["fail"])
        self.assertIn("E999 sources.yml:12 the engine's own words", str(caught.exception))
        self.assertEqual(caught.exception.run.code, 3)

    def test_unparseable_output_surfaces_what_the_engine_said(self) -> None:
        """The failure here is never "invalid JSON" — it is whatever the engine
        printed instead, which is the only text that can tell anyone why."""
        with self.assertRaises(engine.EngineError) as caught:
            engine.json(self.vault, ["not-json"])
        self.assertIn("W007 a warning the engine printed", str(caught.exception))

    def test_run_does_not_raise_on_a_non_zero_exit(self) -> None:
        """`check` failing is the product working. `run` reports it as a Run so
        a route can answer 200-with-findings rather than 500."""
        result = engine.run(self.vault, ["fail"])
        self.assertEqual(result.code, 3)
        self.assertFalse(result.ok)
        self.assertIn("half an answer", result.stdout)

    def test_a_missing_engine_is_a_run_not_an_explosion(self) -> None:
        os.environ.pop("RM_WEB_ENGINE")
        os.environ["PATH"] = str(self.base)  # no report-maker, no repo checkout
        engine._located = None
        original = engine.__file__
        try:
            # Pretend this module lives outside the repository, so the checkout
            # fallback cannot find bin/report-maker either.
            engine.__file__ = str(self.base / "server" / "engine.py")
            with self.assertRaises(engine.EngineMissing):
                quietly(lambda: engine.locate(refresh=True))
        finally:
            engine.__file__ = original
            engine._located = None


# ── the denylist ─────────────────────────────────────────────────────────────


class Denials(BridgeTest):
    def assertRefused(self, args: list[str], because: str) -> None:
        with self.assertRaises(engine.Refused) as caught:
            engine.run(self.vault, args)
        self.assertIn(because, str(caught.exception).lower())
        self.assertFalse(self.ran(), "a refused command was spawned anyway")

    def test_template_install_is_refused(self) -> None:
        """Spec requirement 5: it clones an arbitrary git repository named in the
        request. The refusal lives at the bridge because a route can forget."""
        self.assertRefused(
            ["template", "install", "https://github.com/attacker/design"], "disabled"
        )

    def test_template_update_is_refused(self) -> None:
        """The same hole with a different door: it re-fetches the git URLs
        recorded in the vault, and in GitHub mode the vault is the user's."""
        self.assertRefused(["template", "update"], "disabled")

    def test_harmless_template_commands_still_work(self) -> None:
        """An over-broad denial would be its own bug — `templates` and
        `template new` touch nothing outside the vault."""
        self.assertEqual(engine.run(self.vault, ["template", "new", "x"]).code, 0)
        self.assertTrue(self.ran())

    def test_diagrams_are_refused_when_the_env_says_so(self) -> None:
        self.assertRefused(["diagrams"], "headless chrome")
        self.assertRefused(["diagrams", "--prepare", "a.mmd"], "headless chrome")

    def test_diagrams_run_when_the_operator_enables_them(self) -> None:
        os.environ["RM_WEB_DIAGRAMS"] = "1"
        self.assertEqual(engine.run(self.vault, ["diagrams"]).code, 0)
        self.assertTrue(self.ran())

    def test_a_report_id_that_reads_like_a_denied_command_is_not_one(self) -> None:
        """The match is on the subcommand shape, not on any token anywhere: a
        report called `template` is a report."""
        got = self.echo("template", "install")
        self.assertEqual(got["args"], ["echo-argv", "template", "install"])

    def test_the_vault_cannot_be_redirected_by_an_argument(self) -> None:
        """`-C` is the containment. Every spelling of "work somewhere else" is
        refused, including the attached-value short form."""
        for arg in ["-C", "-C/etc", "--vault", "--vault=/etc", "--vault=../.."]:
            self.assertRefused(["echo-argv", arg], "session")

    def test_a_nul_byte_is_refused(self) -> None:
        self.assertRefused(["echo-argv", "a\x00b"], "nul")

    def test_the_guard_can_be_asked_before_a_session_exists(self) -> None:
        with self.assertRaises(engine.Refused):
            engine.guard(["template", "install", "url"])
        engine.guard(["list", "--json"])


# ── containment ──────────────────────────────────────────────────────────────


class Containment(BridgeTest):
    def assertOutside(self, path: Path | str) -> None:
        with self.assertRaises(engine.Refused) as caught:
            engine.run(path, ["echo-argv"])
        self.assertIn("sessions root", str(caught.exception))

    def test_a_vault_outside_the_sessions_root_is_refused(self) -> None:
        self.assertOutside(self.base)
        self.assertOutside(Path(tempfile.gettempdir()))
        self.assertOutside(Path(os.sep))

    def test_the_sessions_root_itself_is_not_a_vault(self) -> None:
        """A `-C` on the root would let one request build another session's
        reports — every vault beneath it is a target."""
        self.assertOutside(self.root)

    def test_dot_dot_cannot_climb_out(self) -> None:
        self.assertOutside(self.vault / ".." / ".." / "..")

    def test_a_symlink_is_judged_by_where_it_lands(self) -> None:
        """Resolve first, compare second. A link planted inside a session that
        points at the filesystem root is outside, however innocent its path."""
        link = self.root / "s-abc" / "escape"
        link.symlink_to(Path(os.sep))
        self.assertOutside(link)

    def test_a_session_vault_is_allowed(self) -> None:
        self.assertEqual(engine.run(self.vault, ["echo-argv"]).code, 0)
        deeper = self.vault / "reports"
        deeper.mkdir()
        self.assertEqual(engine.run(deeper, ["echo-argv"]).code, 0)

    def test_nothing_runs_without_a_declared_root(self) -> None:
        """Fail closed. A server that never declared its sessions root must not
        default to one that would permit `-C /`."""
        engine._root = None
        os.environ.pop("RM_WEB_ROOT", None)
        with self.assertRaises(engine.EngineError) as caught:
            engine.run(self.vault, ["echo-argv"])
        self.assertIn("sessions root", str(caught.exception))

    def test_rm_web_root_implies_the_sessions_folder(self) -> None:
        engine._root = None
        os.environ["RM_WEB_ROOT"] = str(self.base)
        self.assertEqual(engine.sessions_root(), self.root)
        self.assertEqual(engine.run(self.vault, ["echo-argv"]).code, 0)


# ── the environment a command inherits ───────────────────────────────────────


class Environment(BridgeTest):
    def child_env(self) -> dict:
        return json_loads(engine.run(self.vault, ["echo-env"]).stdout)

    def test_no_secret_reaches_the_subprocess(self) -> None:
        """Spec requirement 10 says no secret reaches the browser. The same
        applies downward: `sync` shells out to git, and git runs helpers."""
        os.environ["RM_WEB_GITHUB_CLIENT_SECRET"] = "shh"
        os.environ["GITHUB_TOKEN"] = "ghp_realone"
        os.environ["SOME_API_KEY"] = "sk-live"
        child = self.child_env()
        self.assertNotIn("RM_WEB_GITHUB_CLIENT_SECRET", child)
        self.assertNotIn("GITHUB_TOKEN", child)
        self.assertNotIn("SOME_API_KEY", child)
        self.assertNotIn("shh", child.values())

    def test_a_caller_may_pass_a_credential_deliberately(self) -> None:
        """The push that needs a token gets one, per call. Ambient is the thing
        being refused, not deliberate."""
        child = json_loads(
            engine.run(self.vault, ["echo-env"], env={"GIT_ASKPASS": "/usr/bin/true"}).stdout
        )
        self.assertEqual(child["GIT_ASKPASS"], "/usr/bin/true")

    def test_git_can_never_block_on_a_prompt(self) -> None:
        """A process waiting on a prompt that will never come is a worker held
        until the deadline — a denial of service with extra steps."""
        child = self.child_env()
        self.assertEqual(child["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(child["PYTHONUNBUFFERED"], "1")

    def test_stdin_is_closed(self) -> None:
        result = engine.run(self.vault, ["echo-argv"])
        self.assertEqual(result.code, 0)


# ── streaming ────────────────────────────────────────────────────────────────


class Streaming(BridgeTest):
    def test_lines_arrive_one_at_a_time(self) -> None:
        lines = list(engine.stream(self.vault, ["lines", "5"]))
        self.assertEqual(lines[:5], [f"line {n}" for n in range(5)])

    def test_a_refusal_happens_before_the_response_starts(self) -> None:
        """A generator function defers its body to the first `next()`. If the
        guards lived there, a 403 would land in the middle of a build log."""
        with self.assertRaises(engine.Refused):
            engine.stream(self.vault, ["template", "install", "url"])
        with self.assertRaises(engine.Refused):
            engine.stream(self.base, ["lines", "1"])

    def test_abandoning_the_stream_kills_the_command(self) -> None:
        """A browser that closes the connection mid-build abandons the
        generator. The typst it started must not outlive it."""
        pidfile = self.base / "grandchild.pid"
        marker = self.base / "survived"
        lines = engine.stream(self.vault, ["hang", str(pidfile), str(marker)], timeout=30)
        self.assertEqual(next(lines), "started")
        lines.close()

        pid = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail(f"grandchild {pid} outlived the abandoned stream")

    def test_a_silent_command_still_hits_the_deadline(self) -> None:
        started = time.monotonic()
        lines = list(
            engine.stream(
                self.vault,
                ["hang", str(self.base / "p"), str(self.base / "m")],
                timeout=1.0,
            )
        )
        self.assertIn(engine.TIMEOUT_MARKER, lines[-1])
        self.assertLess(time.monotonic() - started, 1.0 + engine.GRACE)


# ── finding the engine ───────────────────────────────────────────────────────


class Locating(BridgeTest):
    def test_the_override_wins(self) -> None:
        self.assertEqual(quietly(lambda: engine.locate(refresh=True)), self.fake)

    def test_a_directory_override_means_its_bin_report_maker(self) -> None:
        os.environ["RM_WEB_ENGINE"] = str(REPO)
        self.assertEqual(
            quietly(lambda: engine.locate(refresh=True)), REPO / "bin" / "report-maker"
        )

    def test_a_broken_override_is_loud(self) -> None:
        """An override that silently does not apply is the worst outcome: the
        server runs, on the wrong engine, and says so nowhere."""
        os.environ["RM_WEB_ENGINE"] = str(self.base / "nope")
        with self.assertRaises(engine.EngineMissing):
            quietly(lambda: engine.locate(refresh=True))

    def test_the_checkout_is_found_without_configuration(self) -> None:
        os.environ.pop("RM_WEB_ENGINE")
        self.assertEqual(
            quietly(lambda: engine.locate(refresh=True)), REPO / "bin" / "report-maker"
        )

    def test_which_engine_won_is_announced(self) -> None:
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            engine.locate(refresh=True)
        self.assertIn(str(self.fake), buffer.getvalue())

    def test_a_script_without_the_execute_bit_still_runs(self) -> None:
        """A checkout from a zip, or a volume mounted noexec. The fallback runs
        it under this interpreter, which is 3.11+ by the same requirement that
        let this module import."""
        self.fake.chmod(0o644)
        quietly(lambda: engine.locate(refresh=True))
        got = self.echo("plain")
        self.assertEqual(got["args"], ["echo-argv", "plain"])


class TheRealEngine(unittest.TestCase):
    """Two facts about the engine as installed, which no fake can stand in for."""

    script = REPO / "bin" / "report-maker"

    def setUp(self) -> None:
        self.env = dict(os.environ)
        os.environ.pop("RM_WEB_ENGINE", None)
        engine.set_sessions_root(Path(tempfile.gettempdir()))
        quietly(lambda: engine.locate(refresh=True))

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env)
        quietly(lambda: engine.locate(refresh=True))

    def test_version_answers(self) -> None:
        version = engine.version()
        self.assertIsNotNone(version)
        self.assertRegex(version, r"^\d")

    @unittest.skipUnless(Path("/usr/bin/python3").exists(), "no system python3")
    def test_the_engine_recovers_from_a_stale_python3(self) -> None:
        """The interpreter trap, asserted rather than assumed.

        `bin/report-maker` is `#!/usr/bin/env python3`, and a server whose first
        `python3` is 3.9 has no `tomllib` for the engine to import. The script
        re-execs itself into a newer interpreter, so this bridge can exec it
        directly — which is only true for as long as this test passes.
        """
        minimal = subprocess.run(
            [str(self.script), "--version"],
            env={"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~")},
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(minimal.returncode, 0, minimal.stderr)
        self.assertIn("report-maker", minimal.stdout + minimal.stderr)


if __name__ == "__main__":
    unittest.main()
