"""Attacks on the web boundary, and the refusals that have to hold.

This suite is written from the attacker's side. Every test here sends the
request a stranger would send — `..`, an absolute path, a symlink out of the
vault, a URL naming the metadata endpoint, a redirect off a public host into
the private network — and then asserts two separate things:

    that the call was refused, and
    that the damage did not happen anyway.

The second half is the point. A test that only asserts an exception passes
against code that reads the file, opens the socket or writes the byte and
*then* raises, which is not a guard, it is an apology. So the refusals are
checked against a canary wherever a canary is possible: a secret file outside
the vault whose contents must never come back and whose bytes must never
change, a real HTTP server on loopback whose request log must stay empty, a
recorder wrapped around `socket.create_connection` that must never see the
private address.

The other half of testing a guard is proving it is not simply refusing
everything. `def within(*args): raise Forbidden(...)` passes every refusal test
in this file, so each guard also has a positive case: a legitimate path
resolves, a symlink that stays inside the vault is allowed, a public URL is
vetted and fetched end to end, a session under its quota proceeds, a client
under its rate limit is served.

Nothing here touches the network. `socket.getaddrinfo` and
`socket.create_connection` are both replaced for the duration of each network
test, and the replacement refuses any destination the test did not set up — so
a suite that starts reaching the real internet fails loudly rather than
becoming slow and flaky on an aeroplane.

    python3 -m unittest discover -s web/tests
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from web.server import security  # noqa: E402

SECRET = "the-vault-boundary-was-crossed"


# ── the helpers a request handler would use ──────────────────────────────────
#
# The guards are never called bare in these tests. Each one is called through
# the two-line helper the API would use it in, so that "was it refused" and
# "did the read/write/connect happen anyway" are questions about the same code
# path a real request takes.


def read_through(vault: Path, path: str) -> str:
    return security.within(vault, path).read_text()


def write_through(vault: Path, path: str, text: str) -> None:
    security.within(vault, path).write_text(text)


def guarded_fetch(url: str, timeout: float = 2.0) -> bytes:
    """Vet, pin, fetch — the whole sequence, exactly as a handler would run it."""
    target = security.check_url(url)
    with security.safe_opener(target, timeout=timeout).open(url) as response:
        return response.read()


class Network:
    """A fake internet: named hosts, routed addresses, and a log of attempts.

    `getaddrinfo` answers from `names`; `create_connection` is allowed only to
    a destination in `routes`, which maps the pinned public address onto the
    loopback port a test server is actually listening on. Anything else raises
    — including every real address on the internet, which is how this suite
    stays offline.
    """

    def __init__(self, names: dict[str, list[str]], routes: dict | None = None):
        self.names = names
        self.routes = routes or {}
        self.attempts: list[tuple[str, int]] = []
        self._real_connect = socket.create_connection
        self._patches: list = []

    def getaddrinfo(self, host, port, family=0, type=0, proto=0, flags=0):
        literals = self.names.get(host)
        if literals is None:
            try:
                ipaddress.ip_address(host)
            except ValueError:
                raise socket.gaierror(
                    socket.EAI_NONAME, f"{host} is not a name this test set up"
                ) from None
            literals = [host]
        answers = []
        for literal in literals:
            ip = ipaddress.ip_address(literal)
            if ip.version == 6:
                answers.append(
                    (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (literal, port, 0, 0))
                )
            else:
                answers.append(
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (literal, port))
                )
        return answers

    def create_connection(self, address, *args, **kwargs):
        host, port = address[0], address[1]
        self.attempts.append((host, port))
        route = self.routes.get((host, port))
        if route is None:
            raise ConnectionRefusedError(
                f"this test never routed {host}:{port} anywhere"
            )
        return self._real_connect(route, *args, **kwargs)

    def __enter__(self) -> Network:
        self._patches = [
            mock.patch("socket.getaddrinfo", self.getaddrinfo),
            mock.patch("socket.create_connection", self.create_connection),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc) -> None:
        for patch in reversed(self._patches):
            patch.stop()


class Canary(HTTPServer):
    """A real HTTP server on loopback that records every request it is given.

    Used two ways: as the thing that must *never* be reached (its log stays
    empty when a guard works), and as the far end of the positive controls, so
    a successful fetch is proved by bytes coming back rather than by nothing
    raising.
    """

    def __init__(self, reply):
        self.log: list[str] = []
        self.reply = reply
        super().__init__(("127.0.0.1", 0), _CanaryHandler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


class _CanaryHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's vocabulary
        self.server.log.append(self.path)
        status, headers, body = self.server.reply(self.path)
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def ok(_path):
    return 200, [("Content-Type", "text/plain")], b"hello from the far end"


def redirect_to(url):
    def reply(_path):
        return 302, [("Location", url)], b""

    return reply


# ── path containment ─────────────────────────────────────────────────────────


class WithinTests(unittest.TestCase):
    """`within` is the boundary between a request and the rest of the disk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.vault = root / "sessions" / "abc" / "vault"
        (self.vault / "reports" / "2026-08-25-audit").mkdir(parents=True)
        (self.vault / "reports" / "2026-08-25-audit" / "main.typ").write_text("body")

        # The canary: a file outside the vault that no request may ever read
        # and no request may ever change.
        self.outside = root / "outside"
        self.outside.mkdir()
        self.secret = self.outside / "secret.txt"
        self.secret.write_text(SECRET)

        # A neighbouring directory whose path is a string prefix of the vault's.
        (root / "sessions" / "abc" / "vault-evil").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def assertSecretIntact(self):
        self.assertEqual(self.secret.read_text(), SECRET)

    # positive: the guard has to let the ordinary case through, or every
    # refusal below would also pass against `within = raise`.

    def test_a_path_inside_the_vault_resolves_and_reads(self):
        text = read_through(self.vault, "reports/2026-08-25-audit/main.typ")
        self.assertEqual(text, "body")

    def test_the_vault_root_itself_is_inside_the_vault(self):
        self.assertEqual(security.within(self.vault, ""), self.vault.resolve())
        self.assertEqual(security.within(self.vault, "."), self.vault.resolve())

    def test_a_symlink_that_stays_inside_the_vault_is_allowed(self):
        # The rule is containment, not "no symlinks". A vault legitimately
        # contains one, and refusing them all would be a different, cruder rule.
        link = self.vault / "shortcut.typ"
        link.symlink_to(self.vault / "reports" / "2026-08-25-audit" / "main.typ")
        self.assertEqual(read_through(self.vault, "shortcut.typ"), "body")

    # the attacks

    def test_dot_dot_is_refused_and_the_secret_is_not_read(self):
        with self.assertRaises(security.Forbidden) as caught:
            read_through(self.vault, "../../../outside/secret.txt")
        self.assertEqual(caught.exception.code, "path_parent")
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertSecretIntact()

    def test_dot_dot_buried_mid_path_is_refused(self):
        with self.assertRaises(security.Forbidden) as caught:
            read_through(
                self.vault, "reports/2026-08-25-audit/../../../outside/secret.txt"
            )
        self.assertEqual(caught.exception.code, "path_parent")
        self.assertSecretIntact()

    def test_dot_dot_cannot_be_written_through(self):
        with self.assertRaises(security.Forbidden):
            write_through(self.vault, "../../../outside/secret.txt", "overwritten")
        self.assertSecretIntact()

    def test_an_absolute_path_is_refused_rather_than_re_rooted(self):
        with self.assertRaises(security.Forbidden) as caught:
            read_through(self.vault, str(self.secret))
        self.assertEqual(caught.exception.code, "path_absolute")
        self.assertSecretIntact()

    def test_an_absolute_path_cannot_be_written_through(self):
        with self.assertRaises(security.Forbidden):
            write_through(self.vault, "/etc/hosts", "nope")
        self.assertSecretIntact()

    def test_a_leading_tilde_is_refused(self):
        with self.assertRaises(security.Forbidden) as caught:
            read_through(self.vault, "~/.ssh/id_rsa")
        self.assertEqual(caught.exception.code, "path_absolute")

    def test_a_symlink_out_of_the_vault_cannot_be_read_through(self):
        (self.vault / "escape").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(security.Forbidden) as caught:
            read_through(self.vault, "escape/secret.txt")
        self.assertEqual(caught.exception.code, "path_symlink")
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertSecretIntact()

    def test_a_symlinked_file_out_of_the_vault_cannot_be_written_through(self):
        # The dangerous half: following this link would overwrite a file that
        # belongs to somebody else, using this session's own write handler.
        (self.vault / "notes.md").symlink_to(self.secret)
        with self.assertRaises(security.Forbidden) as caught:
            write_through(self.vault, "notes.md", "overwritten")
        self.assertEqual(caught.exception.code, "path_symlink")
        self.assertSecretIntact()

    def test_a_symlink_to_a_directory_above_the_vault_is_refused(self):
        (self.vault / "up").symlink_to(self.vault.parent, target_is_directory=True)
        with self.assertRaises(security.Forbidden) as caught:
            read_through(self.vault, "up/vault-evil")
        self.assertEqual(caught.exception.code, "path_symlink")

    def test_a_nul_byte_is_refused_before_any_filesystem_call(self):
        with self.assertRaises(security.Forbidden) as caught:
            read_through(self.vault, "main.typ\x00.png")
        self.assertEqual(caught.exception.code, "path_nul")

    def test_a_sibling_whose_path_is_a_string_prefix_is_outside(self):
        # /…/vault-evil starts with /…/vault. A string prefix test says yes.
        self.assertFalse(
            security._inside(
                self.vault, self.vault.parent / "vault-evil" / "report.typ"
            )
        )
        self.assertTrue(security._inside(self.vault, self.vault / "reports"))

    def test_a_refusal_never_names_the_server_filesystem_layout(self):
        with self.assertRaises(security.Forbidden) as caught:
            read_through(self.vault, "../../../outside/secret.txt")
        rendered = f"{caught.exception.message} {caught.exception.detail}"
        self.assertNotIn(str(self.vault), rendered)
        self.assertNotIn(self.tmp.name, rendered)

    def test_control_characters_never_survive_into_the_message(self):
        with self.assertRaises(security.Forbidden) as caught:
            read_through(self.vault, "/etc/\r\nX-Injected: yes")
        self.assertNotIn("\r", caught.exception.message)
        self.assertNotIn("\n", caught.exception.message)

    def test_contains_answers_the_same_question_without_raising(self):
        self.assertTrue(security.contains(self.vault, "reports"))
        self.assertFalse(security.contains(self.vault, "../outside/secret.txt"))


# ── the SSRF pre-flight ──────────────────────────────────────────────────────


class SsrfTests(unittest.TestCase):
    """`cite` fetches a URL a stranger chose. This is the guard in front of it."""

    def test_a_public_url_is_vetted_and_fetched_end_to_end(self):
        # The positive control. Without it, every refusal below would also pass
        # against a `check_url` that refuses everything.
        server = Canary(ok)
        self.addCleanup(server.stop)
        with Network(
            names={"public.test": ["93.184.216.34"]},
            routes={("93.184.216.34", 80): ("127.0.0.1", server.port)},
        ):
            body = guarded_fetch("http://public.test/page")
        self.assertEqual(body, b"hello from the far end")
        self.assertEqual(server.log, ["/page"])

    def test_loopback_is_refused_and_the_local_server_is_never_touched(self):
        server = Canary(ok)
        self.addCleanup(server.stop)
        with Network(names={}) as net:
            with self.assertRaises(security.Forbidden) as caught:
                guarded_fetch(f"http://127.0.0.1:{server.port}/admin")
        self.assertEqual(caught.exception.code, "url_blocked")
        self.assertIn("loopback", caught.exception.message)
        # The canary: nothing connected, so nothing was served.
        self.assertEqual(server.log, [])
        self.assertEqual(net.attempts, [])

    def test_localhost_by_name_is_refused_too(self):
        with Network(names={"localhost": ["127.0.0.1"]}):
            with self.assertRaises(security.Forbidden) as caught:
                security.check_url("http://localhost:8080/")
        self.assertEqual(caught.exception.code, "url_blocked")

    def test_the_aws_metadata_endpoint_is_refused_by_name(self):
        with Network(names={}):
            with self.assertRaises(security.Forbidden) as caught:
                security.check_url("http://169.254.169.254/latest/meta-data/iam/")
        self.assertEqual(caught.exception.code, "url_metadata")
        # The message has to say why, not merely that. This is the refusal a
        # reader most needs to see the tool making.
        self.assertIn("metadata", caught.exception.message)
        self.assertIn("credentials", caught.exception.detail)

    def test_the_ipv6_metadata_endpoint_is_refused(self):
        with Network(names={}):
            with self.assertRaises(security.Forbidden) as caught:
                security.check_url("http://[fd00:ec2::254]/latest/meta-data/")
        self.assertEqual(caught.exception.code, "url_metadata")

    def test_a_name_resolving_into_the_private_network_is_refused(self):
        with Network(names={"intranet.test": ["10.0.0.7"]}) as net:
            with self.assertRaises(security.Forbidden) as caught:
                guarded_fetch("http://intranet.test/wiki")
        self.assertEqual(caught.exception.code, "url_blocked")
        self.assertIn("10.0.0.7", caught.exception.message)
        self.assertEqual(net.attempts, [])

    def test_every_private_range_is_refused(self):
        blocked = {
            "ten.test": "10.1.2.3",
            "carrier.test": "100.64.0.1",
            "seventeen.test": "172.16.9.9",
            "documentation.test": "203.0.113.5",
            "one92.test": "192.168.1.1",
            "linklocal.test": "169.254.1.1",
            "unique.test": "fc00::1",
            "v6local.test": "fe80::1",
            "multicast.test": "224.0.0.1",
            "zero.test": "0.0.0.0",
        }
        for host, literal in blocked.items():
            with self.subTest(address=literal):
                with Network(names={host: [literal]}):
                    with self.assertRaises(security.Forbidden) as caught:
                        security.check_url(f"http://{host}/")
                self.assertEqual(caught.exception.code, "url_blocked")

    def test_an_ipv4_address_wrapped_in_ipv6_is_unwrapped_before_judging(self):
        # ::ffff:127.0.0.1 is loopback to the network stack and answers False
        # to IPv6Address.is_loopback. Judging the envelope would let it through.
        with Network(names={"wrapped.test": ["::ffff:127.0.0.1"]}):
            with self.assertRaises(security.Forbidden) as caught:
                security.check_url("http://wrapped.test/")
        self.assertEqual(caught.exception.code, "url_blocked")
        self.assertIn("127.0.0.1", caught.exception.message)

    def test_one_private_answer_among_several_refuses_the_whole_name(self):
        # A name with a public A record and a loopback A record is not half
        # safe: whichever the resolver hands the connection is the one used.
        with Network(names={"split.test": ["93.184.216.34", "127.0.0.1"]}) as net:
            with self.assertRaises(security.Forbidden) as caught:
                guarded_fetch("http://split.test/")
        self.assertEqual(caught.exception.code, "url_blocked")
        self.assertEqual(net.attempts, [])

    def test_non_web_schemes_are_refused(self):
        for url in (
            "file:///etc/passwd",
            "ftp://ftp.test/secret",
            "gopher://gopher.test:70/_%0d%0aSET",
            "data:text/html,<script>x</script>",
            "javascript:alert(1)",
            "dict://127.0.0.1:11211/stat",
        ):
            with self.subTest(url=url):
                with self.assertRaises(security.Forbidden) as caught:
                    security.check_url(url)
                self.assertEqual(caught.exception.code, "url_scheme")

    def test_the_opener_has_no_handler_for_file_urls_at_all(self):
        # Belt and braces on the scheme check: even handed a file: URL
        # directly, the opener cannot open one, because no FileHandler was
        # installed. The canary is that the secret does not come back.
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: outside.joinpath("secret.txt").unlink())
        secret = outside / "secret.txt"
        secret.write_text(SECRET)
        with Network(names={"public.test": ["93.184.216.34"]}):
            target = security.check_url("http://public.test/")
            opener = security.safe_opener(target)
            with self.assertRaises(urllib.error.URLError) as caught:
                opener.open(secret.as_uri())
        self.assertNotIn(SECRET, str(caught.exception))

    def test_credentials_in_the_url_are_refused(self):
        with Network(names={"public.test": ["93.184.216.34"]}):
            with self.assertRaises(security.Forbidden) as caught:
                security.check_url("http://user:pass@public.test/")
        self.assertEqual(caught.exception.code, "url_credentials")

    def test_a_name_that_does_not_resolve_is_reported_as_a_bad_request(self):
        with Network(names={}):
            with self.assertRaises(security.Forbidden) as caught:
                security.check_url("http://nowhere.invalid/")
        self.assertEqual(caught.exception.code, "url_dns")
        self.assertEqual(caught.exception.status, 400)

    def test_the_vetted_addresses_come_back_so_the_caller_can_pin_them(self):
        with Network(names={"public.test": ["93.184.216.34", "93.184.216.35"]}):
            target = security.check_url("https://public.test/page")
        self.assertEqual(target.addresses, ("93.184.216.34", "93.184.216.35"))
        self.assertEqual(target.address, "93.184.216.34")
        self.assertEqual(target.port, 443)

    def test_the_opener_refuses_a_host_nobody_vetted(self):
        # Fail closed: the pin map is the allow-list, and a host missing from
        # it is a host no check ever ran on.
        with Network(names={"public.test": ["93.184.216.34"]}):
            target = security.check_url("http://public.test/")
            opener = security.safe_opener(target)
            with self.assertRaises(security.Forbidden) as caught:
                opener.open("http://other.test/")
        self.assertEqual(caught.exception.code, "url_unpinned")


class RedirectTests(unittest.TestCase):
    """A public host that answers `302 Location: http://10.0.0.7/` is the same
    attack, one hop later, and it needs no hostile name server at all."""

    def test_a_redirect_into_the_private_network_is_refused_before_it_is_followed(self):
        server = Canary(redirect_to("http://intranet.test/secrets"))
        self.addCleanup(server.stop)
        with Network(
            names={"public.test": ["93.184.216.34"], "intranet.test": ["10.0.0.7"]},
            routes={("93.184.216.34", 80): ("127.0.0.1", server.port)},
        ) as net:
            with self.assertRaises(security.Forbidden) as caught:
                guarded_fetch("http://public.test/start")
        self.assertEqual(caught.exception.code, "url_blocked")
        self.assertIn("redirect", caught.exception.message)
        # The canaries: the first hop happened, the second never did.
        self.assertEqual(server.log, ["/start"])
        self.assertNotIn(("10.0.0.7", 80), net.attempts)

    def test_a_redirect_to_the_metadata_endpoint_is_refused(self):
        server = Canary(redirect_to("http://169.254.169.254/latest/meta-data/"))
        self.addCleanup(server.stop)
        with Network(
            names={"public.test": ["93.184.216.34"]},
            routes={("93.184.216.34", 80): ("127.0.0.1", server.port)},
        ) as net:
            with self.assertRaises(security.Forbidden) as caught:
                guarded_fetch("http://public.test/start")
        self.assertEqual(caught.exception.code, "url_metadata")
        self.assertNotIn(("169.254.169.254", 80), net.attempts)

    def test_check_redirect_refuses_a_hop_off_http(self):
        for url in ("file:///etc/passwd", "ftp://ftp.test/x", "gopher://g.test/"):
            with self.subTest(url=url):
                with self.assertRaises(security.Forbidden) as caught:
                    security.check_redirect(url)
                self.assertEqual(caught.exception.code, "url_scheme")

    def test_a_redirect_off_http_never_reaches_the_disk(self):
        # Which layer says no varies by Python version — 3.14's own redirect
        # handler refuses a non-web hop before ours is consulted, and older
        # ones happily followed `ftp:`. What must not vary is the outcome, and
        # the canary is that the file's contents do not come back.
        secret_file = Path(tempfile.mkdtemp()) / "secret.txt"
        secret_file.write_text(SECRET)
        server = Canary(redirect_to(secret_file.as_uri()))
        self.addCleanup(server.stop)
        with Network(
            names={"public.test": ["93.184.216.34"]},
            routes={("93.184.216.34", 80): ("127.0.0.1", server.port)},
        ):
            with self.assertRaises(
                (security.Forbidden, urllib.error.URLError)
            ) as caught:
                guarded_fetch("http://public.test/start")
        raised = caught.exception
        self.addCleanup(getattr(raised, "close", lambda: None))
        self.assertNotIn(SECRET, str(raised))

    def test_a_redirect_between_public_hosts_still_works(self):
        # The positive control for the redirect path: re-vetting each hop must
        # not turn every redirect into a refusal.
        second = Canary(ok)
        self.addCleanup(second.stop)
        first = Canary(redirect_to("http://second.test/landed"))
        self.addCleanup(first.stop)
        with Network(
            names={"first.test": ["93.184.216.34"], "second.test": ["93.184.216.35"]},
            routes={
                ("93.184.216.34", 80): ("127.0.0.1", first.port),
                ("93.184.216.35", 80): ("127.0.0.1", second.port),
            },
        ):
            body = guarded_fetch("http://first.test/start")
        self.assertEqual(body, b"hello from the far end")
        self.assertEqual(second.log, ["/landed"])


# ── quotas ───────────────────────────────────────────────────────────────────


class FakeSession:
    """The three fields `enforce` reads. Structural, like the Protocol."""

    def __init__(self, vault: Path, quota: security.Quota):
        self.vault = vault
        self.quota = quota
        self.usage = security.Usage()


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_limits_are_the_ones_the_spec_names(self):
        quota = security.Quota()
        self.assertEqual(quota.disk_bytes, 50 * 1024 * 1024)
        self.assertEqual(quota.wall_seconds, 60)
        self.assertEqual(quota.commands_per_hour, 200)
        self.assertEqual(quota.reports, 20)

    # disk

    def test_a_session_under_its_disk_quota_writes(self):
        session = FakeSession(self.vault, security.Quota(disk_bytes=4096))
        security.enforce(session, "disk")
        (self.vault / "main.typ").write_text("x" * 100)
        self.assertTrue((self.vault / "main.typ").exists())

    def test_a_full_vault_refuses_the_write_before_the_file_appears(self):
        session = FakeSession(self.vault, security.Quota(disk_bytes=4096))
        (self.vault / "big.bin").write_bytes(b"\0" * 5000)
        target = self.vault / "one-more.typ"

        with self.assertRaises(security.QuotaExceeded) as caught:
            security.enforce(session, "disk")
            target.write_text("this must never be written")

        self.assertEqual(caught.exception.limit, "disk_bytes")
        self.assertEqual(caught.exception.value, 4096)
        self.assertEqual(caught.exception.status, 429)
        # The canary: the refusal came first, so the file does not exist.
        self.assertFalse(target.exists())

    def test_dir_size_counts_what_the_folder_weighs(self):
        (self.vault / "a").write_bytes(b"\0" * 1000)
        (self.vault / "sub").mkdir()
        (self.vault / "sub" / "b").write_bytes(b"\0" * 500)
        self.assertGreaterEqual(security.dir_size(self.vault), 1500)

    def test_dir_size_does_not_follow_a_symlink_out_of_the_vault(self):
        # Otherwise a session could be charged for — or made to hang on — a
        # file it does not own, simply by linking to it.
        outside = Path(self.tmp.name) / "outside.bin"
        outside.write_bytes(b"\0" * 200_000)
        (self.vault / "link.bin").symlink_to(outside)
        self.assertLess(security.dir_size(self.vault), 10_000)

    def test_dir_size_survives_a_symlink_loop(self):
        (self.vault / "loop").symlink_to(self.vault, target_is_directory=True)
        self.assertLess(security.dir_size(self.vault), 10_000)

    # commands per hour

    def test_commands_are_refused_once_the_hour_is_spent(self):
        session = FakeSession(self.vault, security.Quota(commands_per_hour=3))
        spawned: list[int] = []

        def run(now: float) -> None:
            security.enforce(session, "command", now=now)
            spawned.append(1)

        for tick in range(3):
            run(1000.0 + tick)
        with self.assertRaises(security.QuotaExceeded) as caught:
            run(1004.0)

        self.assertEqual(caught.exception.limit, "commands_per_hour")
        self.assertEqual(caught.exception.value, 3)
        self.assertGreaterEqual(caught.exception.retry_after, 1)
        # The canary: the fourth engine command never spawned.
        self.assertEqual(len(spawned), 3)

    def test_the_command_window_rolls_rather_than_resetting_on_a_boundary(self):
        session = FakeSession(self.vault, security.Quota(commands_per_hour=2))
        security.enforce(session, "command", now=1000.0)
        security.enforce(session, "command", now=1001.0)
        with self.assertRaises(security.QuotaExceeded):
            security.enforce(session, "command", now=1002.0)
        # An hour after the first command, and only that one, falls out — so
        # exactly one slot opens rather than the whole allowance resetting.
        later = 1000.0 + security.WINDOW_SECONDS + 0.5
        security.enforce(session, "command", now=later)
        with self.assertRaises(security.QuotaExceeded):
            security.enforce(session, "command", now=later + 0.1)

    def test_a_refused_command_is_not_recorded_against_the_session(self):
        session = FakeSession(self.vault, security.Quota(commands_per_hour=1))
        security.enforce(session, "command", now=1000.0)
        for attempt in range(5):
            with self.assertRaises(security.QuotaExceeded):
                security.enforce(session, "command", now=1000.0 + attempt)
        self.assertEqual(len(session.usage.commands), 1)

    # reports

    def test_a_session_at_its_report_limit_never_runs_new(self):
        session = FakeSession(self.vault, security.Quota(reports=2))
        created: list[str] = []

        def create(title: str, count: int) -> None:
            security.enforce(session, "report", count=count)
            created.append(title)

        create("first", count=0)
        create("second", count=1)
        with self.assertRaises(security.QuotaExceeded) as caught:
            create("third", count=2)

        self.assertEqual(caught.exception.limit, "reports")
        self.assertEqual(created, ["first", "second"])

    def test_the_report_count_can_come_from_the_sessions_own_tally(self):
        session = FakeSession(self.vault, security.Quota(reports=1))
        security.enforce(session, "report")
        session.usage.reports = 1
        with self.assertRaises(security.QuotaExceeded):
            security.enforce(session, "report")

    def test_an_unknown_quota_kind_is_a_programming_error_not_a_refusal(self):
        session = FakeSession(self.vault, security.Quota())
        with self.assertRaises(ValueError):
            security.enforce(session, "bandwidth")

    def test_the_error_body_names_the_limit_a_human_can_act_on(self):
        session = FakeSession(self.vault, security.Quota(reports=0))
        with self.assertRaises(security.QuotaExceeded) as caught:
            security.enforce(session, "report", count=0)
        body = caught.exception.payload()["error"]
        self.assertEqual(body["code"], "quota")
        self.assertEqual(body["limit"], "reports")
        self.assertEqual(body["value"], 0)
        self.assertTrue(body["message"])


# ── rate limiting ────────────────────────────────────────────────────────────


class Clock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.limiter = security.RateLimiter(clock=self.clock)

    def serve(self, ip: str, kind: str = "request") -> None:
        self.limiter.check(ip, kind)

    def test_a_client_under_the_limit_is_served(self):
        served = 0
        for _ in range(60):
            self.serve("203.0.113.5")
            served += 1
        self.assertEqual(served, 60)

    def test_the_sixty_first_request_in_a_minute_is_refused_then_recovers(self):
        served: list[int] = []
        for index in range(60):
            self.serve("203.0.113.5")
            served.append(index)

        with self.assertRaises(security.RateLimited) as caught:
            self.serve("203.0.113.5")
            served.append(60)

        self.assertEqual(caught.exception.status, 429)
        self.assertGreaterEqual(caught.exception.retry_after, 1)
        # The canary: the refused request was not served.
        self.assertEqual(len(served), 60)

        # Recovery: the bucket refills continuously, so one second buys one.
        self.clock.advance(1.0)
        self.serve("203.0.113.5")
        with self.assertRaises(security.RateLimited):
            self.serve("203.0.113.5")

        # And a full minute of quiet buys the whole allowance back.
        self.clock.advance(60.0)
        for _ in range(60):
            self.serve("203.0.113.5")

    def test_a_refusal_does_not_take_a_token(self):
        # Charging for a refusal means a client that retries can never
        # recover — a rate limiter turned into a denial of service.
        for _ in range(60):
            self.serve("203.0.113.5")
        for _ in range(20):
            with self.assertRaises(security.RateLimited):
                self.serve("203.0.113.5")
        self.clock.advance(1.0)
        self.serve("203.0.113.5")

    def test_session_creation_is_limited_to_five_an_hour(self):
        created: list[int] = []
        for index in range(5):
            self.serve("203.0.113.5", "session")
            created.append(index)
        with self.assertRaises(security.RateLimited) as caught:
            self.serve("203.0.113.5", "session")
            created.append(5)
        self.assertEqual(len(created), 5)
        self.assertIn("session", caught.exception.message)
        # Twelve minutes buys exactly one more.
        self.clock.advance(720.0)
        self.serve("203.0.113.5", "session")
        with self.assertRaises(security.RateLimited):
            self.serve("203.0.113.5", "session")

    def test_the_two_buckets_are_independent(self):
        for _ in range(60):
            self.serve("203.0.113.5")
        with self.assertRaises(security.RateLimited):
            self.serve("203.0.113.5")
        self.serve("203.0.113.5", "session")

    def test_one_clients_flood_does_not_refuse_another_client(self):
        for _ in range(60):
            self.serve("203.0.113.5")
        with self.assertRaises(security.RateLimited):
            self.serve("203.0.113.5")
        self.serve("198.51.100.9")

    def test_ipv6_is_limited_by_its_sixty_four(self):
        # A single host is routinely handed a whole /64, so limiting the full
        # address limits nothing: the same client takes its next address.
        for index in range(60):
            self.serve(f"2001:db8:1:1::{index:x}")
        with self.assertRaises(security.RateLimited):
            self.serve("2001:db8:1:1::ffff")
        # A different /64 is a different client.
        self.serve("2001:db8:1:2::1")

    def test_the_bucket_map_does_not_grow_without_bound(self):
        # A flood from rotating source addresses must not make the rate
        # limiter itself the resource being exhausted.
        limiter = security.RateLimiter(clock=self.clock, max_keys=50)
        for index in range(400):
            limiter.check(f"198.51.100.{index % 256}", "request")
            self.clock.advance(120.0)
        self.assertLessEqual(len(limiter._buckets), 60)

    def test_allow_is_check_without_the_exception(self):
        self.assertTrue(self.limiter.allow("203.0.113.5"))
        for _ in range(59):
            self.limiter.allow("203.0.113.5")
        self.assertFalse(self.limiter.allow("203.0.113.5"))

    def test_it_is_thread_safe(self):
        # Sixty tokens, twelve threads racing for them: exactly sixty must win.
        limiter = security.RateLimiter(clock=Clock())
        won: list[int] = []
        lock = threading.Lock()

        def hammer():
            for _ in range(20):
                if limiter.allow("203.0.113.5"):
                    with lock:
                        won.append(1)

        threads = [threading.Thread(target=hammer) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(won), 60)


# ── escaping and headers ─────────────────────────────────────────────────────


class EscapingTests(unittest.TestCase):
    def test_a_report_title_cannot_close_a_tag(self):
        title = '<script>fetch("/api/session")</script>'
        self.assertNotIn("<script>", security.esc(title))
        self.assertIn("&lt;script&gt;", security.esc(title))

    def test_both_quote_characters_are_escaped(self):
        # Single quotes too, so title='…' is as safe as title="…".
        escaped = security.esc("\" onmouseover='steal()' x=\"")
        self.assertNotIn('"', escaped)
        self.assertNotIn("'", escaped)

    def test_none_renders_as_nothing_rather_than_the_word_none(self):
        self.assertEqual(security.esc(None), "")

    def test_inline_hashes_ignore_an_external_script(self):
        document = (
            '<script src="/assets/app.js"></script>'
            "<script>window.x = 1;</script>"
            "<style>body { color: red }</style>"
        )
        scripts = security.inline_hashes(document, "script")
        styles = security.inline_hashes(document, "style")
        self.assertEqual(len(scripts), 1)
        self.assertEqual(len(styles), 1)
        self.assertTrue(scripts[0].startswith("'sha256-"))
        # The hash is over the bytes that will actually be served.
        self.assertEqual(
            scripts, security.inline_hashes("<script>window.x = 1;</script>")
        )
        self.assertNotEqual(
            scripts, security.inline_hashes("<script>window.x = 2;</script>")
        )


class HeaderTests(unittest.TestCase):
    def test_the_policy_names_no_external_host(self):
        policy = security.security_headers("abc123")["Content-Security-Policy"]
        self.assertIsNone(re.search(r"https?://", policy))
        self.assertNotIn("*", policy)

    def test_script_runs_only_with_the_nonce(self):
        policy = security.security_headers("abc123")["Content-Security-Policy"]
        self.assertIn("script-src 'nonce-abc123'", policy)
        script = policy.split("script-src ")[1].split(";")[0]
        self.assertNotIn("unsafe-inline", script)
        self.assertNotIn("unsafe-eval", script)
        self.assertIn("default-src 'none'", policy)

    def test_inline_style_is_allowed_and_inline_script_is_not(self):
        """The one concession, pinned so it stays one.

        CodeMirror positions its cursor with a `style` attribute and mounts its
        theme as an injected `<style>`; Radix positions every overlay the same
        way. Under a nonce-only `style-src` the editor renders as an empty box.
        Script is where the containment lives and it is untouched.
        """
        policy = security.security_headers("abc123")["Content-Security-Policy"]
        self.assertIn("style-src 'self' 'unsafe-inline'", policy)
        self.assertNotIn("'unsafe-inline'", policy.split("script-src ")[1].split(";")[0])

    def test_the_page_cannot_be_framed_or_re_based(self):
        headers = security.security_headers("abc123")
        policy = headers["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("base-uri 'none'", policy)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_hsts_only_appears_under_tls(self):
        self.assertNotIn("Strict-Transport-Security", security.security_headers("n"))
        self.assertIn(
            "Strict-Transport-Security", security.security_headers("n", tls=True)
        )

    def test_a_nonce_is_fresh_every_time_and_long_enough_to_be_unguessable(self):
        first, second = security.nonce(), security.nonce()
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 20)

    def test_the_share_page_can_talk_to_nothing(self):
        policy = security.share_headers()["Content-Security-Policy"]
        self.assertIn("connect-src 'none'", policy)
        self.assertIn("script-src 'none'", policy)
        self.assertIsNone(re.search(r"https?://", policy))

    def test_the_share_page_allows_only_the_bytes_it_was_given(self):
        document = "<script>window.x = 1;</script>"
        hashes = security.inline_hashes(document)
        policy = security.share_headers(script_hashes=hashes)[
            "Content-Security-Policy"
        ]
        self.assertIn(hashes[0], policy)
        self.assertNotIn("unsafe-inline", policy)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
