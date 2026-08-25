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

**E015 — a symlink out of the vault.** The three rules above are about what a
report *says*. This one is about what a report *carries*. Typst's sandbox is
`--root`, which is always the vault, and it will not compile `read("../../etc/
passwd")` — but a symlink named `leakdir` pointing at `/etc` is not a `..`, and
`read("/leakdir/passwd")` compiles and typesets the file into the PDF. The
sandbox holds only while no link inside a report folder points out of the vault,
so that has to be a checked property rather than an assumption. A link pointing
*within* the vault is not a finding: sharing one diagram between two reports is a
legitimate thing to do, and typst could read the target through its real path
anyway.

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

import errno
import os
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


def _realpath(path: Path) -> Path:
    """`path` with its symlinks followed, and never an exception.

    `Path.resolve()` is the obvious call and it is the wrong one, because what
    it does to a symlink loop depends on the interpreter: up to Python 3.12 a
    non-strict `resolve()` follows itself up with a `stat()` and turns the
    kernel's `ELOOP` into `RuntimeError("Symlink loop from …")`, while 3.13 and
    later return the path with the loop left unresolved. A rule that walks a
    folder of links somebody else wrote cannot give two different answers on two
    Pythons, and it certainly cannot raise: a linter that dies on the input it
    was written to inspect fails the whole build for the wrong reason, and the
    traceback names neither the link nor the report.

    `os.path.realpath` is the same resolution with neither behaviour. Non-strict
    it swallows every `OSError` and returns as far as it got, identically on
    every version this engine supports. What it will not tell us is *why* it
    stopped, which is `_unfollowable`'s job.
    """
    try:
        return Path(os.path.realpath(path))
    except (OSError, ValueError):  # pragma: no cover — a path with a NUL in it
        # Even a path this process cannot normalise has to be printable, since
        # the only reason we are holding it is to say something about it.
        return path


def relative(path: Path, root: Path) -> str:
    """A path as the vault sees it: relative, POSIX, no leading `./`.

    A finding about a file outside the vault should not be possible, but a
    reporter that raises instead of printing would turn a surprise into a
    crash, so an outsider keeps its absolute path. That sentence was an
    intention until the resolution moved to `_realpath`: this function is handed
    the path of a link somebody else made, and `Path.resolve()` on a looping one
    raises — which is how E015 came to crash `check` on the folder it was
    inspecting rather than report on it.

    The lexical fallback exists for exactly one caller: an E015 finding is
    *about* a symlink, so resolving it lands on the target — outside the vault,
    by definition of the rule — and the finding about a file sitting in
    `reports/…` would print with an absolute path. The file is in the vault; only
    what it points at is not. Nothing else can reach this branch, because
    anything whose real path is inside the root is answered above.
    """
    real_root = _realpath(root)
    for candidate in (_realpath(path), path):
        try:
            return candidate.relative_to(real_root).as_posix()
        except ValueError:
            continue
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


# ── links out of the vault (E015) ─────────────────────────────────────────────
#
# Every other rule in this file reads a report's source. This one reads its
# *folder*, because the thing it is looking for is not written anywhere in the
# prose.
#
# Typst is the sandbox the whole build relies on: it cannot reach the network or
# the shell, and it reads only under `--root`, which `build.py` and `pages.py`
# always set to the vault. It refuses `read("../../etc/passwd")` and
# `read("/../etc/passwd")` with "would escape the project root" — probed, not
# read off the source. What it does not refuse is a symlink. A link named
# `leakdir` inside a report folder, pointing at `/etc`, makes
# `#raw(read("/leakdir/passwd"))` compile, and the file lands in the PDF.
#
# Nothing in this engine creates such a link, and the callers that could have
# each shut the door their own way — the web layer refuses to resolve a request
# path through a link, and its `git` invocations carry `core.symlinks=false`.
# That is three separate promises holding one property, and a property held by
# three promises is held by none of them. This makes it one checked fact.
#
# **Error, not warning.** Every other error here is about the report's argument
# being wrong, and the person who suffers is the reader, who can see the report
# and judge it. This one is about a file the author never chose to publish
# appearing in a document they are about to send someone — a `.env`, an SSH key,
# `/etc/passwd`. The reader cannot detect it, the author may not have written the
# link, and a warning does not fail a build, which means the PDF ships. There is
# also no legitimate report that needs one: a link within the vault is allowed,
# and anything outside it belongs inside the folder, since the folder is the unit
# that gets zipped and handed over and a link would arrive dead anyway.
#
# **What is deliberately not a finding:** a link that stays inside the vault.
# Sharing a diagram or a CSV between two reports by symlink is a reasonable thing
# to do, and typst can read the target through its real path regardless, so
# refusing it would buy nothing and cost somebody a working vault.
#
# **Judged by where it points, not by whether the target exists.** A dangling
# link out of the vault is one `mkdir` away from being a live one, and the report
# folder travels — the link that resolves to nothing here resolves to something
# on the machine it is opened on. So `/etc/nonexistent` is a finding and a
# dangling link to a sibling report is not.
#
# **A loop is not a finding at all.** `loop -> loop`, or `a -> b -> a`, names no
# place: resolution does not end somewhere the rule can hold against the root, it
# does not end. The sentence above is what settles it — a link is judged by where
# it points, a dangling one points outside the vault and a looping one points
# nowhere, and there is no file at the end of it to typeset on any machine this
# folder is ever opened on. Reporting E015 there would say a file nobody chose to
# publish is about to appear in a PDF when none can, and E015 is the one code
# `draft` cannot soften, so a silly link would hard-fail a build with a security
# story that is not true. A rule whose count cannot be trusted is worse than the
# rule not existing, which is the same reason the crash it replaced was worse.
#
# The case against silence is real and it is not this rule's: a loop can never
# come good, and typst reading one fails with an `ELOOP` naming a path and no
# report. But that is folder hygiene, it fails loudly in `build` on the report
# that read it, and the code that means "a file is leaking" is the wrong place to
# smuggle it. If the vault wants a hygiene pass it should get one, with its own
# code, rather than borrowing the meaning of this one.
#
# A loop is also the only unfollowable link that gets this answer. Anything else
# the process cannot follow — a permission missing along the chain, a name too
# long — leaves the target genuinely unknown, and an unknown target is not a
# demonstrated one, so it stays an error.
#
# **A detector, not the control.** `check` runs after `build` in `all`, and a
# hostile author can reach for `status: "draft"`. This rule does not stop a
# writer determined to read their own server's disk; the controls that do are the
# ones at the doors where a link could be created, and they stay. What it does
# catch is the case where the author is the victim: a vault cloned, a report
# folder unzipped, a repository handed over. That folder's `status:` field was
# written by whoever sent it, which is why E015 is the one code `draft` does not
# soften — see `_gate`.


#: Codes a `draft` declaration does not downgrade. Everything else in this file
#: is the writer saying "I know, about my own argument", and they are the one who
#: fixes it. E015 is a property of a folder that travels between people, so the
#: `status:` beside it is not necessarily the word of the person now reading it.
UNDOWNGRADABLE = frozenset({"E015"})


def _outside(target: Path, root: Path) -> bool:
    """Whether `target` sits outside `root`, both already resolved.

    Compared by path components rather than by string prefix, so a vault at
    `…/vault` does not silently contain `…/vault-evil`.
    """
    return not (target == root or root in target.parents)


def _unfollowable(link: Path) -> int | None:
    """The errno the OS gives for following `link`, or `None` if it follows now.

    Asked only about a link `_realpath` gave up on, and only to tell a loop from
    everything else — the two get opposite answers here, and no resolver reports
    which one it hit. `stat` follows the link and so answers for the whole chain,
    which is the question: one loop anywhere in it and nothing comes back.
    """
    try:
        link.stat()
    except OSError as exc:
        return exc.errno
    return None  # pragma: no cover — the link was repaired mid-walk


def _symlink_findings(report: Report, root: Path) -> list[Finding]:
    """E015 — every symlink in the report folder whose target leaves the vault.

    `os.walk` does not follow links (`followlinks=False` is its default and is
    passed here anyway, because this is the one call site where the default is
    load-bearing). A link to a directory therefore arrives in `dirnames` and is
    reported without being descended into — which is both what the rule wants and
    what stops a link to `/` from walking the disk.

    Resolution is per link and cannot raise, so no single link can end the walk
    and leave the rest of the folder uninspected. Three outcomes: it resolves,
    and the rule holds the target against the root; it loops, and points nowhere
    at all, which is not a finding (see the section comment above); or it cannot
    be followed for some other reason, and an unknown target is an error.
    """
    out: list[Finding] = []
    root = _realpath(root)

    for parent, dirnames, filenames in os.walk(report.folder, followlinks=False):
        base = Path(parent)
        for name in sorted(dirnames) + sorted(filenames):
            link = base / name
            if not link.is_symlink():
                continue
            here = relative(link, root)  # for the read() the message shows
            # Each link is resolved on its own, and nothing here may raise: the
            # walk is over a folder of links this engine did not create, so one
            # bad link is an ordinary input, not the end of the inspection.
            target = _realpath(link)
            if target.is_symlink():
                # `_realpath` stopped on a link it could not follow, so `target`
                # still ends in one and names no place to hold against the root.
                # Why it stopped decides everything, and only the OS knows.
                reason = _unfollowable(link)
                if reason == errno.ELOOP:
                    continue  # points nowhere, so it points nowhere outside
                why = os.strerror(reason) if reason else "reason unknown"
                out.append(
                    Finding(
                        "error",
                        "E015",
                        link,
                        1,
                        f"a symlink whose target cannot be resolved ({why}) — a "
                        "link that cannot be shown to stay inside the vault. "
                        "Delete it, or replace it with a copy of what it "
                        "pointed at",
                        report.id,
                    )
                )
                continue
            if not _outside(target, root):
                continue
            out.append(
                Finding(
                    "error",
                    "E015",
                    link,
                    1,
                    f"a symlink to {target}, outside the vault. typst "
                    f'follows it, so read("/{here}{"/…" if link.is_dir() else ""}") '
                    "compiles and typesets that "
                    "file into the PDF — a file nobody chose to publish. Delete "
                    "the link, or copy what you need into the report folder",
                    report.id,
                )
            )
    return out


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

    # E015 — the report folder's own links. Not a rule about the source, so it
    # is asked of the folder rather than of `src`.
    out += _symlink_findings(report, cfg.root)

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

    One code is outside all of it. `draft` is a *writer* saying "I know" about
    their own unfinished argument, and every rule it softens is one that writer
    is the right person to fix. E015 is not about the argument — it is a property
    of a folder that gets zipped and handed over, so the `status:` line sitting
    beside it may have been written by somebody else entirely. A rule a stranger
    can switch off by putting one word in the file it travels with is not a rule.
    See `UNDOWNGRADABLE`.
    """
    if status == "draft":
        return [
            f
            if f.level != "error" or f.code in UNDOWNGRADABLE
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
