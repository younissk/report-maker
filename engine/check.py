"""The house rule, enforced.

Something is either cited, or it is an opinion. There is no third category, and
nothing in a report may sit in between:

    a fact about the world      carries a @key resolving to sources.yml
    a judgement or rating       ends with #assess, or sits inside assessment[…]
    a table, figure or image    goes through srcfig / srcimage / diagram, which
                                cannot be written without a `source:`

That rule is only as good as its enforcement, so it is a build step rather than
a convention. `report-maker check` reads the Typst source and the bibliography
and reports every place the rule is broken, with a file:line to jump to.

Errors fail the build. Warnings do not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import diagrams
from .config import Config
from .workspace import Report, reports

FIGURE_HELPERS = ("srcfig", "srcimage", "diagram")


@dataclass
class Finding:
    level: str  # "error" | "warning"
    code: str
    path: Path
    line: int
    message: str

    def format(self, root: Path) -> str:
        where = f"{self.path.relative_to(root)}:{self.line}"
        return f"  {self.level:<7} {self.code}  {where}  {self.message}"


# ── source scrubbing ─────────────────────────────────────────────────────────
#
# Comments and code blocks are stripped before scanning, but replaced with
# spaces rather than deleted, so every offset still maps to its original line.


def _blank(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def scrub(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", _blank, src, flags=re.S)
    src = re.sub(r"(?<!:)//[^\n]*", _blank, src)  # not https://…
    src = re.sub(r"```.*?```", _blank, src, flags=re.S)
    src = re.sub(r"`[^`\n]*`", _blank, src)
    return src


def line_of(src: str, index: int) -> int:
    return src.count("\n", 0, index) + 1


def call_span(src: str, open_paren: int) -> tuple[int, int]:
    """Offsets of a call's argument list, from `(` to its matching `)`."""
    depth = 0
    i = open_paren
    in_string = False
    while i < len(src):
        char = src[i]
        if in_string:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return open_paren, i + 1
        i += 1
    return open_paren, len(src)


def calls(src: str, name: str) -> list[tuple[int, int, str]]:
    """Every `name(...)` call: (start, end, argument text)."""
    found = []
    for match in re.finditer(rf"(?<![\w.-]){re.escape(name)}\s*\(", src):
        start, end = call_span(src, match.end() - 1)
        found.append((match.start(), end, src[start:end]))
    return found


# ── bibliography ─────────────────────────────────────────────────────────────


def bib_keys(path: Path) -> set[str]:
    """Top-level keys of a Hayagriva file. No YAML dependency — the shape of the
    format makes a column-zero key unambiguous."""
    if not path.is_file():
        return set()
    return {
        match.group(1)
        for match in re.finditer(
            r"^([A-Za-z][\w.:+-]*):\s*$", path.read_text(encoding="utf-8"), re.M
        )
    }


def cited_keys(src: str) -> list[tuple[str, int]]:
    # Typst ends a `@key` reference before trailing punctuation, so `@page.` cites
    # `page` and prints the full stop. Match the same way, or every citation that
    # ends a sentence reads as undefined. A backslash escapes the marker — `\@djeed`
    # is the literal text of a social handle, not a citation.
    return [
        (match.group(1).rstrip(".:+-"), match.start())
        for match in re.finditer(r"(?<![\w@\\])@([A-Za-z][\w.:+-]*)", src)
    ]


def labels(src: str) -> set[str]:
    """Labels the document defines itself, as `… <fig-timing>`.

    Typst spells a cross-reference and a citation the same way: `@fig-timing`
    points at a figure in this document, `@djeed-home` points at a bibliography
    entry. Only the label set can tell them apart, so it has to be read before any
    `@key` is called undefined."""
    return {match.group(1) for match in re.finditer(r"<([A-Za-z][\w.:-]*)>", src)}


# ── rules ────────────────────────────────────────────────────────────────────


def check_report(cfg: Config, report: Report) -> list[Finding]:
    raw = report.main.read_text(encoding="utf-8")
    src = scrub(raw)
    main = report.main
    out: list[Finding] = []

    def add(level: str, code: str, index: int, message: str, path: Path | None = None) -> None:
        out.append(Finding(level, code, path or main, line_of(src, index), message))

    # E001 — the report must declare its bibliography.
    if not re.search(r"^\s*sources:\s*\"", src, re.M):
        add(
            "error",
            "E001",
            0,
            "no `sources:` passed to report.with(…) — a report without a "
            "bibliography cannot cite anything",
        )

    # E004/E005 — a figure without provenance.
    for helper in FIGURE_HELPERS:
        for start, _end, args in calls(src, helper):
            if not re.search(r"(?<![\w.-])source\s*:", args):
                add("error", "E004", start, f"{helper}(…) has no `source:`")
            if helper in ("srcimage", "diagram") and not re.search(
                r"(?<![\w.-])alt\s*:", args
            ):
                add("warning", "W003", start, f"{helper}(…) has no `alt:` text")

    helper_spans = [
        (start, end)
        for helper in FIGURE_HELPERS
        for start, end, _ in calls(src, helper)
    ]

    def inside_helper(index: int) -> bool:
        return any(start <= index < end for start, end in helper_spans)

    # E002/E003/W002 — the raw primitives bypass the source contract.
    for name, code, level, advice in (
        ("image", "E002", "error", "use srcimage(…) so the image carries a source"),
        ("figure", "E003", "error", "use srcfig(…) so the figure carries a source"),
        ("table", "W002", "warning", "wrap it in srcfig(…) unless it is inside one"),
    ):
        for start, _end, _args in calls(src, name):
            if inside_helper(start):
                continue
            add(level, code, start, f"bare {name}(…) — {advice}")

    # E006/W001 — citations and the bibliography must agree.
    keys = bib_keys(report.sources)
    defined = labels(raw)
    cited = cited_keys(src)
    for key, index in cited:
        if keys and key not in keys and key not in defined:
            add(
                "error",
                "E006",
                index,
                f"@{key} is not defined in {report.sources.name}",
            )
    used = {key for key, _ in cited} - defined
    for key in sorted(keys - used):
        out.append(
            Finding(
                "warning",
                "W001",
                report.sources,
                1,
                f"{key} is never cited (it still appears in References, which "
                "lists every reviewed source)",
            )
        )

    # E007 — a diagram whose SVG was never rendered would compile to nothing.
    for missing in diagrams.missing_svgs(report):
        out.append(
            Finding(
                "error",
                "E007",
                missing,
                1,
                "no rendered .svg — run `report-maker diagrams`",
            )
        )

    return out


def check(cfg: Config, target: str | None = None) -> list[Finding]:
    return [f for report in reports(cfg, target) for f in check_report(cfg, report)]


def report_findings(cfg: Config, findings: list[Finding]) -> int:
    """Print findings grouped by severity. Returns the process exit code."""
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    for finding in errors + warnings:
        print(finding.format(cfg.root))
    if not findings:
        print("  cited or opinion — no findings")
    else:
        print(f"\n  {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0
