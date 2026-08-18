"""The house rule, enforced.

Something is either cited, or it is an opinion. There is no third category, and
nothing in a report may sit in between:

    a fact about the world      carries a @key resolving to sources.yml
    a judgement or rating       ends with #assess, or sits inside assessment[…]
    a table, figure or image    goes through srcfig / srcimage / diagram, which
                                cannot be written without a `source:`
    a verbatim quotation        goes through srcquote, and must still be found,
                                word for word, in the archived copy of the page

That rule is only as good as its enforcement, so it is a build step rather than
a convention. `report-maker check` reads the Typst source and the bibliography
and reports every place the rule is broken, with a file:line to jump to.

The last row is the strongest one, and the only rule here that can catch a
sentence that *looks* sourced. A citation says a page exists; a quote checked
against `snapshots/<key>.txt` says the page said this. It only applies once a
report has an archive to check against — see the quote rules below.

Errors fail the build. Warnings do not.

The data rules — E010, E011 and W005–W009 — are defined in `data.py`, because
that module owns what a registered CSV is. They are *run* from here, so the
default path enforces them: `report-maker data check` is the narrow command for
asking about them alone, not the only place they fire. A checksum rule nothing
runs is decorative, and E011 is the rule that stands between a refreshed
spreadsheet and a signed-off report carrying the old number. They cost nothing
in a vault with no numbers in it: a report is only scanned when it has a `data/`
folder or a `srctable(` in its source.

## The truth rules

Everything above is a rule about *form*: is there a `source:`, does the `@key`
resolve, is the figure wrapped. Form is checkable and it is not enough. Three
rules here exist because a report can satisfy every one of the rules above and
still not be true:

**E012 — starter residue.** A freshly scaffolded report used to pass this file
clean, because the starter's example content is impeccably formed: the KPIs on
the cover are cited-or-marked, the example finding is severity-rated, and
`@example-page` resolves perfectly to a `sources.yml` entry pointing at
https://example.com/page. So a half-written report built to a branded PDF
carrying invented numbers and a *fabricated citation* — and for an engine whose
whole product claim is cited-or-opinion, a fabricated citation that passes the
linter is the worst failure available. It was one careless `report-maker new`
away. E012 is not a heuristic: `scaffold.py` resolves exactly which starter a
report came from, so this is a diff against a known file.

**E013 — a bare link that never became a source.** W001 catches a key nobody
cited; nothing caught the inverse, a citation that never became a key. A URL
dropped into prose or a footnote looks cited to a reader and is invisible to
everything that makes a citation mean anything here: the References section, the
snapshot archive, `verify`'s drift detection, and the density score. This is not
hypothetical — it is what happens when a bibliography is configured, then every
substantive claim is footnoted with a bare link anyway, because the footnote was
the shorter path at the moment of writing. Nobody chooses that, which is why it
has to be an error rather than a paragraph of documentation.

**`status:` and the final gate.** The two above make the linter stricter, and a
stricter linter that cannot be told "I know, I am not finished" is a linter
people run with `--warn-only`. So a report may declare `status:`, and while it
says `draft` its errors are reported as warnings and `check` exits 0. The other
half is the valuable one: `final` is *refused* while any error stands. That is
what turns this file from a build obstacle into the definition of finished. A
report with no `status:` behaves exactly as it did before, and `status:` never
suppresses anything in a report that calls itself final.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path

from . import diagrams, snapshot, sources
from .config import Config
from .workspace import (
    FIELDS,
    STATUS_PATTERN,
    STATUSES,
    Report,
    reports,
    status_in,
)

FIGURE_HELPERS = ("srcfig", "srcimage", "diagram")

# Helpers that quote a source rather than reproduce a figure. Both may carry a
# `locator:`, and a locator is a promise that the words can be found at a known
# place in an archived page — which is what E008 checks.
QUOTE_HELPERS = ("srcquote", "claim")

# How close a span of the snapshot has to be before it is worth showing back to
# the writer as "did you mean this?". Below it, the near miss is noise.
QUOTE_MATCH_FLOOR = 0.75

# The calls a starter uses to put a *specific* number, rating, id or quotation on
# the page. Prose is deliberately not on this list: a report legitimately keeps a
# heading called "Scope and method", and a rule that flags it is a rule people
# learn to ignore. What cannot legitimately survive is an invented figure.
EXAMPLE_CALLS = ("kpis", "finding", "verdict", "claim", "scorecard")

# Named arguments whose value is a claim rather than a setting. Everything whose
# value is content (`[…]`) counts too — that is where a starter's example prose
# lives — but `severity: "high"` and `tone: "caution"` are vocabulary, and a
# report that keeps them has not thereby kept a fabrication.
#
# `id:` is left out for the same reason a bibliography key is (see
# `_residue_sources`): it is the writer's filing decision, not an assertion about
# the world. Every audit ever written numbers its first finding F-01, and a rule
# that reads that as residue would fire on a perfectly-written report — teaching
# people to start at F-02 to appease the linter, which is exactly the rule nobody
# keeps. Nothing is lost by it: a finding still carrying the starter's `title:`
# or its `evidence:`/`impact:`/`action:` prose is caught on those instead.
CONTENT_ARGS = frozenset({"title", "attribution", "caption"})

# The fields of a bibliography entry that assert something, most damning first.
# `type:` and `date:` are shape rather than substance, so an entry that keeps
# `type: Web` is not residue — but an entry still pointing at example.com is.
SOURCE_FIELDS = ("url", "title", "note")

# A slot the scaffolder fills in. A starter value containing one is not residue —
# `title: "{{title}}"` was always going to be replaced, and was.
SLOT = "{{"

# A placeholder that survived into a report. `{{…}}` is unambiguous. The angled
# form has to be narrow, because Typst spells a label `<references>` and a linter
# that calls a label a placeholder is worse than no linter: so it must contain a
# space, or be the shouted kind (`<TODO>`, `<YOUR NAME>`).
LEFTOVER = re.compile(
    r"\{\{[^}\n]*\}\}"
    r"|<[A-Za-z][A-Za-z0-9 _./-]*\s[A-Za-z0-9 _./-]*>"
    r"|<[A-Z][A-Z0-9_-]{2,}>"
)

# A URL sitting in prose. Stops at the delimiters Typst and Markdown put around
# one; trailing sentence punctuation is trimmed afterwards, because `…/page.`
# ends a sentence rather than naming a file.
PROSE_URL = re.compile(r"https?://[^\s\"'`<>\[\]{}()]+", re.I)

# Appended to an error that a `draft` report has downgraded, so the output says
# why it is a warning rather than leaving the reader to work it out.
DRAFT_NOTE = " (draft — an error once this report leaves draft)"


@dataclass
class Finding:
    level: str  # "error" | "warning"
    code: str
    path: Path
    line: int
    message: str
    # Which report the finding belongs to. Carried rather than derived later,
    # because a path only maps back to a report id if you already know the vault
    # layout, and `findings_json` should not have to guess.
    report: str = ""

    def format(self, root: Path) -> str:
        return f"  {self.level:<7} {self.code}  {relative(self.path, root)}:{self.line}  {self.message}"


def relative(path: Path, root: Path) -> str:
    """A path as the vault sees it: relative, POSIX, no leading `./`.

    A finding about a file outside the vault should not be possible, but a
    reporter that raises instead of printing would turn a surprise into a
    crash, so an outsider keeps its absolute path.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


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


# ── call arguments ───────────────────────────────────────────────────────────
#
# The rules above only ever ask whether an argument is *present*, which a regex
# answers. The quote rules need the argument's value — the string that was
# quoted, the key it was attributed to — so the list has to be split properly.
# Splitting is by hand rather than by regex because a value can be content
# (`[@key]`), another call, or a string with a comma inside it.

NAMED_ARG = re.compile(r"^\s*([A-Za-z][\w-]*)\s*:")

TYPST_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _split_args(args: str) -> list[str]:
    """The top-level comma-separated pieces of an argument list."""
    inner = args.strip()
    if inner.startswith("("):
        inner = inner[1:-1] if inner.endswith(")") else inner[1:]
    pieces: list[str] = []
    depth = 0
    in_string = False
    start = 0
    index = 0
    while index < len(inner):
        char = inner[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            pieces.append(inner[start:index])
            start = index + 1
        index += 1
    pieces.append(inner[start:])
    return [piece for piece in pieces if piece.strip()]


def arguments(args: str) -> tuple[list[str], dict[str, str]]:
    """A call's arguments as (positional, named), each still as source text."""
    positional: list[str] = []
    named: dict[str, str] = {}
    for piece in _split_args(args):
        match = NAMED_ARG.match(piece)
        if match:
            named[match.group(1)] = piece[match.end() :].strip()
        else:
            positional.append(piece.strip())
    return positional, named


def string_literal(text: str) -> str | None:
    """The value of a Typst string literal, or None when the text is not one.

    "Not one" covers content (`[…]`), a variable, a concatenation and an
    unterminated string alike. All of them mean the same thing to a caller: there
    is nothing here that can be compared against an archived page.
    """
    text = text.strip()
    if not text.startswith('"'):
        return None
    out: list[str] = []
    index = 1
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(TYPST_ESCAPES.get(text[index + 1], text[index + 1]))
            index += 2
            continue
        if char == '"':
            # Anything after the closing quote means this was an expression that
            # merely began with a string, so it is not a literal.
            return "".join(out) if not text[index + 1 :].strip() else None
        out.append(char)
        index += 1
    return None


# ── quotations and the archive ───────────────────────────────────────────────
#
# A quote is compared against the snapshot on a normalised form, because the
# differences that survive copy-and-paste are never the ones that matter: a page
# sets its apostrophes curly, wraps its lines somewhere else, and writes an em
# dash where the report writes a hyphen. Case is folded for the comparison but
# kept for display, so a near miss can be shown back in the words the page used.

FOLDING = str.maketrans(
    {
        **dict.fromkeys("‘’‚‛′", "'"),
        **dict.fromkeys("“”„«»″", '"'),
        **dict.fromkeys("‐‑‒–—―−", "-"),
        # Invisible characters a page picks up from its own typesetting. They are
        # not whitespace, so `split()` would leave them sitting inside a word.
        **dict.fromkeys("​‌‍﻿­", ""),
    }
)


def fold(text: str) -> str:
    """Whitespace collapsed, quotes and dashes unified. Still readable.

    `split()` already handles every kind of Unicode space, so the table above
    only has to name the characters that are not whitespace.
    """
    return " ".join(text.translate(FOLDING).split())


def quote_found(quote: str, text: str) -> bool:
    """Whether the page said this. Substring, not similarity: a quotation that
    only nearly matches is a misquotation, and the writer has to see it."""
    return fold(quote).casefold() in fold(text).casefold()


def closest_span(quote: str, text: str, floor: float = QUOTE_MATCH_FLOOR) -> str | None:
    """The span of `text` that most nearly says what `quote` says, if any is
    close enough to be worth printing.

    Windows are the length of the quote in words, which is what makes this
    affordable on a page-sized snapshot: one pass, with difflib's cheap upper
    bounds rejecting almost every window before the real ratio is computed.
    """
    words = fold(text).split()
    needle = fold(quote).casefold()
    size = max(1, len(needle.split()))
    matcher = SequenceMatcher(None, "", needle, autojunk=False)
    best: str | None = None
    best_ratio = floor
    for start in range(max(1, len(words) - size + 1)):
        window = " ".join(words[start : start + size])
        matcher.set_seq1(window.casefold())
        if matcher.real_quick_ratio() < best_ratio or matcher.quick_ratio() < best_ratio:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best, best_ratio = window, ratio
    # The window starts and ends on word boundaries, not on sentence ones, so it
    # tends to pick up the page's own quotation marks and the comma after them.
    # They are not part of what was said, and printing them inside the message's
    # own quotes reads as a bug.
    return best.strip(" \"'(),;") if best else None


def quotable_text(report: Report, key: str) -> str | None:
    """The archived text for a key, when there is text worth comparing against.

    An archived PDF or image leaves an empty `.txt` behind — the bytes are
    evidence, but there is nothing in them to match a sentence against. That is
    not a violation, so it reads the same as no snapshot here: E009 has nothing
    to say and says nothing.
    """
    text = snapshot.read_text(report, key)
    return text if text and text.strip() else None


# ── bibliography ─────────────────────────────────────────────────────────────


def bib_keys(path: Path) -> set[str]:
    """Top-level keys of a Hayagriva file.

    Delegated to `sources.keys`, which recognises a key by the same column-zero
    shape this function used to match itself. One reader means the linter and
    the sources panel can never disagree about what a key is.
    """
    return sources.keys(path)


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


# ── starter residue (E012) ───────────────────────────────────────────────────
#
# The comparison is against a known file, not against a guess. `Report.template_id`
# reads the design out of the report's own import line, and `scaffold._starter`
# walks the same `extends` lineage `report-maker new` walked when it copied the
# skeleton in — so "is this still the starter's text?" is a diff, and the answer
# is either yes or no rather than probably.
#
# Two things keep it quiet enough to be worth having. Values the scaffolder was
# always going to fill (`{{title}}`) are not residue, and neither is prose: only
# the values that assert something are compared.


def starter_for(cfg: Config, report: Report) -> Path | None:
    """The starter folder this report was scaffolded from, if it can be found.

    None on anything unresolvable — a design that has been deleted, renamed, or
    that never shipped a starter. A rule that cannot establish its baseline says
    nothing; a linter that raises because a template moved is a linter that gets
    switched off before it ever catches anything.
    """
    from . import scaffold, vault  # local: both read the vault, neither reads rules

    try:
        return scaffold._starter(cfg, vault.template(cfg, report.template_id()))
    except (vault.VaultError, OSError):
        return None


def _flat(text: str) -> str:
    """A value with its whitespace collapsed.

    Byte-identical is the idea, but re-indenting a block is not editing it, and a
    writer who reflowed the starter's KPIs without changing a number has changed
    nothing. Collapsing whitespace makes the comparison see that.
    """
    return " ".join(text.split())


def _short(text: str, limit: int = 56) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _meta_values(src: str) -> dict[str, tuple[str, int]]:
    """Each metadata field's string value, with the offset of its name.

    Same shape `workspace.Report.meta` reads, but it keeps the offset so a
    finding can point at the line rather than at the top of the file.
    """
    out: dict[str, tuple[str, int]] = {}
    pattern = re.compile(r'^[ \t]*([A-Za-z][\w-]*)[ \t]*:[ \t]*"((?:[^"\\]|\\.)*)"', re.M)
    for match in pattern.finditer(src):
        name = match.group(1)
        if name in FIELDS and name not in out:
            out[name] = (match.group(2).replace('\\"', '"'), match.start(1))
    return out


def _content_pieces(args: str) -> list[str]:
    """The arguments of a call that assert something, normalised for comparison.

    `source:` is left out on purpose: a residual `@example-page` is reported once,
    against the bibliography entry it resolves to, and reporting it twice would
    make the fabricated citation look like two problems instead of one.
    """
    positional, named = arguments(args)
    pieces = list(positional)
    for name, value in named.items():
        if name == "source":
            continue
        if name in CONTENT_ARGS or value.strip().startswith("["):
            pieces.append(f"{name}: {value}")
    return [_flat(piece) for piece in pieces if piece.strip() and SLOT not in piece]


def _residue_findings(cfg: Config, report: Report, src: str) -> list[Finding]:
    """E012, for one report. `src` is the scrubbed main.typ."""
    out: list[Finding] = []
    bib_text = (
        report.sources.read_text(encoding="utf-8", errors="replace")
        if report.sources.is_file()
        else ""
    )

    # Unfilled placeholders need no baseline: `{{title}}` in a built report is a
    # scaffolding failure whatever design it came from.
    for path, text in ((report.main, src), (report.sources, bib_text)):
        for match in LEFTOVER.finditer(text):
            out.append(
                Finding(
                    "error",
                    "E012",
                    path,
                    line_of(text, match.start()),
                    f"{match.group(0)} is an unfilled placeholder — write the "
                    "real value, or delete the line",
                    report.id,
                )
            )

    starter = starter_for(cfg, report)
    if starter is None:
        return out

    starter_main = starter / "main.typ"
    if starter_main.is_file():
        skeleton = scrub(starter_main.read_text(encoding="utf-8"))

        # Metadata: the cover's own assertions about the document.
        baseline = _meta_values(skeleton)
        for field, (value, index) in _meta_values(src).items():
            example = baseline.get(field, ("", 0))[0]
            if value and SLOT not in example and value == example:
                out.append(
                    Finding(
                        "error",
                        "E012",
                        report.main,
                        line_of(src, index),
                        f"{field}: is still the starter's text — replace it or "
                        "delete the field",
                        report.id,
                    )
                )

        # Example blocks: one finding per block, not one per number. A block the
        # writer has half-edited still fires, because a cover KPI nobody chose is
        # a fabrication whether or not the two beside it were replaced.
        for name in EXAMPLE_CALLS:
            examples = {
                piece
                for _s, _e, args in calls(skeleton, name)
                for piece in _content_pieces(args)
            }
            if not examples:
                continue
            for start, _end, args in calls(src, name):
                kept = [p for p in _content_pieces(args) if p in examples]
                if not kept:
                    continue
                out.append(
                    Finding(
                        "error",
                        "E012",
                        report.main,
                        line_of(src, start),
                        f"{name}(…) is still the starter's example "
                        f"({_short(kept[0])}) — replace it with this report's "
                        "own, or delete the block",
                        report.id,
                    )
                )

    starter_bib = starter / "sources.yml"
    if starter_bib.is_file() and bib_text:
        out += _residue_sources(report, bib_text, starter_bib)
    return out


def _residue_sources(report: Report, bib_text: str, starter_bib: Path) -> list[Finding]:
    """The half of E012 that matters most: a bibliography entry nobody wrote.

    `@example-page` resolving to https://example.com/page is a citation the
    linter has always been happy with and a reader would take at face value. It
    is the one residue that turns a half-finished report into a false one.

    The comparison is on values, never on the key. `example-page` is a perfectly
    good name for a real source, and a report that repointed the entry at a page
    it actually rests on has done the work — the name it filed it under is its
    own business. What no report may keep is the starter's URL or the starter's
    title, and one finding per entry is enough to say so.
    """
    out: list[Finding] = []
    baseline = sources.parse_text(starter_bib.read_text(encoding="utf-8"))
    for source in sources.parse_text(bib_text):
        for field in SOURCE_FIELDS:
            value = _flat(_field_text(source, field))
            if not value or SLOT in value:
                continue
            if not any(_flat(_field_text(other, field)) == value for other in baseline):
                continue
            out.append(
                Finding(
                    "error",
                    "E012",
                    report.sources,
                    source.line,
                    f"{source.key}: {field}: is still the starter's example "
                    f"({_short(value)}) — cite something this report actually "
                    "rests on, or delete the entry",
                    report.id,
                )
            )
            break
    return out


def _field_text(source: sources.Source, field: str) -> str:
    """One bibliography field as a flat string, whatever shape it was written in."""
    if field == "url":
        return source.url or ""
    if field == "title":
        return source.title
    value = source.fields.get(field)
    return value if isinstance(value, str) else ""


# ── links that never became sources (E013) ───────────────────────────────────
#
# The exact inverse of W001. A `#link("https://…")` is not a citation: it reaches
# no References entry, no snapshot, no drift check and no density score. It only
# *looks* like one, which is the whole problem.
#
# `#link(<label>)` is untouched — a cross-reference is not a citation, and the
# two are only distinguishable because Typst spells them differently here. So is
# the brand pack's own `org.url`, which is page furniture on the same footing as
# the logo, and anything inside a comment or a code block, which `scrub` has
# already blanked.


def normal_url(url: str) -> str:
    """A URL reduced to what two spellings of the same page have in common."""
    text = re.sub(r"(?i)^[a-z][a-z0-9+.-]*://", "", url.strip())
    text = re.sub(r"(?i)^www\.", "", text)
    return text.rstrip("/").casefold()


def _brand_url(cfg: Config, report: Report) -> str:
    """The organisation's own address, normalised, or `""` if it cannot be read."""
    from . import brand, vault  # local: brand reads the vault, the vault reads no rules

    try:
        pack = vault.template(cfg, report.template_id()).brand_pack
        org = brand.load(cfg, pack).get("org") or {}
        return normal_url(str(org.get("url") or ""))
    except Exception:  # noqa: BLE001 — a malformed brand pack is not a citation fault
        return ""


def _link_findings(cfg: Config, report: Report, src: str) -> list[Finding]:
    """E013, for one report. `src` is the scrubbed main.typ."""
    registered = {
        normal_url(source.url)
        for source in sources.parse(report.sources)
        if source.url
    }
    exempt = (registered | {_brand_url(cfg, report)}) - {""}

    out: list[Finding] = []
    seen: set[str] = set()
    for match in PROSE_URL.finditer(src):
        url = match.group(0).rstrip(".,;:!?")
        key = normal_url(url)
        if key in exempt or key in seen:
            continue
        seen.add(key)
        out.append(
            Finding(
                "error",
                "E013",
                report.main,
                line_of(src, match.start()),
                f"{url} is linked but never became a source — a bare URL reaches "
                f"no References entry, no snapshot and no drift check; run "
                f"`report-maker cite {report.id} {url}`, then cite it with @key",
                report.id,
            )
        )
    return out


# ── the numbers a table reads (E010, E011, W005–W009) ────────────────────────
#
# `data.py` owns these rules and `data.to_findings` was written to hand them
# back in this module's record type. All that is needed here is to ask.


def _data_findings(report: Report, src: str) -> list[Finding]:
    """The data rules, converted, for a report that has numbers to check."""
    from . import data  # local: data reads this module's scanner lazily

    if not (data.data_dir(report).is_dir() or f"{data.CALL}(" in src):
        return []
    return [
        replace(finding, report=report.id)
        for finding in data.to_findings(data.findings(report))
    ]


# ── rules ────────────────────────────────────────────────────────────────────


def check_report(cfg: Config, report: Report) -> list[Finding]:
    raw = report.main.read_text(encoding="utf-8")
    src = scrub(raw)
    main = report.main
    out: list[Finding] = []

    def add(level: str, code: str, index: int, message: str, path: Path | None = None) -> None:
        out.append(
            Finding(level, code, path or main, line_of(src, index), message, report.id)
        )

    # E001 — the report must declare its bibliography.
    if not re.search(r"^\s*sources:\s*\"", src, re.M):
        add(
            "error",
            "E001",
            0,
            "no `sources:` passed to report.with(…) — a report without a "
            "bibliography cannot cite anything",
        )

    # E004/E005 — a figure, or a quotation, without provenance.
    for helper in (*FIGURE_HELPERS, "srcquote"):
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
                report.id,
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
                report.id,
            )
        )

    # W004 — a quotation that does not say where in the source it came from.
    # This one holds whether or not the page was ever archived: "they said it
    # somewhere on the site" is not a citation a reader can follow.
    for start, _end, args in calls(src, "srcquote"):
        if "locator" not in arguments(args)[1]:
            add(
                "warning",
                "W004",
                start,
                "srcquote(…) has no `locator:` — name the page, section, heading "
                "or timestamp the words sit at",
            )

    # E008/E009 — a quotation checked against the archived page.
    #
    # Only once the report has a snapshots/ folder. A vault that has never run
    # `report-maker cite` has nothing to check against, and a linter that turns
    # a whole existing vault red is a linter people switch off. The first
    # snapshot in a report turns both rules on for that report.
    if snapshot.dir_for(report).is_dir():
        out += _quote_findings(report, src, defined)

    # E012/E013 — the two rules about truth rather than form.
    out += _residue_findings(cfg, report, src)
    out += _link_findings(cfg, report, src)

    # E010/E011/W005–W009 — the data rules. They live in `data.py`, for the same
    # reason W010 lives in `score.py`: one module owns the definition. What they
    # must not do is live *only* there. E011 is the rule that catches a
    # spreadsheet moving under a report that already quotes it, and a rule
    # reachable only from `report-maker data check` fires from nothing — the
    # default path stays green and the PDF rebuilds with the new number.
    #
    # The guard keeps the promise in README.md: a vault with no CSV never pays
    # for scanning one. `data/` present means there are numbers to check; a
    # `srctable(` in the source means the report claims to read some, and E010
    # has to be able to say the file is not there.
    out += _data_findings(report, src)

    # W010 — depth rather than density: a load-bearing passage whose every
    # citation resolves to one source family. The rule lives in `score.py`, where
    # the definition of a family already is, for the same reason the data rules
    # live in `data.py` — a second copy here would be a second answer to the
    # question of who a document rests on. Imported inside the function because
    # `score` imports this module at module scope.
    from . import score as depth

    out += depth.family_findings(report)

    # W011 — a status nobody recognises. It is treated as unstated, which is the
    # safe direction: a typo must never hand a report the leniency of `draft`.
    # (W007–W009 are data.py's column rules and W010 is score.py's; the warning
    # codespace is shared across every module that reports findings.)
    stated = STATUS_PATTERN.search(src)
    status = status_in(src)
    if stated and not status:
        add(
            "warning",
            "W011",
            stated.start(),
            f'status: "{stated.group(1).strip()}" is not one of '
            f"{', '.join(STATUSES)} — read as if the field were absent",
        )

    return _gate(report, out, status, stated.start() if stated else 0, src)


def _gate(
    report: Report, out: list[Finding], status: str, index: int, src: str
) -> list[Finding]:
    """What a report's declared status does to its findings.

    `draft` is a writer saying "I know". Its errors are still found, still
    printed and still say what is wrong — they are reported as warnings, so an
    unfinished report is not a broken build. That is the price of the two rules
    above being strict enough to be worth having.

    `final` is the report making a claim about itself, and this is the half that
    earns the field: a document that says it is finished while the rule is broken
    is asserting something untrue, so the refusal is itself an error. Nothing is
    ever downgraded here — a `final` report keeps every error it has.

    Anything else, including no status at all, behaves exactly as it always did.
    """
    if status == "draft":
        return [
            f
            if f.level != "error"
            else replace(f, level="warning", message=f.message + DRAFT_NOTE)
            for f in out
        ]
    errors = sum(1 for f in out if f.level == "error")
    if status == "final" and errors:
        out = out + [
            Finding(
                "error",
                "E014",
                report.main,
                line_of(src, index),
                f'status: "final" is refused while {errors} error(s) stand — fix '
                "them, or say draft until they are fixed",
                report.id,
            )
        ]
    return out


def _quote_findings(report: Report, src: str, defined: set[str]) -> list[Finding]:
    """E008 and E009, for a report that has an archive to check against."""
    out: list[Finding] = []

    def keys_of(args_named: dict[str, str]) -> list[str]:
        # Only real citations: a `@fig-one` inside `source:` points at a figure
        # in this document, and has no page behind it to archive.
        return [
            key
            for key, _ in cited_keys(args_named.get("source", ""))
            if key not in defined
        ]

    # E008 — a locator promises the words can be found at a known place. Without
    # the archive, the promise rests on the page still saying what it said.
    for helper in QUOTE_HELPERS:
        for start, _end, args in calls(src, helper):
            named = arguments(args)[1]
            if "locator" not in named:
                continue
            for key in keys_of(named):
                if snapshot.read_record(report, key) is None:
                    out.append(
                        Finding(
                            "error",
                            "E008",
                            report.main,
                            line_of(src, start),
                            f"@{key} has no snapshot — run report-maker cite --refresh",
                            report.id,
                        )
                    )

    # E009 — the load-bearing one: the archive has to actually say it.
    for start, _end, args in calls(src, "srcquote"):
        positional, named = arguments(args)
        # `quote` is the first positional argument, but Typst will take it by
        # name too, and a rule with a spelling that skips it is not a rule.
        written = named.get("quote") or (positional[0] if positional else "")
        quote = string_literal(written) if written else None
        if not quote or not quote.strip():
            # Not a string literal, so there is nothing to compare. The `assert`
            # in srcquote(…) itself fails the build on that, which is the right
            # place for it — the linter should not have to evaluate Typst.
            continue
        archived = [
            (key, text)
            for key in keys_of(named)
            if (text := quotable_text(report, key)) is not None
        ]
        if not archived or any(quote_found(quote, text) for _, text in archived):
            continue
        key, text = archived[0]
        near = closest_span(quote, text)
        detail = f' — closest text in the snapshot: "{near}"' if near else ""
        # Named by the file, not by the key: snapshot.py maps characters a key
        # may legally carry but a filesystem may not, and the message has to name
        # something the reader can actually open.
        where = snapshot.text_path(report, key).name
        out.append(
            Finding(
                "error",
                "E009",
                report.main,
                line_of(src, start),
                f"the quoted words are not in snapshots/{where}{detail}",
                report.id,
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


# ── JSON, for the app ────────────────────────────────────────────────────────


def findings_json(
    cfg: Config, findings: list[Finding], *, score: dict | None = None
) -> dict:
    """The same findings the human output prints, as `check --json` prints them.

    Paths are vault-relative POSIX, because that is the only form that survives
    the trip to another process: the app hands them straight back to a file
    channel that refuses anything outside the vault. The counts are included
    rather than left to the reader so a status bar never has to walk the list.
    """
    payload: dict = {
        "vault": str(cfg.root),
        "errors": sum(1 for f in findings if f.level == "error"),
        "warnings": sum(1 for f in findings if f.level == "warning"),
        "findings": [
            {
                "level": finding.level,
                "code": finding.code,
                "path": relative(finding.path, cfg.root),
                "line": finding.line,
                "message": finding.message,
                "report": finding.report,
            }
            for finding in findings
        ],
    }
    if score is not None:
        payload["score"] = score
    return payload
