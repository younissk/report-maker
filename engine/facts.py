"""The facts of a build, gathered so the document can state them.

Everything this engine does to earn trust happens in the vault and stops there.
The pages are archived, their bytes are hashed, quotations are checked word for
word against the archive, `verify` says which pages have moved since — and none
of it reaches the person holding the PDF. They get the same object either way: a
report whose sources were all archived and whose quotes were all verified looks
exactly like one whose evidence was never fetched at all. The colophon is where
that difference becomes visible, and this module is what it reads.

Four groups of facts, written to `.build/facts/<report-id>.json` immediately
before Typst runs:

    toolchain    which typst compiled it, which engine, which python
    provenance   when, from which revision of the vault, clean or dirty
    evidence     sources, how many archived and over what dates, how many
                 quotations were checked verbatim, how much of the prose is
                 cited or marked as assessment
    inputs       which declared data files actually produced rows

The typst version is here because a document is a rendering, not a value. The
same source compiled by two typst releases can paginate differently, hyphenate
differently, or lay a table out differently, and today the engine resolves the
binary with `shutil.which` and records nothing about it — so a rebuild that comes
out different has no named cause. `typst --version` is one subprocess per build,
and it turns "it looks different now" into "it was compiled by 0.15.1, this one
by 0.13.0".

WHY THIS EXISTS AT ALL. In an earlier repository of the owner's, a plan document
explicitly flagged a data gap and put it out of scope; a stats document dutifully
recorded the consequence of that gap; and the published report then built its
headline on precisely the missing data without inheriting either caveat. Both
honest documents existed. Neither reached the artifact that needed them, because
nothing carried them there — a caveat lives in the document a person happened to
write it in, and the reader of the *next* document never sees it. A colophon
cannot drift that way: it is generated from the run that produced the file it is
printed in, so an incomplete run says so on its own face. A method statement
typed by hand is a claim about a build; this is a record of one.

Nothing here may ever fail a build. A fact that cannot be gathered degrades to
`unknown` and is named in `gaps`, because a colophon that admits it does not know
the typst version is still worth more than no colophon, and a report that will
not compile because the git binary is missing is worse than both. That rule is
applied per group rather than wholesale: an unreadable `sources.yml` must not
also blank out the toolchain.

The file is written on every compile, even when every fact failed, because the
design reads it with Typst's `json()` and a missing path is a compile error. An
all-`unknown` colophon is the honest output for a build we could not describe.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from . import __version__ as ENGINE_VERSION
from . import check, data, gitsync, score, snapshot, sources
from .config import Config
from .workspace import Report, reports

# What every string-valued fact says when it could not be determined. One
# spelling, so the Typst side has one thing to test for.
UNKNOWN = "unknown"

# Under .build/, beside the staged designs: generated, disposable, and rebuilt on
# the next compile. Never under out/ — out/ is what you hand somebody, and these
# are an input to the thing you hand them.
FACTS_DIR = "facts"

# The helper whose quotations `check` verifies against the archive (E009). Named
# here rather than imported from `check` because this module counts what passed,
# not what failed, and the two questions are allowed to drift apart.
QUOTE_CALL = "srcquote"

# Long enough that `typst --version` on a cold cache still answers, short enough
# that a wedged binary cannot hang a build.
VERSION_TIMEOUT = 10.0


# ── the record ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Toolchain:
    """What compiled this document, by name and version."""

    typst: str = UNKNOWN
    engine: str = UNKNOWN
    python: str = UNKNOWN


@dataclass(frozen=True)
class Provenance:
    """When this build ran, and from which state of the vault.

    `dirty` is `None` rather than `False` outside a repository: "there were no
    uncommitted changes" is a claim, and we have no way to make it about a folder
    git has never heard of. The Typst side prints the three cases separately.

    Inside one, `dirty` is whatever `git status` says about the vault — edits and
    untracked files alike, which is `gitsync.state`'s own definition and the one
    the app already shows. It answers the question a reader of a colophon
    actually has: was the revision named above the whole of what went in?
    """

    built: str = UNKNOWN
    repo: bool = False
    revision: str = UNKNOWN
    branch: str | None = None
    dirty: bool | None = None


@dataclass(frozen=True)
class Evidence:
    """What the report rests on, counted.

    `archived` and `unarchived` do not sum to `sources`, and that is deliberate:
    an interview, a measurement of our own or a registered CSV has no URL and can
    never be archived, so counting it as unarchived would invent a failing. Only
    an entry with a `url:` and no snapshot is unarchived — that one is a page we
    could have kept and did not.

    `quotations_verified` counts the quotations whose words were actually found
    in the archived copy of the page they cite. It is the strongest sentence a
    colophon can carry, and it is not the same as "check found no E009": a
    quotation citing a source that was never archived raises nothing and verifies
    nothing.
    """

    sources: int = 0
    archived: int = 0
    unarchived: int = 0
    archived_from: str = ""
    archived_to: str = ""
    quotations: int = 0
    quotations_verified: int = 0
    cited: int = 0
    assessed: int = 0
    unmarked: int = 0
    density: float = 0.0


@dataclass(frozen=True)
class DataInput:
    """One declared data file, and the shape it turned out to have."""

    rel: str
    key: str
    rows: int
    columns: int


@dataclass(frozen=True)
class Inputs:
    """The data files the report declared, and which of them carried anything.

    A registered CSV with no rows is the shape the burgwiss failure took: the
    pipeline ran, the file was produced, the table was placed, and the numbers
    were never there. `empty` names those files so the colophon can say it in
    the document rather than leaving it in a log nobody reads.
    """

    declared: int = 0
    with_rows: int = 0
    empty: tuple[str, ...] = ()
    files: tuple[DataInput, ...] = ()


@dataclass(frozen=True)
class Facts:
    """Everything the colophon knows, plus what it could not find out.

    `gaps` names the groups that failed to gather. It is the difference between a
    colophon that is quiet because there was nothing to say and one that is quiet
    because something broke, and a reader deserves to be able to tell them apart.
    """

    report: str
    toolchain: Toolchain = field(default_factory=Toolchain)
    provenance: Provenance = field(default_factory=Provenance)
    evidence: Evidence = field(default_factory=Evidence)
    inputs: Inputs = field(default_factory=Inputs)
    gaps: tuple[str, ...] = ()


# ── toolchain ────────────────────────────────────────────────────────────────


@lru_cache(maxsize=8)
def typst_version(binary: str) -> str:
    """`typst --version`, or `unknown`.

    Cached on the binary name: a vault of eighty reports asks this eighty times
    per build and the answer cannot change inside one process. Every failure mode
    — not on PATH, non-zero exit, a hang, a binary that is not typst at all —
    lands on `unknown`, because the point of the field is to name the compiler
    when we can, never to have an opinion about whether the build should proceed.
    """
    resolved = shutil.which(binary)
    if resolved is None:
        return UNKNOWN
    try:
        result = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    if result.returncode != 0:
        return UNKNOWN
    # Some builds print the version on stderr. Take whichever spoke, first line
    # only: `typst 0.15.1 (unknown commit)` is the whole of what we want.
    lines = (result.stdout or result.stderr or "").strip().splitlines()
    return lines[0].strip() if lines and lines[0].strip() else UNKNOWN


def toolchain(cfg: Config) -> Toolchain:
    return Toolchain(
        typst=typst_version(cfg.typst),
        engine=ENGINE_VERSION,
        python=platform.python_version(),
    )


# ── provenance ───────────────────────────────────────────────────────────────


def _now() -> str:
    """The moment of the build, with its offset — the same shape `snapshot` uses
    for a fetch, so two timestamps in the same document read the same way."""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def provenance(cfg: Config) -> Provenance:
    """When, and from what.

    `gitsync.state` is the cheap never-raising probe the app already polls, so a
    vault that is not a repository is a fact here rather than an error. The
    revision comes from `gitsync.log` over the vault path, which answers "what
    version of *this vault* is this" — for a vault filed inside a larger
    repository that is the honest answer, because HEAD may have moved for reasons
    that never touched a report.
    """
    built = _now()
    state = gitsync.state(cfg)
    if not state.repo:
        return Provenance(built=built)

    revision = UNKNOWN
    try:
        rows = gitsync.log(cfg, cfg.root, limit=1)
    except gitsync.GitError:
        rows = []
    if rows:
        revision = str(rows[0].get("short") or rows[0].get("sha") or "") or UNKNOWN

    return Provenance(
        built=built,
        repo=True,
        revision=revision,
        branch=state.branch,
        dirty=bool(state.dirty),
    )


# ── evidence ─────────────────────────────────────────────────────────────────


def _archive_dates(records: Sequence[dict]) -> tuple[str, str]:
    """The first and last day anything in this report was archived.

    Dates only — the ISO datetime carries an offset, and a range that spans two
    of them would compare as text and sort wrongly. A day is the granularity a
    reader of a colophon cares about anyway.
    """
    days = sorted(
        {str(record.get("fetched", ""))[:10] for record in records}
        - {"", "undated"}
    )
    return (days[0], days[-1]) if days else ("", "")


def quotations(report: Report) -> tuple[int, int]:
    """(quotations written, quotations found verbatim in the archive).

    This walks `srcquote` calls exactly the way E009 does, through the same
    scanner, and asks the same `quote_found` question — but records the passes
    rather than the failures. A quotation whose sources were never archived
    counts as written and not as verified, which is the accurate thing to say:
    nothing checked it, so nothing stands behind it but the writer's typing.

    Only string literals are counted, because only a string literal can be
    compared; `srcquote` itself asserts on anything else at compile time, so in
    a document that builds there is nothing else to count.
    """
    if not report.main.is_file():
        return (0, 0)
    raw = report.main.read_text(encoding="utf-8", errors="replace")
    src = check.scrub(raw)
    defined = check.labels(raw)

    written = verified = 0
    for _start, _end, args in check.calls(src, QUOTE_CALL):
        positional, named = check.arguments(args)
        argument = named.get("quote") or (positional[0] if positional else "")
        quote = check.string_literal(argument) if argument else None
        if not quote or not quote.strip():
            continue
        written += 1
        # A `@fig-one` inside `source:` is a cross-reference to this document,
        # not a page with an archive behind it.
        keys = [
            key
            for key, _index in check.cited_keys(named.get("source", ""))
            if key not in defined
        ]
        texts = [
            text for key in keys if (text := check.quotable_text(report, key)) is not None
        ]
        if any(check.quote_found(quote, text) for text in texts):
            verified += 1
    return written, verified


def evidence(cfg: Config, report: Report) -> Evidence:
    """The evidence behind one report, counted from the vault as it stands."""
    entries = sources.parse(report.sources)
    archived = snapshot.records(report)

    kept = [archived[entry.key] for entry in entries if entry.key in archived]
    unarchived = sum(
        1 for entry in entries if entry.key not in archived and entry.url is not None
    )
    first, last = _archive_dates(kept)
    written, verified = quotations(report)
    density = score.score_report(cfg, report)

    return Evidence(
        sources=len(entries),
        archived=len(kept),
        unarchived=unarchived,
        archived_from=first,
        archived_to=last,
        quotations=written,
        quotations_verified=verified,
        cited=density.cited,
        assessed=density.assessed,
        unmarked=density.unmarked,
        density=density.density,
    )


# ── inputs ───────────────────────────────────────────────────────────────────


def inputs(report: Report) -> Inputs:
    """Which declared data files actually produced rows.

    `data.scan` has already read every CSV to describe it, so the row count is
    the file's own answer rather than an assumption about the pipeline that wrote
    it. A file with zero rows is the case worth printing: everything upstream
    reported success and the table has nothing in it.
    """
    files = tuple(
        DataInput(
            rel=datafile.rel,
            key=datafile.key,
            rows=datafile.rows,
            columns=datafile.columns,
        )
        for datafile in data.scan(report)
    )
    return Inputs(
        declared=len(files),
        with_rows=sum(1 for datafile in files if datafile.rows > 0),
        empty=tuple(datafile.rel for datafile in files if datafile.rows <= 0),
        files=files,
    )


# ── gathering ────────────────────────────────────────────────────────────────


def gather(cfg: Config, report: Report) -> Facts:
    """Every fact about this build of this report, with the ones that failed named.

    Each group is attempted on its own and every exception is caught, including
    the ones nobody has thought of yet: this runs inside `build`, and the whole
    contract of the module is that a fact we could not gather does not stop a
    report compiling. A group that raises leaves its defaults in place and adds
    its name to `gaps`, so the colophon says which part of its own account is
    missing instead of quietly printing zeros.
    """
    gaps: list[str] = []
    collected: dict = {}
    for name, produce in (
        ("toolchain", lambda: toolchain(cfg)),
        ("provenance", lambda: provenance(cfg)),
        ("evidence", lambda: evidence(cfg, report)),
        ("inputs", lambda: inputs(report)),
    ):
        try:
            collected[name] = produce()
        except Exception:  # noqa: BLE001 — see the docstring: never fail a build
            gaps.append(name)
    return Facts(report=report.id, gaps=tuple(gaps), **collected)


# ── on disk ──────────────────────────────────────────────────────────────────


def path_for(cfg: Config, report: Report) -> Path:
    """`.build/facts/<report-id>.json`, mirroring the report tree the way `out/`
    does, so two reports with the same slug in different folders cannot collide."""
    return cfg.build / FACTS_DIR / f"{report.id}.json"


def project_path(cfg: Config, report: Report) -> str:
    """The value a report passes as `colophon:`.

    Project-absolute, like every other path a report hands to its design: Typst
    resolves a leading "/" against `--root`, and a relative path would break the
    moment the report folder moved.
    """
    return cfg.project_path(path_for(cfg, report))


def to_json(facts: Facts) -> dict:
    """The record as JSON sees it — every sequence a list.

    `asdict` leaves a tuple a tuple, which `json.dumps` happens to serialise as
    an array anyway; converting here means a caller that does something else with
    this payload does not have to know which fields were declared frozen.
    """
    payload = asdict(facts)
    payload["gaps"] = list(payload["gaps"])
    payload["inputs"]["empty"] = list(payload["inputs"]["empty"])
    payload["inputs"]["files"] = [dict(entry) for entry in payload["inputs"]["files"]]
    return payload


def write(cfg: Config, report: Report) -> Facts:
    """Gather and write the facts for one report, and return them.

    The file is written even when every group failed — see the module docstring:
    the design reads this path with `json()`, and a colophon that has to print
    `unknown` is a working document while a missing file is a compile error.
    """
    facts = gather(cfg, report)
    path = path_for(cfg, report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_json(facts), indent=2) + "\n", encoding="utf-8")
    return facts


def build(cfg: Config, target: str | None = None) -> list[Facts]:
    """Write the facts for every report, or for one target, and return them.

    `build.py` calls `write` per report as it compiles it; this is the standalone
    form, for a command that wants to show the facts without building anything.
    """
    return [write(cfg, report) for report in reports(cfg, target)]


# ── output ───────────────────────────────────────────────────────────────────


def _percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def report_facts(cfg: Config, collected: Sequence[Facts]) -> int:
    """Print the facts the way the colophon states them. Always 0 — this is a
    description of a build, and a description cannot fail."""
    if not collected:
        print("  no reports to describe")
        return 0
    for facts in collected:
        tools, prov, ev, ins = (
            facts.toolchain,
            facts.provenance,
            facts.evidence,
            facts.inputs,
        )
        print(f"  {facts.report}")
        print(f"    built       {prov.built}")
        print(f"    toolchain   {tools.typst} · report-maker {tools.engine} · python {tools.python}")
        if prov.repo:
            dirt = "with uncommitted changes" if prov.dirty else "clean"
            print(f"    vault       {prov.revision} on {prov.branch or 'no branch'}, {dirt}")
        else:
            print("    vault       not under version control")
        window = (
            f" ({ev.archived_from} – {ev.archived_to})"
            if ev.archived_from and ev.archived_to
            else ""
        )
        print(
            f"    evidence    {ev.sources} source(s), {ev.archived} archived{window}, "
            f"{ev.unarchived} unarchived"
        )
        print(
            f"    quotations  {ev.quotations_verified} of {ev.quotations} verified "
            f"verbatim · density {_percent(ev.density)}"
        )
        if ins.declared:
            print(f"    data        {ins.with_rows} of {ins.declared} declared file(s) carried rows")
            for rel in ins.empty:
                print(f"                  {rel} produced no rows")
        if facts.gaps:
            print(f"    not known   {', '.join(facts.gaps)}")
    return 0
