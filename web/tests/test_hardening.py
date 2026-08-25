"""The four holes an adversarial pass found, each with the probe that found it.

Every test here started as something that *worked* against the shipped server,
which is why they are grouped by attack rather than by module. A regression on
any one of them is not a style problem; it is the server doing the thing again.

    the diagrams DoS      seventeen bytes of mermaid in `diagrams/` and a Build
                          press, on a server whose banner says diagrams are off:
                          `npm install`, 190 packages, a headless Chrome, and
                          460 MB into a vault with a 50 MB ceiling
    the redirect SSRF     a public host that answers `302 Location:
                          http://169.254.169.254/…` — the pre-flight checked the
                          first hop and `engine/snapshot.py` followed the second
    the resolver split    `0177.0.0.1` — `getaddrinfo` on macOS says 177.0.0.1
                          and reads as public, `inet_aton` says 127.0.0.1 and
                          reads as loopback, and the fetcher may use either
    the unscrubbed error  every refusal went out without the session's vault
                          prefix, so an engine traceback published the session
                          id — the cookie's value — in the response body

and two smaller ones: `--into ../../../../tmp` reaching the engine, and the
interpreter's own tree in a traceback naming the exact Python build.

The redirect tests stand up real HTTP servers on loopback. That is the only way
to assert on a chain: a mocked opener would prove `trace` calls what it says it
calls and nothing about what urllib does with a `Location:` header, which is the
half that was wrong.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from web.server import app, engine, routes, security, sessions

# Absolute rather than relative: `python3 -m unittest discover -s web/tests`
# loads these files as top-level modules with no package around them, and a
# relative import dies there. The absolute form works under both that and
# `python3 -m unittest web.tests.test_hardening`.
from web.tests.test_api import Base, serving


# ── a loopback stand-in for whatever a redirect points at ────────────────────


class _Quiet(http.server.BaseHTTPRequestHandler):
    """No request logging. A test that prints a line per hop is a test nobody
    reads the failure of."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: D102
        pass


def _page(status: int, body: bytes = b"<html>a page</html>"):
    class Handler(_Quiet):
        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _redirect_to(location: str, status: int = 302):
    class Handler(_Quiet):
        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


@contextlib.contextmanager
def _listening(handler):
    """One handler on an ephemeral loopback port, for the length of a `with`."""
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _pointing_at(port: int, host: str = "shortener.test") -> security.ResolvedTarget:
    """A vetted target whose pin is loopback but whose *name* is not.

    The pin is what `safe_opener` connects to and the name is what every
    subsequent hop is judged against, so this is how a chain gets walked on a
    machine with no public host to borrow: hop one is arranged, and hops two
    onward are judged exactly as they would be in production.
    """
    return security.ResolvedTarget(
        url=f"http://{host}:{port}/start",
        scheme="http",
        host=host,
        port=port,
        addresses=("127.0.0.1",),
    )


# ── the redirect SSRF ────────────────────────────────────────────────────────


class RedirectsAreVetted(unittest.TestCase):
    """Spec requirement 4's second sentence, which used to be unenforced.

    `check_url` judged the URL that was typed and nothing else. A host that
    passed it and then redirected needed no hostile name server, no DNS trick
    and no timing: one `Location:` header, and `engine/snapshot.py` — which
    checks the scheme of a hop and not its address — fetched whatever it named.
    """

    def _refusal(self, location: str) -> security.Refused:
        with _listening(_redirect_to(location)) as port:
            with self.assertRaises(security.Refused) as caught:
                security.trace(_pointing_at(port))
        return caught.exception

    def test_a_hop_to_the_metadata_endpoint_is_refused_by_name(self) -> None:
        exc = self._refusal("http://169.254.169.254/latest/meta-data/")
        self.assertEqual(exc.code, "url_metadata")
        self.assertIn("169.254.169.254", exc.message)

    def test_a_hop_to_loopback_is_refused(self) -> None:
        exc = self._refusal("http://127.0.0.1:1/x")
        self.assertEqual(exc.code, "url_blocked")
        self.assertIn("loopback", exc.message)

    def test_a_hop_to_a_private_range_is_refused(self) -> None:
        exc = self._refusal("http://10.1.2.3/x")
        self.assertEqual(exc.code, "url_blocked")
        self.assertIn("private", exc.message)

    def test_a_hop_written_in_octal_is_refused(self) -> None:
        """The two bugs meeting: a redirect, to a spelling of loopback."""
        exc = self._refusal("http://0177.0.0.1:1/x")
        self.assertEqual(exc.code, "url_blocked")
        self.assertIn("127.0.0.1", exc.message)

    def test_a_hop_off_http_is_refused_rather_than_returned(self) -> None:
        """urllib will not *follow* `file:`, and that is not the same as safe.

        It reports the refusal as an `HTTPError` whose `geturl()` is
        `file:///etc/passwd`, so a `trace` that returned the final URL of an
        error response would hand its caller the exact string it exists to
        refuse — on the strength of never having connected to it.
        """
        exc = self._refusal("file:///etc/passwd")
        self.assertEqual(exc.code, "url_redirect")

    def test_a_second_hop_is_judged_even_when_the_first_was_fine(self) -> None:
        """Two servers, so the chain is a chain: hop one answers, hop two is
        where the refusal has to happen."""
        with _listening(_page(200)) as destination:
            with _listening(
                _redirect_to(f"http://127.0.0.1:{destination}/x")
            ) as first:
                with self.assertRaises(security.Refused) as caught:
                    security.trace(_pointing_at(first))
        self.assertEqual(caught.exception.code, "url_blocked")

    def test_a_page_that_does_not_redirect_is_returned_unchanged(self) -> None:
        with _listening(_page(200)) as port:
            target = _pointing_at(port)
            self.assertEqual(security.trace(target), target.url)

    def test_a_404_is_a_finding_and_not_a_refusal(self) -> None:
        """A cited page that has gone is what `check` and `verify` exist to
        report. Refusing the request would hide the finding."""
        with _listening(_page(404, b"gone")) as port:
            target = _pointing_at(port)
            self.assertEqual(security.trace(target), target.url)

    def test_a_host_that_cannot_be_reached_fails_closed(self) -> None:
        """Fail closed, or the guard is something an attacker turns off by
        refusing our request and answering the engine's."""
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead = probe.getsockname()[1]
        with self.assertRaises(security.Refused) as caught:
            security.trace(_pointing_at(dead), timeout=2.0)
        self.assertEqual(caught.exception.code, "url_unreachable")


# ── the resolver split ───────────────────────────────────────────────────────


class AddressSpellings(unittest.TestCase):
    """Two parsers in one chain, disagreeing.

    `getaddrinfo("0177.0.0.1")` answers 177.0.0.1 on macOS — it drops the
    leading zero — while `inet_aton`, glibc and curl read the same string as
    octal and answer 127.0.0.1. A guard that consulted only the resolver
    approved a URL that the thing doing the fetching resolves to loopback.
    """

    def _code(self, url: str) -> str:
        with self.assertRaises(security.Refused) as caught:
            security.check_url(url)
        return caught.exception.code

    def test_octal_loopback(self) -> None:
        self.assertEqual(self._code("http://0177.0.0.1/"), "url_blocked")

    def test_hex_loopback(self) -> None:
        self.assertEqual(self._code("http://0x7f.1/"), "url_blocked")

    def test_a_bare_integer(self) -> None:
        self.assertEqual(self._code("http://2130706433/"), "url_blocked")

    def test_short_form(self) -> None:
        self.assertEqual(self._code("http://127.1/"), "url_blocked")

    def test_octal_metadata_endpoint(self) -> None:
        self.assertEqual(
            self._code("http://0251.0376.0251.0376/latest/meta-data/"), "url_metadata"
        )

    def test_a_canonical_literal_still_reads_as_resolved(self) -> None:
        """The message matters. "169.254.169.254 is a spelling of
        169.254.169.254" explains nothing, so a literal already written the one
        way every parser agrees on is left to the resolver pass."""
        with self.assertRaises(security.Refused) as caught:
            security.check_url("http://169.254.169.254/")
        self.assertIn("resolves to", caught.exception.message)

    def test_a_hostname_is_not_mistaken_for_a_literal(self) -> None:
        self.assertEqual(list(security._literal_forms("example.com")), [])
        self.assertEqual(list(security._literal_forms("news.bbc.co.uk")), [])


# ── the diagrams DoS ─────────────────────────────────────────────────────────


class DiagramsStayOff(unittest.TestCase):
    """Spec requirement 6, on the path that actually runs.

    `guard` refuses `report-maker diagrams`, and that was never the whole
    surface: `report-maker all` renders diagrams as its second step, so a
    stranger who writes a `.mmd` and presses Build gets the headless Chrome —
    and, before it, an `npm install` from the public registry.
    """

    def test_node_is_removed_from_the_environment_when_diagrams_are_off(self) -> None:
        env = engine._env(None)
        for entry in env.get("PATH", "").split(os.pathsep):
            if not entry:
                continue
            for tool in engine._NODE_TOOLS:
                self.assertFalse(
                    (Path(entry) / tool).exists(),
                    f"{tool} is still reachable at {entry}",
                )

    def test_node_is_left_alone_when_the_operator_turns_diagrams_on(self) -> None:
        os.environ[engine.DIAGRAMS_ENV] = "1"
        self.addCleanup(os.environ.pop, engine.DIAGRAMS_ENV, None)
        self.assertEqual(engine._env(None).get("PATH"), os.environ.get("PATH"))

    def test_only_the_directories_holding_node_are_dropped(self) -> None:
        """A blunt fix that emptied `PATH` would take git and typst with it."""
        root = Path(tempfile.mkdtemp(prefix="rm-web-path-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "node").mkdir()
        (root / "node" / "npm").write_text("#!/bin/sh\n")
        (root / "system").mkdir()
        (root / "system" / "git").write_text("#!/bin/sh\n")
        kept = engine._without_node(
            os.pathsep.join([str(root / "node"), str(root / "system")])
        )
        self.assertEqual(kept, str(root / "system"))

    @unittest.skipUnless(shutil.which("npm"), "npm is not installed on this machine")
    def test_a_build_with_a_mmd_file_does_not_install_mermaid(self) -> None:
        """The probe that found it, end to end.

        Measured rather than asserted about: the vault is weighed before and
        after, because what made this worth fixing was not that a diagram
        rendered — it was 460 MB arriving inside a 50 MB quota, which no check
        on the *next* write can undo.
        """
        tmp = Path(tempfile.mkdtemp(prefix="rm-web-mmd-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with serving(tmp) as (client, _ctx):
            self.assertEqual(client.post("/api/session").status, 201)
            listed = client.get("/api/reports").json()["reports"]
            report_id = listed[0]["id"]
            wrote = client.put(
                f"/api/reports/{report_id}/file"
                f"?path=reports/{report_id}/diagrams/attack.mmd",
                {"text": "graph TD\n  A-->B\n"},
            )
            self.assertEqual(wrote.status, 200)

            vault = _sole_vault(tmp)
            before = security.dir_size(vault)

            built = client.post(f"/api/reports/{report_id}/build")
            self.assertEqual(built.status, 200)
            self.assertFalse(built.json()["diagrams"])

            self.assertFalse(
                (vault / ".build" / "mermaid").exists(),
                "mermaid-cli was installed into a session vault with diagrams off",
            )
            self.assertLess(
                security.dir_size(vault) - before,
                20 * 1024 * 1024,
                "the build grew the vault by more than a report's worth",
            )


def _sole_vault(root: Path) -> Path:
    store = root / sessions.SESSIONS_DIRNAME
    return next(entry / "vault" for entry in store.iterdir() if entry.is_dir())


# ── arguments that become paths ──────────────────────────────────────────────


class GroupAndSlugAreContained(unittest.TestCase):
    """`--into` and `--slug` are joined onto `reports/` by the engine.

    The engine refuses `../../../../tmp/escape` today, but by accident of
    ordering: it computes the project-relative path of `sources.yml` a few lines
    before it calls `mkdir`, and that computation happens to raise. A
    containment guarantee that depends on which line of somebody else's
    function runs first is not one.
    """

    def test_a_parent_segment_is_refused(self) -> None:
        with self.assertRaises(security.Refused) as caught:
            routes._relative("../../../../tmp/escape", "group")
        self.assertEqual(caught.exception.code, "bad_argument")

    def test_an_absolute_path_is_refused(self) -> None:
        with self.assertRaises(security.Refused):
            routes._relative("/etc", "group")

    def test_a_home_relative_path_is_refused(self) -> None:
        with self.assertRaises(security.Refused):
            routes._relative("~/elsewhere", "group")

    def test_an_ordinary_group_survives(self) -> None:
        self.assertEqual(routes._relative("clients/acme", "group"), "clients/acme")

    def test_the_route_refuses_before_the_engine_is_spawned(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="rm-web-group-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with serving(tmp) as (client, _ctx):
            self.assertEqual(client.post("/api/session").status, 201)
            answer = client.post(
                "/api/reports", {"title": "ok", "group": "../../../../tmp/escape"}
            )
            self.assertEqual(answer.status, 400)
            self.assertEqual(answer.code(), "bad_argument")


# ── the unscrubbed error body ────────────────────────────────────────────────


class ErrorsAreScrubbedToo(Base):
    """The session id is the cookie's value, and it was in the error bodies.

    `_serve` took the session out of `_dispatch`'s *return value*, and a
    handler that raises never returns one — so every refusal went out with no
    vault prefix to strip and no id to redact. An engine traceback names the
    vault it was run in, and a session vault's path *is* the session id, so the
    one response nobody looks at published the credential that `HttpOnly`
    exists to keep out of reach.
    """

    def test_an_engine_traceback_carries_neither_the_id_nor_the_vault(self) -> None:
        with serving(self.tmp) as (client, ctx):
            self.assertEqual(client.post("/api/session").status, 201)
            session = sessions.get(ctx.root, _sole_id(ctx))

            listed = client.get("/api/reports").json()["reports"]
            report_id = listed[0]["id"]
            # Break the vault's own config, which is the cheapest way to make
            # the engine raise inside a stdlib frame and print the whole tree.
            broken = client.put(
                f"/api/reports/{report_id}/file?path=report-maker.toml",
                {"text": "[vault\nnot toml ["},
            )
            self.assertEqual(broken.status, 200)

            answer = client.get("/api/reports")
            self.assertGreaterEqual(answer.status, 400)
            body = answer.body.decode("utf-8")
            self.assertNotIn(session.id, body)
            self.assertNotIn(str(session.vault), body)
            self.assertNotIn(str(ctx.root), body)

    def test_a_refusal_before_any_session_opens_still_answers(self) -> None:
        """The scrubber's new input must not become a new way to 500."""
        with serving(self.tmp) as (client, _ctx):
            answer = client.get("/api/reports")
            self.assertEqual(answer.status, 401)
            self.assertEqual(answer.code(), "no_session")

    def test_the_interpreter_tree_is_not_published(self) -> None:
        """A traceback that names `/…/python@3.14/3.14.4/…` turns "try
        everything" into a list of CVEs that apply to this box."""
        pairs = dict(app._roots(_ctx_stub(self.tmp), None))
        import sys

        self.assertIn(str(sys.base_prefix), pairs)
        self.assertIn(str(Path(sys.base_prefix).resolve()), pairs)


# ── the constraint typst's sandbox rests on ──────────────────────────────────


class NoSymlinkReachesAVault(Base):
    """Typst's `--root` refuses `..` and refuses an absolute path. It does not
    refuse a symlink.

    Probed, not assumed: with `leakdir -> /etc` planted in a session vault,
    `#raw(read("/leakdir/passwd"))` compiles without complaint and the contents
    are typeset into the PDF the session downloads. So `--root` is a real
    boundary only while no link crosses into a vault, and everything that could
    put one there has to keep refusing.

    Three things do, and this is where that is written down. If a route is ever
    added that unpacks an archive or accepts an upload, it joins them — and the
    failure it would cause does not look like a path bug, it looks like a report
    with somebody's `/etc/passwd` set in it.
    """

    def test_a_write_will_not_follow_a_link_out_of_the_vault(self) -> None:
        vault = self.tmp / "vault"
        (vault / "reports").mkdir(parents=True)
        (vault / "escape").symlink_to("/etc")
        with self.assertRaises(security.Refused) as caught:
            security.within(vault, "escape/passwd")
        self.assertEqual(caught.exception.code, "path_symlink")

    def test_a_clone_checks_symlinks_out_as_ordinary_files(self) -> None:
        """`core.symlinks=false` is what makes a repository's own links inert.

        Asserted on the constant rather than on a clone, because the clone needs
        a token and a network and this needs neither — and what would break is
        somebody editing the tuple, which this notices.
        """
        from web.server import github

        self.assertIn("core.symlinks=false", github.GIT_HARDENING)

    def test_a_seeded_vault_and_a_built_one_hold_no_links(self) -> None:
        """The third route in: the engine itself, populating a vault.

        Looked at rather than reasoned about. `init`, `new`, `stage`, `build`
        and `pages` all run here, and afterwards the tree is walked for anything
        that is a link — which is the question, and not whether the word
        appears in a source file.
        """
        with serving(self.tmp) as (client, _ctx):
            self.assertEqual(client.post("/api/session").status, 201)
            report_id = client.get("/api/reports").json()["reports"][0]["id"]
            client.post(f"/api/reports/{report_id}/build")
            vault = _sole_vault(self.tmp)
            links = [
                str(path.relative_to(vault))
                for path in vault.rglob("*")
                if path.is_symlink()
            ]
            self.assertEqual(links, [])


def _sole_id(ctx: routes.Ctx) -> str:
    return next(entry.name for entry in ctx.store.iterdir() if entry.is_dir())


def _ctx_stub(root: Path) -> routes.Ctx:
    return routes.Ctx(
        root=root,
        store=root / "sessions",
        shares=root / "shares",
        client=None,
        tls=False,
        limiter=security.RateLimiter(),
    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
