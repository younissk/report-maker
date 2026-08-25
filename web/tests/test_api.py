"""The API, driven against a real server, a real session and a real engine.

The whole loop with no account — create a session, scaffold a report, edit it,
build it, check it, read the page images, share it, open the share with no
cookie at all — plus the refusals that have to hold while it does. Nothing here
is stubbed: `app.build()` returns the same `ThreadingHTTPServer` that
`python3 -m web` starts, bound to an ephemeral port, and every answer comes out
of a `report-maker` subprocess.

That is deliberate and it is the reason this file is slow. A test that mocked
the engine would prove the route table is spelled right and nothing else — and
what can actually break here is the seam between the two: an argument the
engine reads differently than expected, a path the manifest names in a form the
guard refuses, a build that exits non-zero for a reason that is the product
working. None of those are visible to a mock.

The build tests skip without `typst`, because the machine is allowed not to
have it and a suite that cannot run at all teaches nobody anything. Everything
else — the guards, the router, the static handler — runs anywhere.
"""

from __future__ import annotations

import contextlib
import http.cookiejar
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from web.server import app, engine, routes, security, sessions

HAS_TYPST = shutil.which("typst") is not None

# A report that passes `check`: one cited fact, one marked judgement, and
# nothing in between. Written out in full rather than patched into the starter,
# because the thing being tested is that a *valid* report survives the whole
# round trip, and a starter with two fields edited is a starter.
GOOD_TYP = '''\
#import "/.build/design/base/report.typ": report
#import "/.build/design/base/components.typ": *

#show: report.with(
  title: "Pricing on the public site",
  subtitle: "What the vendor publishes, and what it does not.",
  kind: "Report",
  author: "The test suite",
  role: "Reviewer",
  date: datetime(year: 2026, month: 8, day: 25),
  subject: "Published pricing",
  doc-id: "RM-TEST-1",
  sources: "/reports/{id}/sources.yml",
  abstract: [
    The project documents itself on a public page @terms. Whether that is
    enough for this client is a judgement, and it is marked as one.
  ],
)

= Finding

The project publishes its source and its licence on a public page @terms.

Nothing else here needs a second source #assess.
'''

GOOD_YML = '''\
terms:
  type: Web
  title: "report-maker — the repository and its licence"
  author: "Youniss Kandah"
  url: "https://github.com/younissk/report-maker"
  date: 2026-08-25
'''


# ── driving it ───────────────────────────────────────────────────────────────


class Answer:
    """One response, kept whole. Status, headers and body — because half the
    assertions in this file are about a header rather than a payload."""

    def __init__(self, status: int, headers, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def code(self) -> str:
        """The stable error code. Assertions go on this and never on wording —
        a message is allowed to be reworded without breaking a suite."""
        return self.json()["error"]["code"]


class Client:
    """A browser, near enough: it keeps cookies and it follows nothing.

    Redirects are not followed, because two of the things worth asserting here
    are *that* a route redirects and where to.
    """

    def __init__(self, base: str) -> None:
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )

    def call(self, method: str, path: str, body=None, headers=None, cookies=True) -> Answer:
        data = None
        sent = dict(headers or {})
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            sent.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=sent)
        opener = self.opener if cookies else urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=120) as answer:
                return Answer(answer.status, answer.headers, answer.read())
        except urllib.error.HTTPError as exc:
            # An HTTPError is also a response object holding an open socket.
            # Read it, then close it — an unclosed one surfaces later as a
            # ResourceWarning attached to whichever test happened to be running.
            try:
                return Answer(exc.code, exc.headers, exc.read())
            finally:
                exc.close()

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, body if body is not None else {}, **kw)

    def put(self, path, body=None, **kw):
        return self.call("PUT", path, body if body is not None else {}, **kw)

    def delete(self, path, **kw):
        return self.call("DELETE", path, **kw)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@contextlib.contextmanager
def serving(root: Path, client: Path | None = None, limiter=None):
    """The real server on an ephemeral port, for the length of a `with`.

    The rate limiter is replaced with a permissive one unless a test asks for
    the real numbers. Five session creations an hour is the correct production
    limit and it is tested in `RateLimits`; leaving it in place everywhere else
    would mean a suite that fails on its sixth test for a reason unrelated to
    any of them.
    """
    options = app.Options(host="127.0.0.1", port=0, root=root, client=client)
    server, ctx = app.build(options)
    ctx.limiter = limiter or security.RateLimiter(
        requests_per_minute=100_000, sessions_per_hour=100_000
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(f"http://127.0.0.1:{server.server_address[1]}"), ctx
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="rm-web-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


# ── the loop ─────────────────────────────────────────────────────────────────


@unittest.skipUnless(HAS_TYPST, "typst is not installed on this machine")
class TheWholeLoop(Base):
    """Land on the site with no account and end with a link somebody can open.

    One test rather than eight, on purpose. Each step consumes the previous
    one's output — you cannot share a report you did not build, and you cannot
    build one you did not write — so splitting them into separate tests would
    mean either rebuilding the world eight times or sharing state between tests
    that pretend not to. The failure message names the step.
    """

    def test_no_account_to_a_share_link(self) -> None:
        with serving(self.tmp) as (client, ctx):
            # ── a session, and a vault that already has a report in it ──
            made = client.post("/api/session")
            self.assertEqual(made.status, 201, made.body)
            session = made.json()
            self.assertEqual(session["mode"], "try")
            self.assertNotIn("id", session, "the session id must not reach the browser")
            self.assertNotIn("vault", session, "a server path must not reach the browser")

            cookie = made.headers["Set-Cookie"]
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Lax", cookie)

            listed = client.get("/api/reports").json()["reports"]
            self.assertEqual(len(listed), 1, "a try-mode session lands on a starter")

            # ── a second report, filed in a folder ──
            created = client.post(
                "/api/reports",
                {"title": "Pricing on the public site", "group": "clients/acme"},
            )
            self.assertEqual(created.status, 201, created.body)
            report = created.json()["created"]
            self.assertTrue(report.startswith("clients/acme/"), report)

            # ── the folder, as the engine's manifest names it ──
            read = client.get(f"/api/reports/{report}").json()
            names = {f["name"] for f in read["files"]}
            self.assertEqual(names, {"main.typ", "sources.yml", "todos.md"})

            main = f"reports/{report}/main.typ"
            yml = f"reports/{report}/sources.yml"

            # ── write it: one cited fact, one marked judgement ──
            wrote = client.put(
                f"/api/reports/{report}/file?path={main}",
                {"text": GOOD_TYP.replace("{id}", report)},
            )
            self.assertEqual(wrote.status, 200, wrote.body)
            self.assertEqual(
                client.put(f"/api/reports/{report}/file?path={yml}", {"text": GOOD_YML}).status,
                200,
            )
            back = client.get(f"/api/reports/{report}/file?path={main}").json()
            self.assertIn("@terms", back["text"])

            # ── build ──
            built = client.post(f"/api/reports/{report}/build").json()
            self.assertTrue(built["ok"], built["stdout"] + built["stderr"])
            self.assertEqual(built["code"], 0)
            self.assertTrue(built["artefacts"]["pdf"])
            self.assertGreater(built["artefacts"]["pages"], 0)

            # ── check: the whole product claim, and it is green ──
            found = client.get(f"/api/check?target={report}").json()
            self.assertEqual(found["errors"], 0, found["findings"])
            self.assertNotIn(str(ctx.root), json.dumps(found), "a server path escaped")

            # ── read it the way a phone does ──
            pages = client.get(f"/api/reports/{report}/pages").json()
            self.assertGreater(pages["count"], 0)
            self.assertEqual(pages["pages"][0], f"/api/reports/{report}/page/1")
            png = client.get(pages["pages"][0])
            self.assertEqual(png.status, 200)
            self.assertEqual(png.headers["Content-Type"], "image/png")
            self.assertTrue(png.body.startswith(b"\x89PNG"), "that is not a PNG")

            pdf = client.get(f"/api/reports/{report}/pdf")
            self.assertEqual(pdf.headers["Content-Type"], "application/pdf")
            self.assertTrue(pdf.body.startswith(b"%PDF"))

            # ── share it ──
            published = client.post(f"/api/share/{report}")
            self.assertEqual(published.status, 201, published.body)
            link = published.json()
            self.assertEqual(link["url"], f"/s/{link['token']}")
            self.assertNotIn("path", link, "a share must never carry a server path")

            # ── and open it as a stranger: no cookie, no session, no auth ──
            public = client.get(link["url"], cookies=False)
            self.assertEqual(public.status, 200)
            self.assertIsNone(public.headers.get("Set-Cookie"), "a share sets no cookie")
            self.assertIn("text/html", public.headers["Content-Type"])
            policy = public.headers["Content-Security-Policy"]
            self.assertIn("default-src 'none'", policy)
            self.assertIn("sha256-", policy, "the bundle's own script is allowed by hash")
            self.assertNotIn("unsafe-inline", policy)
            self.assertIn(b"Pricing on the public site", public.body)
            self.assertGreater(len(public.body), 10_000, "an evidence bundle is not small")

    def test_a_report_that_fails_check_is_not_shareable(self) -> None:
        """The starter is red on purpose, and a share is the outward-facing act.

        Refusing here is the whole product claim held at its one public edge: a
        report that says it is finished while `check` disagrees does not get a
        link. Nothing is lost — `status: "draft"` shares fine and says on its
        own face that it is unfinished.
        """
        with serving(self.tmp) as (client, _):
            client.post("/api/session")
            starter = client.get("/api/reports").json()["reports"][0]["id"]
            refused = client.post(f"/api/share/{starter}")
            self.assertEqual(refused.status, 400)
            self.assertEqual(refused.code(), "not_shareable")
            self.assertIn("check", refused.json()["error"]["message"])


# ── the refusals ─────────────────────────────────────────────────────────────


class TheDoorIsShut(Base):
    """Every guard the spec calls a requirement, asked for over the wire.

    Reading the code proves the guard is written. Only a probe proves it is
    reached — that no route gets to the filesystem or to a subprocess by a path
    that skipped it.
    """

    def setUp(self) -> None:
        super().setUp()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.client, self.ctx = self.stack.enter_context(serving(self.tmp))
        self.client.post("/api/session")
        self.report = self.client.get("/api/reports").json()["reports"][0]["id"]

    # ── spec 9/10: one 401, whatever the reason ──

    def test_every_reason_a_session_did_not_open_answers_the_same(self) -> None:
        stranger = Client(self.client.base)
        shapes = [
            stranger.get("/api/reports"),  # no cookie at all
            stranger.get("/api/reports", headers={"Cookie": "rm_session=" + "A" * 43}),
            stranger.get("/api/reports", headers={"Cookie": "rm_session=../../etc"}),
            stranger.get("/api/reports", headers={"Cookie": "rm_session="}),
            stranger.get("/api/reports", headers={"Cookie": "nonsense"}),
        ]
        for answer in shapes:
            self.assertEqual(answer.status, 401)
        bodies = {answer.body for answer in shapes}
        self.assertEqual(len(bodies), 1, "the 401 body tells a stranger which cause it was")

    def test_an_expired_session_is_indistinguishable_from_an_unknown_one(self) -> None:
        # Aged past the TTL by rewriting the record the sweeper reads, which is
        # the same state a session reaches by being left alone for a day.
        store = self.ctx.store
        record = next(store.glob("*/session.json"))
        held = json.loads(record.read_text())
        held["last_seen"] = 0.0
        record.write_text(json.dumps(held))

        expired = self.client.get("/api/reports")
        unknown = Client(self.client.base).get(
            "/api/reports", headers={"Cookie": "rm_session=" + "A" * 43}
        )
        self.assertEqual(expired.status, 401)
        self.assertEqual(expired.body, unknown.body)

    # ── spec 2: path containment ──

    def test_a_traversal_in_the_path_parameter_is_refused(self) -> None:
        for attempt, expected in (
            ("../../../../etc/passwd", "path_parent"),
            ("/etc/passwd", "path_absolute"),
            ("reports/../../../etc/passwd", "path_parent"),
            ("~/.ssh/id_rsa", "path_absolute"),
        ):
            with self.subTest(attempt):
                answer = self.client.get(
                    f"/api/reports/{self.report}/file?path={urllib.parse.quote(attempt)}"
                )
                self.assertEqual(answer.status, 403)
                self.assertEqual(answer.code(), expected)
                self.assertNotIn(str(self.ctx.root), answer.body.decode())

    def test_a_symlink_out_of_the_vault_is_refused(self) -> None:
        vault = next(self.ctx.store.glob("*/vault"))
        (vault / "leak.txt").symlink_to("/etc/passwd")
        answer = self.client.get(f"/api/reports/{self.report}/file?path=leak.txt")
        self.assertEqual(answer.status, 403)
        self.assertEqual(answer.code(), "path_symlink")
        self.assertNotIn(b"root:", answer.body)

    def test_a_traversal_in_a_static_path_is_refused(self) -> None:
        dist = self.tmp / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<html><script src='/assets/a.js'></script></html>")
        (dist / "assets" / "a.js").write_text("console.log(1)")
        with serving(self.tmp / "root2", client=dist) as (client, _):
            for attempt in ("/../../../etc/passwd", "/assets/../../../../etc/passwd"):
                with self.subTest(attempt):
                    answer = client.get(attempt)
                    self.assertEqual(answer.status, 403)
                    self.assertEqual(answer.code(), "path_parent")

    # ── spec 5: template install ──

    def test_template_install_is_refused(self) -> None:
        answer = self.client.post(
            "/api/templates/install", {"url": "https://github.com/anyone/anything.git"}
        )
        self.assertEqual(answer.status, 403)
        self.assertEqual(answer.code(), "forbidden")
        self.assertIn("template install", answer.json()["error"]["message"])
        # And `templates` itself still works — the denial is one subcommand, not
        # the whole family.
        self.assertEqual(self.client.get("/api/templates").status, 200)

    # ── spec 4: SSRF ──

    def test_cite_refuses_the_addresses_that_matter(self) -> None:
        for url, code in (
            ("http://169.254.169.254/latest/meta-data/", "url_metadata"),
            ("http://127.0.0.1/", "url_blocked"),
            ("http://[::1]/", "url_blocked"),
            ("http://10.0.0.7/", "url_blocked"),
            ("http://192.168.1.1/", "url_blocked"),
            ("file:///etc/passwd", "url_scheme"),
            ("http://user:secret@example.com/", "url_credentials"),
        ):
            with self.subTest(url):
                answer = self.client.post(f"/api/sources/{self.report}/cite", {"url": url})
                self.assertEqual(answer.status, 403, answer.body)
                self.assertEqual(answer.code(), code)

    def test_an_online_verify_of_a_whole_vault_is_refused(self) -> None:
        """Nothing pre-flights a list nobody can enumerate first."""
        answer = self.client.get("/api/verify?online=1")
        self.assertEqual(answer.status, 403)
        self.assertEqual(answer.code(), "forbidden")
        # Offline is the default and answers normally.
        self.assertEqual(self.client.get("/api/verify").status, 200)

    # ── spec 7: quotas ──

    def test_the_disk_quota_refuses_the_next_write(self) -> None:
        vault = next(self.ctx.store.glob("*/vault"))
        (vault / "big.bin").write_bytes(b"x" * (self.ctx.quota.disk_bytes + 1))
        answer = self.client.put(
            f"/api/reports/{self.report}/file?path=reports/{self.report}/notes.md",
            {"text": "hello"},
        )
        self.assertEqual(answer.status, 429)
        self.assertEqual(answer.code(), "quota")
        self.assertEqual(answer.json()["error"]["limit"], "disk_bytes")

    def test_the_command_allowance_runs_out(self) -> None:
        session = sessions.get(self.ctx.root, next(self.ctx.store.iterdir()).name)
        assert session is not None
        session.quota_used.commands = [__import__("time").time()] * self.ctx.quota.commands_per_hour
        sessions.touch(session)
        answer = self.client.get("/api/reports")
        self.assertEqual(answer.status, 429)
        self.assertEqual(answer.json()["error"]["limit"], "commands_per_hour")
        self.assertIn("Retry-After", answer.headers)

    # ── the body, and the write that cannot come from elsewhere ──

    def test_a_body_larger_than_the_cap_is_refused_before_it_is_read(self) -> None:
        answer = self.client.put(
            f"/api/reports/{self.report}/file?path=reports/{self.report}/notes.md",
            b'{"text":"' + b"a" * (app.MAX_BODY + 10) + b'"}',
        )
        self.assertEqual(answer.status, 413)
        self.assertEqual(answer.code(), "too_large")

    def test_a_write_another_site_started_is_refused(self) -> None:
        answer = self.client.post(
            "/api/reports",
            {"title": "Not yours"},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(answer.status, 403)
        self.assertEqual(answer.code(), "cross_site")

    # ── spec: nothing about this machine in a response ──

    def test_no_response_carries_a_server_path_or_the_session_id(self) -> None:
        sid = next(self.ctx.store.iterdir()).name
        seen = [
            self.client.get("/api/check"),
            self.client.get("/api/brand"),
            self.client.get(f"/api/reports/{self.report}"),
            self.client.get("/api/session"),
            self.client.get("/api/health"),
            self.client.get("/api/reports/nope/pdf"),
        ]
        for answer in seen:
            text = answer.body.decode("utf-8", "replace")
            self.assertNotIn(str(self.ctx.root), text)
            self.assertNotIn(str(self.ctx.store), text)
            self.assertNotIn(sid, text)
            self.assertNotIn("Traceback", text)

    def test_an_unknown_route_is_a_json_404_under_api(self) -> None:
        answer = self.client.get("/api/nothing/here")
        self.assertEqual(answer.status, 404)
        self.assertEqual(answer.code(), "not_found")

    def test_the_wrong_method_is_405_and_not_404(self) -> None:
        answer = self.client.delete("/api/reports")
        self.assertEqual(answer.status, 405)
        self.assertEqual(answer.code(), "bad_method")

    def test_a_bad_report_id_never_becomes_a_path(self) -> None:
        for attempt in ("..", "../../etc", "/etc/passwd", ""):
            with self.subTest(attempt):
                answer = self.client.get(
                    "/api/sources/" + urllib.parse.quote(attempt, safe="")
                )
                self.assertIn(answer.status, (400, 404), answer.body)
                self.assertNotIn(b"root:", answer.body)

    def test_deleting_a_session_takes_the_vault_with_it(self) -> None:
        vaults = list(self.ctx.store.iterdir())
        self.assertEqual(len(vaults), 1)
        gone = self.client.delete("/api/session")
        self.assertEqual(gone.status, 200)
        self.assertIn("Max-Age=0", gone.headers["Set-Cookie"])
        self.assertEqual(list(self.ctx.store.iterdir()), [])
        self.assertEqual(self.client.get("/api/session").status, 401)

    def test_deleting_a_session_that_never_existed_says_the_same_thing(self) -> None:
        stranger = Client(self.client.base)
        answer = stranger.delete("/api/session", headers={"Cookie": "rm_session=" + "B" * 43})
        self.assertEqual(answer.status, 200, "or it becomes an oracle for other ids")


# ── the rate limiter, with the real numbers ──────────────────────────────────


class RateLimits(Base):
    """Spec requirement 8, at its production settings.

    Its own server because every other test in this file needs more than five
    sessions an hour, and a limiter relaxed for convenience everywhere is a
    limiter nobody has tested.
    """

    def test_session_creation_is_capped_per_address(self) -> None:
        limiter = security.RateLimiter(requests_per_minute=1000, sessions_per_hour=3)
        with serving(self.tmp, limiter=limiter) as (client, _):
            statuses = [Client(client.base).post("/api/session").status for _ in range(4)]
        self.assertEqual(statuses[:3], [201, 201, 201])
        self.assertEqual(statuses[3], 429)

    def test_a_refused_request_carries_a_retry_after(self) -> None:
        limiter = security.RateLimiter(requests_per_minute=2, sessions_per_hour=100)
        with serving(self.tmp / "r2", limiter=limiter) as (client, _):
            answers = [client.get("/api/health") for _ in range(5)]
        refused = [a for a in answers if a.status == 429]
        self.assertTrue(refused, "the limiter never fired")
        self.assertEqual(refused[0].code(), "rate_limited")
        self.assertIn("Retry-After", refused[0].headers)


# ── the static handler ───────────────────────────────────────────────────────


class TheFrontend(Base):
    """Serving somebody else's build, under the app's own CSP."""

    def setUp(self) -> None:
        super().setUp()
        self.dist = self.tmp / "dist"
        (self.dist / "assets").mkdir(parents=True)
        (self.dist / "index.html").write_text(
            '<!doctype html><html><head><script type="module" src="/assets/a.js">'
            "</script></head><body></body></html>"
        )
        (self.dist / "assets" / "a.js").write_text("console.log(1)")

    def test_the_page_carries_a_nonce_and_a_policy_with_no_external_host(self) -> None:
        with serving(self.tmp / "root", client=self.dist) as (client, _):
            page = client.get("/")
        self.assertEqual(page.status, 200)
        policy = page.headers["Content-Security-Policy"]
        self.assertIn("default-src 'none'", policy)
        self.assertIn("nonce-", policy)
        # Script is the boundary, and it is nonce-only. `style-src` allows
        # inline style because CodeMirror and Radix cannot position anything
        # without it — asserted narrowly here so the two cannot be confused.
        script = policy.split("script-src ")[1].split(";")[0]
        self.assertNotIn("unsafe-inline", script)
        self.assertNotIn("unsafe-eval", script)
        self.assertIn("style-src 'self' 'unsafe-inline'", policy)
        self.assertNotIn("http://", policy, "no external host may appear in the policy")

        # The nonce in the header is the nonce on the tag, and Vite writes no
        # nonce of its own — so if this ever stops holding, the app's own
        # bundle stops loading and the page is blank.
        nonce = policy.split("nonce-")[1].split("'")[0]
        self.assertIn(f'nonce="{nonce}"'.encode(), page.body)
        self.assertEqual(page.headers["Cache-Control"], "no-store")

    def test_two_requests_do_not_share_a_nonce(self) -> None:
        with serving(self.tmp / "root", client=self.dist) as (client, _):
            first = client.get("/").headers["Content-Security-Policy"]
            second = client.get("/").headers["Content-Security-Policy"]
        self.assertNotEqual(first, second, "a reused nonce is a guessable nonce")

    def test_an_unknown_page_falls_back_but_an_unknown_api_route_does_not(self) -> None:
        with serving(self.tmp / "root", client=self.dist) as (client, _):
            page = client.get("/reports/anything/write")
            api = client.get("/api/anything")
            share = client.get("/s/anything")
        self.assertEqual(page.status, 200)
        self.assertIn(b"<!doctype html>", page.body)
        self.assertEqual(api.status, 404)
        self.assertIn(b'"error"', api.body)
        self.assertEqual(share.status, 404)
        self.assertIn(b'"error"', share.body)

    def test_a_hashed_asset_is_cached_and_the_shell_is_not(self) -> None:
        with serving(self.tmp / "root", client=self.dist) as (client, _):
            asset = client.get("/assets/a.js")
            page = client.get("/index.html")
        self.assertIn("immutable", asset.headers["Cache-Control"])
        self.assertEqual(asset.headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(asset.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(page.headers["Cache-Control"], "no-store")

    def test_with_no_build_the_api_still_answers(self) -> None:
        with serving(self.tmp / "root", client=self.tmp / "absent") as (client, _):
            self.assertEqual(client.get("/api/health").status, 200)
            self.assertEqual(client.get("/").status, 404)


# ── the router ───────────────────────────────────────────────────────────────


class TheRouter(unittest.TestCase):
    """`match`, on its own. The greedy id is where this gets subtle.

    A report id contains slashes — `clients/acme/2026-08-12-audit` — so the
    pattern for `/api/reports/{id...}/file` has to take a *run* of segments and
    stop before the literal that follows. Get the ordering wrong and
    `/api/reports/a/b/file` is read as a report called "a/b/file", which then
    404s from the manifest with a message about a report nobody asked for.
    """

    def test_a_greedy_id_stops_at_the_literal_that_follows_it(self) -> None:
        handler, params, _ = routes.match("GET", "/api/reports/clients/acme/x/file")
        self.assertIs(handler, routes.file_read)
        self.assertEqual(params["id"], "clients/acme/x")

    def test_a_bare_id_takes_everything(self) -> None:
        handler, params, _ = routes.match("GET", "/api/reports/clients/acme/x")
        self.assertIs(handler, routes.report_read)
        self.assertEqual(params["id"], "clients/acme/x")

    def test_two_placeholders_bind_separately(self) -> None:
        handler, params, _ = routes.match("GET", "/api/reports/a/b/page/7")
        self.assertIs(handler, routes.report_page)
        self.assertEqual((params["id"], params["n"]), ("a/b", "7"))

    def test_a_segment_is_decoded_exactly_once(self) -> None:
        _, params, _ = routes.match("GET", "/api/reports/a%2Fb")
        self.assertEqual(params["id"], "a/b")
        _, params, _ = routes.match("GET", "/api/reports/a%252e%252e")
        self.assertEqual(params["id"], "a%2e%2e", "a second decode is how %252e becomes ..")

    def test_the_public_share_route_needs_no_session(self) -> None:
        handler, params, auth = routes.match("GET", "/s/abcdef")
        self.assertIs(handler, routes.share_read)
        self.assertFalse(auth)
        self.assertEqual(params["token"], "abcdef")

    def test_every_api_route_but_the_open_three_needs_a_session(self) -> None:
        open_routes = {
            ("POST", "/api/session"),
            ("DELETE", "/api/session"),
            ("GET", "/api/health"),
            ("GET", "/api/github/status"),
            ("GET", "/s/{token}"),
        }
        for method, pattern, _, auth in routes.TABLE:
            with self.subTest(f"{method} {pattern}"):
                self.assertEqual(auth, (method, pattern) not in open_routes)

    def test_an_unknown_path_matches_nothing(self) -> None:
        self.assertIsNone(routes.match("GET", "/api/nope"))
        self.assertIsNone(routes.match("GET", "/anything"))

    def test_a_known_path_with_the_wrong_method_is_405(self) -> None:
        with self.assertRaises(security.Refused) as caught:
            routes.match("PATCH", "/api/reports")
        self.assertEqual(caught.exception.status, 405)


# ── the engine is wired before anything serves ───────────────────────────────


class Startup(Base):
    def test_building_the_server_declares_the_sessions_root(self) -> None:
        """Without this the bridge refuses every spawn, by design. It is a
        startup step, so it is asserted at startup rather than trusted."""
        options = app.Options(host="127.0.0.1", port=0, root=self.tmp)
        server, ctx = app.build(options)
        try:
            self.assertEqual(engine.sessions_root(), ctx.store)
            self.assertEqual(ctx.store, ctx.root / "sessions")
        finally:
            server.server_close()

    def test_the_default_bind_is_loopback(self) -> None:
        """Spec requirement 1. The default is the security posture, not a
        convenience, so it is asserted rather than assumed."""
        self.assertEqual(app.parse([]).host, "127.0.0.1")
        self.assertFalse(app._loopback("0.0.0.0"))
        self.assertTrue(app._loopback("127.0.0.1"))
        self.assertTrue(app._loopback("localhost"))
        self.assertTrue(app._loopback("::1"))

    def test_the_scrubber_removes_the_vault_and_the_id(self) -> None:
        options = app.Options(host="127.0.0.1", port=0, root=self.tmp)
        server, ctx = app.build(options)
        try:
            pairs = app._roots(ctx, None)
            dirty = {"vault": str(ctx.store / "abc" / "vault"), "note": f"at {ctx.root}/x"}
            clean = app._scrub(dirty, pairs, "abc")
            self.assertNotIn(str(ctx.root), json.dumps(clean))
            self.assertNotIn("abc", json.dumps(clean))
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
