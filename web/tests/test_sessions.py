"""Sessions, on a scratch store, against the real engine.

Four properties are load-bearing, and each one fails quietly rather than loudly.

The first is **containment**. A session id arrives from a cookie and is used as a
directory name, so it is the shortest path from a stranger's keyboard to the
server's filesystem. The tests push traversal, a dot segment, an empty string and
a planted symlink at it, and all of them have to come back as the same `None` a
typo would produce.

The second is **that the credential stays server-side**. It is easy to write a
handler that returns the session so the UI can show something, and the vault path
alone contains the id — which would hand the token to JavaScript in the same
response that set it `HttpOnly`. So `to_json` is asserted on what is *absent*
from it, and `repr` on what it refuses to print.

The third is that a **seeded vault really is a vault**: `report-maker list` has to
answer for it, because a folder that only looks right is exactly the failure a
mocked engine would hide. Nothing here is mocked.

The fourth is the one that will look like a bug to somebody later: a brand-new
report **fails `check` with E012, on purpose**, and the session records that it
did. That is the product's argument — the starter's invented numbers and its
citation to example.com are a fabrication, and the engine refuses to build one.
A future change that silences it, or a seed that reaches for `status: "draft"` to
look clean, has to break a test rather than ship.

    python3 -m unittest discover -s web/tests
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from web.server import sessions  # noqa: E402

HOUR = 3600.0


def cli(vault: Path, *args: str) -> sessions.Run:
    """The engine, asked directly — the second opinion these tests check against."""
    return sessions._spawn(vault, list(args), 60.0)


def plain(root: Path) -> sessions.Session:
    """A session with nothing scaffolded into it.

    Everything outside `TestSeeding` is about ids, cookies and expiry, none of
    which knows or cares what is in the vault — and a suite that spawns three
    subprocesses per assertion is a suite people stop running. GitHub mode is
    exactly that session: the repo is the store there, so `create` seeds nothing.
    """
    return sessions.create(root, mode="github")


class StoreCase(unittest.TestCase):
    """A scratch RM_WEB_ROOT per test."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="rm-web-sessions-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)


# ── the lifecycle ────────────────────────────────────────────────────────────


class TestLifecycle(StoreCase):
    def test_create_get_touch_destroy_round_trip(self) -> None:
        made = sessions.create(self.root)
        self.assertEqual(made.mode, "try")
        self.assertTrue(made.vault.is_dir())
        self.assertTrue(made.record.is_file())
        self.assertEqual(made.vault.parent.name, made.id)
        self.assertEqual(made.vault.parent.parent.name, sessions.SESSIONS_DIRNAME)

        found = sessions.get(self.root, made.id)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.id, made.id)
        self.assertEqual(found.label, made.label)
        self.assertEqual(found.vault, made.vault)
        self.assertEqual(found.mode, "try")
        self.assertAlmostEqual(found.created, made.created, places=3)

        before = found.last_seen
        time.sleep(0.01)
        sessions.touch(found)
        self.assertGreater(found.last_seen, before)
        reread = sessions.get(self.root, made.id)
        assert reread is not None
        self.assertAlmostEqual(reread.last_seen, found.last_seen, places=3)

        sessions.destroy(self.root, made.id)
        self.assertFalse(made.vault.parent.exists())
        self.assertIsNone(sessions.get(self.root, made.id))

    def test_destroy_is_silent_on_an_id_that_opens_nothing(self) -> None:
        """DELETE /session must not become an oracle for other people's ids."""
        sessions.destroy(self.root, "nope")
        sessions.destroy(self.root, "../../../etc")
        sessions.destroy(self.root, "A" * 43)

    def test_an_unknown_mode_is_refused(self) -> None:
        with self.assertRaises(sessions.SessionError):
            sessions.create(self.root, mode="dropbox")


# ── the id, as a boundary ────────────────────────────────────────────────────


class TestIdIsABoundary(StoreCase):
    def test_a_bad_id_returns_none_rather_than_raising(self) -> None:
        """Every one of these is a 401 with the same body. None of them is a 500."""
        for sid in (
            "",
            "   ",
            "short",
            "../../../etc/passwd",
            "..",
            ".",
            "a/b",
            "a\\b",
            "a.b" + "x" * 40,
            "%2e%2e%2fetc",
            "\x00" + "a" * 42,
            "A" * 200,
            "A" * 43,  # well formed, and simply not a session
        ):
            with self.subTest(sid=sid):
                self.assertIsNone(sessions.get(self.root, sid))

    def test_a_non_string_id_returns_none(self) -> None:
        """A JSON body or a header parser can hand this layer anything."""
        for sid in (None, 42, b"a" * 43, ["a" * 43]):
            with self.subTest(sid=sid):
                self.assertIsNone(sessions.get(self.root, sid))  # type: ignore[arg-type]

    def test_a_symlinked_session_directory_is_refused(self) -> None:
        """The gate the shape check cannot see.

        `sessions/<well-formed-id>` is a name we control, but only until somebody
        with a foothold on the box plants a link there. Resolving the path and
        re-testing its parent is what catches it.
        """
        made = plain(self.root)
        outside = self.root / "elsewhere"
        outside.mkdir()
        (outside / "vault").mkdir()
        shutil.copy(made.record, outside / sessions.RECORD_NAME)

        planted = "P" * 43
        link = self.root / sessions.SESSIONS_DIRNAME / planted
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):  # pragma: no cover — Windows
            self.skipTest("this filesystem does not do symlinks")

        self.assertIsNone(sessions.get(self.root, planted))
        sessions.destroy(self.root, planted)
        self.assertTrue(outside.is_dir(), "destroy followed a symlink out of the store")

    def test_an_id_differing_only_in_case_is_refused(self) -> None:
        """macOS is case-insensitive, so the directory lookup is not the check.

        `sessions/Ab…` and `sessions/aB…` are one folder there. Without the
        `compare_digest` against the id recorded *inside* the file, a mistyped or
        deliberately re-cased token would open somebody else's session on exactly
        the platform most of this is developed on.
        """
        made = plain(self.root)
        flipped = made.id.swapcase()
        if flipped == made.id:  # pragma: no cover — a token of only digits
            self.skipTest("this id has no letters to re-case")
        self.assertIsNone(sessions.get(self.root, flipped))

    def test_an_unreadable_record_is_not_a_session(self) -> None:
        made = plain(self.root)
        made.record.write_text("{ truncated", encoding="utf-8")
        self.assertIsNone(sessions.get(self.root, made.id))

    def test_a_record_claiming_another_id_is_refused(self) -> None:
        made = plain(self.root)
        record = json.loads(made.record.read_text(encoding="utf-8"))
        record["id"] = "Z" * 43
        made.record.write_text(json.dumps(record), encoding="utf-8")
        self.assertIsNone(sessions.get(self.root, made.id))


# ── expiry ───────────────────────────────────────────────────────────────────


class TestSweep(StoreCase):
    def _age(self, session: sessions.Session, hours: float) -> None:
        session.last_seen = time.time() - hours * HOUR
        sessions._save(session)

    def test_sweep_removes_the_expired_and_leaves_the_live(self) -> None:
        live = plain(self.root)
        dead = plain(self.root)
        self._age(dead, 48)

        self.assertEqual(sessions.sweep(self.root), 1)
        self.assertFalse(dead.vault.parent.exists())
        self.assertTrue(live.vault.parent.exists())
        self.assertIsNotNone(sessions.get(self.root, live.id))
        self.assertIsNone(sessions.get(self.root, dead.id))

        self.assertEqual(sessions.sweep(self.root), 0)

    def test_an_expired_session_is_gone_before_the_sweeper_runs(self) -> None:
        """`get` reports it gone; only `sweep` deletes. Both halves matter — a
        read that removes a folder is a surprise, and a session that outlives its
        TTL because nothing swept yet is a hole."""
        made = plain(self.root)
        self._age(made, 25)
        self.assertIsNone(sessions.get(self.root, made.id))
        self.assertTrue(made.vault.is_dir())

    def test_ttl_is_configurable_and_measured_from_last_seen(self) -> None:
        made = plain(self.root)
        self._age(made, 2)
        self.assertIsNone(sessions.get(self.root, made.id, ttl_hours=1))
        self.assertIsNotNone(sessions.get(self.root, made.id, ttl_hours=3))

    def test_a_directory_with_no_record_is_swept_on_its_own_age(self) -> None:
        """Nothing can open it, so it is not a session — it is a disk leak."""
        orphan = self.root / sessions.SESSIONS_DIRNAME / ("O" * 43)
        orphan.mkdir(parents=True)
        old = time.time() - 48 * HOUR
        os.utime(orphan, (old, old))
        self.assertEqual(sessions.sweep(self.root), 1)
        self.assertFalse(orphan.exists())

    def test_sweep_never_raises_on_a_store_that_is_not_there(self) -> None:
        self.assertEqual(sessions.sweep(self.root / "never-created"), 0)

    def test_the_sweeper_thread_actually_sweeps(self) -> None:
        """A background thread that silently never runs is the failure this
        catches: the disk fills up hours later and nothing points at why."""
        import threading

        dead = plain(self.root)
        self._age(dead, 48)

        stop = threading.Event()
        thread = sessions.sweeper(self.root, interval_seconds=0.02, stop=stop)
        self.addCleanup(stop.set)
        try:
            deadline = time.time() + 5
            while dead.vault.parent.exists() and time.time() < deadline:
                time.sleep(0.02)
            self.assertFalse(dead.vault.parent.exists(), "the sweeper never ran")
        finally:
            stop.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive(), "the sweeper ignored its stop event")
        self.assertTrue(thread.daemon, "a sweep is never a reason to keep a process up")


# ── the cookie ───────────────────────────────────────────────────────────────


class TestCookie(StoreCase):
    def setUp(self) -> None:
        super().setUp()
        os.environ.pop("RM_WEB_SECURE_COOKIE", None)
        self.session = plain(self.root)

    def test_the_cookie_carries_httponly_samesite_and_path(self) -> None:
        cookie = sessions.cookie_for(self.session)
        self.assertIn(f"{sessions.COOKIE_NAME}={self.session.id}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertIn(f"Max-Age={int(sessions.TTL_HOURS * HOUR)}", cookie)

    def test_secure_is_off_over_plain_http_and_on_when_asked(self) -> None:
        """A Secure cookie over http is silently dropped, so the 127.0.0.1
        default cannot set one — and a deployment behind TLS has to be able to."""
        self.assertNotIn("Secure", sessions.cookie_for(self.session))
        self.assertIn("; Secure", sessions.cookie_for(self.session, secure=True))

        os.environ["RM_WEB_SECURE_COOKIE"] = "1"
        self.addCleanup(os.environ.pop, "RM_WEB_SECURE_COOKIE", None)
        self.assertIn("; Secure", sessions.cookie_for(self.session))
        self.assertIn("; Secure", sessions.clear_cookie())

    def test_clearing_matches_the_attributes_it_was_set_with(self) -> None:
        """A mismatched Path or SameSite leaves a second cookie of the same name
        and the session appears to survive its own deletion."""
        cleared = sessions.clear_cookie()
        self.assertIn("Max-Age=0", cleared)
        self.assertIn("Path=/", cleared)
        self.assertIn("HttpOnly", cleared)
        self.assertIn("SameSite=Lax", cleared)
        self.assertNotIn(self.session.id, cleared)

    def test_parse_cookie_finds_the_id_among_others(self) -> None:
        header = f"theme=dark; {sessions.COOKIE_NAME}={self.session.id}; ab=1"
        self.assertEqual(sessions.parse_cookie(header), self.session.id)

    def test_parse_cookie_returns_none_rather_than_raising(self) -> None:
        for header in (
            None,
            "",
            "theme=dark",
            f"{sessions.COOKIE_NAME}=",
            f"{sessions.COOKIE_NAME}=../../etc/passwd",
            f"{sessions.COOKIE_NAME}=short",
            "=;;=;",
            f"not_{sessions.COOKIE_NAME}={'A' * 43}",
        ):
            with self.subTest(header=header):
                self.assertIsNone(sessions.parse_cookie(header))

    def test_a_quoted_value_still_parses(self) -> None:
        header = f'{sessions.COOKIE_NAME}="{self.session.id}"'
        self.assertEqual(sessions.parse_cookie(header), self.session.id)


# ── what the browser is allowed to see ───────────────────────────────────────


class TestNothingSecretLeaves(StoreCase):
    def test_to_json_carries_neither_the_id_nor_the_vault_path(self) -> None:
        """The vault path contains the id, so returning it would defeat HttpOnly
        in the same response that set it."""
        made = plain(self.root)
        made.token = "gho_" + "x" * 36
        payload = json.dumps(made.to_json())

        self.assertNotIn(made.id, payload)
        self.assertNotIn(str(made.vault), payload)
        self.assertNotIn(made.token, payload)
        self.assertNotIn("vault", payload)

        body = made.to_json()
        self.assertEqual(body["mode"], made.mode)
        self.assertEqual(body["label"], made.label)

    def test_the_record_on_disk_does_keep_the_token(self) -> None:
        """It is 0o700 on the server and is never served; the browser copy is the
        one that has to be poor."""
        made = plain(self.root)
        made.token = "gho_" + "y" * 36
        sessions._save(made)
        self.assertEqual(json.loads(made.record.read_text())["token"], made.token)

        reread = sessions.get(self.root, made.id)
        assert reread is not None
        self.assertEqual(reread.token, made.token)

    def test_repr_prints_neither_the_id_nor_the_token(self) -> None:
        """The leak nobody decides on: a debug print, or a traceback.

        The vault path counts as the id — it is the id with two directory names
        around it — which is precisely what the first version of this module got
        wrong and this assertion caught.
        """
        made = plain(self.root)
        made.token = "gho_" + "z" * 36
        self.assertNotIn(made.id, repr(made))
        self.assertNotIn(str(made.vault), repr(made))
        self.assertNotIn(made.token, repr(made))
        self.assertIn(made.label, repr(made), "a log line needs something to name")

    def test_ids_are_unguessable_and_distinct(self) -> None:
        ids = {plain(self.root).id for _ in range(3)}
        self.assertEqual(len(ids), 3)
        for sid in ids:
            self.assertGreaterEqual(len(sid), 43)


# ── the seeded vault ─────────────────────────────────────────────────────────


class TestSeeding(StoreCase):
    """One seeded session, shared: seeding spawns the engine three times."""

    root: Path
    session: sessions.Session

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="rm-web-seed-"))
        cls.session = sessions.create(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self) -> None:  # not StoreCase's — this class owns its store
        pass

    def test_the_seeded_folder_is_really_a_vault(self) -> None:
        self.assertTrue((self.session.vault / "report-maker.toml").is_file())
        self.assertTrue((self.session.vault / "reports").is_dir())
        self.assertTrue((self.session.vault / "brand" / "brand.json").is_file())

    def test_the_engine_lists_the_seeded_report(self) -> None:
        """The check a mocked engine could not make: the CLI answers for it."""
        done = cli(self.session.vault, "list", "--json")
        self.assertEqual(done.code, 0, done.complaint())
        rows = json.loads(done.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], sessions.SEED_TITLE)
        self.assertEqual(rows[0]["template"], "base")

    def test_the_report_folder_holds_what_new_scaffolds(self) -> None:
        reports = sorted((self.session.vault / "reports").glob("*/main.typ"))
        self.assertEqual(len(reports), 1)
        folder = reports[0].parent
        for name in ("main.typ", "sources.yml", "todos.md"):
            self.assertTrue((folder / name).is_file(), name)

    def test_check_reports_E012_against_a_brand_new_report(self) -> None:
        """INTENDED. Do not "fix" this test.

        The starter's cover KPIs are invented and `@example-page` resolves to
        example.com, so an unedited scaffold is a fabricated report — and for an
        engine whose whole claim is cited-or-opinion, a fabricated citation that
        passes the linter is the worst failure available. E012 is why it does not.
        The web build seeds exactly this and shows the findings on first load.

        If this ever goes green, the change that made it green removed the
        demonstration, and the seed needs rethinking rather than the assertion.
        """
        done = cli(self.session.vault, "check", "--json")
        self.assertEqual(done.code, 1, "a fresh scaffold must not pass check")
        payload = json.loads(done.stdout)
        codes = [f["code"] for f in payload["findings"] if f["level"] == "error"]
        self.assertIn("E012", codes)
        self.assertGreater(payload["errors"], 0)

    def test_the_session_records_that_the_starter_is_red(self) -> None:
        """Measured from `check --json`, not assumed — so the UI's explainer can
        never outlive the findings it explains."""
        self.assertTrue(self.session.starter_findings)
        body = self.session.to_json()
        self.assertTrue(body["starterFindings"])
        self.assertEqual(body["starterExplainer"], sessions.STARTER_EXPLAINER)

        reread = sessions.get(self.root, self.session.id)
        assert reread is not None
        self.assertTrue(reread.starter_findings)

    def test_the_seed_never_declares_a_status(self) -> None:
        """`status: "draft"` would turn those errors into warnings and make the
        new vault look clean. That is the one shortcut this seed must not take:
        it would trade the product's argument for a lie about the report."""
        source = (self.session.vault / "reports").glob("*/main.typ")
        for path in source:
            self.assertNotIn("status:", path.read_text(encoding="utf-8"))

    def test_the_title_invites_the_edit_the_report_needs(self) -> None:
        self.assertIn("edit", sessions.SEED_TITLE.lower())

    def test_seeding_is_counted_against_the_command_quota(self) -> None:
        self.assertGreaterEqual(self.session.quota_used.since(0), 3)

    def test_disk_bytes_measures_the_vault(self) -> None:
        used = sessions.disk_bytes(self.session)
        self.assertGreater(used, 0)
        self.assertLess(used, sessions.DISK_QUOTA_BYTES)


class TestSeedingFailure(StoreCase):
    def test_a_failed_seed_leaves_no_half_built_session(self) -> None:
        """A session whose vault is half-made is worse than no session: the
        failure surfaces later, somewhere else, as a broken vault."""

        def refuse(vault: Path, args, timeout: float) -> sessions.Run:
            return sessions.Run(2, "", "no typst, no anything", " ".join(args))

        original = sessions._run
        sessions.use_engine(refuse)
        self.addCleanup(sessions.use_engine, original)

        with self.assertRaises(sessions.SessionError):
            sessions.create(self.root)

        store = self.root / sessions.SESSIONS_DIRNAME
        self.assertEqual(list(store.iterdir()), [])

    def test_a_github_session_is_not_seeded(self) -> None:
        """The repo is the store there; scaffolding into it would be this layer
        inventing content nobody asked for."""
        made = sessions.create(self.root, mode="github")
        self.assertEqual(made.mode, "github")
        self.assertTrue(made.vault.is_dir())
        self.assertEqual(list(made.vault.iterdir()), [])
        self.assertFalse(made.starter_findings)


# ── the engine bridge ────────────────────────────────────────────────────────


class TestEngineBridge(StoreCase):
    def test_the_cli_is_found_and_answers(self) -> None:
        self.assertIsNotNone(sessions._locate())
        done = sessions._spawn(self.root, ["--version"], 30.0)
        self.assertEqual(done.code, 0, done.complaint())
        self.assertIn("report-maker", done.stdout + done.stderr)

    def test_the_child_never_inherits_the_server_secrets(self) -> None:
        """Typst cannot read the environment, which is the sandbox this build
        leans on — but a secret the child never receives cannot leak through a
        hole nobody has found yet."""
        os.environ["RM_WEB_GITHUB_CLIENT_SECRET"] = "shhh"
        self.addCleanup(os.environ.pop, "RM_WEB_GITHUB_CLIENT_SECRET", None)

        prefix = sessions._locate()
        assert prefix is not None
        seen = {}

        original = subprocess.run

        def spy(argv, **kwargs):
            seen.update(kwargs.get("env") or {})
            return original(argv, **kwargs)

        subprocess.run = spy  # type: ignore[assignment]
        self.addCleanup(setattr, subprocess, "run", original)
        sessions._spawn(self.root, ["--version"], 30.0)

        self.assertNotIn("RM_WEB_GITHUB_CLIENT_SECRET", seen)
        self.assertIn("PATH", seen)

    def test_the_local_spawn_is_only_for_a_store_no_bridge_owns(self) -> None:
        """With no sessions root declared, the shared bridge cannot answer and
        this module falls back — which is the case every test here runs in."""
        made = sessions.create(self.root)
        self.assertTrue((made.vault / "report-maker.toml").is_file())

    def test_the_shared_bridge_is_preferred_when_it_can_answer(self) -> None:
        """A wiring step nobody has to remember.

        If seeding kept its own spawn path in a running server, that path would
        be a second way into `report-maker` with none of the bridge's argument
        guard on it. So the bridge is reached for rather than injected.
        """
        try:
            from web.server import engine
        except ImportError:  # pragma: no cover — the bridge is part of this build
            self.skipTest("web/server/engine.py is not in this build")

        store = self.root / sessions.SESSIONS_DIRNAME
        store.mkdir(parents=True, exist_ok=True)
        # The declared root is process-global, so it is put back by hand rather
        # than through the setter, which has no "undeclare".
        self.addCleanup(setattr, engine, "_root", getattr(engine, "_root", None))
        engine.set_sessions_root(store)

        seen: list[list[str]] = []
        original = engine.run

        def spy(vault, args, timeout=60.0, env=None):
            seen.append(list(args))
            return original(vault, args, timeout, env)

        engine.run = spy  # type: ignore[assignment]
        self.addCleanup(setattr, engine, "run", original)

        made = sessions.create(self.root)
        self.assertEqual(seen[0], ["init"])
        self.assertTrue((made.vault / "report-maker.toml").is_file())

    def test_use_engine_routes_the_subprocesses(self) -> None:
        calls: list[list[str]] = []

        def spy(vault: Path, args, timeout: float) -> sessions.Run:
            calls.append(list(args))
            return sessions.Run(0, "{}", "", " ".join(args))

        original = sessions._run
        sessions.use_engine(spy)
        self.addCleanup(sessions.use_engine, original)

        sessions.create(self.root)
        self.assertEqual(calls[0], ["init"])
        self.assertEqual(calls[1], ["new", sessions.SEED_TITLE])
        self.assertEqual(calls[2], ["check", "--json"])


# ── the quota record ─────────────────────────────────────────────────────────


class TestQuota(unittest.TestCase):
    def test_the_window_rolls(self) -> None:
        now = time.time()
        quota = sessions.Quota()
        quota.record(now - 2 * HOUR)
        quota.record(now - 10)
        self.assertEqual(quota.since(now - HOUR), 1)
        quota.prune(now - HOUR)
        self.assertEqual(len(quota.commands), 1)

    def test_it_survives_a_round_trip_through_the_record(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="rm-web-quota-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        made = plain(root)
        made.quota_used.record()
        sessions._save(made)

        reread = sessions.get(root, made.id)
        assert reread is not None
        self.assertEqual(len(reread.quota_used.commands), len(made.quota_used.commands))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
