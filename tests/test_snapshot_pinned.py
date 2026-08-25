"""Fetching where the caller looked, not where the name points the second time.

`engine/snapshot.py` has always refused every scheme but http and https, and
that check answers the question "what protocol will we speak". The question a
caller who *vetted* a URL needs answered is "which machine will we speak it to",
and the two come apart in the ordinary case:

    somebody resolves acme.example, judges every address it answers with,
    finds them all public, and hands the URL to `report-maker cite`
      → this module resolves acme.example again
      → the second answer is 169.254.169.254
      → the scheme is still https, so nothing objects

and again one layer along, with no name server involved at all:

    the vetted page answers 302 Location: http://127.0.0.1:9/…
      → http is a web scheme, so the hop is followed
      → the private page lands in snapshots/, where whoever asked for the
        citation reads it straight back out of the report folder

Both are closed by `pinned=`, and the tests below are written as the attack
rather than as the API: each one stands up a real HTTP server on loopback,
because a mocked opener would prove that `http_fetch` calls what it says it
calls and nothing at all about what urllib does with a `Location:` header —
which is the half that was wrong.

Loopback stands in for "somewhere a fetch must not reach". It is the same shape
as the metadata endpoint and the RFC 1918 ranges, and unlike them it can be made
to answer inside a test, so the archived bytes can be asserted on: the first
test here shows the private body genuinely arriving in a `Fetched`.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import os
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import cli as cli_mod  # noqa: E402
from engine import snapshot  # noqa: E402
from engine.snapshot import SnapshotError  # noqa: E402

SECRET = b"<html>ami-role-credentials</html>"
PAGE = b"<html><body>a page worth citing</body></html>"


# ── servers ──────────────────────────────────────────────────────────────────


class _Quiet(http.server.BaseHTTPRequestHandler):
    """No request logging: a test that prints a line per hop is a test whose
    failure nobody reads."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: D102
        pass


def _serving(handler) -> contextlib.AbstractContextManager:
    """One handler on an ephemeral loopback port, for the length of a `with`."""

    @contextlib.contextmanager
    def run():
        server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    return run()


def _static(body: bytes, status: int = 200):
    class Handler(_Quiet):
        seen: list[str] = []

        def do_GET(self) -> None:
            type(self).seen.append(self.headers.get("Host", ""))
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    Handler.seen = []
    return Handler


def _turncoat(location: str):
    """A page first, a redirect afterwards — the server the guard cannot see.

    This is the whole residual hole in one class. A caller fetches it, gets a
    page, judges it, and hands the URL onward; the next request for the very
    same URL is answered with a `Location:` pointing wherever the operator
    likes. Nothing about the first answer predicts the second, which is why a
    check performed before the fetch cannot substitute for a pin during it.
    """

    class Handler(_Quiet):
        served = 0

        def do_GET(self) -> None:
            type(self).served += 1
            if type(self).served == 1:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(PAGE)))
                self.end_headers()
                self.wfile.write(PAGE)
                return
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

    Handler.served = 0
    return Handler


# ── the hole, and the pin that closes it ─────────────────────────────────────


class ARedirectAfterTheCheck(unittest.TestCase):
    """The second fetch is the one nobody vetted."""

    def test_unpinned_the_private_body_is_archived(self) -> None:
        """What the scheme check permits, stated as a fact rather than implied.

        This is not a regression guard, it is the reason the option exists: a
        fetch that only asks "is this http?" archives the metadata endpoint the
        moment a vetted host decides to point at it. The default path is
        deliberately left exactly as it was — a person citing a URL at a
        terminal is both the caller and the vetter — so this behaviour is still
        here, and a caller who is *not* that person has to say so.
        """
        keeper = _static(SECRET)
        with _serving(keeper) as private:
            with _serving(_turncoat(f"http://127.0.0.1:{private}/creds")) as public:
                url = f"http://127.0.0.1:{public}/page"
                first = snapshot.http_fetch(url, timeout=5.0)
                self.assertEqual(first.body, PAGE)

                second = snapshot.http_fetch(url, timeout=5.0)
        self.assertEqual(second.body, SECRET)
        self.assertIn(f":{private}/creds", second.final_url)
        # The same list the pinned test asserts is *empty*, filled here by the
        # same handler class — so that assertion is one this arrangement can
        # genuinely make, rather than a list nothing ever writes to.
        self.assertEqual(keeper.seen, [f"127.0.0.1:{private}"])

    def test_pinned_the_hop_off_the_origin_is_refused(self) -> None:
        """Same servers, same order, one argument different."""
        keeper = _static(SECRET)
        with _serving(keeper) as private:
            with _serving(_turncoat(f"http://127.0.0.1:{private}/creds")) as public:
                url = f"http://127.0.0.1:{public}/page"
                first = snapshot.http_fetch(url, timeout=5.0, pinned="127.0.0.1")
                self.assertEqual(first.body, PAGE)

                with self.assertRaises(SnapshotError) as caught:
                    snapshot.http_fetch(url, timeout=5.0, pinned="127.0.0.1")
                served = list(keeper.seen)

        message = str(caught.exception)
        self.assertIn("refusing to follow the redirect", message)
        self.assertIn(f"127.0.0.1:{public}", message)
        # And the private server was never asked. A refusal that fetched first
        # and complained afterwards would have archived nothing and leaked
        # everything.
        self.assertEqual(served, [])

    def test_a_hop_that_stays_on_the_pinned_origin_is_followed(self) -> None:
        """The pin is not "no redirects", it is "no redirects nobody vetted".

        A site that answers /page with /page/ is the commonest thing on the web,
        and it connects to the address that was already approved. Refusing it
        would make the option unusable and teach a caller to leave it off.
        """

        class Handler(_Quiet):
            def do_GET(self) -> None:
                if self.path == "/page":
                    self.send_response(302)
                    self.send_header("Location", "/page/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(PAGE)))
                self.end_headers()
                self.wfile.write(PAGE)

        with _serving(Handler) as port:
            got = snapshot.http_fetch(
                f"http://127.0.0.1:{port}/page", timeout=5.0, pinned="127.0.0.1"
            )
        self.assertEqual(got.body, PAGE)
        self.assertTrue(got.final_url.endswith("/page/"))

    def test_a_hop_off_http_is_still_refused_under_a_pin(self) -> None:
        """The older guard is not replaced by the newer one."""
        with _serving(_turncoat("file:///etc/passwd")) as port:
            url = f"http://127.0.0.1:{port}/page"
            snapshot.http_fetch(url, timeout=5.0, pinned="127.0.0.1")
            with self.assertRaises(SnapshotError) as caught:
                snapshot.http_fetch(url, timeout=5.0, pinned="127.0.0.1")
        self.assertIn("only http and https", str(caught.exception))


# ── what pinning must not cost ───────────────────────────────────────────────


class ThePinKeepsTheName(unittest.TestCase):
    def test_the_host_header_is_the_hostname_not_the_address(self) -> None:
        """Connect to the literal, speak to the name.

        The `Host` header is asserted because it is the visible half of the same
        property that matters invisibly for TLS: `HTTPSConnection.connect` wraps
        the socket with `server_hostname=self.host`, so the certificate is still
        verified against the hostname the caller asked for, with the default
        context's `check_hostname` and `CERT_REQUIRED` untouched. Pinning here
        is a replacement socket factory, not a relaxed verification — a plain
        HTTP request is enough to demonstrate which name travels, and no test
        can demonstrate a certificate check that was never turned off.
        """
        handler = _static(PAGE)
        with _serving(handler) as port:
            got = snapshot.http_fetch(
                f"http://localhost:{port}/x", timeout=5.0, pinned="127.0.0.1"
            )
        self.assertEqual(got.body, PAGE)
        self.assertEqual(handler.seen, [f"localhost:{port}"])

    def test_a_proxy_in_the_environment_does_not_get_the_fetch(self) -> None:
        """`build_opener` installs a `ProxyHandler` from the environment, and a
        proxy resolves the hostname at its end — which is the pin undone by a
        variable nobody in this process set."""
        dead = _closed_port()
        with _serving(_static(PAGE)) as port:
            with _environment(http_proxy=f"http://127.0.0.1:{dead}"):
                got = snapshot.http_fetch(
                    f"http://127.0.0.1:{port}/x", timeout=5.0, pinned="127.0.0.1"
                )
        self.assertEqual(got.body, PAGE)

    def test_an_unpinned_host_cannot_be_connected_to(self) -> None:
        """Fail closed. A pin map with nothing in it for this host is not a
        reason to look the host up, it is a refusal."""
        with _serving(_static(PAGE)) as port:
            with self.assertRaises(SnapshotError) as caught:
                # The pin is minted for the URL's own origin, so the way to
                # reach an unpinned one is a redirect — covered above. Here the
                # connection class is exercised directly, which is the layer
                # that has to fail closed even if a redirect guard is bypassed.
                opener = snapshot._guarded_opener({("elsewhere", 80): "127.0.0.1"}, None)
                opener.open(f"http://127.0.0.1:{port}/x", timeout=5.0)
        self.assertIn("this fetch is pinned", str(caught.exception))


# ── the watcher ──────────────────────────────────────────────────────────────


class EveryHopIsOffered(unittest.TestCase):
    """`on_redirect` is the seam for a caller that can judge a hop itself."""

    def test_it_sees_the_hop_and_can_abort_by_raising(self) -> None:
        class Vetoed(Exception):
            pass

        seen: list[str] = []

        def watch(url: str) -> None:
            seen.append(url)
            raise Vetoed(url)

        with _serving(_static(SECRET)) as private:
            with _serving(_turncoat(f"http://127.0.0.1:{private}/creds")) as public:
                url = f"http://127.0.0.1:{public}/page"
                snapshot.http_fetch(url, timeout=5.0, on_redirect=watch)
                with self.assertRaises(Vetoed):
                    snapshot.http_fetch(url, timeout=5.0, on_redirect=watch)

        self.assertEqual(seen, [f"http://127.0.0.1:{private}/creds"])

    def test_it_may_pin_the_hop_it_vetted(self) -> None:
        """A caller that resolved the hop and approved the address says so by
        returning it, and the fetch follows a chain that stays checked
        end to end."""
        with _serving(_static(SECRET)) as elsewhere:
            with _serving(_turncoat(f"http://127.0.0.1:{elsewhere}/next")) as public:
                url = f"http://127.0.0.1:{public}/page"
                snapshot.http_fetch(url, timeout=5.0, pinned="127.0.0.1")
                got = snapshot.http_fetch(
                    url,
                    timeout=5.0,
                    pinned="127.0.0.1",
                    on_redirect=lambda hop: "127.0.0.1",
                )
        self.assertEqual(got.body, SECRET)


# ── the fetcher factory, which is what the CLI hands onward ──────────────────


class TheDefaultPathIsUntouched(unittest.TestCase):
    def test_no_pin_returns_the_function_itself(self) -> None:
        """Not "equivalent to" — the same object. `report-maker cite <url>` at a
        terminal makes the fetch this module has always made, and the cheapest
        way to be sure of that is for there to be nothing in between."""
        self.assertIs(snapshot.fetcher(), snapshot.http_fetch)

    def test_a_pinned_run_refuses_a_second_host(self) -> None:
        """One address names one machine.

        `verify` walks every archived source in a report, and a single
        `--pinned-address` cannot stand for two of them. Connecting the second
        host to the first one's address would produce a mismatch reported as
        evidence drift — a false statement about somebody's sources — so it is
        refused instead.
        """
        fetch = snapshot.fetcher(pinned="127.0.0.1")
        with _serving(_static(PAGE)) as port:
            self.assertEqual(fetch(f"http://127.0.0.1:{port}/a").body, PAGE)
            with self.assertRaises(SnapshotError) as caught:
                fetch(f"http://localhost:{port}/b")
        self.assertIn("one address cannot stand for two hosts", str(caught.exception))

    def test_a_name_is_not_an_address(self) -> None:
        for value in ("metadata.internal", "", "127.0.0.1:80", "127.0.0.1/8"):
            with self.subTest(value=value):
                with self.assertRaises(SnapshotError):
                    snapshot.fetcher(pinned=value)


class TheFlag(unittest.TestCase):
    """`--pinned-address` on `cite` and on `verify`, refusing a name at the door."""

    def test_both_commands_carry_it(self) -> None:
        parsed = cli_mod.parser().parse_args(
            ["cite", "acme/2026-01-01-x", "https://a.example/", "--pinned-address", "203.0.113.7"]
        )
        self.assertEqual(parsed.pinned_address, "203.0.113.7")
        parsed = cli_mod.parser().parse_args(
            ["verify", "--pinned-address", "203.0.113.7"]
        )
        self.assertEqual(parsed.pinned_address, "203.0.113.7")

    def test_without_it_nothing_is_pinned(self) -> None:
        parsed = cli_mod.parser().parse_args(["verify"])
        self.assertIsNone(parsed.pinned_address)
        self.assertIs(snapshot.fetcher(pinned=parsed.pinned_address), snapshot.http_fetch)

    def test_a_hostname_is_a_usage_error(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            cli_mod._ip_literal("metadata.internal")
        with open(os.devnull, "w", encoding="utf-8") as quiet:
            with contextlib.redirect_stderr(quiet):
                with self.assertRaises(SystemExit):
                    cli_mod.parser().parse_args(
                        ["verify", "--pinned-address", "metadata.internal"]
                    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def _environment(**values: str):
    before = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, was in before.items():
            if was is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = was


if __name__ == "__main__":
    unittest.main()
