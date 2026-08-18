"""`bin/report-maker` under an interpreter that is too old.

The engine is Python 3.11+ — `config.py` imports `tomllib`, which does not
exist before then. The entry point's shebang is `#!/usr/bin/env python3`, and
that resolves to the *first* python3 on PATH, which on macOS is
`/usr/bin/python3`: Python 3.9.

This is not a hypothetical. It is how the packaged app failed. An Electron app
launched from Finder gets `/usr/bin:/bin:/usr/sbin:/sbin` and nothing else; the
app appends Homebrew as a fallback, but appending puts it *after* the system
directories, so `/usr/bin/python3` still won the lookup. Every engine call from
the installed app came back:

    ModuleNotFoundError: No module named 'tomllib'

— a traceback four imports deep, naming neither Python nor a version, from an
app that had just reported a clean `doctor` in a terminal. So the entry point
checks its own interpreter and moves to a newer one before importing anything.

These tests run the real script as a subprocess under a real old interpreter,
because that is the only thing that proves it: a unit test that imported the
module would already be running under a Python new enough for the bug not to
exist. Where the machine has no old interpreter, they skip rather than pretend.

    python3 -m unittest tests.test_entrypoint
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin" / "report-maker"

#: Interpreters that are too old to run the engine. `/usr/bin/python3` is the
#: one that matters — it is what the shebang finds on a stock macOS — but the
#: list is searched so the test still means something on a machine that has an
#: old Python somewhere else.
OLD_CANDIDATES = ("/usr/bin/python3", "python3.9", "python3.10")


def _an_old_interpreter() -> str | None:
    for candidate in OLD_CANDIDATES:
        found = candidate if Path(candidate).is_file() else shutil.which(candidate)
        if not found:
            continue
        probe = subprocess.run(
            [found, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            continue
        major, minor = (int(part) for part in probe.stdout.split())
        if (major, minor) < (3, 11):
            return found
    return None


OLD = _an_old_interpreter()
NEEDS_OLD = unittest.skipIf(
    OLD is None, "no Python older than 3.11 on this machine to run the script under"
)

#: The PATH a packaged app actually gets, plus the fallback directories the app
#: appends. System directories first, which is the whole problem.
FINDER_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"


class OldInterpreter(unittest.TestCase):
    def run_script(self, python: str, *args: str, path: str | None = None) -> subprocess.CompletedProcess:
        env = {"HOME": str(Path.home()), "PATH": path or FINDER_PATH}
        return subprocess.run(
            [python, str(SCRIPT), *args], capture_output=True, text=True, env=env
        )

    @NEEDS_OLD
    def test_the_script_runs_under_an_interpreter_too_old_for_the_engine(self) -> None:
        assert OLD is not None
        result = self.run_script(OLD, "--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("report-maker", result.stdout)

    @NEEDS_OLD
    def test_it_never_surfaces_the_tomllib_import_error(self) -> None:
        """The symptom that made this take a packaged build to find."""
        assert OLD is not None
        result = self.run_script(OLD, "--version")
        self.assertNotIn("tomllib", result.stdout + result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stdout + result.stderr)

    @NEEDS_OLD
    def test_a_real_command_works_and_not_just_version(self) -> None:
        """`--version` exits before the vault machinery. `doctor` does not, so it
        is the one that proves the engine actually imported."""
        assert OLD is not None
        result = self.run_script(
            OLD, "-C", str(ROOT / "examples" / "demo-vault"), "doctor"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("vault", result.stdout)

    def test_the_current_interpreter_is_not_re_executed(self) -> None:
        """This test suite already runs on 3.11+; the guard must be a no-op there
        rather than a spawn on every invocation."""
        result = self.run_script(sys.executable, "--version", path=FINDER_PATH)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("report-maker", result.stdout)


class MissingInterpreter(unittest.TestCase):
    """When there is genuinely nothing new enough, say so in one sentence."""

    @NEEDS_OLD
    def test_it_names_python_and_the_version_rather_than_a_missing_module(self) -> None:
        assert OLD is not None
        # A copy with the look-aside directories emptied, so the search can fail
        # on a machine where Homebrew is present. Editing the constant is the
        # only way to reach the branch without uninstalling Python.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            stripped = Path(tmp) / "report-maker"
            stripped.write_text(
                SCRIPT.read_text().replace(
                    'LOOKASIDE = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")',
                    "LOOKASIDE = ()",
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [OLD, str(stripped), "--version"],
                capture_output=True,
                text=True,
                env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin"},
            )
        self.assertNotEqual(result.returncode, 0)
        message = result.stdout + result.stderr
        self.assertIn("Python", message)
        self.assertIn("3.11", message)
        self.assertNotIn("tomllib", message)


if __name__ == "__main__":
    unittest.main()
