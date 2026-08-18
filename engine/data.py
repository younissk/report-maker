"""A CSV in a report folder is a source, and is treated as one.

A number is a fact about the world, so by the house rule it is either cited or it
is an opinion — and a number typed into the prose is neither. It was true of some
export, once. The export moves on; the sentence does not. Nothing in the file
records which export it was, so nothing can tell anybody that the sentence has
stopped being true.

The fix is not discipline, it is plumbing. The numbers live in
`reports/<id>/data/*.csv`, the table reads them at compile time through
`srctable` (see `templates/base/data.typ`), and this module registers the file in
`sources.yml` as a `Misc` entry carrying its sha256, its shape and its path. That
one move buys three things at once: the figures appear in the References
inventory like every other piece of evidence, a reader can be told exactly which
bytes the table was built from, and a changed file becomes a *build failure*
rather than a silently different number.

That last one is the point. E011 fires when the sha recorded in `sources.yml` no
longer matches the file on disk, which is the moment a stale spreadsheet would
otherwise slip into a signed-off report. The other rules are hygiene around it:
E010 catches a table pointed at nothing, W005 catches data nobody used, W006
catches a table citing the wrong entry.

## Degenerate derivations — W007, W008, W009

E010 catches a table pointed at nothing and E011 catches a table pointed at
something that moved. Neither catches the failure that actually happens: a table
pointed at something whose cells are *absent*, carrying a column that a collector
filled with nothing, or with one value, or with zeros.

The worked example is a report in a sibling repository. Its exporter read
`sig.get("ams_course_count", 0) or 0` and derived a label from the result — `if
cc == 0: return "WHITE SPACE (absent in AMS)"`. Its collector returned 0 for
every row when the source database was missing. The surviving database in fact
held 421 courses for the category the report published as untapped white space,
and the report's own provenance line listed five sources while silently omitting
the one that had failed. Nothing in that chain was a bug in the ordinary sense.
Each step did what it said. The defect was that a missing source and a measured
zero were spelled the same way, and every layer downstream believed the spelling.

So three warnings, all of them cheap, all of them about the shape of a column
rather than the shape of the file:

- **W007** — a declared column that is entirely empty. That is a source that
  failed, arriving as data.
- **W008** — a column carrying one value in every row. Almost always a join that
  matched nothing, or a default nothing overwrote.
- **W009** — a numeric column that is exactly 0 all the way down. The same
  suspicion as W008, said in the dialect the failure above spoke.

They are warnings and not errors on purpose: a genuinely constant column exists
(a currency, a survey year), and a linter that refuses to build a legitimate
report is a linter people route around. One finding per column, most specific
first, so a column of zeros is reported as zeros rather than twice.

The render side of the same rule lives in `templates/base/data.typ`: an empty
cell prints an explicit not-measured mark, never a blank and never a zero.

## Having looked, and found nothing

The house rule already says absence is reported as absence — "no pricing on any
reviewed page @key". But `@key` there is a page that merely failed to mention the
thing, and "this page does not say X" is a weaker statement than "I searched the
corpus for X and it is not there". Until there is a source shape for the second,
every absence claim is quietly citing the first.

`absence_source` builds one: a `Misc` entry recording a completed search — the
corpus, the exact query, the date, and the zero result — so a search that found
nothing is filed, inventoried in References and cited exactly like a page that
said something. It is our own measurement, so it carries `author: own search`,
the same way a data file carries `author: own data`.

Findings leave here as plain tuples and are converted to `check.Finding` by
`to_findings`, so this module does not have to be edited every time the linter's
own record type grows a field.
"""

from __future__ import annotations

import csv as csvlib
import dataclasses
import datetime as dt
import hashlib
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import sources
from .config import Config
from .workspace import Report, reports

# Where a report keeps its numbers. Alongside `diagrams/` and `snapshots/`: the
# evidence a report rests on travels inside the report folder, so moving, zipping
# or handing the folder over takes the evidence with it.
DATA_DIR = "data"

SUFFIXES = (".csv", ".tsv", ".tab")

# Citation keys for data files are prefixed, so a bibliography that mixes pages,
# interviews and exports still sorts the exports together and a reader seeing
# `@data-prices` in the prose knows what kind of thing they are about to look at.
KEY_PREFIX = "data-"

# The same idea for a search that found nothing: `@absence-ams-excel` reads, in
# the middle of a sentence, as the kind of claim it is supporting.
ABSENCE_PREFIX = "absence-"

# A search we ran is a measurement we took, so it is attributed the way the
# starter attributes one — see `own-measurement` there, and `own data` above.
ABSENCE_AUTHOR = "own search"

# The last segment of an absence entry's `note:`, and deliberately last: `registry`
# below identifies a data file by a note *ending* in a path with a data suffix, so
# an absence entry whose free-form note happened to end in "prices.csv" would
# otherwise be mistaken for a registered CSV. Putting the invariant marker at the
# end makes that collision impossible rather than unlikely.
ABSENCE_RESULT = "result: no matches"

# The Typst helper this module reasons about. It is not `table` — a bare table is
# check.py's W002 — and not `srcfig`; it is the one call that names a file.
CALL = "srctable"

NOTE_SEP = " · "

SHA_IN_NOTE = re.compile(r"sha256:\s*([0-9a-fA-F]+)")

# The first positional argument of a call, when it is a string literal. A path
# built from a variable is not something a linter can follow, and pretending
# otherwise would produce confident nonsense.
PATH_ARG = re.compile(r'^\(\s*"((?:[^"\\]|\\.)*)"')

# Ornament a number can wear without ceasing to be one, kept in step with the
# `_ORNAMENT` list in templates/base/data.typ. The lists match; the tests either
# side of them differ, and deliberately. Typst uses it to decide which columns to
# right-align, and is loose enough that a column of ISO dates lines up with its
# neighbours. Here it only decides which row is the header, so it asks `float`
# and a date is a word.
ORNAMENT = (",", " ", " ", "%", "$", "€", "£", "(", ")", "'", "+", "_")


class DataError(RuntimeError):
    pass


def _rules():
    """The linter's scanner, imported late.

    This module borrows `scrub`, `calls`, `cited_keys` and `line_of` from
    `check`, and `check` merges the findings below into its own run. Importing
    lazily keeps that dependency one-way at import time, so neither module has to
    care which of the two loads first.
    """
    from . import check

    return check


# ── one data file ────────────────────────────────────────────────────────────


@dataclass
class DataFile:
    """A data file as the bibliography will describe it.

    `rel` is relative to the report folder, not the vault, because that is the
    part that stays true when the report is filed somewhere else — and it is what
    goes in the `note:`, where it has to still make sense a year later.
    """

    path: Path
    rel: str
    key: str
    sha256: str
    rows: int
    columns: int
    headers: list[str] = field(default_factory=list)
    size: int = 0
    mtime: float = 0.0
    delimiter: str = ","

    @property
    def date(self) -> str:
        """When the numbers were last written, as an ISO date.

        The modification time is the honest answer to "as of when?" for a file we
        exported ourselves, and it is what goes in the entry's `date:`.
        """
        return dt.datetime.fromtimestamp(self.mtime).date().isoformat()

    @property
    def note(self) -> str:
        """The `note:` line: checksum, shape, path — in that order, because the
        checksum is the part a rule reads back."""
        return NOTE_SEP.join(
            (
                f"sha256:{self.sha256}",
                f"{self.rows} rows × {self.columns} columns",
                self.rel,
            )
        )

    @property
    def title(self) -> str:
        words = [w for w in re.split(r"[^A-Za-z0-9]+", self.path.stem) if w]
        human = " ".join(words) or self.path.name
        return f"{human[:1].upper()}{human[1:]} — data file"


def sha_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(path: Path) -> str:
    # Lenient: a data file exported by a spreadsheet is as likely to carry a BOM
    # or a stray cp1252 byte as not, and neither is a reason to refuse to
    # describe it. Typst reads the bytes itself when it builds the table.
    return Path(path).read_bytes().decode("utf-8-sig", errors="replace")


def sniff(text: str, path: Path) -> str:
    """The delimiter the file actually uses.

    The extension is only a hint — plenty of `.csv` files from European
    spreadsheets are semicolon-separated — so the sniffer gets first say and the
    extension is the fallback for the files it cannot read (a single column has
    no delimiter to find).
    """
    default = "\t" if Path(path).suffix.lower() in (".tsv", ".tab") else ","
    sample = "\n".join(text.splitlines()[:20])
    if not sample.strip():
        return default
    try:
        return csvlib.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csvlib.Error:
        return default


def _numeric(cell: str) -> bool:
    stripped = str(cell).strip()
    for token in ORNAMENT:
        stripped = stripped.replace(token, "")
    if not stripped:
        return False
    try:
        float(stripped)
    except ValueError:
        return False
    return True


def _has_header(text: str, table: Sequence[Sequence[str]]) -> bool:
    """Whether the first row names the columns or counts something.

    Shape decides it wherever shape can: a row of labels does not contain bare
    numbers, and a table of numbers has them somewhere below the labels. Those
    two tests settle almost every real file, and they settle it the same way a
    person glancing at the file would.

    `csv.Sniffer` is asked only about the genuinely ambiguous case — text all the
    way down — because it is a guess either way there, and because it is wrong
    often enough on ordinary priced-column files to not be worth trusting first.
    """
    if len(table) < 2:
        return False
    if any(_numeric(cell) for cell in table[0]):
        return False
    if any(_numeric(cell) for row in table[1:] for cell in row):
        return True
    try:
        return csvlib.Sniffer().has_header("\n".join(text.splitlines()[:20]))
    except csvlib.Error:
        return True


def _rel(path: Path, report: Report | None) -> str:
    if report is None:
        return Path(path).name
    try:
        return Path(path).resolve().relative_to(report.folder.resolve()).as_posix()
    except ValueError:
        return Path(path).name


def key_for(path: Path, report: Report | None = None) -> str:
    """The bibliography key a data file gets by default.

    `data/prices.csv` becomes `data-prices`, and `data/2026/q1.csv` becomes
    `data-2026-q1` — the folders under `data/` join in so two quarters filed side
    by side do not collide. A file registered by hand under some other key keeps
    it; `scan` reads the real key back out of `sources.yml`.
    """
    rel = _rel(path, report)
    stem = rel[: len(rel) - len(Path(rel).suffix)] if Path(rel).suffix else rel
    if stem.startswith(DATA_DIR + "/"):
        stem = stem[len(DATA_DIR) + 1 :]
    words = [w for w in re.split(r"[^A-Za-z0-9]+", stem.lower()) if w]
    return KEY_PREFIX + ("-".join(words) or "file")


def describe(path: Path, *, report: Report | None = None) -> DataFile:
    """Read a data file and say what it is: shape, dialect, checksum.

    `report` is optional so the function can be pointed at a file that has not
    been filed yet — `add` describes a candidate before it decides where it
    belongs. Without it, `rel` degrades to the bare filename.
    """
    path = Path(path)
    if not path.is_file():
        raise DataError(f"no such data file: {path}")
    text = _text(path)
    delimiter = sniff(text, path)
    table = [row for row in csvlib.reader(text.splitlines(), delimiter=delimiter) if row]
    headers = [cell.strip() for cell in table[0]] if _has_header(text, table) else []
    stat = path.stat()
    return DataFile(
        path=path,
        rel=_rel(path, report),
        key=key_for(path, report),
        sha256=sha_of(path),
        rows=max(len(table) - (1 if headers else 0), 0),
        columns=max((len(row) for row in table), default=0),
        headers=headers,
        size=stat.st_size,
        mtime=stat.st_mtime,
        delimiter=delimiter,
    )


# ── columns, and the shapes a failed collector leaves ────────────────────────


@dataclass(frozen=True)
class Column:
    """One column's body cells, with whatever the file calls it.

    Cells are stripped and padded to the table's width, so a row that ran out of
    commas early contributes an empty cell rather than disappearing from the
    column. A value that is not there because the line was short is not there.
    """

    index: int
    header: str
    cells: tuple[str, ...]

    @property
    def name(self) -> str:
        """How a message should refer to this column.

        The header when the file declares one; otherwise its 1-based position,
        counted the way a person counts columns in a spreadsheet rather than the
        way Python counts them.
        """
        return self.header or f"column {self.index + 1}"


def columns_of(path: Path) -> list[Column]:
    """Read a data file column-wise.

    `describe` reads the same file for its shape and checksum and this reads it
    again for its contents, which is one extra read of a file small enough to sit
    in a report folder. The alternative is caching the parse on `DataFile`, and a
    cached parse that can disagree with the bytes on disk is precisely the class
    of defect this module exists to prevent.
    """
    path = Path(path)
    if not path.is_file():
        raise DataError(f"no such data file: {path}")
    text = _text(path)
    delimiter = sniff(text, path)
    table = [row for row in csvlib.reader(text.splitlines(), delimiter=delimiter) if row]
    if not table:
        return []
    headers = [cell.strip() for cell in table[0]] if _has_header(text, table) else []
    body = table[1:] if headers else table
    width = max(len(row) for row in table)
    return [
        Column(
            index=index,
            header=headers[index] if index < len(headers) else "",
            cells=tuple(
                (row[index].strip() if index < len(row) else "") for row in body
            ),
        )
        for index in range(width)
    ]


def _number(cell: str) -> float | None:
    """A cell as a number, or None when it is not one.

    The same ornament `_numeric` strips, because "€0.00" and "(0)" are zeros and
    a rule about zeros that only recognised the bare glyph would miss every
    formatted export.
    """
    stripped = str(cell).strip()
    for token in ORNAMENT:
        stripped = stripped.replace(token, "")
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def degenerate(datafile: DataFile) -> list[tuple[str, str]]:
    """The degenerate-derivation warnings for one file, as (code, message).

    A column is examined on its own, and reports at most one finding: the checks
    run most specific first — empty, then all-zero, then constant — because a
    column of zeros satisfies both W009 and W008, and saying the same thing twice
    in two different words trains people to skim both.

    W008 and W009 need more than one row to mean anything. A single-row file
    cannot have a constant column in any sense worth warning about; every column
    in it is constant by arithmetic.
    """
    out: list[tuple[str, str]] = []
    for column in columns_of(datafile.path):
        if not column.cells:
            continue
        rows = len(column.cells)
        values = [cell for cell in column.cells if cell]

        # W007 — the source failed, and arrived as data. A collector that returns
        # nothing writes exactly this file, and every reader downstream sees a
        # column that was measured and came back blank.
        if not values:
            out.append(
                (
                    "W007",
                    f'{datafile.rel}: "{column.name}" is empty in all {rows} rows '
                    "— a declared column with no values is a source that failed, "
                    "not a measurement of nothing. Check the export that produced "
                    "it; if the thing genuinely is not there, record the search "
                    "with `report-maker data absence` and cite that, because a "
                    "table reading this column would print an absence as a figure",
                )
            )
            continue

        if len(values) < rows or rows < 2:
            continue  # a partly-filled column is a fact; a one-row file is not

        numbers = [_number(cell) for cell in values]

        # W009 — the burgwiss shape, in its own dialect. `get(key, 0) or 0` turns
        # a missing database into a column of exact zeros, and a zero is a fact
        # about the world that anything downstream is entitled to derive from.
        if all(number == 0 for number in numbers):
            out.append(
                (
                    "W009",
                    f'{datafile.rel}: "{column.name}" is exactly 0 in all {rows} '
                    "rows — that is what a collector returns when its source is "
                    "missing, not usually what a measurement returns. Confirm the "
                    "zeros were measured before any label, ranking or share is "
                    "derived from them; a derived label inherits the defect "
                    "silently and reads as a finding",
                )
            )
            continue

        # W008 — one value, every row. A join that matched nothing fills a column
        # with the default it was given, and the default is indistinguishable from
        # a reading once it is set in a table.
        if len(set(values)) == 1:
            out.append(
                (
                    "W008",
                    f'{datafile.rel}: "{column.name}" says {values[0]!r} in all '
                    f"{rows} rows — a constant column is usually a join that "
                    "matched nothing or a default nothing overwrote, rather than "
                    "a measurement that came out the same every time. Check the "
                    "export before a table cites it; if it is genuinely constant, "
                    "it is a caption, not a column",
                )
            )

    return out


# ── the bibliography side ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Registered:
    """What `sources.yml` currently says about one data file."""

    key: str
    sha256: str | None
    line: int


def source_entry(datafile: DataFile, *, title: str | None = None) -> dict:
    """The hayagriva record for a data file.

    `Misc` with an author of "own data" is how the bibliography already spells a
    measurement we took ourselves — see the `own-measurement` entry in the
    starter — and an export is a measurement of a system we run. The `note:`
    carries the checksum and the shape, which is what makes the entry falsifiable
    rather than decorative.
    """
    return {
        "type": "Misc",
        "title": title or datafile.title,
        "author": "own data",
        "date": datafile.date,
        "note": datafile.note,
    }


def _note_of(source: sources.Source) -> str:
    value = source.fields.get("note")
    return value if isinstance(value, str) else ""


def registry(report: Report) -> dict[str, Registered]:
    """Which bibliography entry stands for which data file.

    Read back out of the notes this module writes, keyed by the report-relative
    path they end with. Reading it back rather than recomputing the key is what
    lets somebody register a file under a name of their own choosing without
    every rule below deciding it is wrong.
    """
    found: dict[str, Registered] = {}
    for source in sources.parse(report.sources):
        note = _note_of(source)
        if not note:
            continue
        rel = note.rsplit(NOTE_SEP, 1)[-1].strip()
        if not rel.lower().endswith(SUFFIXES):
            continue
        match = SHA_IN_NOTE.search(note)
        found[rel] = Registered(
            key=source.key,
            sha256=match.group(1).lower() if match else None,
            line=source.line,
        )
    return found


# ── what is in a report ──────────────────────────────────────────────────────


def data_dir(report: Report) -> Path:
    return report.folder / DATA_DIR


def paths(report: Report) -> list[Path]:
    """The data files a report has, excluding the archived revisions of them.

    `datarev` keeps superseded exports beside the live one as
    `prices.<date>.csv`. Those are history, not a second data file: counted here
    they would each become a W005 nobody can act on and an unregistered file the
    linter nags about, so keeping the archive carefully would make the report
    harder to check. Imported lazily for the same reason as `_rules` — `datarev`
    reads this module, and the dependency stays one-way at import time.
    """
    from . import datarev

    root = data_dir(report)
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUFFIXES
        and not datarev.is_revision(path)
        and not any(
            part.startswith((".", "_")) for part in path.relative_to(root).parts
        )
    )


def scan(report: Report) -> list[DataFile]:
    """Every data file the report carries, with the key it is registered under."""
    known = registry(report)
    found = []
    for path in paths(report):
        datafile = describe(path, report=report)
        registered = known.get(datafile.rel)
        if registered is not None:
            datafile.key = registered.key
        found.append(datafile)
    return found


def resolve(report: Report, written: str) -> Path:
    """The file a `srctable("…")` argument names.

    A leading "/" is project-absolute — Typst resolves it against the vault root,
    which is why the house style insists on it. Anything else is resolved against
    the report folder, so the rules can still say something useful about a report
    that used a relative path.
    """
    if written.startswith("/"):
        return report.cfg.root / written.lstrip("/")
    return report.folder / written


def referenced(report: Report) -> dict[str, list[int]]:
    """Every `srctable()` call, as written path → the lines it is called on.

    The keys are the literal strings in the source, not resolved paths: a rule
    that reports a missing file has to quote back what the author actually typed,
    or the message is about a path they never wrote.
    """
    rules = _rules()
    if not report.main.is_file():
        return {}
    src = rules.scrub(report.main.read_text(encoding="utf-8"))
    found: dict[str, list[int]] = {}
    for start, _end, args in rules.calls(src, CALL):
        match = PATH_ARG.match(args)
        if match is None:
            continue
        found.setdefault(match.group(1), []).append(rules.line_of(src, start))
    return found


# ── rules ────────────────────────────────────────────────────────────────────


def findings(report: Report) -> list[tuple[str, str, Path, int, str]]:
    """The data rules, as (level, code, path, line, message).

    Plain tuples rather than `check.Finding`, so this module keeps working while
    the linter's record type changes underneath it — `to_findings` does the
    conversion at the boundary.
    """
    rules = _rules()
    out: list[tuple[str, str, Path, int, str]] = []
    known = {datafile.path.resolve(): datafile for datafile in scan(report)}
    registered = registry(report)
    used: set[Path] = set()

    if report.main.is_file():
        src = rules.scrub(report.main.read_text(encoding="utf-8"))
        for start, _end, args in rules.calls(src, CALL):
            match = PATH_ARG.match(args)
            if match is None:
                continue  # a computed path; nothing here can follow it
            written = match.group(1)
            line = rules.line_of(src, start)
            datafile = known.get(resolve(report, written).resolve())

            # E010 — a table built from nothing. Typst would fail too, but with a
            # message about a file, not about a report.
            if datafile is None:
                out.append(
                    (
                        "error",
                        "E010",
                        report.main,
                        line,
                        f'srctable("{written}") — no such file. Paths are '
                        f"project-absolute: /reports/{report.id}/{DATA_DIR}/<name>.csv",
                    )
                )
                continue
            used.add(datafile.path.resolve())

            # W006 — cited, but not at the file the numbers came from. The table
            # would carry a source that says nothing about these figures.
            cited = {key for key, _index in rules.cited_keys(args)}
            if datafile.key in cited:
                continue
            if datafile.rel in registered:
                naming = ", ".join(f"@{key}" for key in sorted(cited)) or "no source"
                out.append(
                    (
                        "warning",
                        "W006",
                        report.main,
                        line,
                        f"srctable({datafile.rel}) cites {naming}, but the file is "
                        f"registered as @{datafile.key} — cite the data, not "
                        "something next to it",
                    )
                )
            else:
                out.append(
                    (
                        "warning",
                        "W006",
                        report.main,
                        line,
                        f"{datafile.rel} is not registered in {report.sources.name} — "
                        f"run `report-maker data add {report.id} {datafile.rel}` so "
                        "the numbers carry a checksummed source",
                    )
                )

    # W005 — data nobody used. Not an error: a file may be staged before the
    # section that reads it is written. It is still worth saying, because the
    # other way this happens is a table that was deleted and a file that was not.
    for path, datafile in known.items():
        if path in used:
            continue
        out.append(
            (
                "warning",
                "W005",
                datafile.path,
                1,
                f"no {CALL}() reads {datafile.rel} — place the table, or remove "
                "the file so it is not mistaken for evidence",
            )
        )

    # W007 / W008 / W009 — the shape of the numbers, not their provenance. These
    # run over every data file the report carries rather than only the ones a
    # table reads, because a degenerate column is worth knowing about while the
    # file is still being staged, and because the report that quotes it may be
    # written next week. They point at line 1: a column is declared in the header
    # row, and a warning about a whole column has no better line to name.
    for datafile in known.values():
        for code, message in degenerate(datafile):
            out.append(("warning", code, datafile.path, 1, message))

    # E011 — the load-bearing one. Everything above is tidiness; this is the rule
    # that turns a refreshed spreadsheet from a silently wrong number into a
    # failed build.
    for datafile in known.values():
        record = registered.get(datafile.rel)
        if record is None or record.sha256 is None:
            continue
        if record.sha256 == datafile.sha256:
            continue
        out.append(
            (
                "error",
                "E011",
                report.sources,
                record.line,
                f"@{record.key} records sha256:{record.sha256} for {datafile.rel}, "
                f"but the file is now sha256:{datafile.sha256} — the report's "
                "numbers may have moved under it. Re-read every table citing "
                f"@{record.key}, then re-register with "
                f"`report-maker data add {report.id} {datafile.rel}`",
            )
        )

    return out


LEVEL_ORDER = {"error": 0, "warning": 1}


def check(cfg: Config, target: str | None = None) -> list[tuple[str, str, Path, int, str]]:
    """Every data finding in the vault, or under one target. Errors first."""
    records = [record for report in reports(cfg, target) for record in findings(report)]
    return sorted(records, key=lambda r: (LEVEL_ORDER.get(r[0], 9), str(r[2]), r[3]))


def to_findings(records: Sequence[tuple[str, str, Path, int, str]]) -> list:
    """Convert to whatever `check.Finding` currently is.

    Built defensively and field by field: `check` is where the citation rule
    lives and its record type is allowed to grow, and a data rule that stopped
    reporting because a field was added somewhere else would be exactly the kind
    of silent gap this module exists to close.
    """
    from . import check as rules

    finding = rules.Finding
    names = (
        {f.name for f in dataclasses.fields(finding)}
        if dataclasses.is_dataclass(finding)
        else set()
    )
    out = []
    for level, code, path, line, message in records:
        values = {
            "level": level,
            "code": code,
            "path": Path(path),
            "line": int(line),
            "message": message,
        }
        if not names:
            out.append(finding(**values))
            continue
        kwargs = {name: value for name, value in values.items() if name in names}
        for spec in dataclasses.fields(finding):
            if spec.name in kwargs:
                continue
            if (
                spec.default is not dataclasses.MISSING
                or spec.default_factory is not dataclasses.MISSING
            ):
                continue
            kwargs[spec.name] = _blank(str(spec.type))
        out.append(finding(**kwargs))
    return out


def _blank(annotation: str):
    """A harmless value for a field this module knows nothing about."""
    if "int" in annotation:
        return 0
    if "str" in annotation:
        return ""
    return None


# ── commands ─────────────────────────────────────────────────────────────────


def one(cfg: Config, target: str) -> Report:
    found = reports(cfg, target)
    if len(found) != 1:
        raise DataError(
            f"{target!r} matches {len(found)} reports — name exactly one: "
            + ", ".join(report.id for report in found)
        )
    return found[0]


def _locate(cfg: Config, report: Report, given: str) -> Path:
    """Find the file the user meant, from wherever they are standing."""
    candidate = Path(given)
    tries = (
        [candidate, cfg.root / str(candidate).lstrip("/")]
        if candidate.is_absolute()
        else [
            Path.cwd() / candidate,
            data_dir(report) / candidate,
            report.folder / candidate,
            cfg.root / candidate,
        ]
    )
    for path in tries:
        if path.is_file():
            return path.resolve()
    raise DataError(f"no such file: {given}")


def _house(report: Report, src: Path) -> Path:
    """Bring a data file inside the report folder.

    Evidence lives with the report that cites it, the same way snapshots do. A
    report folder that can be moved, zipped or handed to a client whole is the
    reason the layout is shaped this way, and a table reading a CSV from
    somewhere else on the disk quietly breaks that.
    """
    try:
        src.relative_to(report.folder.resolve())
        return src
    except ValueError:
        pass
    folder = data_dir(report)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / src.name
    existed = dest.is_file()
    if existed and sha_of(dest) == sha_of(src):
        return dest
    shutil.copy2(src, dest)
    verb = "replaced" if existed else "copied"
    print(f"  {verb} {dest.relative_to(report.cfg.root)}")
    return dest


def add(
    cfg: Config,
    target: str,
    csv_path: str,
    *,
    key: str | None = None,
    title: str | None = None,
) -> DataFile:
    """Register a data file as a source, and return what was registered.

    `upsert` rather than `append`: re-running this on a refreshed export is the
    documented way to clear E011, so it has to rewrite the checksum rather than
    quietly do nothing.
    """
    report = one(cfg, target)
    datafile = describe(_house(report, _locate(cfg, report, csv_path)), report=report)
    if key:
        datafile.key = key
    else:
        registered = registry(report).get(datafile.rel)
        if registered is not None:
            datafile.key = registered.key
    sources.upsert(
        report.sources,
        sources.Source(key=datafile.key, fields=source_entry(datafile, title=title)),
    )
    return datafile


def inventory(cfg: Config, target: str | None = None) -> list[DataFile]:
    return [datafile for report in reports(cfg, target) for datafile in scan(report)]


def srctable_call(cfg: Config, datafile: DataFile) -> str:
    """The line to paste into the report. Printed by `data add`, because the
    whole registration is pointless until a table actually reads the file."""
    return (
        f"#srctable(\n"
        f'  "{cfg.project_path(datafile.path)}",\n'
        f"  caption: [What the reader should take from these numbers.],\n"
        f"  source: [@{datafile.key}],\n"
        f")"
    )


# ── a search that found nothing ──────────────────────────────────────────────
#
# Two words that look the same on the page and are not: a page that does not
# mention a price, and a search of every page that established there is no price
# anywhere. Only the second is evidence of absence, and until it has a shape in
# the bibliography, every absence claim in the vault is citing the first.

# How many words of the corpus and of the query reach the key. Both halves have
# to be in it: search one corpus twice and `@absence-ams` stops identifying which
# search, which is the point at which a reader can no longer check the claim.
ABSENCE_KEY_TOKENS = 2


def _key_words(text: str) -> list[str]:
    return [word for word in re.split(r"[^A-Za-z0-9]+", text.lower()) if word]


def absence_key(corpus: str, query: str, taken: Sequence[str] | set[str] = ()) -> str:
    """A key for one completed search, unused in this bibliography."""
    parts = (
        _key_words(corpus)[:ABSENCE_KEY_TOKENS]
        + _key_words(query)[:ABSENCE_KEY_TOKENS]
    )
    base = ABSENCE_PREFIX + ("-".join(parts) or "search")
    if base not in set(taken):
        return base
    suffix = 2
    while f"{base}-{suffix}" in set(taken):
        suffix += 1
    return f"{base}-{suffix}"


def absence_note(corpus: str, query: str, note: str | None = None) -> str:
    """The `note:` line: what was searched, what was asked, and the result.

    `ABSENCE_RESULT` is last so the note can never end in something that looks
    like a data-file path — see the constant. The free-form note sits before it,
    which is also where a reader wants it: the caveats belong with the method,
    not after the result.
    """
    parts = [f"searched: {corpus.strip()}", f'query: "{query.strip()}"']
    if note and note.strip():
        parts.append(note.strip())
    parts.append(ABSENCE_RESULT)
    return NOTE_SEP.join(parts)


def absence_source(
    corpus: str,
    query: str,
    *,
    date: str | None = None,
    note: str | None = None,
    key: str | None = None,
    taken: Sequence[str] | set[str] = (),
) -> sources.Source:
    """A bibliography entry for a search that returned nothing.

    `Misc` with `author: own search`, the way a data file is `Misc` with
    `author: own data` — both are measurements we took, and the bibliography
    already spells one of them that way. The four things that make the claim
    falsifiable are all in the entry: which corpus, which exact query, on which
    date, and the result. Somebody who doubts it can run the query again.

    Corpus and query are required and must say something, because an entry that
    records a search without recording what was searched is a citation that
    cannot be checked, which is the failure the whole module is about.
    """
    if not corpus.strip():
        raise DataError("an absence source needs the corpus that was searched")
    if not query.strip():
        raise DataError("an absence source needs the exact query that was run")
    when = (date or dt.date.today().isoformat()).strip()
    return sources.Source(
        key=key or absence_key(corpus, query, taken),
        fields={
            "type": "Misc",
            "title": f'Search of {corpus.strip()} for "{query.strip()}" — no results',
            "author": ABSENCE_AUTHOR,
            "date": when,
            "note": absence_note(corpus, query, note),
        },
    )


def add_absence(
    cfg: Config,
    target: str,
    corpus: str,
    query: str,
    *,
    date: str | None = None,
    note: str | None = None,
    key: str | None = None,
) -> tuple[Report, sources.Source]:
    """Record a completed search in a report's bibliography.

    `append`, not `upsert`, unless the caller names the key. Searching the same
    corpus again next quarter is a *second* search: the first one is still true
    of the date it was run, and a report that cited it is still standing on
    something. Overwriting it would delete evidence in the same move that adds
    some, which is the rule `verify --refresh` already follows for snapshots.
    """
    report = one(cfg, target)
    taken = sources.keys(report.sources)
    source = absence_source(
        corpus, query, date=date, note=note, key=key, taken=taken
    )
    if key:
        sources.upsert(report.sources, source)
    else:
        sources.append(report.sources, source)
    return report, source


def absence_line(source: sources.Source, corpus: str, query: str) -> str:
    """The sentence to paste. An absence source is only worth adding if a
    sentence actually makes the absence claim, so the command hands one over."""
    return (
        f'No result for "{query.strip()}" anywhere in {corpus.strip()} '
        f"@{source.key}."
    )


# ── JSON and printing ────────────────────────────────────────────────────────


def _shown(path: Path, root: Path | None) -> str:
    if root is None:
        return str(path)
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def to_json(files: Sequence[DataFile], *, root: Path | None = None) -> list[dict]:
    return [
        {
            "key": datafile.key,
            "rel": datafile.rel,
            "path": _shown(datafile.path, root),
            "sha256": datafile.sha256,
            "rows": datafile.rows,
            "columns": datafile.columns,
            "headers": datafile.headers,
            "size": datafile.size,
            "mtime": datafile.mtime,
            "date": datafile.date,
            "delimiter": datafile.delimiter,
        }
        for datafile in files
    ]


def findings_json(
    records: Sequence[tuple[str, str, Path, int, str]], *, root: Path | None = None
) -> list[dict]:
    return [
        {
            "level": level,
            "code": code,
            "path": _shown(Path(path), root),
            "line": line,
            "message": message,
        }
        for level, code, path, line, message in records
    ]


def report_files(cfg: Config, files: Sequence[DataFile]) -> int:
    if not files:
        print("  no data files — put the numbers in reports/<id>/data/*.csv")
        return 0
    for datafile in files:
        shape = f"{datafile.rows}×{datafile.columns}"
        print(
            f"  {datafile.key:<28} {shape:>9}  {datafile.sha256[:12]}  "
            f"{_shown(datafile.path, cfg.root)}"
        )
    return 0


def report_absence(
    report: Report, source: sources.Source, corpus: str, query: str
) -> int:
    """What `data absence` prints. Shaped like `cite`'s output, and for the same
    reason: a command that files an entry and leaves you to go and look up what
    it called it has moved the clerical work rather than removed it."""
    print(f"  → {_shown(report.sources, report.cfg.root)} ({source.key})")
    print(f"\n  Cite it with: {absence_line(source, corpus, query)}")
    return 0


def report_findings(
    cfg: Config, records: Sequence[tuple[str, str, Path, int, str]]
) -> int:
    """Print data findings in the same shape `check` uses. Errors set the code."""
    errors = [record for record in records if record[0] == "error"]
    for level, code, path, line, message in records:
        where = f"{_shown(Path(path), cfg.root)}:{line}"
        print(f"  {level:<7} {code}  {where}  {message}")
    if not records:
        print("  every number reads from a checksummed file — no findings")
    else:
        print(f"\n  {len(errors)} error(s), {len(records) - len(errors)} warning(s)")
    return 1 if errors else 0
