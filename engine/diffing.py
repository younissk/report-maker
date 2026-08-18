"""What changed in a report since a given revision — in the report's own terms.

`git diff` answers the question a text file asks: which lines moved. A report
asks a different one. Which claims changed, which evidence arrived and which was
withdrawn, which judgements were added, and did the version on the cover move.
Those are the things the people who read the document — a client, a reviewer,
the person whose name is on the cover — actually need listed, and none of them
survive a line-oriented diff intact. Rewrapping a paragraph churns every line in
it and changes nothing at all; rewording one sentence around the same citation
is a single changed claim, not a deletion and an unrelated insertion.

So this module reads both revisions *as reports*. It pulls the old bytes out of
git, extracts the same five things from each side — metadata, sources, claims,
assessments, figures — and pairs them up. Claims are matched by similarity
rather than equality, which is the whole reason the module exists: above
`CLAIM_MATCH` a pair is one changed claim, below it they are two unrelated
statements. Everything else pairs on exact text, because a figure or an
assessment that reads differently *is* a different one.

Named `diffing` rather than `diff` so that neither `difflib` nor anybody's local
variable called `diff` can shadow it.

What `key` holds depends on the kind, and it is always the most recognisable
name the change has:

    source-*        the source key
    claim-*         the keys the claim cites, joined with "+"
    assessment-*    a short digest of the text — a judgement has no other name
    figure-*        the figure's caption, or its helper when it has none
    meta-changed    the metadata field
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from . import check, sources
from .config import Config
from .workspace import FIELDS, Report, reports

# Above this ratio, two statements are the same claim reworded. Below it, they
# are two different claims. 0.6 is difflib's own "close enough" convention, and
# it holds up here: a rewritten sentence keeps its subject, its citation and most
# of its vocabulary, while two genuinely different claims rarely share half.
CLAIM_MATCH = 0.6

# A line that carries structure rather than prose. Blanked before statements are
# read, so a heading or an import never reads as a claim.
STRUCTURAL = re.compile(r"^[ \t]*(?:=+[ \t]|#import\b|#include\b|#let\b|#set\b|#show\b)")

# A sentence ends at terminal punctuation followed by whitespace or a closing
# delimiter — `…@example-page.]` ends a sentence, `3.5%` does not. `#assess`
# also ends one, and belongs to the sentence it closes: two judgements marked
# back to back are two assessments, not one long one.
SENTENCE_END = re.compile(r"[.!?](?=[\s\]\)},]|$)|#assess\b")

# Typst scaffolding around a statement: `#lede[`, `evidence: [`, a stray `],`.
# Stripped so a change list shows the sentence and not the call it sits in.
LEAD = re.compile(r"^(?:#[A-Za-z][\w-]*|[A-Za-z][\w-]*[ \t]*:|[-+*][ \t]|[\s\[\](),]+)+")
TRAIL = re.compile(r"[\s\]\),]+$")

# `#assess` marks a judgement; `#assessment[…]` opens a block of them. The word
# boundary keeps the first from matching the second.
ASSESS = re.compile(r"#assess\b")
ASSESSMENT_BLOCK = re.compile(r"(?<![\w.-])assessment\s*\[")

# `#helper(` — the only thing that opens code mode from markup. Parentheses in
# ordinary prose ("(see below)") must not, or half a sentence would vanish.
CALL = re.compile(r"#[A-Za-z][\w.-]*[ \t]*\(")

# Arguments that carry provenance rather than prose. Blanked before statements
# are read, so `source: [@example-page]` never reads as a claim of its own —
# a figure's citation belongs to the figure, not to a sentence.
META_ARGS = ("source", "alt", "attribution", "locator")
META_ARG = re.compile(rf"(?<![\w.-])(?:{'|'.join(META_ARGS)})[ \t]*:[ \t]*")
STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
BARE_VALUE = re.compile(r"[^,)\]\n]*")

# Helpers that *are* a claim: the body is the statement and the `source:` is its
# citation, so they are lifted out whole rather than left to the prose scanner,
# which would strip the citation away with the rest of the argument list.
CLAIM_HELPERS = ("claim", "srcquote")

CAPTION = re.compile(r"(?<![\w.-])caption[ \t]*:[ \t]*\[")
# A named argument, recognised only where one can start — after `(` or `,`.
ARG_NAME = re.compile(r"([(,][ \t\r\n]*)([A-Za-z][\w-]*[ \t]*:)")
DELIMITERS = re.compile(r"[\[\]\"()]")

# Which counts bucket each kind falls into, and the order a changelog reads in:
# what the document claims to be, then the evidence, then what rests on it.
GROUPS = {
    "meta": "metadata",
    "source": "sources",
    "claim": "claims",
    "assessment": "assessments",
    "figure": "figures",
}

KIND_ORDER = (
    "meta-changed",
    "source-added",
    "source-changed",
    "source-removed",
    "claim-added",
    "claim-changed",
    "claim-removed",
    "assessment-added",
    "assessment-removed",
    "figure-added",
    "figure-removed",
)

META_ORDER = (*FIELDS, "date")


class DiffError(RuntimeError):
    pass


@dataclass
class Change:
    kind: str
    key: str
    before: str | None = None
    after: str | None = None
    # The line in the working copy, so a removal has none — there is nowhere in
    # the file as it stands now for an editor to put the cursor.
    line: int | None = None


@dataclass
class ReportDiff:
    id: str
    rev: str
    changes: list[Change] = field(default_factory=list)
    counts: dict = field(default_factory=dict)


# ── reading the old revision ─────────────────────────────────────────────────
#
# Everything here goes through `git`, and every failure of `git` is a question a
# person can answer — the vault is not versioned, the revision is misspelt, the
# report is newer than the revision. So each one gets its own message saying
# which of those it is and what to do about it, rather than a returncode.


def _git(cfg: Config, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(cfg.root), *args],
            capture_output=True,
            text=True,
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise DiffError(
            "git is not installed — `report-maker diff` reads the previous "
            "revision with `git show`"
        ) from exc


def _require_repo(cfg: Config) -> None:
    if _git(cfg, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise DiffError(
            f"{cfg.root} is not inside a git repository, so there is no previous "
            "revision to compare against.\n"
            f"  Start one with `git -C {cfg.root} init` and commit the vault."
        )


def _require_rev(cfg: Config, rev: str) -> str:
    result = _git(cfg, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if result.returncode != 0:
        raise DiffError(
            f"no such revision: {rev}\n"
            f"  `git -C {cfg.root} log --oneline` lists what is there. A vault "
            "with a single commit has no HEAD~1 yet."
        )
    return result.stdout.strip()


def _prefix(cfg: Config) -> str:
    """Where the vault sits inside the repository.

    `git show <rev>:<path>` takes a path from the repository root, and a vault is
    very often a folder inside a larger repository rather than the whole of it.
    """
    return _git(cfg, "rev-parse", "--show-prefix").stdout.strip()


def _show(cfg: Config, rev: str, path: Path, prefix: str) -> str | None:
    """A vault file as it was at `rev`, or None when it was not there."""
    rel = path.resolve().relative_to(cfg.root.resolve()).as_posix()
    result = _git(cfg, "show", f"{rev}:{prefix}{rel}")
    return result.stdout if result.returncode == 0 else None


# ── what a report is made of ─────────────────────────────────────────────────


@dataclass(frozen=True)
class _Statement:
    text: str  # normalised: whitespace collapsed, Typst scaffolding stripped
    line: int
    offset: int = 0
    keys: tuple[str, ...] = ()  # the sources it cites, for a claim


@dataclass(frozen=True)
class _Figure:
    helper: str
    label: str  # the caption, which is what a reader recognises it by
    text: str  # the whole call, collapsed — this is the identity
    line: int
    span: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class _Side:
    """One revision of a report, reduced to the five things worth diffing."""

    meta: dict[str, tuple[str, int]]
    sources: list[sources.Source]
    claims: list[_Statement]
    assessments: list[_Statement]
    figures: list[_Figure]


def _collapse(text: str) -> str:
    return " ".join(text.split())


# ── the prose view ───────────────────────────────────────────────────────────
#
# Everything below blanks rather than deletes. A blanked character keeps its
# newline and its position, so every offset in the view still names the line it
# came from in the file, and a change can carry a line number worth jumping to.
# `check.scrub` does the same thing with comments, for the same reason.


def _erase(text: str, spans: Sequence[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for index in range(max(start, 0), min(end, len(chars))):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def _meta_arg_spans(text: str) -> list[tuple[int, int]]:
    """Where each provenance argument sits, name and value together."""
    spans = []
    for match in META_ARG.finditer(text):
        start, value = match.start(), match.end()
        if value >= len(text):
            spans.append((start, len(text)))
        elif text[value] == "[":
            spans.append((start, check.call_span(text, value)[1]))
        elif text[value] == '"':
            closing = STRING.match(text, value)
            spans.append((start, closing.end() if closing else len(text)))
        else:
            # A bare value — `source: none`, or a variable. It ends where the
            # argument does.
            spans.append((start, BARE_VALUE.match(text, value).end()))
    return spans


def _mask_args(chars: list[str], text: str, start: int, end: int) -> None:
    """Blank a call's argument list, keeping only what is inside `[…]`.

    Prose in Typst lives in content blocks. Everything else between the
    parentheses is machinery — argument names, string literals, severities,
    identifiers — and reading it as a sentence is how a changelog ends up
    quoting `"F-01", severity: "high"` back at a client as a claim.
    """
    def blank(position: int) -> None:
        if position < end and chars[position] != "\n":
            chars[position] = " "

    content = 0
    in_string = False
    index = start
    while index < end:
        char = text[index]
        if in_string:
            if char == "\\":
                if content == 0:
                    blank(index)
                    blank(index + 1)
                index += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "[":
            content += 1
            blank(index)
            index += 1
            continue
        elif char == "]":
            content = max(content - 1, 0)
            blank(index)
            index += 1
            continue
        if content == 0:
            blank(index)
        index += 1


def _prose(src: str, hidden: Sequence[tuple[int, int]]) -> str:
    """The source reduced to what a reader would call prose.

    Three passes: the spans already accounted for elsewhere go (figures and
    quotations are their own kind of change), then the lines that carry
    structure rather than sentences, then the argument-list machinery of every
    remaining `#helper(…)` call.
    """
    view = _erase(src, [*hidden, *_meta_arg_spans(src)])
    view = "\n".join(
        " " * len(line) if STRUCTURAL.match(line) else line for line in view.split("\n")
    )
    # Every call is masked against the unmodified `view`, never against the
    # partly-masked result, so a call nested inside a content block is read the
    # same way whether or not its parent has been through here already.
    chars = list(view)
    for match in CALL.finditer(view):
        start, end = check.call_span(view, match.end() - 1)
        _mask_args(chars, view, start, end)
    return "".join(chars)


def _paragraphs(text: str) -> list[tuple[int, int]]:
    """(start, end) of each run of prose.

    A blank line ends a run, and a line opening a Typst call starts a new one:
    `#lede[…]` and the `#finding(…)` under it are two statements whether or not
    anybody left a blank line between them.
    """
    spans: list[tuple[int, int]] = []
    start: int | None = None
    end = offset = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if (not stripped or stripped.startswith("#")) and start is not None:
            spans.append((start, end))
            start = None
        if stripped:
            if start is None:
                start = offset
            end = offset + len(line)
        offset += len(line) + 1
    if start is not None:
        spans.append((start, end))
    return spans


def _statements(prose: str) -> list[_Statement]:
    """Every sentence-ish run of prose in the view, with where it sits."""
    out: list[_Statement] = []
    for start, end in _paragraphs(prose):
        # Newlines become spaces so a sentence may span lines, and the
        # substitution is length-preserving so offsets still map back to `src`.
        block = prose[start:end].replace("\n", " ")
        for first, last in _sentence_spans(block):
            raw = block[first:last]
            lead = len(raw) - len(raw.lstrip())
            match = LEAD.match(raw[lead:])
            if match:
                lead += match.end()
            text = TRAIL.sub("", _collapse(raw[lead:]))
            if not text:
                continue
            offset = start + first + lead
            out.append(_Statement(text=text, line=check.line_of(prose, offset), offset=offset))
    return out


def _sentence_spans(block: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in SENTENCE_END.finditer(block):
        spans.append((cursor, match.end()))
        cursor = match.end()
    if block[cursor:].strip():
        spans.append((cursor, len(block)))
    return spans


def _caption(args: str) -> str:
    match = CAPTION.search(args)
    if not match:
        return ""
    start, end = check.call_span(args, match.end() - 1)
    return _collapse(args[start + 1 : end - 1])


def _figures(src: str) -> list[_Figure]:
    out = [
        _Figure(
            helper=helper,
            label=_caption(args)[:72] or helper,
            text=_collapse(src[start:end]),
            line=check.line_of(src, start),
            span=(start, end),
        )
        for helper in check.FIGURE_HELPERS
        for start, end, args in check.calls(src, helper)
    ]
    return sorted(out, key=lambda figure: figure.line)


def _quotation(args: str) -> str:
    """The words of a `claim(…)` / `srcquote(…)`, with the machinery removed.

    Only names in argument position go — a `Note:` that happens to open the
    quotation is part of the quotation, and dropping it would put words in the
    subject's mouth that they did not say.
    """
    text = _erase(args, _meta_arg_spans(args))
    text = ARG_NAME.sub(lambda m: m.group(1) + " " * len(m.group(2)), text)
    return _collapse(DELIMITERS.sub(" ", text)).strip(" ,")


def _quotations(src: str, defined: set[str]) -> tuple[list[_Statement], list[tuple[int, int]]]:
    """The `claim(…)` and `srcquote(…)` calls, each as one claim.

    Lifted out before the prose scan because their citation lives in the
    `source:` argument, which the prose scan blanks. A quotation without a
    citation is left behind for that scan to pick up as ordinary prose — it is
    not evidence, whatever the helper it was written with says.
    """
    statements: list[_Statement] = []
    spans: list[tuple[int, int]] = []
    for helper in CLAIM_HELPERS:
        for start, end, args in check.calls(src, helper):
            keys = _keys(args, defined)
            text = _quotation(args)
            if not keys or not text:
                continue
            statements.append(_Statement(text, check.line_of(src, start), start, keys))
            spans.append((start, end))
    return statements, spans


def _keys(text: str, defined: set[str]) -> tuple[str, ...]:
    """The sources a piece of text cites.

    A cross-reference to a figure in this document is spelt exactly like a
    citation and is not one, so the document's own labels have to filter first.
    """
    return tuple(
        dict.fromkeys(key for key, _ in check.cited_keys(text) if key not in defined)
    )


def _metadata(main: str) -> dict[str, tuple[str, int]]:
    """The `report.with(…)` fields, each with the line it is set on.

    `Report.meta()` answers almost the same question, but it reads from disk and
    the old revision has no file on disk, and it reports no line numbers.
    """
    out: dict[str, tuple[str, int]] = {}
    for name in FIELDS:
        match = re.search(rf'^\s*{re.escape(name)}:\s*"((?:[^"\\]|\\.)*)"', main, re.M)
        if match:
            out[name] = (match.group(1).replace('\\"', '"'), check.line_of(main, match.start()))
    match = re.search(
        r"date:\s*datetime\(\s*year:\s*(\d+),\s*month:\s*(\d+),\s*day:\s*(\d+)", main
    )
    if match:
        year, month, day = (int(part) for part in match.groups())
        out["date"] = (f"{year:04d}-{month:02d}-{day:02d}", check.line_of(main, match.start()))
    return out


def _read(main: str, bibliography: str) -> _Side:
    src = check.scrub(main)
    defined = check.labels(main)
    figures = _figures(src)
    quotations, quoted = _quotations(src, defined)
    blocks = [check.call_span(src, match.end() - 1) for match in ASSESSMENT_BLOCK.finditer(src)]

    claims = list(quotations)
    assessments: list[_Statement] = []
    for statement in _statements(_prose(src, [f.span for f in figures] + quoted)):
        keys = _keys(statement.text, defined)
        if keys:
            # A sentence that both cites and judges is filed as a claim: the
            # evidence is the part a reader of the changelog needs to see move.
            claims.append(_Statement(statement.text, statement.line, statement.offset, keys))
        elif ASSESS.search(statement.text) or any(
            start <= statement.offset < end for start, end in blocks
        ):
            assessments.append(statement)

    claims.sort(key=lambda statement: statement.offset)
    return _Side(
        meta=_metadata(main),
        sources=sources.parse_text(bibliography),
        claims=claims,
        assessments=assessments,
        figures=figures,
    )


# ── pairing ──────────────────────────────────────────────────────────────────


def _pair(
    old: Sequence, new: Sequence, threshold: float | None
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Match old items to new ones by `.text`: (pairs, unmatched old, unmatched new).

    Identical text pairs first and unconditionally, so an unchanged statement can
    never be stolen by a fuzzy match elsewhere in the document. What is left over
    is paired best-ratio-first when a threshold is given, and not at all when it
    is not — a reworded claim is one change, a reworded figure caption is a
    different figure.
    """
    by_text: dict[str, list[int]] = {}
    for index, item in enumerate(new):
        by_text.setdefault(item.text, []).append(index)

    pairs: list[tuple[int, int]] = []
    used: set[int] = set()
    loose: list[int] = []
    for index, item in enumerate(old):
        bucket = by_text.get(item.text)
        if bucket:
            match = bucket.pop(0)
            used.add(match)
            pairs.append((index, match))
        else:
            loose.append(index)

    if threshold is not None:
        scored: list[tuple[float, int, int]] = []
        for index in loose:
            for candidate in range(len(new)):
                if candidate in used:
                    continue
                matcher = SequenceMatcher(None, old[index].text, new[candidate].text)
                # quick_ratio is an upper bound on ratio, so anything it rules
                # out cannot come back — worth it on a report with 200 claims.
                if matcher.quick_ratio() <= threshold:
                    continue
                ratio = matcher.ratio()
                if ratio > threshold:
                    scored.append((ratio, index, candidate))
        scored.sort(key=lambda row: (-row[0], row[1], row[2]))
        taken: set[int] = set()
        for _ratio, index, candidate in scored:
            if index in taken or candidate in used:
                continue
            taken.add(index)
            used.add(candidate)
            pairs.append((index, candidate))
        loose = [index for index in loose if index not in taken]

    return pairs, loose, [index for index in range(len(new)) if index not in used]


# ── the five comparisons ─────────────────────────────────────────────────────


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _claim_key(statement: _Statement) -> str:
    keys = list(statement.keys)
    return "+".join(keys[:3]) + ("+…" if len(keys) > 3 else "")


def _diff_meta(old: dict, new: dict) -> list[Change]:
    order = {name: index for index, name in enumerate(META_ORDER)}
    names = sorted(set(old) | set(new), key=lambda name: (order.get(name, len(order)), name))
    out = []
    for name in names:
        before = old.get(name, (None, None))[0]
        after, line = new.get(name, (None, None))
        if before != after:
            out.append(Change("meta-changed", name, before, after, line))
    return out


def _flat(value, prefix: str = "") -> dict[str, str]:
    """A source's fields as dotted paths, so a difference names itself.

    `url.date` and `author[0].given-name` are what a person needs told; "the
    entry changed" is not.
    """
    if isinstance(value, Mapping):
        out: dict[str, str] = {}
        for name, item in value.items():
            out.update(_flat(item, f"{prefix}.{name}" if prefix else str(name)))
        return out
    if isinstance(value, (list, tuple)):
        out = {}
        for index, item in enumerate(value):
            out.update(_flat(item, f"{prefix}[{index}]"))
        return out
    return {prefix: "" if value is None else str(value)}


def _describe(source: sources.Source) -> str:
    parts = [source.type]
    if source.title:
        parts.append(source.title)
    if source.url:
        parts.append(source.url)
    return " — ".join(parts)


def _fields_changed(old: sources.Source, new: sources.Source) -> tuple[str, str] | None:
    before, after = _flat(old.fields), _flat(new.fields)
    if not before or not after:
        # One side did not parse. Comparing the bytes is the only honest thing
        # left, and it still tells the truth about whether the entry moved.
        before = {"(block)": _collapse(old.raw)}
        after = {"(block)": _collapse(new.raw)}
    names = [
        name for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)
    ]
    if not names:
        return None

    def render(flat: dict[str, str]) -> str:
        # Both sides list the same names, so a field present on one side and
        # absent on the other reads as `—` rather than silently disappearing.
        return "; ".join(f"{name}: {flat.get(name, '—')}" for name in names)

    return render(before), render(after)


def _diff_sources(old: list[sources.Source], new: list[sources.Source]) -> list[Change]:
    before = {source.key: source for source in old}
    after = {source.key: source for source in new}
    out = []
    for key, source in after.items():
        if key not in before:
            out.append(Change("source-added", key, None, _describe(source), source.line))
            continue
        pair = _fields_changed(before[key], source)
        if pair:
            out.append(Change("source-changed", key, pair[0], pair[1], source.line))
    for key, source in before.items():
        if key not in after:
            out.append(Change("source-removed", key, _describe(source), None, None))
    return out


def _diff_claims(old: list[_Statement], new: list[_Statement]) -> list[Change]:
    pairs, gone, fresh = _pair(old, new, CLAIM_MATCH)
    out = [
        Change("claim-changed", _claim_key(new[j]), old[i].text, new[j].text, new[j].line)
        for i, j in pairs
        if old[i].text != new[j].text
    ]
    out += [Change("claim-removed", _claim_key(old[i]), old[i].text, None, None) for i in gone]
    out += [Change("claim-added", _claim_key(new[j]), None, new[j].text, new[j].line) for j in fresh]
    return out


def _diff_assessments(old: list[_Statement], new: list[_Statement]) -> list[Change]:
    _pairs, gone, fresh = _pair(old, new, None)
    out = [
        Change("assessment-removed", _digest(old[i].text), old[i].text, None, None) for i in gone
    ]
    out += [
        Change("assessment-added", _digest(new[j].text), None, new[j].text, new[j].line)
        for j in fresh
    ]
    return out


def _diff_figures(old: list[_Figure], new: list[_Figure]) -> list[Change]:
    _pairs, gone, fresh = _pair(old, new, None)
    out = [Change("figure-removed", old[i].label, old[i].text, None, None) for i in gone]
    out += [Change("figure-added", new[j].label, None, new[j].text, new[j].line) for j in fresh]
    return out


# ── the diff ─────────────────────────────────────────────────────────────────


def counts(changes: Sequence[Change]) -> dict:
    """Totals per group, every bucket present so a table can be rendered blind."""
    out = {group: {"added": 0, "removed": 0, "changed": 0} for group in GROUPS.values()}
    for change in changes:
        kind, action = change.kind.rsplit("-", 1)
        out[GROUPS[kind]][action] += 1
    return out


def _order(change: Change) -> tuple:
    index = KIND_ORDER.index(change.kind) if change.kind in KIND_ORDER else len(KIND_ORDER)
    return (index, change.line if change.line is not None else 0, change.key)


def _diff_report(cfg: Config, report: Report, rev: str, prefix: str) -> ReportDiff:
    old_main = _show(cfg, rev, report.main, prefix)
    if old_main is None:
        rel = report.main.relative_to(cfg.root).as_posix()
        raise DiffError(
            f"{rel} did not exist at {rev} — the report was added after that "
            "revision, so there is nothing to compare it with.\n"
            f"  `git -C {cfg.root} log --oneline -- {prefix}{rel}` shows when it arrived."
        )
    # A bibliography that was not there yet is an empty one, not an error: a
    # report may perfectly well have gained its sources.yml since the revision,
    # and every entry in it is then genuinely an addition.
    old_bib = _show(cfg, rev, report.sources, prefix) or ""

    new_main = report.main.read_text(encoding="utf-8", errors="replace")
    new_bib = (
        report.sources.read_text(encoding="utf-8", errors="replace")
        if report.sources.is_file()
        else ""
    )

    before, after = _read(old_main, old_bib), _read(new_main, new_bib)
    changes = (
        _diff_meta(before.meta, after.meta)
        + _diff_sources(before.sources, after.sources)
        + _diff_claims(before.claims, after.claims)
        + _diff_assessments(before.assessments, after.assessments)
        + _diff_figures(before.figures, after.figures)
    )
    changes.sort(key=_order)
    return ReportDiff(id=report.id, rev=rev, changes=changes, counts=counts(changes))


def diff(cfg: Config, target: str, rev: str = "HEAD~1") -> list[ReportDiff]:
    _require_repo(cfg)
    _require_rev(cfg, rev)
    prefix = _prefix(cfg)
    return [_diff_report(cfg, report, rev, prefix) for report in reports(cfg, target)]


# ── output ───────────────────────────────────────────────────────────────────


def to_json(diffs: Sequence[ReportDiff]) -> dict:
    return {
        "rev": diffs[0].rev if diffs else "",
        "count": sum(len(d.changes) for d in diffs),
        "counts": counts([change for d in diffs for change in d.changes]),
        "diffs": [
            {
                "id": d.id,
                "rev": d.rev,
                "changes": [asdict(change) for change in d.changes],
                "counts": d.counts,
            }
            for d in diffs
        ],
    }


# Groups whose text says something the key does not, and so gets a was/now body.
# An assessment's key *is* its sentence, and a figure's key is its caption; for
# those two the body would only be the Typst call, which is not news to anyone.
BODIES = frozenset({"metadata", "sources", "claims"})


def _short(text: str, width: int = 108) -> str:
    text = _collapse(text)
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _label(group: str, change: Change) -> str:
    """What to call this change on the page.

    A judgement has no name — only the digest that gives it machine identity —
    so the sentence itself is the only label a reader can use.
    """
    if group != "assessments":
        return change.key
    return _short(change.after if change.after is not None else change.before or "", 92)


def _headline(diff_: ReportDiff) -> str:
    parts = []
    for group, actions in diff_.counts.items():
        total = sum(actions.values())
        if total:
            detail = ", ".join(f"{n} {name}" for name, n in actions.items() if n)
            parts.append(f"{group} {total} ({detail})")
    return " · ".join(parts)


def report_diffs(cfg: Config, diffs: Sequence[ReportDiff]) -> int:
    """Print the change list as a changelog. Always exit 0 — a diff is news, not
    a failure, and nothing here can tell a good change from a bad one."""
    if not diffs:
        print(f"  no reports matched in {cfg.root}")
        return 0

    for diff_ in diffs:
        if not diff_.changes:
            print(f"\n  {diff_.id}  ·  no changes since {diff_.rev}")
            continue
        print(f"\n  {diff_.id}  ·  {len(diff_.changes)} change(s) since {diff_.rev}")
        print(f"    {_headline(diff_)}")
        for group in GROUPS.values():
            rows = [c for c in diff_.changes if GROUPS[c.kind.rsplit("-", 1)[0]] == group]
            if not rows:
                continue
            print(f"\n    {group}")
            for change in rows:
                action = change.kind.rsplit("-", 1)[1]
                where = f"  (line {change.line})" if change.line else ""
                print(f"      {action:<8} {_label(group, change)}{where}")
                if group not in BODIES:
                    continue
                if change.before is not None:
                    print(f"                 was  {_short(change.before)}")
                if change.after is not None:
                    print(f"                 now  {_short(change.after)}")
    return 0
