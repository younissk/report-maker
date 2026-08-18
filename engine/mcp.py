"""The vault, exposed to an agent — with the citation rule still in force.

A model writing a report is the case the house rule was written for. It can
produce a page of confident prose in a second, and every sentence of it will read
like a fact whether or not anything stands behind it. `report-maker check`
already refuses that at build time, but a build-time gate is a gate you can walk
past for hours before it closes: the agent writes, the agent moves on, and the
findings arrive long after the reasoning that caused them is gone.

So this module puts the gate at the point of writing. It speaks MCP over stdio,
and the tool an agent reaches for to edit a report — `write_report` — runs the
check itself, compares the findings against the ones that were there before, and
**restores the file byte for byte** if the write introduced a new error. The
agent does not get to decide whether to run the linter; writing *is* running it.
The comparison is against the previous state rather than against zero on purpose,
so a report that is already failing can still be edited towards a fix.

Two smaller things this file is careful about, both of which break the protocol
outright when they go wrong:

*Nothing but protocol frames may reach stdout.* Every command in the engine
prints — `build` prints paths, `cite` prints the key it chose — and a single
stray line of that on stdout desynchronises the client's parser. Every tool call
therefore runs inside `redirect_stdout`, and what it captures goes to stderr,
where a log belongs. Tools that actually want the output (`build`) capture it
themselves, inside that outer guard.

*A fault the model caused is data, not a transport error.* A bad report id, a
missing argument, a vault that is not a vault — all come back as an `isError`
tool result the model can read and correct. Only genuine protocol faults (an
unparseable frame, an unknown method, an unknown tool) become JSON-RPC errors,
because those say the client is broken, not the reasoning.

    report-maker -C <vault> mcp        # speaks JSON-RPC on stdin/stdout
"""

from __future__ import annotations

import json
import sys
import traceback
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

from . import __version__
from . import build as build_mod
from . import check as check_mod
from . import cite as cite_mod
from . import diffing
from . import scaffold
from . import score as score_mod
from . import snapshot
from . import sources as sources_mod
from . import vault as vault_mod
from . import verify as verify_mod
from .config import Config, load
from .workspace import Report, reports

# The revision of MCP this server implements. An older client is answered in its
# own version rather than corrected — the handshake is the one place where being
# accommodating costs nothing.
PROTOCOL_VERSION = "2025-06-18"

KNOWN_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")

SERVER_NAME = "report-maker"

# What the client is told the vault is for, before it calls anything. A model
# that reads this before its first tool call writes a citable report; one that
# discovers the rule from a rejected write writes two drafts.
INSTRUCTIONS = """\
This vault holds reports built by report-maker. Its one rule is that a statement
is either cited or it is an opinion, and there is no third category:

  · a fact about the world carries an @key that resolves to the report's
    sources.yml
  · a judgement, rating, forecast or recommendation ends with #assess, or sits
    inside assessment[…]
  · a table or figure goes through srcfig(…, source: [@key]), an image through
    srcimage(…), a quotation through srcquote(…, locator:)
  · absence of evidence is reported as absence ("no pricing on any reviewed
    page @key"), never as a claim about the underlying fact

Write sources before prose: add_source fetches a URL, archives a copy of it next
to the report, and hands back the key to cite. Then write_report, which runs the
rule over what you wrote and refuses a write that breaks it.
"""

# How much of a build log to hand back. Typst's failures are at the end of it,
# and the middle is a list of paths the model does not need.
TAIL_LINES = 40

RESOURCE_SCHEME = "report://"

RESOURCE_FILES = {
    "main.typ": "text/x-typst",
    "sources.yml": "application/yaml",
}


class McpError(RuntimeError):
    """A tool cannot do what it was asked. Reported to the model, not the client."""


class Refused(McpError):
    """A tool declined, and the reason is structured data rather than a sentence.

    `write_report` raises this when a strict write is rolled back: the findings
    that caused the refusal are the whole point of the answer, so they travel as
    the payload rather than being flattened into a message.
    """

    def __init__(self, payload: dict) -> None:
        super().__init__(str(payload.get("detail", "refused")))
        self.payload = payload


# ── the wire ─────────────────────────────────────────────────────────────────
#
# JSON-RPC 2.0, one message per line. These four codes are the standard ones;
# -32002 is MCP's own "resource not found".

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
RESOURCE_NOT_FOUND = -32002


class _Fault(Exception):
    """A protocol-level fault: the client is wrong, not the model."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _result(ident, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _error(ident, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def _content(text: str, *, is_error: bool = False) -> dict:
    """The MCP tool-result shape. Always one text block, always JSON inside it —
    a model parses one thing well and two things badly."""
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _payload(payload: dict, *, is_error: bool = False) -> dict:
    # `default=str` because a payload assembled from engine records may carry a
    # Path; a protocol frame that fails to serialise is worse than a stringified
    # path in one field.
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    return _content(text, is_error=is_error)


def _tail(text: str, lines: int = TAIL_LINES) -> str:
    kept = text.rstrip("\n").splitlines()
    return "\n".join(kept[-lines:])


def _describe(exc: BaseException) -> dict:
    """An exception as something a model can act on.

    `workspace.reports` raises `SystemExit` for an unknown target — it is a CLI
    at heart — and its message ("no such report: x; known reports: …") is exactly
    what the model needs, so it is carried through rather than swallowed.
    """
    text = str(exc).strip()
    return {
        "error": text or exc.__class__.__name__,
        "type": exc.__class__.__name__,
    }


# ── tool arguments ───────────────────────────────────────────────────────────


def _string(args: Mapping, name: str, *, required: bool = False, default=None):
    value = args.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise McpError(f"`{name}` is required and must be a non-empty string")
        return default
    if not isinstance(value, str):
        raise McpError(f"`{name}` must be a string, not {type(value).__name__}")
    return value


def _body(args: Mapping, name: str) -> str | None:
    """File contents, where an empty string is a legitimate value and `None`
    means "leave this file alone"."""
    value = args.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise McpError(
            f"`{name}` must be the full text of the file as a string, "
            f"not {type(value).__name__}"
        )
    return value


def _uri(params: Mapping) -> str:
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri.strip():
        raise _Fault(INVALID_PARAMS, "`uri` is required and must be a string")
    return uri.strip()


def _flag(args: Mapping, name: str, default: bool) -> bool:
    value = args.get(name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    # Models write `"true"` often enough that refusing it is pedantry.
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise McpError(f"`{name}` must be true or false, not {value!r}")


VAULT_ARG = {
    "type": "string",
    "description": (
        "Path to the vault — the folder holding report-maker.toml. Omit it to "
        "use the vault the server was started with, which is the usual case."
    ),
}

TARGET_ARG = {
    "type": "string",
    "description": (
        "A report id (clients/acme/2026-08-12-audit), a bare slug when it is "
        "unambiguous, or a folder to take every report under it. Omit for the "
        "whole vault."
    ),
}

REPORT_ARG = {
    "type": "string",
    "description": (
        "The report to act on: a full id from list_reports, or a bare slug when "
        "it is unambiguous. Must resolve to exactly one report."
    ),
}


def _schema(properties: dict, required: Sequence[str] = ()) -> dict:
    return {
        "type": "object",
        "properties": {**properties, "vault": VAULT_ARG},
        "required": list(required),
        "additionalProperties": False,
    }


# ── the tools ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    run: Callable[["Server", dict], dict]

    def advertised(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.schema,
        }


def _report_row(report: Report) -> dict:
    """One row of `list --json`. Deliberately the same shape the CLI prints, so
    the app and the agent are looking at one description of a report."""
    return {
        "id": report.id,
        "group": report.group,
        "template": report.template_id(),
        "built": report.pdf.exists(),
        "stale": report.is_stale(),
        **report.meta(),
    }


def _tool_list_reports(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    found = reports(cfg, _string(args, "target"))
    return {"vault": str(cfg.root), "reports": [_report_row(r) for r in found]}


def _tool_read_report(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    report = server.report(cfg, _string(args, "report", required=True))
    return {
        "id": report.id,
        "template": report.template_id(),
        "main": _read(report.main),
        "sources": _read(report.sources),
        "paths": {
            "main": check_mod.relative(report.main, cfg.root),
            "sources": check_mod.relative(report.sources, cfg.root),
        },
        "archived": snapshot.dir_for(report).is_dir(),
    }


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _signature(cfg: Config, finding: check_mod.Finding) -> tuple[str, str, str]:
    """What makes two findings "the same finding".

    The line is left out on purpose. Adding a paragraph shifts every line below
    it, and a finding that moved down four lines is not a new finding — treating
    it as one would make every edit look like a regression.
    """
    return (finding.code, check_mod.relative(finding.path, cfg.root), finding.message)


def introduced(
    cfg: Config,
    before: Sequence[check_mod.Finding],
    after: Sequence[check_mod.Finding],
) -> list[check_mod.Finding]:
    """The error-level findings the write added, as a multiset difference.

    Counting rather than set-subtracting matters: a report that cited one missing
    key and now cites two has introduced one error, and set semantics would say
    it introduced none.
    """
    was = Counter(_signature(cfg, f) for f in before if f.level == "error")
    fresh: list[check_mod.Finding] = []
    for finding in after:
        if finding.level != "error":
            continue
        sig = _signature(cfg, finding)
        if was[sig]:
            was[sig] -= 1
        else:
            fresh.append(finding)
    return fresh


def _tool_write_report(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    report = server.report(cfg, _string(args, "report", required=True))
    main = _body(args, "main")
    bibliography = _body(args, "sources")
    strict = _flag(args, "strict", True)

    if main is None and bibliography is None:
        raise McpError(
            "nothing to write — pass `main`, `sources`, or both, each holding the "
            "complete new contents of that file"
        )

    before = check_mod.check(cfg, report.id)

    # The bytes as they are now, so a rollback can restore the file exactly.
    # `None` records a file that did not exist, which must be removed again
    # rather than left behind as an empty one.
    saved: list[tuple[Path, bytes | None]] = []
    for path, text in ((report.main, main), (report.sources, bibliography)):
        if text is None:
            continue
        saved.append((path, path.read_bytes() if path.is_file() else None))
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n" so the file on disk is the string that was sent, on every
        # platform — a rollback that has to reason about line endings is not a
        # rollback.
        path.write_text(text, encoding="utf-8", newline="\n")

    written = [check_mod.relative(path, cfg.root) for path, _ in saved]
    after = check_mod.check(cfg, report.id)
    fresh = introduced(cfg, before, after)

    payload = {
        "report": report.id,
        "strict": strict,
        "written": written,
        "rolledBack": False,
        "introduced": check_mod.findings_json(cfg, fresh)["findings"],
        "check": check_mod.findings_json(cfg, after),
    }

    if fresh and strict:
        for path, original in saved:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)
        payload["rolledBack"] = True
        payload["written"] = []
        payload["detail"] = (
            f"refused: the write introduced {len(fresh)} new error-level "
            f"finding(s), so {', '.join(written)} was restored to its previous "
            "bytes and nothing was written. Something is either cited or it is "
            "an opinion — add the source with add_source and cite it, or mark "
            "the sentence as an assessment — then call write_report again. Pass "
            "strict: false only if you mean to leave the error in the file."
        )
        raise Refused(payload)

    if fresh:
        payload["detail"] = (
            f"written with strict off: {len(fresh)} new error-level finding(s) "
            "are now in the vault and `report-maker check` will fail until they "
            "are resolved."
        )
    else:
        errors = payload["check"]["errors"]
        payload["detail"] = (
            f"written; no new error introduced ({errors} error(s) remain in this "
            "report from before the write)."
            if errors
            else "written; the report passes the citation rule."
        )
    return payload


def _tool_list_sources(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    report = server.report(cfg, _string(args, "report", required=True))
    records = {}
    for source in sources_mod.parse(report.sources):
        record = snapshot.read_record(report, source.key)
        if record is not None:
            records[source.key] = record
    return {
        "report": report.id,
        "sources": sources_mod.rows(report, snapshots=records),
    }


def _tool_add_source(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    report = server.report(cfg, _string(args, "report", required=True))
    source = cite_mod.cite(
        cfg,
        report.id,
        _string(args, "url", required=True),
        key=_string(args, "key"),
        fetch=server.fetch,
    )
    record = snapshot.read_record(report, source.key)
    return {
        "report": report.id,
        "key": source.key,
        "cite": f"@{source.key}",
        "title": source.title,
        "url": source.url,
        "accessed": source.accessed,
        "snapshot": (
            {"sha256": record.get("sha256"), "fetched": record.get("fetched")}
            if record
            else None
        ),
    }


def _tool_check(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    findings = check_mod.check(cfg, _string(args, "target"))
    return check_mod.findings_json(cfg, findings)


def _tool_score(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    return score_mod.to_json(score_mod.score(cfg, _string(args, "target")))


def _tool_verify(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    drifts = verify_mod.verify(
        cfg,
        _string(args, "target"),
        fetch=server.fetch,
        offline=_flag(args, "offline", False),
    )
    return verify_mod.to_json(drifts)


def _tool_diff(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    report = server.report(cfg, _string(args, "report", required=True))
    rev = _string(args, "rev", default="HEAD~1")
    return diffing.to_json(diffing.diff(cfg, report.id, rev))


def _tool_build(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    target = _string(args, "target")
    # This tool wants the output, so it captures its own inside the dispatcher's
    # guard rather than letting the log-forwarding swallow it.
    buffer = StringIO()
    code = 0
    try:
        with redirect_stdout(buffer):
            build_mod.build(cfg, target, force=_flag(args, "force", False))
    except build_mod.BuildError as exc:
        code = 1
        print(str(exc), file=buffer)
    return {
        "target": target or "",
        "code": code,
        "output": _tail(buffer.getvalue()),
        "ok": code == 0,
    }


def _tool_new_report(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    folder = scaffold.new_report(
        cfg,
        title=_string(args, "title", required=True),
        into=_string(args, "into"),
        template=_string(args, "template", default="base"),
        kind=_string(args, "kind"),
        author=_string(args, "author"),
    )
    report_id = folder.relative_to(cfg.reports).as_posix()
    return {
        "id": report_id,
        "folder": check_mod.relative(folder, cfg.root),
        "main": check_mod.relative(folder / "main.typ", cfg.root),
        "sources": check_mod.relative(folder / "sources.yml", cfg.root),
        "detail": (
            "scaffolded. Fill sources.yml first: a claim with no key to point at "
            "has to be downgraded to an assessment."
        ),
    }


def _tool_list_templates(server: Server, args: dict) -> dict:
    cfg = server.config(_string(args, "vault"))
    return {
        tid: {
            "title": tpl.title,
            "group": tpl.group,
            "description": tpl.description,
            "extends": tpl.extends,
            "brand": tpl.brand_pack,
            "builtin": tpl.builtin,
            "folder": str(tpl.folder),
        }
        for tid, tpl in vault_mod.templates(cfg).items()
    }


TOOLS: tuple[Tool, ...] = (
    Tool(
        name="list_reports",
        description=(
            "List the reports in the vault: id, group, design, cover metadata, "
            "and whether the built PDF is missing or stale. Start here — every "
            "other tool takes a report id from this list."
        ),
        schema=_schema({"target": TARGET_ARG}),
        run=_tool_list_reports,
    ),
    Tool(
        name="read_report",
        description=(
            "Read one report: the Typst body (main.typ), the bibliography "
            "(sources.yml) and the design it is built with. Read both before "
            "editing either — a citation exists only if its key is a block in "
            "sources.yml, so the two files are one document."
        ),
        schema=_schema({"report": REPORT_ARG}, ["report"]),
        run=_tool_read_report,
    ),
    Tool(
        name="write_report",
        description=(
            "Write main.typ and/or sources.yml for one report, then run the "
            "citation rule over the result.\n\n"
            "The rule of this vault is that a statement is either cited or it is "
            "an opinion: a fact carries an @key resolving to sources.yml, a "
            "judgement ends with #assess or sits inside assessment[…], and every "
            "figure, image and quotation carries a source:.\n\n"
            "With strict — the default — this tool enforces that and cannot be "
            "talked out of it. It runs check before the write and again after, "
            "and if the write introduced a NEW error-level finding it rolls the "
            "write back: the file is restored byte for byte, nothing is written, "
            "and the findings come back as an error result. Fix them — add the "
            "source and cite it, or mark the sentence as an assessment — and "
            "call again.\n\n"
            "\"New\" is measured against the findings from before your write, not "
            "against zero, so a report that is already failing stays editable "
            "towards a fix: you only have to not make it worse.\n\n"
            "Pass strict: false only when you deliberately intend to leave an "
            "error in the file. The write then happens and the findings come "
            "back unchanged.\n\n"
            "Both arguments are whole files: they replace the contents, they do "
            "not patch them. Read the file first."
        ),
        schema=_schema(
            {
                "report": REPORT_ARG,
                "main": {
                    "type": "string",
                    "description": (
                        "The complete new contents of main.typ. Omit to leave "
                        "the file untouched."
                    ),
                },
                "sources": {
                    "type": "string",
                    "description": (
                        "The complete new contents of sources.yml, in hayagriva "
                        "form. Omit to leave the file untouched. Prefer "
                        "add_source for a web page — it archives the page as "
                        "well as naming it."
                    ),
                },
                "strict": {
                    "type": "boolean",
                    "description": (
                        "Default true: roll the write back and return the "
                        "findings if it introduces a new error-level finding. "
                        "Set false only to write deliberately-failing prose."
                    ),
                },
            },
            ["report"],
        ),
        run=_tool_write_report,
    ),
    Tool(
        name="list_sources",
        description=(
            "The bibliography of one report as data: key, type, title, author, "
            "URL, access date, whether the page is archived, and how often each "
            "key is actually cited. A source with uses 0 is listed in References "
            "but nothing rests on it — that is the W001 warning."
        ),
        schema=_schema({"report": REPORT_ARG}, ["report"]),
        run=_tool_list_sources,
    ),
    Tool(
        name="add_source",
        description=(
            "Fetch a URL, archive a verbatim copy of it next to the report, and "
            "add it to that report's sources.yml. Returns the key to cite with.\n\n"
            "Use this rather than writing an entry by hand: the archived copy is "
            "what lets verify tell you the page has since changed, and what lets "
            "a quotation be checked word for word against what the page actually "
            "said. Requires network access."
        ),
        schema=_schema(
            {
                "report": REPORT_ARG,
                "url": {
                    "type": "string",
                    "description": "The http(s) URL to cite and archive.",
                },
                "key": {
                    "type": "string",
                    "description": (
                        "Citation key to use. Omit to let the engine derive a "
                        "readable, collision-free one from the page title."
                    ),
                },
            },
            ["report", "url"],
        ),
        run=_tool_add_source,
    ),
    Tool(
        name="check",
        description=(
            "Run the citation rule over the vault, or over one report or folder. "
            "Returns every finding with a vault-relative path and a line number. "
            "Errors fail a build; warnings do not."
        ),
        schema=_schema({"target": TARGET_ARG}),
        run=_tool_check,
    ),
    Tool(
        name="score",
        description=(
            "Evidence density: per report and per section, how many statements "
            "are cited, how many are marked as assessment, and how many are "
            "neither. The unmarked count is the work still to do, and sections "
            "says where it is concentrated."
        ),
        schema=_schema({"target": TARGET_ARG}),
        run=_tool_score,
    ),
    Tool(
        name="verify",
        description=(
            "Re-fetch every archived source and report drift: ok, changed, gone, "
            "unsnapshotted. Run it before shipping a report written a while ago "
            "— a page that has been rewritten no longer says what the report "
            "claims it says. Requires network access unless offline is set."
        ),
        schema=_schema(
            {
                "target": TARGET_ARG,
                "offline": {
                    "type": "boolean",
                    "description": (
                        "Do not touch the network; report what is archived and "
                        "when, without re-fetching."
                    ),
                },
            }
        ),
        run=_tool_verify,
    ),
    Tool(
        name="diff",
        description=(
            "What changed in a report since a git revision, in the vocabulary of "
            "evidence rather than of lines: sources added, removed or edited; "
            "claims added, removed or reworded; assessments; figures; cover "
            "metadata. The vault must be a git repository."
        ),
        schema=_schema(
            {
                "report": REPORT_ARG,
                "rev": {
                    "type": "string",
                    "description": "Git revision to compare against. Default HEAD~1.",
                },
            },
            ["report"],
        ),
        run=_tool_diff,
    ),
    Tool(
        name="build",
        description=(
            "Compile reports to PDF with Typst. Returns the exit code and the "
            "tail of the output; a non-zero code means Typst rejected the source "
            "and the output names the file and line."
        ),
        schema=_schema(
            {
                "target": TARGET_ARG,
                "force": {
                    "type": "boolean",
                    "description": "Rebuild even when the PDF is up to date.",
                },
            }
        ),
        run=_tool_build,
    ),
    Tool(
        name="new_report",
        description=(
            "Scaffold a new report folder — main.typ and sources.yml from a "
            "design — filed under reports/<into>/<date>-<slug>. Returns the new "
            "report id to pass to the other tools."
        ),
        schema=_schema(
            {
                "title": {
                    "type": "string",
                    "description": "Report title, also the source of the folder slug.",
                },
                "into": {
                    "type": "string",
                    "description": (
                        "Folder under reports/ to file it in, e.g. clients/acme. "
                        "Nesting is the filing system; there is no index."
                    ),
                },
                "template": {
                    "type": "string",
                    "description": "Design id from list_templates. Default base.",
                },
                "kind": {
                    "type": "string",
                    "description": 'Cover kind, e.g. "Company Audit", "Proposal".',
                },
                "author": {"type": "string", "description": "Cover author."},
            },
            ["title"],
        ),
        run=_tool_new_report,
    ),
    Tool(
        name="list_templates",
        description=(
            "The designs available in this vault, built-in and vault-local, with "
            "what each one inherits and which brand pack it uses. A report names "
            "its design when it is created."
        ),
        schema=_schema({}),
        run=_tool_list_templates,
    ),
)

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


# ── the server ───────────────────────────────────────────────────────────────


@dataclass
class Server:
    """One session. Holds the default vault and nothing else that matters.

    `fetch` is injectable for the same reason it is everywhere else in the
    engine: the tests must not touch the network, and a server that hard-wires
    `urllib` cannot be driven as a library.
    """

    vault: Path | None = None
    fetch: snapshot.Fetcher = snapshot.http_fetch
    log: object = sys.stderr
    client: dict = field(default_factory=dict)
    protocol: str = PROTOCOL_VERSION

    # ── vault and reports

    def config(self, vault: str | None = None) -> Config:
        start = Path(vault).expanduser() if vault else self.vault
        return load(start)

    def report(self, cfg: Config, ident: str) -> Report:
        found = reports(cfg, ident)
        if len(found) != 1:
            named = ", ".join(r.id for r in found)
            raise McpError(
                f"{ident!r} matches {len(found)} reports — name exactly one of: {named}"
            )
        return found[0]

    # ── dispatch

    def handle(self, message: object) -> dict | None:
        """One incoming frame in, one response frame out — or None for a
        notification, which by definition is never answered."""
        if not isinstance(message, Mapping):
            return _error(None, INVALID_REQUEST, "a request must be a JSON object")
        ident = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            return _error(ident, INVALID_REQUEST, "`method` is required")
        params = message.get("params")
        params = params if isinstance(params, Mapping) else {}

        try:
            result = self._dispatch(method, params)
        except _Fault as fault:
            return None if ident is None else _error(ident, fault.code, str(fault))
        except Exception as exc:  # a broken tool must not take the session down
            self._note(traceback.format_exc())
            return (
                None
                if ident is None
                else _error(ident, INTERNAL_ERROR, f"{exc.__class__.__name__}: {exc}")
            )
        return None if ident is None else _result(ident, result)

    def _dispatch(self, method: str, params: Mapping) -> dict:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method.startswith("notifications/"):
            return {}
        if method == "tools/list":
            return {"tools": [tool.advertised() for tool in TOOLS]}
        if method == "tools/call":
            return self.call_tool(params)
        if method == "resources/list":
            return {"resources": self.resources()}
        if method == "resources/read":
            return {"contents": [self.read_resource(_uri(params))]}
        raise _Fault(METHOD_NOT_FOUND, f"unknown method: {method}")

    def _initialize(self, params: Mapping) -> dict:
        info = params.get("clientInfo")
        self.client = dict(info) if isinstance(info, Mapping) else {}
        asked = params.get("protocolVersion")
        # Speak the client's dialect when it is one we know and older than ours;
        # otherwise answer in ours and let it decide.
        if isinstance(asked, str) and asked in KNOWN_VERSIONS:
            self.protocol = asked
        else:
            self.protocol = PROTOCOL_VERSION
        return {
            "protocolVersion": self.protocol,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False},
            },
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    # ── tools

    def call_tool(self, params: Mapping) -> dict:
        name = params.get("name")
        tool = TOOLS_BY_NAME.get(name) if isinstance(name, str) else None
        if tool is None:
            raise _Fault(
                INVALID_PARAMS,
                f"unknown tool: {name!r} — call tools/list for what this server offers",
            )
        raw = params.get("arguments")
        args = dict(raw) if isinstance(raw, Mapping) else {}

        # The stdout guard. Every engine command prints, and one printed line on
        # stdout desynchronises the client's parser for the rest of the session.
        noise = StringIO()
        try:
            with redirect_stdout(noise):
                payload = tool.run(self, args)
        except Refused as refusal:
            return _payload(refusal.payload, is_error=True)
        except (Exception, SystemExit) as exc:
            # SystemExit is listed explicitly: `workspace.reports` raises it for
            # an unknown target, and its message is the useful answer.
            self._note(f"{tool.name}: {exc}")
            return _payload(_describe(exc), is_error=True)
        finally:
            self._note(noise.getvalue().rstrip("\n"), prefix=tool.name)
        return _payload(payload)

    # ── resources

    def resources(self) -> list[dict]:
        """Both files of every report. A vault that cannot be resolved lists
        nothing rather than failing — a client enumerates resources at startup,
        long before the user has said which vault they meant."""
        try:
            cfg = self.config()
        except Exception as exc:
            self._note(f"resources/list: {exc}")
            return []
        out: list[dict] = []
        for report in reports(cfg):
            for name, mime in RESOURCE_FILES.items():
                out.append(
                    {
                        "uri": f"{RESOURCE_SCHEME}{report.id}/{name}",
                        "name": f"{report.id}/{name}",
                        "description": (
                            "the report body" if name == "main.typ" else "its bibliography"
                        ),
                        "mimeType": mime,
                    }
                )
        return out

    def read_resource(self, uri: str) -> dict:
        if not uri.startswith(RESOURCE_SCHEME):
            raise _Fault(
                INVALID_PARAMS, f"not a report resource: {uri!r} (expected report://…)"
            )
        rest = uri[len(RESOURCE_SCHEME) :]
        report_id, _, name = rest.rpartition("/")
        if not report_id or name not in RESOURCE_FILES:
            raise _Fault(
                INVALID_PARAMS,
                f"{uri!r} names no file — expected report://<id>/main.typ or "
                "report://<id>/sources.yml",
            )
        try:
            cfg = self.config()
            report = self.report(cfg, report_id)
        except (Exception, SystemExit) as exc:
            # SystemExit again: `workspace.reports` raises it for an unknown id.
            raise _Fault(RESOURCE_NOT_FOUND, str(exc)) from exc
        path = report.main if name == "main.typ" else report.sources
        if not path.is_file():
            raise _Fault(RESOURCE_NOT_FOUND, f"{uri} does not exist in this vault")
        return {
            "uri": uri,
            "mimeType": RESOURCE_FILES[name],
            "text": path.read_text(encoding="utf-8"),
        }

    # ── the loop

    def run(self, stdin, stdout) -> int:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                self._emit(stdout, _error(None, PARSE_ERROR, f"invalid JSON: {exc}"))
                continue
            if isinstance(message, list):
                # Batching was removed from MCP in 2025-06-18, and answering half
                # a batch is worse than saying so.
                self._emit(
                    stdout,
                    _error(None, INVALID_REQUEST, "JSON-RPC batches are not supported"),
                )
                continue
            response = self.handle(message)
            if response is not None:
                self._emit(stdout, response)
        return 0

    def _emit(self, stdout, frame: dict) -> None:
        stdout.write(json.dumps(frame, ensure_ascii=False, default=str) + "\n")
        stdout.flush()

    def _note(self, text: str, prefix: str = SERVER_NAME) -> None:
        if not text or self.log is None:
            return
        for line in text.rstrip("\n").splitlines():
            print(f"[{prefix}] {line}", file=self.log)


def serve(
    vault: str | Path | None = None,
    *,
    stdin=None,
    stdout=None,
    log=None,
) -> int:
    """Run the server until stdin closes. The `mcp` command is this and nothing
    else — the transport is a loop, the behaviour is the tools."""
    server = Server(
        vault=Path(vault).expanduser() if vault else None,
        log=sys.stderr if log is None else log,
    )
    return server.run(stdin or sys.stdin, stdout or sys.stdout)
