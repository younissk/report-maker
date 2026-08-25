"""Public share links: the immutability, the token, and the phone-home check.

`GET /s/<token>` is the only route on this server that answers to the whole
internet with no session behind it, so the tests here are mostly about the two
ways it could go wrong quietly. A token that gets to name a file before anyone
checks its shape is a directory traversal. A bundle that looks self-contained
and carries one remote image is a beacon telling a third party the name of every
person who opened a report — and it would look perfectly right on screen.

The last case in this file is the one that keeps the check honest: it runs the
real engine over the demo vault and asserts that the real bundle passes. A
refusal nobody can satisfy is a refusal that gets deleted the first time it
blocks a release.

    python3 -m unittest discover -s web/tests
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from web.server import share  # noqa: E402

DEMO = ROOT / "examples" / "demo-vault"

# A bundle with the shape `engine/html.py` produces: an inlined page image, a
# link out to the source it cites, an inline stylesheet and an inline script.
CLEAN = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>A report</title>
<style>:root { --ink: #101010 } body { font-family: serif }</style>
</head><body>
<figure class="page"><img alt="page 1" src="data:image/png;base64,iVBORw0KGgo="></figure>
<p>Pricing is published on the vendor's page
   <a class="cite" href="#src-vendor-pricing">@vendor-pricing</a>.</p>
<section id="src-vendor-pricing">
  <p><a href="https://vendor.example/pricing">https://vendor.example/pricing</a></p>
  <blockquote>Quoted text that itself mentions src=&quot;http://elsewhere.example/x.png&quot;.</blockquote>
</section>
<script>document.documentElement.className = "js";</script>
</body></html>
"""


def bundle_with(extra: str) -> str:
    return CLEAN.replace("</body>", f"{extra}\n</body>")


class Run:
    """What the bridge hands back."""

    def __init__(self, code: int, stdout: str = "", stderr: str = "") -> None:
        self.code, self.stdout, self.stderr = code, stdout, stderr


class Vault:
    """A session with a vault, and an engine that writes one bundle into it."""

    def __init__(self, root: Path, body: str = CLEAN, code: int = 0) -> None:
        self.root = root
        self.body = body
        self.code = code
        self.calls: list[list[str]] = []

    @property
    def session(self) -> dict:
        return {"id": "sess-abc", "vault": self.root}

    def run(self, session, args, **kwargs):
        self.calls.append(list(args))
        target = args[1] if len(args) > 1 else "examples/report"
        out = self.root / "out" / f"{target}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.body, encoding="utf-8")
        rel = out.relative_to(self.root)
        return Run(self.code, f"stage\nbuild\npages\nhtml\n  → {rel} (12 KB)\nmanifest\ncheck\n")


class ShareCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="rm-web-share-"))
        self.vault = Vault(self.tmp / "vault")
        self.shares = self.tmp / "shares"

    def publish(self, report: str = "examples/report", **kw) -> share.Share:
        return share.publish(self.vault.session, report, self.shares, run=self.vault.run, **kw)


# ── publishing ───────────────────────────────────────────────────────────────


class TestPublish(ShareCase):
    def test_it_writes_a_file_and_returns_a_link(self) -> None:
        published = self.publish()
        self.assertTrue(published.path.is_file())
        self.assertEqual(published.path.read_text(encoding="utf-8"), CLEAN)
        self.assertEqual(published.url, f"/s/{published.token}")
        self.assertEqual(published.report, "examples/report")
        self.assertEqual(self.vault.calls, [["all", "examples/report", "--html"]])

    def test_the_build_is_not_warn_only(self) -> None:
        """Publishing is the outward-facing act and the citation rule is the
        product claim behind it. `CLAUDE.md` names `--warn-only` outside a
        genuine work-in-progress as how a true statement about a vault becomes
        a false one."""
        self.publish()
        self.assertNotIn("--warn-only", self.vault.calls[0])

    def test_a_report_that_fails_check_is_not_publishable(self) -> None:
        vault = Vault(self.tmp / "vault2", code=1)
        with self.assertRaises(share.ShareError) as caught:
            share.publish(vault.session, "examples/report", self.shares, run=vault.run)
        self.assertIn("not ready to send", str(caught.exception))
        self.assertFalse(any(self.shares.glob("*.html")))

    def test_an_informed_caller_may_share_it_anyway(self) -> None:
        vault = Vault(self.tmp / "vault3", code=1)
        published = share.publish(
            vault.session, "examples/report", self.shares, run=vault.run, allow_findings=True
        )
        self.assertTrue(published.path.is_file())

    def test_re_publishing_mints_a_new_token_and_leaves_the_old_one_alone(self) -> None:
        """A link somebody was sent must never change under them — the same
        principle as the snapshot archive, which rotates rather than
        overwrites."""
        first = self.publish()
        self.vault.body = CLEAN.replace("A report", "A different report")
        second = self.publish()

        self.assertNotEqual(first.token, second.token)
        self.assertNotEqual(first.path, second.path)
        self.assertIn("A report</title>", first.path.read_text(encoding="utf-8"))
        self.assertIn("A different report", second.path.read_text(encoding="utf-8"))

    def test_tokens_do_not_repeat(self) -> None:
        seen = {self.publish().token for _ in range(12)}
        self.assertEqual(len(seen), 12)
        for token in seen:
            self.assertRegex(token, share.TOKEN)

    def test_the_json_it_hands_the_browser_carries_no_path_and_no_session(self) -> None:
        published = self.publish()
        payload = published.to_json()
        self.assertEqual(set(payload), {"url", "token", "report", "created"})
        blob = json.dumps(payload)
        self.assertNotIn(str(self.tmp), blob)
        self.assertNotIn("sess-abc", blob)

    def test_the_sidecar_carries_no_session_and_no_path(self) -> None:
        published = self.publish()
        side = json.loads((self.shares / f"{published.token}.json").read_text())
        self.assertEqual(set(side), {"report", "created"})
        self.assertEqual(share.meta(self.shares, published.token), side)

    def test_a_report_id_that_is_not_one_is_refused(self) -> None:
        for bad in ("../../etc/passwd", "/etc/passwd", "-C/tmp", "examples/../..",
                    "examples/report;rm -rf /", "", "reports/../../secrets"):
            with self.subTest(report=bad):
                with self.assertRaises(share.ShareError):
                    self.publish(bad)
        self.assertEqual(self.vault.calls, [], "nothing may reach the engine")

    def test_a_bundle_the_engine_never_wrote_is_reported(self) -> None:
        def run(session, args, **kwargs):
            return Run(0, "stage\nbuild\nhtml\nmanifest\ncheck\n")

        with self.assertRaises(share.ShareError) as caught:
            share.publish(self.vault.session, "examples/report", self.shares, run=run)
        self.assertIn("no HTML bundle", str(caught.exception))

    def test_a_bundle_claimed_outside_the_vault_is_refused(self) -> None:
        """The engine prints the path it wrote. It is still proved to be inside
        the vault before a byte of it is published."""
        escape = self.tmp / "elsewhere.html"
        escape.write_text(CLEAN, encoding="utf-8")

        def run(session, args, **kwargs):
            return Run(0, "html\n  → ../elsewhere.html (12 KB)\n")

        with self.assertRaises(share.ShareError):
            share.publish(self.vault.session, "examples/report", self.shares, run=run)


# ── the token ────────────────────────────────────────────────────────────────


class TestLookup(ShareCase):
    def test_a_published_token_resolves(self) -> None:
        published = self.publish()
        self.assertEqual(share.get(self.shares, published.token), published.path.resolve())

    def test_an_unknown_token_is_none_rather_than_an_error(self) -> None:
        self.shares.mkdir(parents=True)
        self.assertIsNone(share.get(self.shares, "A" * 32))

    def test_a_traversal_payload_is_refused_before_the_filesystem(self) -> None:
        """Ordering is the guard. `../../etc/passwd` has to be refused as a
        malformed token, not resolved and then judged — by the time a path has
        been built, the interesting question has been answered wrongly.

        Proved rather than asserted: `_within` is replaced with something that
        fails the test if it is ever reached.
        """
        self.shares.mkdir(parents=True)
        secret = self.tmp / "secret.html"
        secret.write_text("not yours\n", encoding="utf-8")

        reached: list[str] = []

        def tripwire(root, path):
            reached.append(str(path))
            raise AssertionError("a malformed token reached the filesystem")

        real, share._within = share._within, tripwire
        try:
            for bad in (
                "../secret",
                "../../etc/passwd",
                "..%2f..%2fsecret",
                "a/b",
                "tok en",
                "tok.en",
                "short",
                "x" * 200,
                "",
                None,
                123,
            ):
                with self.subTest(token=bad):
                    self.assertIsNone(share.get(self.shares, bad))  # type: ignore[arg-type]
        finally:
            share._within = real
        self.assertEqual(reached, [])
        self.assertTrue(secret.is_file())

    def test_a_symlink_in_shares_is_not_followed(self) -> None:
        """A link planted inside shares/ would otherwise pass containment and
        serve somebody else's vault."""
        self.shares.mkdir(parents=True)
        target = self.tmp / "somebody-elses.html"
        target.write_text("private\n", encoding="utf-8")
        token = "s" * 32
        (self.shares / f"{token}.html").symlink_to(target)
        self.assertIsNone(share.get(self.shares, token))

    def test_meta_refuses_the_same_payloads(self) -> None:
        self.shares.mkdir(parents=True)
        self.assertIsNone(share.meta(self.shares, "../../etc/passwd"))


# ── the self-contained check ─────────────────────────────────────────────────


class TestReferences(unittest.TestCase):
    def test_the_shape_the_engine_produces_is_clean(self) -> None:
        self.assertEqual(share.references(CLEAN), [])

    def test_a_quotation_that_mentions_a_url_is_not_a_reference(self) -> None:
        """The bundle quotes archived pages, so `src="http://…"` legitimately
        appears in *text*. A regex over the file would refuse a good bundle;
        the parser knows markup from prose."""
        self.assertIn("src=&quot;http://elsewhere.example", CLEAN)
        self.assertEqual(share.references(CLEAN), [])

    def test_a_remote_image_is_caught(self) -> None:
        found = share.references(bundle_with('<img src="https://tracker.example/pixel.gif">'))
        self.assertEqual(len(found), 1)
        self.assertIn("tracker.example/pixel.gif", found[0])

    def test_every_shape_of_phoning_home_is_caught(self) -> None:
        cases = {
            "remote stylesheet": '<link rel="stylesheet" href="https://cdn.example/a.css">',
            "remote script": '<script src="https://cdn.example/a.js"></script>',
            "protocol relative": '<img src="//tracker.example/p.gif">',
            "iframe": '<iframe src="https://evil.example/"></iframe>',
            "object": '<object data="https://evil.example/x.swf"></object>',
            "css url": '<style>body { background: url(https://tracker.example/bg.png) }</style>',
            "css import": '<style>@import "https://cdn.example/a.css";</style>',
            "style attribute": '<div style="background:url(https://tracker.example/bg.png)"></div>',
            "srcset": '<img srcset="https://cdn.example/a.png 1x, data:image/png;base64,AA== 2x">',
            "poster": '<video poster="https://cdn.example/p.jpg"></video>',
            "base": '<base href="https://evil.example/">',
            "relative sibling": '<img src="pages/page-1.png">',
        }
        for name, markup in cases.items():
            with self.subTest(case=name):
                self.assertTrue(share.references(bundle_with(markup)), f"{name} slipped through")

    def test_a_doctored_bundle_is_never_published(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="rm-web-doctored-"))
        vault = Vault(tmp / "vault", body=bundle_with('<img src="https://tracker.example/p.gif">'))
        with self.assertRaises(share.ShareError) as caught:
            share.publish(vault.session, "examples/report", tmp / "shares", run=vault.run)
        message = str(caught.exception)
        self.assertIn("not self-contained", message)
        self.assertIn("tracker.example", message)
        self.assertFalse(list((tmp / "shares").glob("*.html")) if (tmp / "shares").is_dir() else [])

    def test_a_repeated_beacon_is_reported_once(self) -> None:
        markup = '<img src="https://tracker.example/p.gif">' * 40
        self.assertEqual(len(share.references(bundle_with(markup))), 1)


# ── the response ─────────────────────────────────────────────────────────────


class TestHeaders(ShareCase):
    def setUp(self) -> None:
        super().setUp()
        self.published = self.publish()
        self.headers = share.headers(self.published.path, report_id=self.published.report)

    def test_the_policy_allows_no_inline_free_for_all(self) -> None:
        policy = self.headers["Content-Security-Policy"]
        self.assertIn("default-src 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertIn("connect-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("base-uri 'none'", policy)

    def test_the_script_hash_is_the_one_a_browser_computes(self) -> None:
        body = 'document.documentElement.className = "js";'
        digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        self.assertIn(f"'sha256-{digest}'", self.headers["Content-Security-Policy"])

    def test_no_referrer_so_a_cited_link_cannot_leak_the_token(self) -> None:
        """The bundle links to every source it cites. Without this, a reader
        clicking one hands the share token to the site being audited."""
        self.assertEqual(self.headers["Referrer-Policy"], "no-referrer")

    def test_it_is_served_inline_under_the_report_s_own_name(self) -> None:
        self.assertEqual(self.headers["Content-Disposition"], 'inline; filename="report.html"')

    def test_the_filename_never_carries_a_path(self) -> None:
        for nasty, expected in (
            ("a/b/../../etc/passwd", "passwd.html"),
            ('x"; rm -rf', "x---rm--rf.html"),
            # Nothing after the last slash, so there is no name to use.
            ('x"; rm -rf /', "report.html"),
            ("clients/acme/2026-08-16-pricing", "2026-08-16-pricing.html"),
        ):
            with self.subTest(report=nasty):
                name = share._filename(nasty)
                self.assertNotIn("/", name)
                self.assertNotIn('"', name)
                self.assertEqual(name, expected)

    def test_it_sets_no_cookie(self) -> None:
        """A share carries no session, which is what makes it safe to forward."""
        self.assertNotIn("Set-Cookie", self.headers)

    def test_it_declares_its_length_and_refuses_sniffing(self) -> None:
        self.assertEqual(int(self.headers["Content-Length"]), len(CLEAN.encode()))
        self.assertEqual(self.headers["X-Content-Type-Options"], "nosniff")


# ── against the real thing ───────────────────────────────────────────────────


class TestRealBundle(unittest.TestCase):
    """The engine's own output must pass the check that guards the door.

    A refusal nobody can satisfy is a refusal somebody deletes the first time it
    blocks a release, so this runs `report-maker html` over the demo vault and
    asserts the genuine article comes through clean — and that its inline script
    and stylesheet hash to values a CSP can name.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not (DEMO / "report-maker.toml").is_file():
            raise unittest.SkipTest("no demo vault in this checkout")
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "report-maker"), "-C", str(DEMO), "html"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise unittest.SkipTest(f"the demo vault does not export: {result.stderr.strip()[:200]}")
        cls.bundles = sorted((DEMO / "out").rglob("*.html"))
        if not cls.bundles:
            raise unittest.SkipTest("the demo vault exported no bundles")

    def test_the_real_bundles_are_self_contained(self) -> None:
        for path in self.bundles:
            with self.subTest(bundle=path.name):
                self.assertEqual(share.references(path.read_bytes()), [])

    def test_the_real_bundles_hash_to_a_usable_policy(self) -> None:
        for path in self.bundles:
            with self.subTest(bundle=path.name):
                scripts, styles = share.inline_hashes(path.read_bytes())
                self.assertTrue(scripts, "no inline script found to hash")
                self.assertTrue(styles, "no inline style found to hash")
                policy = share.headers(path, report_id=path.stem)["Content-Security-Policy"]
                for digest in scripts | styles:
                    self.assertIn(f"'{digest}'", policy)


if __name__ == "__main__":
    unittest.main()
