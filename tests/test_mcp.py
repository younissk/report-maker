"""The MCP server, driven as a library.

The server is a loop around a dispatch table, and the loop is the least
interesting part of it — so these tests feed `Server.handle` framed requests
directly and read the frames it hands back. Only the stdout-hygiene test goes
through `run()`, because that is the one property the loop itself owns.

Three things carry the module.

The first is `write_report`. It is the reason this server exists: it is what
makes the citation rule un-bypassable for an agent, and its contract is
delicate in a specific way — a *new* error rolls the write back, an error that
was already there does not, and a rollback has to leave the file exactly as it
found it. All three are asserted below, the last on bytes rather than on text.

The second is that the tools are described well enough to be used correctly. A
client model reads `tools/list` and nothing else, so an undescribed argument is
a bug in the same sense a wrong return value is.

The third is stdout. Every engine command prints, and a single printed line on
stdout desynchronises the client's JSON parser for the rest of the session.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import mcp  # noqa: E402
from engine.snapshot import Fetched  # noqa: E402

REPORT_ID = "2026-08-18-demo"

VAULT_TOML = """[vault]
reports = "reports"
"""

MAIN = """#import "/.build/design/base/report.typ": *

#show: report.with(
  title: "Demo",
  author: "Tester",
  date: datetime(year: 2026, month: 8, day: 18),
  sources: "/reports/2026-08-18-demo/sources.yml",
)

= Findings

The vendor lists three tiers on its pricing page @alpha.

Our reading is that the middle tier is the one they sell.#assess
"""

SOURCES = """alpha:
  type: Web
  title: "Alpha pricing"
  url:
    value: "https://example.com/alpha"
    date: "2026-08-18"
"""

# A sentence citing a key that is in no bibliography — E006, the cheapest way to
# make a write introduce a real error.
UNCITED = MAIN + "\nA second vendor withdrew from the market @nowhere.\n"


class Session:
    """A client, near enough: it numbers requests and unwraps results."""

    def __init__(self, vault: Path) -> None:
        self.log = StringIO()
        self.server = mcp.Server(vault=vault, log=self.log)
        self.counter = 0

    def send(self, method: str, **params) -> dict | None:
        self.counter += 1
        return self.server.handle(
            {"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params}
        )

    def notify(self, method: str, **params) -> dict | None:
        return self.server.handle({"jsonrpc": "2.0", "method": method, "params": params})

    def result(self, method: str, **params) -> dict:
        response = self.send(method, **params)
        assert response is not None, f"{method} was not answered"
        assert "error" not in response, response.get("error")
        return response["result"]

    def call(self, name: str, **arguments) -> dict:
        """One tool call, as the MCP content envelope."""
        return self.result("tools/call", name=name, arguments=arguments)

    def data(self, name: str, **arguments) -> tuple[dict, bool]:
        """The payload a tool returned, and whether it was flagged as an error."""
        envelope = self.call(name, **arguments)
        return json.loads(envelope["content"][0]["text"]), envelope["isError"]


class VaultCase(unittest.TestCase):
    """A vault of one report that passes the citation rule."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="rm-mcp-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "report-maker.toml").write_text(VAULT_TOML, encoding="utf-8")
        self.folder = self.tmp / "reports" / REPORT_ID
        self.folder.mkdir(parents=True)
        self.main = self.folder / "main.typ"
        self.sources = self.folder / "sources.yml"
        self.main.write_text(MAIN, encoding="utf-8")
        self.sources.write_text(SOURCES, encoding="utf-8")
        self.session = Session(self.tmp)


# ── the handshake ────────────────────────────────────────────────────────────


class Handshake(VaultCase):
    def test_initialize_advertises_tools_and_resources(self) -> None:
        result = self.session.result("initialize", protocolVersion="2025-06-18")
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])
        self.assertIn("resources", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "report-maker")
        # The instructions are the only thing a model reads before its first
        # call, so the rule has to be in them.
        self.assertIn("cited", result["instructions"])

    def test_an_older_client_is_answered_in_its_own_version(self) -> None:
        result = self.session.result("initialize", protocolVersion="2025-03-26")
        self.assertEqual(result["protocolVersion"], "2025-03-26")

    def test_an_unknown_version_gets_ours(self) -> None:
        result = self.session.result("initialize", protocolVersion="1999-01-01")
        self.assertEqual(result["protocolVersion"], mcp.PROTOCOL_VERSION)

    def test_a_notification_is_never_answered(self) -> None:
        self.assertIsNone(self.session.notify("notifications/initialized"))

    def test_ping_is_an_empty_result(self) -> None:
        self.assertEqual(self.session.result("ping"), {})

    def test_an_unknown_method_is_a_protocol_error(self) -> None:
        response = self.session.send("prompts/list")
        self.assertEqual(response["error"]["code"], mcp.METHOD_NOT_FOUND)


# ── what the client is told the tools are ────────────────────────────────────


class Advertisement(VaultCase):
    EXPECTED = {
        "list_reports",
        "read_report",
        "write_report",
        "list_sources",
        "add_source",
        "check",
        "score",
        "verify",
        "diff",
        "build",
        "new_report",
        "list_templates",
    }

    def tools(self) -> dict[str, dict]:
        listed = self.session.result("tools/list")["tools"]
        return {tool["name"]: tool for tool in listed}

    def test_every_contracted_tool_is_listed(self) -> None:
        self.assertEqual(set(self.tools()), self.EXPECTED)

    def test_every_tool_has_a_schema_a_model_can_act_on(self) -> None:
        for name, tool in self.tools().items():
            with self.subTest(tool=name):
                self.assertGreater(len(tool["description"]), 60)
                schema = tool["inputSchema"]
                self.assertEqual(schema["type"], "object")
                # Every tool takes an optional vault, per the contract.
                self.assertIn("vault", schema["properties"])
                for arg, spec in schema["properties"].items():
                    self.assertTrue(spec.get("description"), f"{name}.{arg}")
                for required in schema["required"]:
                    self.assertIn(required, schema["properties"])

    def test_the_write_tool_advertises_the_rollback(self) -> None:
        # The description is the mechanism: a client that does not know the write
        # can be refused will treat a refusal as a bug and retry it verbatim.
        text = self.tools()["write_report"]["description"]
        for phrase in ("strict", "roll", "byte", "cited"):
            self.assertIn(phrase, text.lower())

    def test_an_unknown_tool_is_a_protocol_error(self) -> None:
        response = self.session.send("tools/call", name="nope", arguments={})
        self.assertEqual(response["error"]["code"], mcp.INVALID_PARAMS)

    def test_a_bad_report_id_is_a_tool_error_not_a_protocol_error(self) -> None:
        payload, is_error = self.session.data("read_report", report="not-a-report")
        self.assertTrue(is_error)
        self.assertIn("no such report", payload["error"])


# ── reading ──────────────────────────────────────────────────────────────────


class Reading(VaultCase):
    def test_list_reports_names_the_one_report(self) -> None:
        payload, is_error = self.session.data("list_reports")
        self.assertFalse(is_error)
        self.assertEqual([r["id"] for r in payload["reports"]], [REPORT_ID])
        self.assertEqual(payload["reports"][0]["title"], "Demo")

    def test_read_report_returns_both_files(self) -> None:
        payload, _ = self.session.data("read_report", report=REPORT_ID)
        self.assertEqual(payload["main"], MAIN)
        self.assertEqual(payload["sources"], SOURCES)
        self.assertEqual(payload["template"], "base")

    def test_list_sources_counts_the_citations(self) -> None:
        payload, _ = self.session.data("list_sources", report=REPORT_ID)
        (row,) = payload["sources"]
        self.assertEqual(row["key"], "alpha")
        self.assertEqual(row["uses"], 1)
        self.assertIsNone(row["snapshot"])

    def test_check_passes_on_the_fixture(self) -> None:
        payload, is_error = self.session.data("check")
        self.assertFalse(is_error)
        self.assertEqual(payload["errors"], 0)
        self.assertEqual(payload["warnings"], 0)

    def test_score_counts_the_statements(self) -> None:
        payload, _ = self.session.data("score", target=REPORT_ID)
        self.assertEqual(payload["cited"], 1)
        self.assertEqual(payload["assessed"], 1)


# ── writing, which is the whole point ────────────────────────────────────────


class Writing(VaultCase):
    def test_a_clean_write_lands(self) -> None:
        text = MAIN.replace(
            "= Findings", "= Findings\n\nThe page names no support hours @alpha."
        )
        payload, is_error = self.session.data("write_report", report=REPORT_ID, main=text)
        self.assertFalse(is_error)
        self.assertFalse(payload["rolledBack"])
        self.assertEqual(payload["introduced"], [])
        self.assertEqual(payload["check"]["errors"], 0)
        self.assertEqual(self.main.read_text(encoding="utf-8"), text)
        self.assertEqual(payload["written"], [f"reports/{REPORT_ID}/main.typ"])

    def test_both_files_can_be_written_at_once(self) -> None:
        bibliography = SOURCES + 'beta:\n  type: Web\n  title: "Beta"\n'
        text = MAIN.replace("@alpha.", "@alpha, and a second one @beta.")
        payload, is_error = self.session.data(
            "write_report", report=REPORT_ID, main=text, sources=bibliography
        )
        self.assertFalse(is_error)
        self.assertEqual(self.sources.read_text(encoding="utf-8"), bibliography)
        self.assertEqual(len(payload["written"]), 2)

    def test_a_strict_write_that_breaks_the_rule_is_rolled_back(self) -> None:
        before = self.main.read_bytes()
        payload, is_error = self.session.data(
            "write_report", report=REPORT_ID, main=UNCITED
        )
        self.assertTrue(is_error)
        self.assertTrue(payload["rolledBack"])
        self.assertEqual(payload["written"], [])
        # The file is not merely equivalent — it is the same bytes.
        self.assertEqual(self.main.read_bytes(), before)
        (finding,) = payload["introduced"]
        self.assertEqual(finding["code"], "E006")
        self.assertIn("nowhere", finding["message"])
        self.assertIn("either cited or it is an opinion", payload["detail"])

    def test_a_rollback_removes_a_file_it_created(self) -> None:
        # sources.yml did not exist before the write, so restoring "the previous
        # bytes" means having no file at all.
        self.sources.unlink()
        payload, is_error = self.session.data(
            "write_report",
            report=REPORT_ID,
            main=UNCITED,
            sources='beta:\n  type: Web\n  title: "Beta"\n',
        )
        self.assertTrue(is_error)
        self.assertTrue(payload["rolledBack"])
        self.assertFalse(self.sources.exists())

    def test_a_non_strict_write_lands_despite_the_finding(self) -> None:
        payload, is_error = self.session.data(
            "write_report", report=REPORT_ID, main=UNCITED, strict=False
        )
        self.assertFalse(is_error)
        self.assertFalse(payload["rolledBack"])
        self.assertEqual(self.main.read_text(encoding="utf-8"), UNCITED)
        self.assertEqual(payload["check"]["errors"], 1)
        self.assertEqual(len(payload["introduced"]), 1)

    def test_a_report_that_already_fails_stays_editable(self) -> None:
        # The rule is "do not make it worse", not "must be clean" — otherwise the
        # first error in a report would lock the agent out of fixing it.
        self.main.write_text(UNCITED, encoding="utf-8")
        edited = UNCITED.replace("= Findings", "= Findings\n\nA further fact @alpha.")
        payload, is_error = self.session.data(
            "write_report", report=REPORT_ID, main=edited
        )
        self.assertFalse(is_error)
        self.assertFalse(payload["rolledBack"])
        self.assertEqual(payload["introduced"], [])
        self.assertEqual(payload["check"]["errors"], 1)
        self.assertEqual(self.main.read_text(encoding="utf-8"), edited)

    def test_the_same_error_twice_is_one_new_error(self) -> None:
        # Multiset, not set: citing a missing key a second time is a regression
        # even though that finding's shape was already present.
        self.main.write_text(UNCITED, encoding="utf-8")
        worse = UNCITED + "\nAnd a third vendor left @nowhere.\n"
        payload, is_error = self.session.data(
            "write_report", report=REPORT_ID, main=worse
        )
        self.assertTrue(is_error)
        self.assertEqual(len(payload["introduced"]), 1)
        self.assertEqual(self.main.read_text(encoding="utf-8"), UNCITED)

    def test_writing_nothing_is_refused_with_an_explanation(self) -> None:
        payload, is_error = self.session.data("write_report", report=REPORT_ID)
        self.assertTrue(is_error)
        self.assertIn("nothing to write", payload["error"])

    def test_a_missing_report_argument_is_reported_to_the_model(self) -> None:
        payload, is_error = self.session.data("write_report", main=MAIN)
        self.assertTrue(is_error)
        self.assertIn("`report` is required", payload["error"])


# ── the tools that would otherwise reach the network ─────────────────────────


PAGE = (
    b"<!doctype html><html><head><title>Beta pricing</title></head>"
    b"<body><h1>Pricing</h1><p>Three tiers.</p></body></html>"
)


class Fetching(VaultCase):
    """`fetch` is a field on the server for exactly this: no test touches the web."""

    def setUp(self) -> None:
        super().setUp()
        self.asked: list[str] = []

        def fetcher(url: str) -> Fetched:
            self.asked.append(url)
            return Fetched(
                url=url,
                status=200,
                content_type="text/html; charset=utf-8",
                body=PAGE,
                final_url=url,
            )

        self.session.server.fetch = fetcher

    def test_add_source_archives_the_page_and_hands_back_the_key(self) -> None:
        payload, is_error = self.session.data(
            "add_source", report=REPORT_ID, url="https://example.com/beta", key="beta"
        )
        self.assertFalse(is_error)
        self.assertEqual(payload["cite"], "@beta")
        self.assertTrue(payload["snapshot"]["sha256"])
        self.assertIn("beta:", self.sources.read_text(encoding="utf-8"))
        self.assertTrue((self.folder / "snapshots" / "beta.html").is_file())
        self.assertEqual(self.asked, ["https://example.com/beta"])

    def test_verify_offline_reports_the_archive_without_fetching(self) -> None:
        self.session.data(
            "add_source", report=REPORT_ID, url="https://example.com/beta", key="beta"
        )
        payload, is_error = self.session.data("verify", target=REPORT_ID, offline=True)
        self.assertFalse(is_error)
        states = {d["key"]: d["state"] for d in payload["drifts"]}
        self.assertEqual(states, {"alpha": "unsnapshotted", "beta": "offline"})
        self.assertEqual(len(self.asked), 1)  # the add, and nothing since


# ── resources ────────────────────────────────────────────────────────────────


class Resources(VaultCase):
    def test_both_files_of_every_report_are_listed(self) -> None:
        uris = {r["uri"] for r in self.session.result("resources/list")["resources"]}
        self.assertEqual(
            uris,
            {
                f"report://{REPORT_ID}/main.typ",
                f"report://{REPORT_ID}/sources.yml",
            },
        )

    def test_reading_a_resource_returns_its_text(self) -> None:
        result = self.session.result(
            "resources/read", uri=f"report://{REPORT_ID}/sources.yml"
        )
        (content,) = result["contents"]
        self.assertEqual(content["text"], SOURCES)
        self.assertEqual(content["mimeType"], "application/yaml")

    def test_an_unknown_resource_is_a_protocol_error(self) -> None:
        response = self.session.send("resources/read", uri="report://nope/main.typ")
        self.assertEqual(response["error"]["code"], mcp.RESOURCE_NOT_FOUND)

    def test_a_foreign_uri_is_refused(self) -> None:
        response = self.session.send("resources/read", uri="file:///etc/passwd")
        self.assertEqual(response["error"]["code"], mcp.INVALID_PARAMS)


# ── the transport ────────────────────────────────────────────────────────────


class Transport(VaultCase):
    def drive(self, *messages: dict) -> tuple[list[dict], str]:
        stdin = StringIO("".join(json.dumps(m) + "\n" for m in messages))
        stdout = StringIO()
        log = StringIO()
        server = mcp.Server(vault=self.tmp, log=log)
        server.run(stdin, stdout)
        lines = [line for line in stdout.getvalue().splitlines() if line]
        # The contract: every single line on stdout parses as one JSON frame.
        return [json.loads(line) for line in lines], log.getvalue()

    def test_only_protocol_frames_reach_stdout(self) -> None:
        # `new_report` scaffolds files and prints every path it writes. That
        # output must end up on stderr, never between two frames.
        frames, log = self.drive(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "new_report", "arguments": {"title": "Second"}},
            },
        )
        # Two requests, one notification, two frames.
        self.assertEqual([f["id"] for f in frames], [1, 2])
        payload = json.loads(frames[1]["result"]["content"][0]["text"])
        self.assertTrue(payload["id"].endswith("-second"))
        self.assertIn("main.typ", log)

    def test_an_unparseable_line_is_answered_and_the_session_continues(self) -> None:
        stdin = StringIO('{"nope\n' + json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n")
        stdout = StringIO()
        mcp.Server(vault=self.tmp, log=StringIO()).run(stdin, stdout)
        frames = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
        self.assertEqual(frames[0]["error"]["code"], mcp.PARSE_ERROR)
        self.assertEqual(frames[1]["id"], 7)

    def test_a_batch_is_refused_rather_than_half_answered(self) -> None:
        stdin = StringIO(json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]) + "\n")
        stdout = StringIO()
        mcp.Server(vault=self.tmp, log=StringIO()).run(stdin, stdout)
        frame = json.loads(stdout.getvalue().strip())
        self.assertEqual(frame["error"]["code"], mcp.INVALID_REQUEST)


if __name__ == "__main__":
    unittest.main()
