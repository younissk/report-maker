"""Sync tests — mostly a list of the pushes that must not happen.

`sync` is the only part of the engine that can destroy work rather than merely
produce a bad report, so every safety rule in `gitsync` gets a real repository
here: a bare "remote" on disk, two clones, a branch genuinely behind another, a
genuinely detached HEAD. Faking the state would test the message and not the
rule, and the rule is the thing that matters.

No network is involved — a bare repository in a temp folder is a real git remote
in every way that counts, including rejecting a non-fast-forward push.

Git identity comes from the environment rather than from config, and the user's
own global and system config is switched off for the whole module, so a stray
`commit.gpgsign` or `push.default` on the developer's machine cannot change what
these tests prove.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import gitsync  # noqa: E402
from engine.config import DEFAULTS, Config  # noqa: E402

GIT_ENV = {
    # An identity, so `git commit` never stops to ask for one.
    "GIT_AUTHOR_NAME": "Test Writer",
    "GIT_AUTHOR_EMAIL": "writer@example.invalid",
    "GIT_COMMITTER_NAME": "Test Writer",
    "GIT_COMMITTER_EMAIL": "writer@example.invalid",
    "GIT_AUTHOR_DATE": "2026-08-01T09:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-08-01T09:00:00+00:00",
    # The developer's own git config must not reach these tests.
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    # Nothing here may ever block on a credential prompt.
    "GIT_TERMINAL_PROMPT": "0",
    # Set per-test in setUp, listed here so tearDown restores it.
    "GIT_CEILING_DIRECTORIES": "",
}


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result


def vault(root: Path) -> Config:
    """Make `root` a vault. The layout is irrelevant to gitsync — what matters is
    that Config.root points there — but a real report keeps the fixtures honest."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "report-maker.toml").write_text("[vault]\n", encoding="utf-8")
    return Config(root=root, data=dict(DEFAULTS))


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class GitCase(unittest.TestCase):
    """A temp directory and a scrubbed git environment for every test."""

    def setUp(self) -> None:
        self._env = {key: os.environ.get(key) for key in GIT_ENV}
        os.environ.update(GIT_ENV)
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        # Stop git's repository discovery at the temp root, so "this folder is
        # not a repository" stays true even when TMPDIR happens to sit inside
        # one. The ceiling itself is never searched; everything below it is.
        os.environ["GIT_CEILING_DIRECTORIES"] = str(self.tmp)

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    # ── fixtures ─────────────────────────────────────────────────────────────

    def repo(self, name: str = "vault") -> Config:
        """A vault that is its own git repository, with one commit in it."""
        cfg = vault(self.tmp / name)
        git("init", "-q", "-b", "main", ".", cwd=cfg.root)
        write(cfg.root / "reports" / "2026-08-01-first" / "main.typ", "= First\n")
        git("add", "-A", cwd=cfg.root)
        git("commit", "-qm", "first", cwd=cfg.root)
        return cfg

    def remote(self) -> Path:
        bare = self.tmp / "origin.git"
        git("init", "-q", "--bare", "-b", "main", str(bare), cwd=self.tmp)
        return bare

    def cloned(self, bare: Path, name: str) -> Config:
        """A vault cloned from `bare`, tracking origin/main."""
        git("clone", "-q", str(bare), name, cwd=self.tmp)
        cfg = vault(self.tmp / name)
        git("add", "-A", cwd=cfg.root)
        git("commit", "-qm", "vault", cwd=cfg.root)
        git("push", "-q", "-u", "origin", "main", cwd=cfg.root)
        return cfg

    def head(self, cfg: Config) -> str:
        return git("rev-parse", "HEAD", cwd=cfg.root).stdout.strip()

    def remote_head(self, bare: Path) -> str:
        return git("rev-parse", "refs/heads/main", cwd=bare).stdout.strip()


# ── state ────────────────────────────────────────────────────────────────────


class StateTests(GitCase):
    def test_folder_that_is_not_a_repository(self) -> None:
        """The app polls `state` on a timer, so this is the case that must be
        cheap, silent and never an exception."""
        cfg = vault(self.tmp / "loose")
        st = gitsync.state(cfg)
        self.assertFalse(st.repo)
        self.assertIsNone(st.branch)
        self.assertEqual(st.dirty, [])
        self.assertEqual((st.ahead, st.behind), (0, 0))

    def test_clean_repository(self) -> None:
        st = gitsync.state(self.repo())
        self.assertTrue(st.repo)
        self.assertEqual(st.branch, "main")
        self.assertIsNone(st.upstream)
        self.assertEqual(st.dirty, [])

    def test_dirty_repository_lists_paths(self) -> None:
        cfg = self.repo()
        write(cfg.root / "reports" / "2026-08-01-first" / "main.typ", "= First\n\nmore\n")
        write(cfg.root / "reports" / "2026-08-01-first" / "sources.yml", "k:\n  type: Web\n")
        st = gitsync.state(cfg)
        self.assertTrue(st.repo)
        self.assertIn("reports/2026-08-01-first/main.typ", st.dirty)
        self.assertTrue(any("sources.yml" in path for path in st.dirty))

    def test_detached_head_has_no_branch(self) -> None:
        cfg = self.repo()
        git("checkout", "-q", "--detach", "HEAD", cwd=cfg.root)
        st = gitsync.state(cfg)
        self.assertTrue(st.repo)
        self.assertIsNone(st.branch)

    def test_upstream_and_drift(self) -> None:
        bare = self.remote()
        cfg = self.cloned(bare, "vault")
        st = gitsync.state(cfg)
        self.assertEqual(st.upstream, "origin/main")
        self.assertEqual(st.remote, "origin")
        self.assertEqual((st.ahead, st.behind), (0, 0))

    def test_state_ignores_changes_outside_the_vault(self) -> None:
        """A vault is very often one folder inside a bigger repository. What the
        app shows as dirty must be what sync would commit, and nothing else."""
        outer = self.tmp / "monorepo"
        outer.mkdir()
        git("init", "-q", "-b", "main", ".", cwd=outer)
        cfg = vault(outer / "vault")
        write(outer / "src" / "unrelated.py", "x = 1\n")
        git("add", "-A", cwd=outer)
        git("commit", "-qm", "base", cwd=outer)

        write(outer / "src" / "unrelated.py", "x = 2\n")
        write(cfg.root / "reports" / "2026-08-01-a" / "main.typ", "= A\n")

        dirty = gitsync.state(cfg).dirty
        self.assertFalse(any("unrelated" in path for path in dirty), dirty)
        self.assertTrue(any(path.startswith("reports/") for path in dirty), dirty)


# ── committing ───────────────────────────────────────────────────────────────


class CommitTests(GitCase):
    def test_commit_returns_a_sha_then_none(self) -> None:
        cfg = self.repo()
        write(cfg.root / "reports" / "2026-08-01-first" / "sources.yml", "k:\n  type: Web\n")
        sha = gitsync.commit(cfg, "add sources")
        self.assertIsNotNone(sha)
        self.assertEqual(sha, self.head(cfg))
        # Nothing left to commit — and an empty commit would claim otherwise.
        self.assertIsNone(gitsync.commit(cfg))

    def test_default_message_shape(self) -> None:
        cfg = self.repo()
        write(cfg.root / "reports" / "2026-08-01-first" / "sources.yml", "k:\n")
        gitsync.commit(cfg)
        subject = git("log", "-1", "--format=%s", cwd=cfg.root).stdout.strip()
        self.assertTrue(subject.startswith("report-maker: 1 file(s) — "), subject)

    def test_commit_refuses_a_path_outside_the_vault(self) -> None:
        outer = self.tmp / "monorepo"
        outer.mkdir()
        git("init", "-q", "-b", "main", ".", cwd=outer)
        cfg = vault(outer / "vault")
        write(outer / "secret.txt", "not ours\n")
        git("add", "-A", cwd=outer)
        git("commit", "-qm", "base", cwd=outer)
        write(outer / "secret.txt", "changed\n")

        with self.assertRaises(gitsync.GitError) as caught:
            gitsync.commit(cfg, "sneak", paths=["../secret.txt"])
        self.assertIn("outside the vault", str(caught.exception))
        # And it stayed uncommitted.
        self.assertIn("secret.txt", git("status", "--porcelain", cwd=outer).stdout)

    def test_commit_only_takes_the_vault_along(self) -> None:
        """Changes elsewhere in the repository are not ours to commit, even when
        somebody else already staged them."""
        outer = self.tmp / "monorepo"
        outer.mkdir()
        git("init", "-q", "-b", "main", ".", cwd=outer)
        cfg = vault(outer / "vault")
        write(outer / "src" / "unrelated.py", "x = 1\n")
        git("add", "-A", cwd=outer)
        git("commit", "-qm", "base", cwd=outer)

        write(outer / "src" / "unrelated.py", "x = 2\n")
        git("add", "-A", cwd=outer)  # staged by somebody else
        write(cfg.root / "reports" / "2026-08-01-a" / "main.typ", "= A\n")

        gitsync.commit(cfg, "vault only")
        names = git("show", "--name-only", "--format=", "HEAD", cwd=outer).stdout
        self.assertIn("vault/reports/2026-08-01-a/main.typ", names)
        self.assertNotIn("unrelated.py", names)

    def test_commit_needs_a_repository(self) -> None:
        cfg = vault(self.tmp / "loose")
        with self.assertRaises(gitsync.GitError) as caught:
            gitsync.commit(cfg)
        self.assertIn(f"git -C {cfg.root} init", str(caught.exception))


# ── the refusals ─────────────────────────────────────────────────────────────


class RefusalTests(GitCase):
    def test_no_upstream_names_the_command_that_sets_one(self) -> None:
        cfg = self.repo()
        message = gitsync.push(cfg)
        self.assertIn("no upstream", message)
        self.assertIn("git push -u origin main", message)

    def test_detached_head_is_refused(self) -> None:
        bare = self.remote()
        cfg = self.cloned(bare, "vault")
        write(cfg.root / "reports" / "2026-08-01-a" / "main.typ", "= A\n")
        gitsync.commit(cfg, "work")
        before = self.remote_head(bare)
        git("checkout", "-q", "--detach", "HEAD", cwd=cfg.root)

        message = gitsync.push(cfg)
        self.assertIn("detached", message)
        self.assertIn("git switch -c", message)
        self.assertEqual(self.remote_head(bare), before)

    def test_behind_the_remote_is_refused(self) -> None:
        """Built for real: a second clone advances the remote, this one fetches
        and is now behind. The only push that could succeed here is one that
        overwrites, which is the push this module exists to refuse."""
        bare = self.remote()
        mine = self.cloned(bare, "mine")
        theirs = self.tmp / "theirs"
        git("clone", "-q", str(bare), "theirs", cwd=self.tmp)
        write(theirs / "reports" / "2026-08-02-b" / "main.typ", "= B\n")
        git("add", "-A", cwd=theirs)
        git("commit", "-qm", "theirs", cwd=theirs)
        git("push", "-q", "origin", "main", cwd=theirs)

        write(mine.root / "reports" / "2026-08-01-a" / "main.typ", "= A\n")
        gitsync.commit(mine, "mine")
        git("fetch", "-q", cwd=mine.root)

        st = gitsync.state(mine)
        self.assertEqual(st.behind, 1)
        self.assertEqual(st.ahead, 1)

        before = self.remote_head(bare)
        message = gitsync.push(mine)
        self.assertIn("behind", message)
        self.assertIn("git pull --rebase", message)
        self.assertEqual(self.remote_head(bare), before, "the remote was moved")

    def test_not_a_repository_is_refused(self) -> None:
        message = gitsync.push(vault(self.tmp / "loose"))
        self.assertIn("not a git repository", message)

    def test_refusal_rules_read_without_a_remote(self) -> None:
        clean = gitsync.GitState(repo=True, branch="main", upstream="origin/main")
        self.assertIsNone(gitsync.push_refusal(clean))
        for st in (
            gitsync.GitState(repo=False),
            gitsync.GitState(repo=True, branch=None, upstream="origin/main"),
            gitsync.GitState(repo=True, branch="main"),
            gitsync.GitState(repo=True, branch="main", upstream="origin/main", behind=2),
        ):
            self.assertIsNotNone(gitsync.push_refusal(st))

    def test_push_never_forces(self) -> None:
        """Every git invocation of a successful push, inspected. `--force` is the
        one flag that turns a mistake into somebody else's lost work."""
        bare = self.remote()
        cfg = self.cloned(bare, "vault")
        write(cfg.root / "reports" / "2026-08-01-a" / "main.typ", "= A\n")
        gitsync.commit(cfg, "work")

        seen: list[tuple[str, ...]] = []
        real = gitsync._run

        def recording(config, *args):
            seen.append(args)
            return real(config, *args)

        gitsync._run = recording
        try:
            message = gitsync.push(cfg)
        finally:
            gitsync._run = real

        self.assertIn("pushed 1 commit", message)
        forbidden = ("--force", "-f", "--force-with-lease", "--delete", "--mirror")
        for args in seen:
            self.assertFalse(
                set(args) & set(forbidden), f"unsafe git invocation: git {' '.join(args)}"
            )


# ── sync ─────────────────────────────────────────────────────────────────────


class SyncTests(GitCase):
    def test_sync_without_push_only_commits(self) -> None:
        bare = self.remote()
        cfg = self.cloned(bare, "vault")
        before = self.remote_head(bare)
        write(cfg.root / "reports" / "2026-08-01-a" / "main.typ", "= A\n")

        result = gitsync.sync(cfg, message="local work")
        self.assertIsNotNone(result["committed"])
        self.assertFalse(result["pushed"])
        self.assertIsNone(result["refused"])
        self.assertEqual(self.remote_head(bare), before, "sync pushed without --push")

    def test_sync_with_push_moves_the_remote(self) -> None:
        bare = self.remote()
        cfg = self.cloned(bare, "vault")
        write(cfg.root / "reports" / "2026-08-01-a" / "main.typ", "= A\n")

        result = gitsync.sync(cfg, message="work", do_push=True)
        self.assertTrue(result["pushed"])
        self.assertIsNone(result["refused"])
        self.assertEqual(self.remote_head(bare), result["committed"])
        self.assertIn("pushed 1 commit", result["detail"])

    def test_sync_reports_a_refusal_without_raising(self) -> None:
        cfg = self.repo()  # no upstream
        write(cfg.root / "reports" / "2026-08-01-a" / "main.typ", "= A\n")

        result = gitsync.sync(cfg, message="work", do_push=True)
        self.assertIsNotNone(result["committed"], "the commit still happened")
        self.assertFalse(result["pushed"])
        self.assertIn("git push -u origin main", result["refused"])

        with redirect_stdout(io.StringIO()) as out:
            code = gitsync.report_sync(cfg, result)
        self.assertEqual(code, 1, "a refused push must set a non-zero exit code")
        self.assertIn("no upstream", out.getvalue())

    def test_sync_with_nothing_to_do(self) -> None:
        bare = self.remote()
        cfg = self.cloned(bare, "vault")
        result = gitsync.sync(cfg, do_push=True)
        self.assertIsNone(result["committed"])
        self.assertFalse(result["pushed"])
        self.assertIsNone(result["refused"])
        self.assertIn("nothing to push", result["detail"])


# ── history ──────────────────────────────────────────────────────────────────


class LogTests(GitCase):
    def test_log_lists_commits_touching_a_path(self) -> None:
        cfg = self.repo()
        folder = cfg.root / "reports" / "2026-08-01-first"
        write(folder / "sources.yml", "k:\n  type: Web\n")
        gitsync.commit(cfg, "cite something")
        write(cfg.root / "reports" / "2026-08-05-other" / "main.typ", "= Other\n")
        gitsync.commit(cfg, "an unrelated report")

        rows = gitsync.log(cfg, folder)
        self.assertEqual([row["subject"] for row in rows], ["cite something", "first"])
        row = rows[0]
        self.assertEqual(set(row), {"sha", "short", "subject", "author", "date", "files"})
        self.assertEqual(row["author"], "Test Writer")
        self.assertTrue(row["date"].startswith("2026-08-01"))
        self.assertTrue(row["sha"].startswith(row["short"]))
        # Scoped to the pathspec: the unrelated report is not in this history.
        self.assertEqual(row["files"], ["reports/2026-08-01-first/sources.yml"])

    def test_log_honours_the_limit(self) -> None:
        cfg = self.repo()
        folder = cfg.root / "reports" / "2026-08-01-first"
        for n in range(4):
            write(folder / "main.typ", f"= First\n\nrevision {n}\n")
            gitsync.commit(cfg, f"edit {n}")
        self.assertEqual(len(gitsync.log(cfg, folder, limit=2)), 2)

    def test_log_paths_are_vault_relative(self) -> None:
        """`git log` speaks from the repository root. The app draws its timeline
        in vault terms, the same as every other path the engine hands it."""
        outer = self.tmp / "monorepo"
        outer.mkdir()
        git("init", "-q", "-b", "main", ".", cwd=outer)
        cfg = vault(outer / "vault")
        folder = cfg.root / "reports" / "2026-08-01-a"
        write(folder / "main.typ", "= A\n")
        git("add", "-A", cwd=outer)
        git("commit", "-qm", "base", cwd=outer)

        rows = gitsync.log(cfg, folder)
        self.assertEqual(rows[0]["files"], ["reports/2026-08-01-a/main.typ"])

    def test_log_of_a_path_with_no_history(self) -> None:
        cfg = self.repo()
        self.assertEqual(gitsync.log(cfg, cfg.root / "reports" / "nothing-here"), [])

    def test_log_refuses_a_path_outside_the_vault(self) -> None:
        cfg = self.repo()
        with self.assertRaises(gitsync.GitError):
            gitsync.log(cfg, "../elsewhere")

    def test_log_needs_a_repository(self) -> None:
        cfg = vault(self.tmp / "loose")
        with self.assertRaises(gitsync.GitError):
            gitsync.log(cfg, cfg.root)


class ShowTests(GitCase):
    def test_show_reads_the_previous_revision(self) -> None:
        cfg = self.repo()
        main = cfg.root / "reports" / "2026-08-01-first" / "main.typ"
        write(main, "= First\n\nrewritten\n")
        gitsync.commit(cfg, "rewrite")
        self.assertEqual(gitsync.show(cfg, "HEAD~1", main), "= First\n")
        self.assertIn("rewritten", gitsync.show(cfg, "HEAD", main))

    def test_show_works_from_a_vault_inside_a_repository(self) -> None:
        """`git show` takes a repository-root path; the engine speaks in
        vault-relative ones. This is the case where the difference shows."""
        outer = self.tmp / "monorepo"
        outer.mkdir()
        git("init", "-q", "-b", "main", ".", cwd=outer)
        cfg = vault(outer / "vault")
        main = write(cfg.root / "reports" / "2026-08-01-a" / "main.typ", "= A\n")
        git("add", "-A", cwd=outer)
        git("commit", "-qm", "base", cwd=outer)
        self.assertEqual(gitsync.show(cfg, "HEAD", main), "= A\n")

    def test_show_of_an_unknown_revision(self) -> None:
        cfg = self.repo()
        with self.assertRaises(gitsync.GitError) as caught:
            gitsync.show(cfg, "v9.9.9", cfg.root / "reports" / "2026-08-01-first" / "main.typ")
        self.assertIn("log --oneline", str(caught.exception))


# ── output ───────────────────────────────────────────────────────────────────


class OutputTests(GitCase):
    def test_to_json_is_the_app_shape(self) -> None:
        st = gitsync.state(self.repo())
        payload = gitsync.to_json(st)
        self.assertEqual(
            set(payload),
            {"repo", "branch", "upstream", "dirty", "ahead", "behind", "remote"},
        )
        self.assertIs(payload["repo"], True)
        self.assertEqual(payload["branch"], "main")

    def test_to_json_carries_sync_and_log_when_asked(self) -> None:
        cfg = self.repo()
        rows = gitsync.log(cfg, cfg.root)
        payload = gitsync.to_json(
            gitsync.state(cfg), result={"committed": None}, log_rows=rows
        )
        self.assertEqual(payload["sync"], {"committed": None})
        self.assertEqual(len(payload["log"]), len(rows))

    def test_report_state_is_never_a_failure(self) -> None:
        cfg = vault(self.tmp / "loose")
        with redirect_stdout(io.StringIO()) as out:
            code = gitsync.report_state(cfg, gitsync.state(cfg))
        self.assertEqual(code, 0)
        self.assertIn(f"git -C {cfg.root} init", out.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
