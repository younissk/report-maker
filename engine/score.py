"""How much of a report rests on evidence, line by line.

`check` answers a yes/no question: is the citation rule broken anywhere. That is
the gate, and it is deliberately silent about a report that breaks no rule while
still saying very little. This module answers the other question — of the prose a
person actually wrote, how much carries a citation, how much is marked as
judgement, and how much is neither.

Classification runs on the same scrubbed source the linter reads and on the same
definition of a citation, so the two can never disagree about what a `@key` is. A
*statement* is a sentence-ish run of prose outside any helper call, heading,
import, `#let` or `#show`, and every statement lands in one of three classes:

    cited      carries a @key that resolves to sources.yml
    assessed   carries #assess, or sits inside assessment[…]
    unmarked   neither — the sentence the house rule exists to catch

Text inside a helper is not a statement, because the helpers already carry their
provenance structurally: `srcfig`, `srcimage` and `diagram` cannot be written
without a `source:`, and `check` fails the build when they are. Density measures
the prose where the rule can be broken *quietly*.

Nothing here fails a build. A thin draft is a fact about a draft, not an error,
and a number is only useful if reading it is free — so `report_scores` always
exits 0.

The per-line classification is the other half of the module. The app paints an
evidence rail down the edge of the editor from `lines`, so `lines` covers every
line of the file, in order, with no gaps, and a line carrying two statements of
different classes takes the worse of the two. A rail that hides the unmarked half
of a line is a rail that lies.

## Depth, which density cannot see

A count of citations flatters a document that has only one source. A section can
be 100% cited and rest entirely on a single domain; three `@key`s that are one
party's own account read, in the table, exactly like three that are independent.
For an audit — where the document *is* the argument — that is the difference
between a finding somebody has to answer and a finding somebody can wave away.

So every bibliography entry also gets a **source family**: the registrable domain
when it has a URL, the publisher or author otherwise. `citations` resolves every
`@key` in the body to one, and the families behind the report, behind each
section and behind each `#finding(…)` or `#assessment` block are counted
alongside the density. `W010` is where that becomes a finding — a passage
carrying enough citations to be load-bearing, every one of which lands on the
same family. Everything else is reported as a number, because a report with two
families is not broken, it is shallower than one with six, and the reader is
entitled to see which.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import check, sources
from .config import Config
from .workspace import Report, reports

# The four classes, and how they rank against each other. Worse wins when two
# statements share a line, because the rail is a warning device: a line that is
# half unmarked has to read as unmarked.
NEUTRAL = "neutral"
CITED = "cited"
ASSESSED = "assessed"
UNMARKED = "unmarked"

SEVERITY = {NEUTRAL: 0, CITED: 1, ASSESSED: 2, UNMARKED: 3}

# Structure, not prose. A directive runs to the end of its statement; a `#name(…)`
# call runs to its matching paren.
DIRECTIVES = ("import", "include", "let", "show", "set")

DIRECTIVE = re.compile(r"#(?:" + "|".join(DIRECTIVES) + r")\b")

# Only a `#`-prefixed call is a call. In Typst markup `foo(bar)` is literal text,
# and treating it as a call would swallow every parenthetical aside in the prose
# — "revenue (see appendix)" would stop being a statement.
CALL = re.compile(r"#([a-zA-Z_][\w-]*(?:\.[a-zA-Z_][\w-]*)*)\(")

# `assessment[…]` and `#assessment(…)` mark their contents rather than hiding
# them, so they are the one call whose span is not structure.
ASSESSMENT = re.compile(r"(?<![\w.-])#?assessment[ \t]*[\[(]")

ASSESS = re.compile(r"#assess\b")

# A bare marker: `#assess`, `#lede[`, `#pagebreak`. Not blanked out of the text —
# sentence splitting has to see it, because `#assess` sits *after* the full stop
# it belongs to — but ignored when asking whether a line holds any prose.
MARKER = re.compile(r"#[a-zA-Z_][\w-]*")

# A markup heading, at the start of a line. `#heading(…)` is found through CALL.
HEADING = re.compile(r"^[ \t]*(=+)[ \t]+(\S.*?)[ \t]*$", re.M)

LABEL = re.compile(r"<[A-Za-z][\w.:-]*>")

# A word, for deciding whether a run is prose at all. Labels and markers are
# stripped before counting, so `<references>` and a lone `]` fall below the bar
# while "It failed." — two words, one of them a single letter — stays above it.
# Erring high here would put holes in the rail.
WORD = re.compile(r"[A-Za-z]+")

MIN_WORDS = 2

# A sentence may also end against a `#`, because `#assess` is written hard up
# against the full stop it belongs to.
SENTENCE_END = re.compile(r"[.!?…]+(?=[\s\"'”’)\]#]|$)")

# A list item starts a new statement even without a full stop before it, so
# `+ Do the thing.#assess` on consecutive lines counts as two, not one.
LIST_ITEM = re.compile(r"^[ \t]*(?:[+\-/]|\d+\.)[ \t]+", re.M)

# A single audit finding — the document's claim about the world, and the block a
# reader acts on. Matched `#`-prefixed for the same reason CALL is: the word
# "finding(" can legitimately appear in prose.
FINDING = re.compile(r"(?<![\w.-])#finding[ \t]*\(")

# Where the party behind a source with no URL is named, in the order hayagriva
# means them. A publisher outranks an author because two papers by different
# authors from one institute are one account of the world, not two.
PARTY_FIELDS = ("publisher", "organization", "author")

# How many citations a section has to carry before resting on one family is worth
# saying out loud. One or two is an introduction, a definition, a note on method:
# a section that names a page and moves on makes no claim whose strength depends
# on who else agrees. By the third the section is being *used* as evidence, and
# whether that evidence comes from one party or three is a fact about how much
# the document proves. Lower, and every scoping paragraph that cites the brief
# would warn — which is how a linter gets switched off. Higher, and a
# three-source section resting entirely on the audited party's own site passes
# unremarked, which is the failure this rule exists for.
LOAD_BEARING = 3

# A finding is load-bearing at one citation. It is not prose that happens to
# carry evidence, it is the claim itself — and the case this whole rule was
# written for is a `severity: "high"` finding resting on a single page the
# audited party controls.
FINDING_LOAD_BEARING = 1


@dataclass(frozen=True)
class LineClass:
    line: int
    kind: str


@dataclass(frozen=True)
class Statement:
    """One sentence-ish run of prose, and what it turned out to be.

    Carried in full rather than reduced to a count, because the same extraction
    is what `diff` calls a claim and what the HTML export lists under a source.
    """

    kind: str
    line: int  # 1-based first line
    end_line: int  # 1-based last line — a statement may span several
    text: str
    keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class Citation:
    """One `@key` in the body, and the party it resolves to.

    Separate from `Statement.keys` because a family question is not a prose
    question: the `@key` inside `srcfig(…, source: [@acme-pricing])` is structure
    to the classifier and evidence to a reader, and a count of who the document
    rests on has to include it.
    """

    key: str
    family: str
    line: int
    index: int  # character offset in the scrubbed source


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    line: int


@dataclass
class ReportScore:
    id: str
    cited: int
    assessed: int
    unmarked: int
    density: float
    sections: list[dict] = field(default_factory=list)
    lines: list[LineClass] = field(default_factory=list)
    sources_total: int = 0
    sources_cited: int = 0
    # Depth. `families` is how many distinct parties the report rests on;
    # `family_counts` is how many citations each of them carries, heaviest first,
    # which is the number that says "and four of the five are the audited party".
    families: int = 0
    family_counts: dict[str, int] = field(default_factory=dict)
    blocks: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.cited + self.assessed + self.unmarked


# ── spans ────────────────────────────────────────────────────────────────────
#
# Everything below works in character offsets against the scrubbed source, which
# is the same length as the original, so `check.line_of` maps any offset back to
# a line in the file the person is editing.


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _statement_end(src: str, start: int) -> int:
    """Where a `#let` / `#show` / `#import` stops.

    At the end of its line, unless it has opened a bracket that has not closed:
    `#show: report.with(…)` spreads its arguments over twenty lines and every one
    of them is structure rather than prose.
    """
    depth = 0
    index = start
    in_string = False
    while index < len(src):
        char = src[index]
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
            depth = max(0, depth - 1)
        elif char == "\n" and depth == 0:
            return index
        index += 1
    return len(src)


def _inside(spans: Sequence[tuple[int, int]], index: int) -> bool:
    return any(start <= index < end for start, end in spans)


def _calls_and_directives(src: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in DIRECTIVE.finditer(src):
        spans.append((match.start(), _statement_end(src, match.end())))
    for match in CALL.finditer(src):
        if match.group(1).split(".")[0] == "assessment":
            continue  # marks its contents, does not hide them
        _, end = check.call_span(src, match.end() - 1)
        spans.append((match.start(), end))
    return _merge(spans)


def _assessment_spans(src: str) -> list[tuple[int, int]]:
    return _merge(
        [check.call_span(src, match.end() - 1) for match in ASSESSMENT.finditer(src)]
    )


def headings(src: str, structure: Sequence[tuple[int, int]] = ()) -> list[Heading]:
    """Every heading, in document order, with the line it starts on.

    Both spellings count: `== Findings` and `#heading(level: 2)[Findings]`. A `=`
    inside a helper call is not a heading, which is why the structural spans are
    passed in.
    """
    found: list[Heading] = []
    for match in HEADING.finditer(src):
        if _inside(structure, match.start()):
            continue
        found.append(
            Heading(
                level=len(match.group(1)),
                title=_clean_title(match.group(2)),
                line=check.line_of(src, match.start()),
            )
        )
    for match in CALL.finditer(src):
        if match.group(1) != "heading":
            continue
        start, end = check.call_span(src, match.end() - 1)
        args = src[start:end]
        level = re.search(r"(?<![\w.-])level\s*:\s*(\d+)", args)
        found.append(
            Heading(
                level=int(level.group(1)) if level else 1,
                title=_clean_title(_content_after(src, end)),
                line=check.line_of(src, match.start()),
            )
        )
    return sorted(found, key=lambda heading: heading.line)


def _content_after(src: str, index: int) -> str:
    """The `[…]` block a call is applied to, as in `#heading(level: 2)[Title]`."""
    if src[index : index + 1] != "[":
        return ""
    start, end = check.call_span(src, index)
    return src[start + 1 : max(start + 1, end - 1)]


def _clean_title(text: str) -> str:
    text = LABEL.sub("", text)
    text = MARKER.sub("", text)
    return re.sub(r"\s+", " ", text.strip(" \t*_[]")).strip()


# ── statements ───────────────────────────────────────────────────────────────


def _is_break(line: str) -> bool:
    """True when a line holds nothing a reader would call prose.

    Markers and the brackets left behind by a blanked call do not count: after
    `#callout(kind: "method")[` loses its call, the trailing `[` is not a
    paragraph of its own.
    """
    return not re.sub(r"[\s\[\](){}]", "", MARKER.sub("", LABEL.sub("", line)))


def _paragraphs(blanked: str) -> list[tuple[int, int]]:
    """Character spans of maximal runs of lines that still hold something.

    A blank line ends a paragraph in Typst, and a call blanked to whitespace
    leaves blank lines behind — which is exactly right, since a `#finding(…)`
    block separates the prose above it from the prose below.
    """
    spans: list[tuple[int, int]] = []
    offset = 0
    open_at: int | None = None
    last_end = 0
    for line in blanked.splitlines(keepends=True):
        end = offset + len(line.rstrip("\r\n"))
        if _is_break(line):
            if open_at is not None:
                spans.append((open_at, last_end))
                open_at = None
        else:
            if open_at is None:
                open_at = offset
            last_end = end
        offset += len(line)
    if open_at is not None:
        spans.append((open_at, last_end))
    return spans


def _splits(text: str, start: int) -> list[int]:
    """Offsets at which a paragraph breaks into statements."""
    points: list[int] = []
    for match in SENTENCE_END.finditer(text):
        end = match.end()
        # `#assess` sits after the full stop it belongs to. Keep it inside the
        # sentence it marks, or every assessed sentence would read as unmarked
        # and the marker would attach to the next one.
        while True:
            trailing = re.match(r"[ \t]*#assess\b", text[end:])
            if not trailing:
                break
            end += trailing.end()
        points.append(start + end)
    for match in LIST_ITEM.finditer(text):
        if match.start() > 0:
            points.append(start + match.start())
    return sorted(set(points))


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    """Shrink a span onto its first and last non-space character, so a sentence
    ending before two blank lines does not paint them."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _is_citation(key: str, keys: set[str], labels: set[str]) -> bool:
    """Whether a `@key` is a citation rather than a cross-reference.

    The same test `check` applies before raising E006: a key the document defines
    as a label points at a figure here, and an empty bibliography accuses nobody.
    """
    if key in labels:
        return False
    return not keys or key in keys


def statements(
    raw: str, *, keys: set[str] | None = None, labels: set[str] | None = None
) -> list[Statement]:
    """Every statement in a report body, classified.

    `keys` is the bibliography; leave it out and any `@key` counts, which is what
    the linter does with an empty `sources.yml`.
    """
    src = check.scrub(raw)
    known = keys if keys is not None else set()
    defined = labels if labels is not None else check.labels(raw)

    structure = _calls_and_directives(src)
    for heading in HEADING.finditer(src):
        if not _inside(structure, heading.start()):
            structure.append((heading.start(), heading.end()))
    structure = _merge(structure)

    assessed_spans = _assessment_spans(src)
    assess_marks = [match.start() for match in ASSESS.finditer(src)]
    citations = [
        (key, index)
        for key, index in check.cited_keys(src)
        if _is_citation(key, known, defined) and not _inside(structure, index)
    ]

    # Blank the structure to spaces, the way `check.scrub` blanks comments: the
    # text keeps its length, so every offset still maps to its original line.
    chars = list(src)
    for start, end in structure:
        for index in range(start, min(end, len(chars))):
            if chars[index] != "\n":
                chars[index] = " "
    blanked = "".join(chars)

    found: list[Statement] = []
    for para_start, para_end in _paragraphs(blanked):
        text = blanked[para_start:para_end]
        bounds = [para_start, *_splits(text, para_start), para_end]
        for first, last in zip(bounds, bounds[1:]):
            start, end = _trim(blanked, first, last)
            if start >= end:
                continue
            body = blanked[start:end]
            prose = MARKER.sub(" ", LABEL.sub(" ", body))
            if len(WORD.findall(prose)) < MIN_WORDS:
                continue  # pure markup — a label, a bracket, a stray marker
            cited = tuple(key for key, index in citations if start <= index < end)
            if cited:
                kind = CITED
            elif any(start <= index < end for index in assess_marks) or _inside(
                assessed_spans, start
            ):
                kind = ASSESSED
            else:
                kind = UNMARKED
            found.append(
                Statement(
                    kind=kind,
                    line=check.line_of(src, start),
                    end_line=check.line_of(src, end - 1),
                    text=re.sub(r"\s+", " ", raw[start:end]).strip(),
                    keys=cited,
                )
            )
    return found


def line_count(raw: str) -> int:
    """How many lines an editor shows for this text.

    One more than the newline count, so a file ending in a newline still has the
    empty last line the cursor can sit on. The rail is drawn per editor line, and
    a missing entry is a hole in it.
    """
    return raw.count("\n") + 1


def line_classes(raw: str, found: Sequence[Statement]) -> list[LineClass]:
    """One entry per line of the file, in order, worst class wins."""
    kinds = [NEUTRAL] * line_count(raw)
    for statement in found:
        for line in range(statement.line, statement.end_line + 1):
            if not 1 <= line <= len(kinds):
                continue
            if SEVERITY[statement.kind] > SEVERITY[kinds[line - 1]]:
                kinds[line - 1] = statement.kind
    return [LineClass(line=index + 1, kind=kind) for index, kind in enumerate(kinds)]


def _tally(found: Sequence[Statement]) -> tuple[int, int, int]:
    return (
        sum(1 for s in found if s.kind == CITED),
        sum(1 for s in found if s.kind == ASSESSED),
        sum(1 for s in found if s.kind == UNMARKED),
    )


def _density(cited: int, assessed: int, unmarked: int) -> float:
    total = cited + assessed + unmarked
    return round((cited + assessed) / total, 4) if total else 0.0


def bounds(raw: str) -> list[tuple[Heading, int | None]]:
    """Each heading with the line the next one starts on, or None at the end.

    The one definition of where a section stops, shared by the density tally and
    the family count. Two answers to "which lines are in this section" would put
    a citation in one section's evidence and the sentence it supports in
    another's.
    """
    src = check.scrub(raw)
    order = headings(src, _calls_and_directives(src))
    return [
        (heading, order[index + 1].line if index + 1 < len(order) else None)
        for index, heading in enumerate(order)
    ]


def sections(
    raw: str, found: Sequence[Statement], cites: Sequence[Citation] = ()
) -> list[dict]:
    """Per-heading totals, as a flat list in document order.

    Flat rather than nested: a heading owns the statements up to the next heading
    of any level, so nothing is counted twice and the app can render the list
    without walking a tree. Statements above the first heading belong to no
    section, which is why the sections rarely sum to the report totals.

    `cites` is optional, and left out the depth columns read as zero rather than
    as a lie: a caller that has not resolved the bibliography does not know how
    many families a section rests on, and guessing one would be worse than
    saying nothing.
    """
    out: list[dict] = []
    for heading, stop in bounds(raw):
        inside = [
            s
            for s in found
            if s.line >= heading.line and (stop is None or s.line < stop)
        ]
        within = [
            c for c in cites if c.line >= heading.line and (stop is None or c.line < stop)
        ]
        cited, assessed, unmarked = _tally(inside)
        counts = family_counts(within)
        out.append(
            {
                "title": heading.title,
                "level": heading.level,
                "cited": cited,
                "assessed": assessed,
                "unmarked": unmarked,
                "density": _density(cited, assessed, unmarked),
                "line": heading.line,
                "citations": len(within),
                "families": len(counts),
                "family": next(iter(counts)) if len(counts) == 1 else None,
            }
        )
    return out


# ── source families ──────────────────────────────────────────────────────────
#
# A family is "which party is this?", answered as coarsely as the entry allows.
# Coarse is the point: `docs.acme.com` and `www.acme.com` are one account of the
# world, and a rule that counted them as two would be satisfied by a document
# that cites the same company twice.


def _registrable(url: str | None) -> str:
    """The registrable domain behind a URL: `acme.com`, `example.co.uk`.

    Which label carries the identity is `sources._host_label`'s decision, and it
    stays there — it is the same question key derivation asks, and two answers to
    it would disagree the first time a source used a country-code domain. All
    this adds is the suffix, by asking that function which candidate it would
    have named: a family reads as a domain, and "resolves to acme.com" names a
    party where "resolves to acme" names a word.
    """
    label = sources._host_label(url)
    if not label:
        return ""
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except ValueError:  # a URL malformed enough that even the host will not parse
        return label
    parts = [part for part in host.removeprefix("www.").split(".") if part]
    # Two labels, or three when the second-level one carries no identity — the
    # same two shapes `_host_label` chooses between.
    for size in (2, 3):
        if len(parts) < size:
            continue
        candidate = ".".join(parts[-size:])
        if sources._host_label(f"https://{candidate}") == label:
            return candidate
    return label


def family(source: sources.Source) -> str:
    """Which party a bibliography entry speaks for.

    A URL settles it: whoever owns the domain published the page, whatever the
    entry's `type` says. Otherwise the publisher, then the author — an interview
    with two people at one company is one account of it. A `url:` written without
    a scheme parses to no host at all, which is the blind spot key derivation
    already has, and gets the same answer: fall through to the publisher rather
    than guess at what the string might have meant.

    An entry that names nobody falls back to its own key, so it is its own
    family. The other direction is tempting and wrong: bucketing every
    unattributed source into a shared "unknown" would let three anonymous entries
    trip a rule about single-sourcing, and a warning that fires because the
    tooling could not tell is a warning people learn to ignore.
    """
    domain = _registrable(source.url)
    if domain:
        return domain
    for name in PARTY_FIELDS:
        # `_display` is where hayagriva's several spellings of a person or an
        # organisation are already flattened; a second flattener here would read
        # `author: {name: …}` differently from the sources panel.
        text = " ".join(sources._display(source.fields.get(name)).split())
        if text:
            return text
    return source.key


def key_families(entries: Sequence[sources.Source]) -> dict[str, str]:
    """key → family, for one bibliography."""
    return {entry.key: family(entry) for entry in entries}


def citations(
    raw: str,
    *,
    families: Mapping[str, str] | None = None,
    keys: set[str] | None = None,
    labels: set[str] | None = None,
) -> list[Citation]:
    """Every citation in a report body, resolved to a family.

    Every one, including the `@key`s inside `srcfig`, `srcquote` and `finding` —
    unlike `statements`, which treats a helper call as structure. The two are
    asking different questions: the classifier asks what a person wrote and this
    asks what the document rests on, and a figure sourced to the audited party's
    own site is as much of an answer to the second as a sentence is.

    What counts as a citation is `_is_citation`'s answer and not a second one: a
    cross-reference to a figure in this document is not evidence, and a `@key`
    with no entry behind it points at nothing, so it supports nothing. With an
    empty bibliography every key is taken at its word — the same leniency the
    linter applies — and stands for its own family, because there is nothing to
    resolve it to and inventing a shared "unknown" would merge parties that have
    nothing to do with each other.
    """
    src = check.scrub(raw)
    known = keys if keys is not None else set()
    defined = labels if labels is not None else check.labels(raw)
    by_key = families or {}
    return [
        Citation(
            key=key,
            family=by_key.get(key) or key,
            line=check.line_of(src, index),
            index=index,
        )
        for key, index in check.cited_keys(src)
        if _is_citation(key, known, defined)
    ]


def family_counts(cites: Sequence[Citation]) -> dict[str, int]:
    """family → how many citations rest on it, heaviest first.

    Ordered rather than alphabetical because the first entry is the answer to the
    question a reader actually has: who is this document mostly quoting? Ties
    break on the name, so the JSON is stable between runs.
    """
    counts: dict[str, int] = {}
    for cite in cites:
        counts[cite.family] = counts.get(cite.family, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _finding_name(args: str) -> str:
    """What to call a finding in a message: its id, else its title, else "".

    Both are read as string literals only. A finding whose id is computed rather
    than written is rare, and naming it by a fragment of Typst source would be
    worse than not naming it at all.
    """
    named = check.arguments(args)[1]
    for field_name in ("id", "title"):
        value = check.string_literal(named.get(field_name, ""))
        if value and value.strip():
            return value.strip()
    return ""


def blocks(raw: str, cites: Sequence[Citation] = ()) -> list[dict]:
    """Every `#finding(…)` and `#assessment[…]`, with the families behind it.

    These are the units of argument, as opposed to the units of prose that
    `statements` returns: a finding is one claim about the world and an
    assessment block is one judgement, and each rests on whatever it cites
    regardless of how many sentences that took.

    Both are counted; only findings are ever warned about. An assessment has
    already told the reader it is opinion — its depth is worth showing and is not
    a defect — while a finding presents itself as established, and a finding that
    rests on one party's account is weaker than it looks.
    """
    src = check.scrub(raw)
    spans: list[tuple[str, str, int, int]] = []  # kind, name, start, end
    for match in FINDING.finditer(src):
        start, end = check.call_span(src, match.end() - 1)
        spans.append(("finding", _finding_name(src[start:end]), match.start(), end))
    for match in ASSESSMENT.finditer(src):
        _, end = check.call_span(src, match.end() - 1)
        spans.append(("assessment", "", match.start(), end))

    out: list[dict] = []
    for kind, name, start, end in sorted(spans, key=lambda span: span[2]):
        within = [c for c in cites if start <= c.index < end]
        counts = family_counts(within)
        out.append(
            {
                "kind": kind,
                "name": name,
                "line": check.line_of(src, start),
                "citations": len(within),
                "families": len(counts),
                "family": next(iter(counts)) if len(counts) == 1 else None,
            }
        )
    return out


def _single_family(count: int, name: str | None, floor: int) -> tuple[str, int] | None:
    """(family, citation count) when a passage is load-bearing and rests on one.

    Below the floor there is nothing to say: a passage that cites once or twice
    has not yet made a claim whose strength depends on corroboration. `name` is
    the sole family, and None when there is more than one — the same value the
    section and block dicts carry, so the warning and the table can never
    disagree about whether a passage is single-sourced.
    """
    return (name, count) if name and count >= floor else None


def _depth_message(where: str, noun: str, name: str, count: int) -> str:
    """W010's wording.

    It names the family and says what that means, because "single source" is a
    statistic and "one party's own account" is the thing the reader has to weigh.
    """
    lead = (
        f"the only citation in {where} resolves to {name}"
        if count == 1
        else f"all {count} citations in {where} resolve to {name}"
    )
    return f"{lead} — {noun} rests on one party's own account"


def family_findings(report: Report) -> list[check.Finding]:
    """W010, for one report.

    Lives here rather than in `check.py` for the same reason the data rules live
    in `data.py`: the definition of a source family is this module's, and a
    second copy of it inside the linter would be a second answer to who a
    document rests on. `check` calls this and reports what comes back.

    A warning and not an error, deliberately. A single-family section is
    sometimes exactly right — a report on what one company published is about one
    company — so this is a question put to the writer, not a verdict on the
    document.
    """
    if not report.main.is_file():
        return []
    raw = report.main.read_text(encoding="utf-8")
    entries = sources.parse(report.sources)
    cites = citations(
        raw,
        families=key_families(entries),
        keys={entry.key for entry in entries},
        labels=check.labels(raw),
    )
    out: list[check.Finding] = []

    def add(line: int, where: str, noun: str, single: tuple[str, int]) -> None:
        out.append(
            check.Finding(
                level="warning",
                code="W010",
                path=report.main,
                line=line,
                message=_depth_message(where, noun, *single),
                report=report.id,
            )
        )

    # No statements passed: this rule asks nothing about density, and the same
    # section boundaries have to be used either way. A second walk of the
    # headings here is how the warning would start naming a different section
    # from the one the table shows.
    for section in sections(raw, (), cites):
        single = _single_family(
            section["citations"], section["family"], LOAD_BEARING
        )
        if single:
            title = section["title"]
            where = f'section "{title}"' if title else "this section"
            add(section["line"], where, "the section", single)

    for block in blocks(raw, cites):
        if block["kind"] != "finding":
            continue
        single = _single_family(
            block["citations"], block["family"], FINDING_LOAD_BEARING
        )
        if single:
            where = f"finding {block['name']}" if block["name"] else "this finding"
            add(block["line"], where, "the finding", single)
    return out


# ── scoring a vault ──────────────────────────────────────────────────────────


def score_report(cfg: Config, report: Report) -> ReportScore:
    raw = report.main.read_text(encoding="utf-8") if report.main.is_file() else ""
    # Parsed once and passed down: the key set, the families and the use counts
    # are three questions about the same file, and reading it three times is how
    # scoring a vault of eighty reports stops being free.
    entries = sources.parse(report.sources)
    keys = {entry.key for entry in entries}
    labels = check.labels(raw)
    found = statements(raw, keys=keys, labels=labels)
    cites = citations(raw, families=key_families(entries), keys=keys, labels=labels)
    cited, assessed, unmarked = _tally(found)
    counts = family_counts(cites)
    # Reuse the sources panel's own count rather than deriving one here, so the
    # "3 of 4 sources cited" on the dashboard and the W001 chips in the panel can
    # never tell a person two different stories.
    uses = sources.use_counts(report)
    return ReportScore(
        id=report.id,
        cited=cited,
        assessed=assessed,
        unmarked=unmarked,
        density=_density(cited, assessed, unmarked),
        sections=sections(raw, found, cites),
        lines=line_classes(raw, found),
        sources_total=len(uses),
        sources_cited=sum(1 for count in uses.values() if count > 0),
        families=len(counts),
        family_counts=counts,
        blocks=blocks(raw, cites),
    )


def score(cfg: Config, target: str | None = None) -> list[ReportScore]:
    return [score_report(cfg, report) for report in reports(cfg, target)]


# ── output ───────────────────────────────────────────────────────────────────


def _merged_families(scores: Sequence[ReportScore]) -> dict[str, int]:
    """The vault's families, summed across reports and ordered like a report's.

    Summed rather than counted per report, because a family that appears in six
    reports is one party the vault leans on, not six.
    """
    counts: dict[str, int] = {}
    for scored in scores:
        for name, count in scored.family_counts.items():
            counts[name] = counts.get(name, 0) + count
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def to_json(scores: Sequence[ReportScore]) -> dict:
    """The `--json` payload, and the `score` block inside `check --json`.

    `sourcesTotal`, `sourcesCited` and `familyCounts` are camelCase because that
    is how the app's `ReportScore` type spells a compound name; every
    single-word name matches the field it came from.
    """
    cited = sum(s.cited for s in scores)
    assessed = sum(s.assessed for s in scores)
    unmarked = sum(s.unmarked for s in scores)
    families = _merged_families(scores)
    return {
        "reports": [
            {
                "id": s.id,
                "cited": s.cited,
                "assessed": s.assessed,
                "unmarked": s.unmarked,
                "density": s.density,
                "sections": s.sections,
                "blocks": s.blocks,
                "lines": [{"line": lc.line, "kind": lc.kind} for lc in s.lines],
                "sourcesTotal": s.sources_total,
                "sourcesCited": s.sources_cited,
                "families": s.families,
                "familyCounts": s.family_counts,
            }
            for s in scores
        ],
        "cited": cited,
        "assessed": assessed,
        "unmarked": unmarked,
        "density": _density(cited, assessed, unmarked),
        "sourcesTotal": sum(s.sources_total for s in scores),
        "sourcesCited": sum(s.sources_cited for s in scores),
        "families": len(families),
        "familyCounts": families,
    }


def _percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def report_scores(cfg: Config, scores: Sequence[ReportScore]) -> int:
    """Print the table. Always 0 — density is information, not a gate."""
    if not scores:
        print("  no reports to score")
        return 0
    width = max([len("report"), *(len(s.id) for s in scores)])
    print(
        f"  {'report':<{width}}  {'cited':>5}  {'assessed':>8}  {'unmarked':>8}"
        f"  {'density':>7}  {'families':>8}  sources"
    )
    for s in scores:
        print(
            f"  {s.id:<{width}}  {s.cited:>5}  {s.assessed:>8}  {s.unmarked:>8}"
            f"  {_percent(s.density):>7}  {s.families:>8}"
            f"  {s.sources_cited}/{s.sources_total}"
        )
    cited = sum(s.cited for s in scores)
    assessed = sum(s.assessed for s in scores)
    unmarked = sum(s.unmarked for s in scores)
    total = cited + assessed + unmarked
    families = _merged_families(scores)
    print(
        f"\n  {len(scores)} report(s), {total} statement(s) — "
        f"{_percent(_density(cited, assessed, unmarked))} cited or marked, "
        f"{unmarked} unmarked"
    )
    if families:
        # The heaviest family, named. "Six sources" and "six sources, four of
        # them the audited party" are different documents, and the second fact
        # is the one a person reading a summary line needs.
        heaviest, count = next(iter(families.items()))
        plural = "y" if len(families) == 1 else "ies"
        print(
            f"  {len(families)} source famil{plural} across "
            f"{sum(families.values())} citation(s) — "
            f"most cited: {heaviest} ({count})"
        )
    return 0
