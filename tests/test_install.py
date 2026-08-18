"""Installing a design from a repository.

The repositories here are real: each test builds one with `git init` in a temp
directory and installs from its `file://` URL. A fake clone would test the parts
that cannot go wrong — what actually needs proving is that a hostile *checkout*
(a symlink, a `..` in a subdir, a shell script sitting next to the Typst) never
reaches the vault, and that when an install is refused the vault is byte-for-byte
what it was beforehand.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import install, scaffold, vault  # noqa: E402
from engine.config import Config, load  # noqa: E402

HAVE_GIT = shutil.which("git") is not None

# A machine running these may have no git identity, and a repository with no
# commit cannot be cloned. Everything git needs is supplied here.
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_TERMINAL_PROMPT": "0",
}

TEMPLATE_TOML = 'title = "House"\ndescription = "A shared house style."\nextends = "base"\nbrand = "default"\n'
REPORT_TYP = '#let house = "v1"\n'

DESIGN = {
    "template.toml": TEMPLATE_TOML,
    "report.typ": REPORT_TYP,
    "README.md": "# House style\n",
    "starter/main.typ": "= {{title}}\n",
    "starter/sources.yml": "example:\n  type: Web\n",
}


def git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), env=GIT_ENV,
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def make_repo(path: Path, files: dict[str, str], *, symlinks: dict[str, str] | None = None) -> str:
    """A one-commit repository holding `files`. Returns the commit sha."""
    path.mkdir(parents=True, exist_ok=True)
    git(["-c", "init.defaultBranch=main", "init", "-q"], path)
    return commit(path, files, symlinks=symlinks, message="initial")


def commit(path: Path, files: dict[str, str], *, symlinks: dict[str, str] | None = None,
           message: str = "change") -> str:
    for rel, text in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for rel, points_at in (symlinks or {}).items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(points_at)
    git(["add", "-A"], path)
    git(["commit", "-q", "-m", message], path)
    return git(["rev-parse", "HEAD"], path)


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class Installing(unittest.TestCase):
    """A scratch vault and a scratch repository, torn down after each test."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        with redirect_stdout(io.StringIO()):
            scaffold.init(self.root / "vault")
        self.cfg: Config = load(self.root / "vault")
        self.repo = self.root / "repo"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ── helpers

    def quiet(self, fn, *args, **kwargs):
        with redirect_stdout(io.StringIO()):
            return fn(*args, **kwargs)

    def url(self) -> str:
        return self.repo.as_uri()

    def prefixed(self, files: dict[str, str], prefix: str) -> dict[str, str]:
        return {f"{prefix}/{rel}": text for rel, text in files.items()}

    def assertNoResidue(self, *ids: str) -> None:
        for tid in ids:
            self.assertFalse(
                (self.cfg.templates / tid).exists(),
                f"templates/{tid} survived a refused install",
            )
        # Nothing may be written outside templates/ either.
        self.assertFalse((self.root / "evil").exists())
        self.assertFalse((self.cfg.root / "evil").exists())


class TestInstall(Installing):
    def test_a_clean_install_takes_the_repository_root(self) -> None:
        sha = make_repo(self.repo, DESIGN)
        item = self.quiet(install.install, self.cfg, self.url())

        self.assertEqual(item.id, "repo")
        self.assertEqual(item.sha, sha)
        folder = self.cfg.templates / "repo"
        self.assertEqual((folder / "report.typ").read_text(encoding="utf-8"), REPORT_TYP)
        self.assertTrue((folder / "starter" / "main.typ").is_file())
        # Repository furniture is skipped, not copied and not refused.
        self.assertFalse((folder / "README.md").exists())

    def test_the_installed_design_is_a_template_the_vault_can_see(self) -> None:
        make_repo(self.repo, DESIGN)
        self.quiet(install.install, self.cfg, self.url(), id="house")
        found = vault.templates(self.cfg)
        self.assertIn("house", found)
        self.assertFalse(found["house"].builtin)
        self.assertEqual(found["house"].title, "House")
        self.assertEqual([t.id for t in vault.lineage(self.cfg, found["house"])], ["base", "house"])

    def test_provenance_is_written_into_the_design_folder(self) -> None:
        sha = make_repo(self.repo, DESIGN)
        self.quiet(install.install, self.cfg, self.url(), id="house")
        record = json.loads((self.cfg.templates / "house" / ".installed.json").read_text(encoding="utf-8"))
        self.assertEqual(record["url"], self.url())
        self.assertEqual(record["sha"], sha)
        self.assertIsNone(record["ref"])
        self.assertIn("installed_at", record)
        self.assertIn("engine", record)

    def test_copied_files_are_never_executable(self) -> None:
        make_repo(self.repo, DESIGN)
        # git preserves the executable bit; the vault must not inherit it.
        (self.repo / "report.typ").chmod(0o755)
        commit(self.repo, {}, message="mode")
        self.quiet(install.install, self.cfg, self.url(), id="house")
        mode = (self.cfg.templates / "house" / "report.typ").stat().st_mode
        self.assertEqual(mode & 0o111, 0)

    def test_install_from_a_subdir(self) -> None:
        make_repo(self.repo, {**self.prefixed(DESIGN, "designs/house"), "README.md": "top\n"})
        item = self.quiet(install.install, self.cfg, self.url(), subdir="designs/house")
        self.assertEqual(item.id, "house")
        self.assertEqual(item.subdir, "designs/house")
        self.assertTrue((self.cfg.templates / "house" / "report.typ").is_file())

    def test_the_url_carries_the_subdir_and_the_ref(self) -> None:
        make_repo(self.repo, self.prefixed(DESIGN, "designs/house"))
        item = self.quiet(install.install, self.cfg, f"{self.url()}#designs/house")
        self.assertEqual(item.subdir, "designs/house")

        url, ref, subdir = install.split_url("https://example.com/o/designs.git@v2#house")
        self.assertEqual((url, ref, subdir), ("https://example.com/o/designs.git", "v2", "house"))
        # The @ of an scp-style remote is not a ref.
        self.assertEqual(install.split_url("git@github.com:o/designs.git"),
                         ("git@github.com:o/designs.git", None, None))

    def test_install_at_a_tag_pins_that_commit(self) -> None:
        tagged = make_repo(self.repo, DESIGN)
        git(["tag", "v1"], self.repo)
        moved = commit(self.repo, {"report.typ": '#let house = "v2"\n'}, message="later")
        self.assertNotEqual(tagged, moved)

        item = self.quiet(install.install, self.cfg, f"{self.url()}@v1", id="house")
        self.assertEqual(item.sha, tagged)
        self.assertEqual(item.ref, "v1")
        self.assertEqual((self.cfg.templates / "house" / "report.typ").read_text(encoding="utf-8"), REPORT_TYP)

    def test_installing_over_a_design_needs_force(self) -> None:
        make_repo(self.repo, DESIGN)
        self.quiet(install.install, self.cfg, self.url(), id="house")
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, self.url(), id="house")
        self.assertIn("--force", str(caught.exception))

        commit(self.repo, {"report.typ": '#let house = "v2"\n'})
        self.quiet(install.install, self.cfg, self.url(), id="house", force=True)
        self.assertEqual(
            (self.cfg.templates / "house" / "report.typ").read_text(encoding="utf-8"),
            '#let house = "v2"\n',
        )


class TestUpdate(Installing):
    def test_update_reports_the_sha_it_moved_to(self) -> None:
        first = make_repo(self.repo, DESIGN)
        self.quiet(install.install, self.cfg, self.url(), id="house")
        second = commit(self.repo, {"report.typ": '#let house = "v2"\n'})

        moved = self.quiet(install.update, self.cfg)
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].previous_sha, first)
        self.assertEqual(moved[0].sha, second)
        self.assertTrue(moved[0].moved)
        self.assertEqual(
            (self.cfg.templates / "house" / "report.typ").read_text(encoding="utf-8"),
            '#let house = "v2"\n',
        )

    def test_update_says_nothing_moved_when_nothing_moved(self) -> None:
        make_repo(self.repo, DESIGN)
        self.quiet(install.install, self.cfg, self.url(), id="house")
        again = self.quiet(install.update, self.cfg, "house")
        self.assertFalse(again[0].moved)

    def test_update_holds_a_pinned_design_at_its_ref(self) -> None:
        tagged = make_repo(self.repo, DESIGN)
        git(["tag", "v1"], self.repo)
        self.quiet(install.install, self.cfg, f"{self.url()}@v1", id="house")
        commit(self.repo, {"report.typ": '#let house = "v2"\n'})
        held = self.quiet(install.update, self.cfg, "house")
        self.assertEqual(held[0].sha, tagged)
        self.assertFalse(held[0].moved)

    def test_update_never_touches_a_local_design(self) -> None:
        self.quiet(scaffold.new_template, self.cfg, "mine", source="base")
        self.assertEqual(install.installed(self.cfg), [])
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.update, self.cfg, "mine")
        self.assertIn("local design", str(caught.exception))
        self.assertTrue((self.cfg.templates / "mine" / "template.toml").is_file())

    def test_update_with_no_installed_designs_says_so(self) -> None:
        with self.assertRaises(install.InstallError):
            self.quiet(install.update, self.cfg)


class TestUninstall(Installing):
    def test_uninstall_removes_the_design_and_its_empty_group(self) -> None:
        make_repo(self.repo, DESIGN)
        self.quiet(install.install, self.cfg, self.url(), id="shared/house")
        self.quiet(install.uninstall, self.cfg, "shared/house")
        self.assertFalse((self.cfg.templates / "shared" / "house").exists())
        self.assertFalse((self.cfg.templates / "shared").exists())
        self.assertNotIn("shared/house", vault.templates(self.cfg))

    def test_uninstall_refuses_a_local_design(self) -> None:
        self.quiet(scaffold.new_template, self.cfg, "mine", source="base")
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.uninstall, self.cfg, "mine")
        self.assertIn("local design", str(caught.exception))
        self.assertTrue((self.cfg.templates / "mine" / "template.toml").is_file())

    def test_installed_lists_only_installed_designs(self) -> None:
        self.quiet(scaffold.new_template, self.cfg, "mine", source="base")
        make_repo(self.repo, DESIGN)
        self.quiet(install.install, self.cfg, self.url(), id="house")
        self.assertEqual([i.id for i in install.installed(self.cfg)], ["house"])
        payload = install.to_json(install.installed(self.cfg))
        self.assertEqual(payload["installed"][0]["url"], self.url())


class TestRefusals(Installing):
    def test_a_traversing_subdir_is_refused(self) -> None:
        make_repo(self.repo, DESIGN)
        for bad in ("../../evil", "/etc", "designs/../../evil"):
            with self.assertRaises(install.InstallError, msg=bad):
                self.quiet(install.install, self.cfg, self.url(), id="house", subdir=bad)
        self.assertNoResidue("house")

    def test_a_subdir_symlinked_out_of_the_clone_is_refused(self) -> None:
        make_repo(self.repo, DESIGN, symlinks={"outside": "../../"})
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, self.url(), id="house", subdir="outside")
        self.assertIn("symlink", str(caught.exception))
        self.assertNoResidue("house")

    def test_a_traversing_template_id_is_refused(self) -> None:
        make_repo(self.repo, DESIGN)
        for bad in ("../../evil", "/evil", "house/../../evil", ".hidden", "_skipped"):
            with self.assertRaises(install.InstallError, msg=bad):
                self.quiet(install.install, self.cfg, self.url(), id=bad)
        self.assertNoResidue("house")
        self.assertFalse((self.cfg.root.parent / "evil").exists())

    def test_a_symlink_in_the_design_is_refused(self) -> None:
        make_repo(self.repo, DESIGN, symlinks={"secrets.typ": "/etc/passwd"})
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, self.url(), id="house")
        self.assertIn("symlink", str(caught.exception))
        self.assertNoResidue("house")

    def test_a_script_beside_the_design_is_refused(self) -> None:
        make_repo(self.repo, {**DESIGN, "install.sh": "#!/bin/sh\nrm -rf ~\n"})
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, self.url(), id="house")
        self.assertIn("install.sh", str(caught.exception))
        self.assertNoResidue("house")

    def test_a_stray_folder_beside_the_design_is_refused(self) -> None:
        make_repo(self.repo, {**DESIGN, "scripts/build.py": "print(1)\n"})
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, self.url(), id="house")
        self.assertIn("--subdir", str(caught.exception))
        self.assertNoResidue("house")

    def test_a_starter_may_not_smuggle_other_file_types(self) -> None:
        make_repo(self.repo, {**DESIGN, "starter/postinstall.js": "process.exit(1)\n"})
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, self.url(), id="house")
        self.assertIn("starter/postinstall.js", str(caught.exception))
        self.assertNoResidue("house")

    def test_malformed_template_toml_leaves_no_residue(self) -> None:
        make_repo(self.repo, {**DESIGN, "template.toml": 'title = "unclosed\n'})
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, self.url(), id="shared/house")
        self.assertIn("template.toml", str(caught.exception))
        self.assertNoResidue("shared/house", "shared")

    def test_an_unresolvable_extends_leaves_no_residue(self) -> None:
        make_repo(self.repo, {**DESIGN, "template.toml": 'title = "House"\nextends = "nothing-here"\n'})
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, self.url(), id="house")
        self.assertIn("nothing-here", str(caught.exception))
        self.assertNoResidue("house")

    def test_a_design_with_no_template_toml_is_refused(self) -> None:
        make_repo(self.repo, {"report.typ": REPORT_TYP})
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, self.url(), id="house")
        self.assertIn("template.toml", str(caught.exception))
        self.assertNoResidue("house")

    def test_a_failed_reinstall_puts_the_previous_design_back(self) -> None:
        make_repo(self.repo, DESIGN)
        self.quiet(install.install, self.cfg, self.url(), id="house")
        before = (self.cfg.templates / "house" / "report.typ").read_text(encoding="utf-8")
        commit(self.repo, {"install.sh": "#!/bin/sh\n"})

        with self.assertRaises(install.InstallError):
            self.quiet(install.install, self.cfg, self.url(), id="house", force=True)
        self.assertEqual((self.cfg.templates / "house" / "report.typ").read_text(encoding="utf-8"), before)
        self.assertTrue((self.cfg.templates / "house" / ".installed.json").is_file())

    def test_an_unreachable_repository_is_an_error_not_a_traceback(self) -> None:
        with self.assertRaises(install.InstallError):
            self.quiet(install.install, self.cfg, (self.root / "nope").as_uri(), id="house")
        self.assertNoResidue("house")

    def test_an_unknown_ref_is_an_error(self) -> None:
        make_repo(self.repo, DESIGN)
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, f"{self.url()}@v9", id="house")
        self.assertIn("v9", str(caught.exception))
        self.assertNoResidue("house")


class TestArgumentInjection(Installing):
    """A URL and a ref are arguments to git, and git reads arguments as options.

    These are regression tests for a live remote-code-execution bug, not
    hypotheticals. `git fetch` parses options *after* its positionals, so the
    commit-sha fallback — `git fetch … origin <ref>` — used to hand a ref of
    `--upload-pack=<command>` straight to git, which ran the command and then
    reported the ref as not found. The install looked like it had been refused
    while the command had already executed.
    """

    def canary(self) -> Path:
        """A file that exists only if something we refused actually ran."""
        return self.root / "executed-by-git"

    def assertNothingRan(self) -> None:
        self.assertFalse(
            self.canary().exists(),
            "a refused URL or ref reached git and executed a command",
        )

    def test_a_ref_that_is_really_an_option_never_reaches_git(self) -> None:
        make_repo(self.repo, DESIGN)
        evil = f"--upload-pack=touch {self.canary()}"
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, self.url(), id="house", ref=evil)
        self.assertIn("may not begin with '-'", str(caught.exception))
        self.assertNothingRan()
        self.assertNoResidue("house")

    def test_a_url_may_not_begin_with_a_dash(self) -> None:
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, f"--upload-pack=touch {self.canary()}")
        self.assertIn("may not begin with '-'", str(caught.exception))
        self.assertNothingRan()

    def test_a_transport_helper_url_is_refused(self) -> None:
        # `ext::` hands the address to a program. Current git refuses it by
        # default, but that default is a config setting, so we say it ourselves.
        with self.assertRaises(install.InstallError) as caught:
            self.quiet(install.install, self.cfg, f'ext::sh -c "touch {self.canary()}"')
        self.assertIn("transport helper", str(caught.exception))
        self.assertNothingRan()

    def test_only_known_transports_are_accepted(self) -> None:
        for bad in ("javascript://x", "data:text/plain,x://y", "ftp://example.com/r.git"):
            with self.subTest(url=bad):
                with self.assertRaises(install.InstallError):
                    self.quiet(install.install, self.cfg, bad, id="house")
        self.assertNoResidue("house")

    def test_a_ref_holding_whitespace_is_refused(self) -> None:
        # Not a legal ref name, and the shape every injected argument takes.
        with self.assertRaises(install.InstallError):
            install.split_url("https://example.com/o/d.git", "main --upload-pack=x")

    def test_the_transports_a_design_really_arrives_over_still_parse(self) -> None:
        self.assertEqual(
            install.split_url("git@github.com:o/designs.git"),
            ("git@github.com:o/designs.git", None, None),
        )
        for good in (
            "https://example.com/o/d.git",
            "http://example.com/o/d.git",
            "ssh://git@example.com/o/d.git",
            "git://example.com/o/d.git",
            "file:///srv/designs",
            "/srv/designs",
        ):
            with self.subTest(url=good):
                self.assertEqual(install.split_url(good)[0], good)

    def test_a_hostile_url_does_not_stop_a_real_install_working(self) -> None:
        # The guard is a filter, not a wall: the ordinary path still installs.
        make_repo(self.repo, DESIGN)
        item = self.quiet(install.install, self.cfg, self.url(), id="house")
        self.assertEqual(item.id, "house")
        self.assertTrue((self.cfg.templates / "house" / "template.toml").is_file())
        self.assertNothingRan()


if __name__ == "__main__":
    unittest.main()
