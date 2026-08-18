"""A spreadsheet changing is a source changing, so it is archived like one.

`data.py` registers a CSV in `sources.yml` carrying its sha256, and E011 fires
the moment the bytes stop matching. That rule is the only thing standing between
a refreshed export and a signed-off report quietly carrying a different number,
and it works precisely because nothing in the engine is allowed to re-hash a file
on save. An editor that silently updated the checksum would leave the feature
with all of its machinery and none of its guarantee.

But numbers do legitimately get corrected, and a rule with no sanctioned way
through it is a rule people route around. This engine already has the shape for
that: `verify --refresh` never overwrites a snapshot, it moves the old copy aside
to `<key>.<fetched-date>.html` first, on the stated principle that an archive you
are willing to overwrite is not an archive. Data gets the same treatment.

    reports/<id>/data/prices.csv              what the report cites today
    reports/<id>/data/prices.2026-08-14.csv   what it cited before the edit
    reports/<id>/data/prices.2026-08-18.csv   what it cites as of the revision

Two rules hold the archive up. A dated revision is **never** overwritten — two
edits landing on one day become `-2` and `-3`, because a revision lost to a
filename collision defeats the entire point. And `reregister` is the **only**
path in the engine that moves a recorded sha: it is a deliberate act with a
visible consequence, and it hands back a summary — "412 rows → 418 rows, +6" —
so the person doing it sees what they just did to the report.

Every version the report has ever cited therefore exists on disk under its own
date: `archive` preserves the outgoing one before a write, `reregister` preserves
the incoming one as it registers it, and neither ever clobbers a file. The chain
has no gaps, which is the payoff. "The figure changed between the draft and the
final" stops being an argument and becomes a diff of two files, and a report can
cite a dated version of its own data the same way it cites a dated snapshot of
somebody else's page. That is the move the snapshot layer made for the web,
applied to the numbers.

No sidecar record is written, unlike `snapshots/<key>.json`. A page needs one
because its URL, fetch time and status live nowhere else; a CSV's shape and
checksum are recomputable from the bytes, and the date is in the filename, so a
record would only be a second thing to keep in sync.

Nothing here reads or writes `main.typ`. A revision changes what the numbers are,
never what the report says about them — re-reading the prose around a changed
table is a person's job, and the summary exists to tell them it is now waiting.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import data, sources
from .config import Config
from .workspace import Report

# A dated revision, matched against the *stem* so the suffix is free to be
# `.csv`, `.tsv` or `.tab`. The date shape is deliberately identical to
# `snapshot.ROTATED`: one archive convention across the whole vault means a
# person who has seen `acme-pricing.2026-03-04.html` already knows what
# `prices.2026-03-04.csv` is without being told.
REVISION = re.compile(
    r"^(?P<stem>.+)\.(?P<date>\d{4}-\d{2}-\d{2})(?:-(?P<counter>\d+))?$"
)

# The shape half of the `note:` that `data.DataFile.note` writes. Read back so a
# revision summary can say how many rows the report *used* to rest on, which is
# the number that makes a delta mean anything. `data.SHA_IN_NOTE` is the other
# half and is imported rather than restated, so the two modules cannot drift.
ROWS_IN_NOTE = re.compile(r"(\d+)\s*rows\s*×\s*(\d+)\s*columns")


# ── naming ───────────────────────────────────────────────────────────────────


def _parts(path: Path) -> tuple[str, str, int] | None:
    """(stem, date, counter) for a dated revision, or None for anything else.

    The counter defaults to 1 rather than 0 so that ordering by it puts an
    un-suffixed `prices.2026-08-18.csv` before `prices.2026-08-18-2.csv`, which
    is the order they were written in.
    """
    match = REVISION.match(Path(path).stem)
    if match is None:
        return None
    return (
        match.group("stem"),
        match.group("date"),
        int(match.group("counter") or 1),
    )


def is_revision(path: Path | str) -> bool:
    """Whether a path is a dated revision rather than a live data file.

    Exported because it is the predicate `data.paths` needs in order to keep
    revisions out of `scan` — a revision is history, not a second data file, and
    a vault with four revisions of one export must not grow four W005 warnings
    and four bibliography entries for them.
    """
    return _parts(Path(path)) is not None


def _free_name(path: Path, date: str) -> Path:
    """The next unused revision filename for `path` on `date`.

    Identical in shape and reasoning to `snapshot.rotate`: the loop exists so
    that a second edit on the same day lands beside the first rather than on top
    of it. Losing a revision to a name collision is the one failure this module
    cannot recover from, and it would look exactly like nothing happening.
    """
    target = path.with_name(f"{path.stem}.{date}{path.suffix}")
    counter = 2
    while target.exists():
        target = path.with_name(f"{path.stem}.{date}-{counter}{path.suffix}")
        counter += 1
    return target


# ── finding the file ─────────────────────────────────────────────────────────


def _locate(report: Report, given: str | Path, *, must_exist: bool = True) -> Path:
    """The data file the user meant, from wherever they are standing.

    The candidate list is deliberately the same one `data add` uses, because "the
    file I mean" has to mean the same thing in `data revise` as it does in `data
    add` or the two commands disagree about what they are pointed at. It is
    restated here rather than imported: that resolver is private to `data`, and a
    module reaching into another module's underscore surface is a dependency
    nobody declared and nobody can see.
    """
    candidate = Path(given)
    tries = (
        [candidate, report.cfg.root / str(candidate).lstrip("/")]
        if candidate.is_absolute()
        else [
            Path.cwd() / candidate,
            data.data_dir(report) / candidate,
            report.folder / candidate,
            report.cfg.root / candidate,
        ]
    )
    for path in tries:
        if path.is_file():
            return path.resolve()
    if must_exist:
        raise data.DataError(f"no such data file: {given}")
    # Nothing on disk: name where it *would* live, so history can still be listed
    # for a file somebody deleted. The revisions are the interesting part then.
    return (data.data_dir(report) / Path(given).name).resolve()


def _inside(report: Report, path: Path) -> Path:
    """Refuse to archive or reregister a file that is not the report's own.

    `data add` copies an outside file in before registering it, and everything
    here assumes that has happened: the report-relative path is what the
    bibliography records, and dated revisions belong beside the file they are
    revisions of. Writing them next to some CSV in `~/Downloads` would scatter a
    report's evidence across the disk, which is the thing the folder layout
    exists to prevent.
    """
    try:
        path.relative_to(report.folder.resolve())
    except ValueError:
        raise data.DataError(
            f"{path} is not inside {report.id} — bring it in first with "
            f"`report-maker data add {report.id} {path}`"
        ) from None
    return path


# ── revisions ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Revision:
    """One dated copy of a data file, as it was when it was preserved.

    `date` is read from the filename rather than from the filesystem. The name is
    what the archive asserts and what a citation would point at; an mtime is
    metadata a copy, a checkout or a backup restore can move, and a revision
    whose stated date drifted would be worse than no date at all.
    """

    path: Path
    rel: str
    date: str
    sha256: str
    rows: int
    columns: int
    size: int


def _revision(report: Report, path: Path, date: str) -> Revision:
    described = data.describe(path, report=report)
    return Revision(
        path=path,
        rel=described.rel,
        date=date,
        sha256=described.sha256,
        rows=described.rows,
        columns=described.columns,
        size=described.size,
    )


def revisions(report: Report, csv_path: str | Path) -> list[Revision]:
    """Every dated revision of one data file, newest first.

    Newest first because that is the order the question arrives in — "what did
    this look like before I touched it?" is asked far more often than "what did
    it look like a year ago" — and because the head of the list is what `archive`
    compares against to decide there is nothing new to keep.

    Directory entries are filtered by hand rather than globbed: a stem may
    legally contain `[`, `?` or `*`, and `Path.glob` would read those as pattern
    syntax and quietly return the wrong set.
    """
    path = _locate(report, csv_path, must_exist=False)
    folder = path.parent
    if not folder.is_dir():
        return []
    stem, suffix = path.stem, path.suffix.lower()
    found: list[tuple[str, int, Revision]] = []
    for sibling in folder.iterdir():
        if not sibling.is_file() or sibling.suffix.lower() != suffix:
            continue
        parsed = _parts(sibling)
        if parsed is None or parsed[0] != stem:
            continue
        _, date, counter = parsed
        found.append((date, counter, _revision(report, sibling, date)))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [revision for _date, _counter, revision in found]


def archive(report: Report, csv_path: str | Path) -> Path | None:
    """Preserve the current bytes as a dated revision. None when there is nothing
    new to keep.

    Call this *before* overwriting a data file. The date is the file's own
    modification date, not today's, because the honest answer to "as of when were
    these the numbers?" is when they were last written — and it is the same date
    `data.source_entry` puts in the entry's `date:`, so the revision filename and
    the bibliography agree about which version they are talking about.

    `copy2` rather than `copy` for the same reason: the revision keeps the mtime
    of the version it holds, so the filename and the filesystem tell one story.

    The no-op is against the newest revision only. Re-running a half-finished
    save must not litter the folder with identical copies, and the newest is the
    only one the current bytes could have come from — an older revision holding
    the same bytes means the numbers went away and came back, which is history
    worth keeping, not a duplicate worth suppressing.
    """
    path = _inside(report, _locate(report, csv_path))
    existing = revisions(report, path)
    if existing and existing[0].sha256 == data.sha_of(path):
        return None
    target = _free_name(path, data.describe(path, report=report).date)
    shutil.copy2(path, target)
    return target


# ── the bibliography side ────────────────────────────────────────────────────


def _rows_recorded(note: str) -> tuple[int | None, int | None]:
    match = ROWS_IN_NOTE.search(note or "")
    if match is None:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _entry(report: Report, key: str) -> sources.Source | None:
    for source in sources.parse(report.sources):
        if source.key == key:
            return source
    return None


def _note(described: data.DataFile, human: str | None) -> str:
    """The `note:` line, with an optional human sentence spliced in.

    The checksum stays first and the report-relative path stays *last*, because
    `data.SHA_IN_NOTE` searches for the one and `data.registry` takes the other
    with an `rsplit`. A note that put the explanation at the end would read fine
    and would silently unregister the file — E011 would stop firing, which is the
    exact failure this whole feature exists to make impossible.

    The sentence is collapsed to one line: it goes into a YAML scalar, and a
    stray newline in there is a bibliography that no longer parses.
    """
    if not human or not human.strip():
        return described.note
    flattened = " ".join(human.split())
    parts = described.note.split(data.NOTE_SEP)
    return data.NOTE_SEP.join(parts[:-1] + [flattened, parts[-1]])


def _headline(before: int | None, after: int) -> str:
    """The one line the UI shows. Written here rather than in the app, because
    the app is a front end over this engine and holds no logic of its own."""
    if before is None:
        return f"registered at {after} rows"
    if before == after:
        return f"{after} rows, unchanged"
    return f"{before} rows → {after} rows, {after - before:+d}"


def reregister(
    report: Report, csv_path: str | Path, *, note: str | None = None
) -> dict:
    """Move the recorded checksum onto the file as it is now, and say what moved.

    This is the only function in the engine that updates a data sha. Everything
    else — `check`, the build, the editor — treats a mismatch as a failure,
    because a checksum a tool may quietly refresh is not a checksum. Going
    through here is a decision, and the returned summary is the receipt.

    The entry is rewritten through `sources.upsert`, which replaces that one
    block and leaves every other byte of `sources.yml` alone: the comment above
    it, the entries either side, the ordering. And the block itself starts from
    the fields already in the file rather than from a fresh `source_entry`, so a
    title somebody chose by hand, or a field this module has never heard of,
    survives a revision. Only `date` and `note` are ours to move.
    """
    path = _inside(report, _locate(report, csv_path))

    # Read the old state first: after the upsert there is nothing left to read it
    # from, and the delta is the whole point of the summary.
    rel = data.describe(path, report=report).rel
    recorded = data.registry(report).get(rel)
    # The registry is read back out of the notes, so it is the authority on which
    # entry stands for this file — a key somebody chose by hand is not ours to
    # rename. Only when there is no such entry do we fall back to the derived key,
    # and then look it up anyway: an entry may exist under it with a note this
    # module cannot parse, and replacing that wholesale would drop its wording.
    key = recorded.key if recorded is not None else data.key_for(path, report)
    previous = _entry(report, key)
    rows_before, columns_before = _rows_recorded(
        str(previous.fields.get("note", "")) if previous else ""
    )

    kept = archive(report, path)
    # Which version did that copy actually preserve? `archive` copies the bytes
    # that are on disk *now*, so it keeps the outgoing version only when it was
    # called before the write — which the editor does and the command line
    # cannot, because by the time `data revise` runs the spreadsheet has already
    # been saved over. Reporting "the previous copy is kept" in that case names
    # a file that holds the incoming numbers, and tells somebody the version
    # their signed-off report cited is safe when it is gone. Work out the truth
    # here rather than phrase around it.
    kept_sha = data.sha_of(kept) if kept is not None else None
    old_sha = recorded.sha256 if recorded is not None else None
    preserved = (
        next(
            (
                revision
                for revision in revisions(report, path)
                if old_sha and revision.sha256 == old_sha
            ),
            None,
        )
        if old_sha
        else None
    )

    described = data.describe(path, report=report)
    described.key = key

    fields = dict(previous.fields) if previous and previous.fields else {}
    fresh = data.source_entry(described)
    for name, value in fresh.items():
        # Fill only what is missing. `date` and `note` are overwritten below
        # because they are the two fields that describe *this* version.
        fields.setdefault(name, value)
    fields["date"] = described.date
    fields["note"] = _note(described, note)
    sources.upsert(
        report.sources, sources.Source(key=described.key, fields=fields)
    )

    return {
        "report": report.id,
        "key": described.key,
        "rel": described.rel,
        "date": described.date,
        "old_sha": old_sha,
        "new_sha": described.sha256,
        "rows_before": rows_before,
        "rows_after": described.rows,
        "columns_before": columns_before,
        "columns_after": described.columns,
        "delta": None if rows_before is None else described.rows - rows_before,
        "archived": _rel(report, kept),
        # What the dated copy this call wrote actually holds — the version being
        # registered, or (when something archived before the write) the one being
        # replaced. `report_change` needs it to name the file honestly.
        "archived_sha": kept_sha,
        "archived_is_previous": bool(kept_sha and old_sha and kept_sha == old_sha),
        # Where the version the report cited until now still exists, if anywhere.
        # None with an `old_sha` set means those bytes were overwritten outside
        # the engine and no copy of them survives — the one outcome this module
        # promises will not happen, so it must be said out loud rather than
        # papered over with a reassuring line about the previous copy.
        "previous_kept": preserved.rel if preserved is not None else None,
        "note": " ".join(note.split()) if note and note.strip() else None,
        "headline": _headline(rows_before, described.rows),
    }


def status(report: Report, csv_path: str | Path) -> dict:
    """What `sources.yml` says about a data file versus what is on disk.

    This is the question the CSV editor asks before it lets somebody type: a file
    whose recorded sha still matches is safe to read, and one that does not is
    already failing E011 and needs a decision rather than another edit.

    `matches` is True only when both checksums are known and equal. An
    unregistered file is not "matching" — there is nothing for it to match — and
    collapsing the two would let a UI paint an unregistered file green.
    """
    path = _locate(report, csv_path, must_exist=False)
    present = path.is_file()
    described = data.describe(path, report=report) if present else None
    rel = described.rel if described else _rel(report, path) or Path(csv_path).name
    recorded = data.registry(report).get(rel)
    current_sha = described.sha256 if described else None
    recorded_sha = recorded.sha256 if recorded is not None else None
    if recorded is not None:
        key = recorded.key
    else:
        key = described.key if described else None
    return {
        "report": report.id,
        "key": key,
        "rel": rel,
        "exists": present,
        "registered": recorded is not None,
        "current_sha": current_sha,
        "recorded_sha": recorded_sha,
        "matches": bool(
            current_sha and recorded_sha and current_sha == recorded_sha
        ),
        "rows": described.rows if described else None,
        "columns": described.columns if described else None,
        "revisions": to_json(revisions(report, path), root=report.cfg.root),
    }


# ── JSON and printing ────────────────────────────────────────────────────────


def _rel(report: Report, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return Path(path).resolve().relative_to(report.folder.resolve()).as_posix()
    except ValueError:
        return Path(path).name


def _shown(path: Path, root: Path | None) -> str:
    if root is None:
        return str(path)
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


def to_json(items: Sequence[Revision], *, root: Path | None = None) -> list[dict]:
    return [
        {
            "rel": revision.rel,
            "path": _shown(revision.path, root),
            "date": revision.date,
            "sha256": revision.sha256,
            "rows": revision.rows,
            "columns": revision.columns,
            "size": revision.size,
        }
        for revision in items
    ]


def one(cfg: Config, target: str) -> Report:
    """The single report a target names. Shares `data.one`'s wording so the two
    commands fail the same way on an ambiguous slug."""
    return data.one(cfg, target)


def report_revisions(
    cfg: Config, items: Sequence[Revision], *, current: Path | None = None
) -> int:
    if current is not None:
        print(f"  {_shown(current, cfg.root)}  (current)")
    if not items:
        print("  no dated revisions — the file has not been revised through the engine")
        return 0
    for revision in items:
        shape = f"{revision.rows}×{revision.columns}"
        print(
            f"  {revision.date}  {shape:>9}  {revision.sha256[:12]}  "
            f"{_shown(revision.path, cfg.root)}"
        )
    return 0


def report_change(cfg: Config, summary: dict) -> int:
    """Print what a revision did, including which version was actually kept.

    The archived copy is named in full, because the reassurance that the old
    numbers still exist is most of the value — which is exactly why the line has
    to be true. Three cases, and they are genuinely different: the previous
    version was preserved, only the incoming one was, or the previous bytes are
    gone. The third is not a footnote. It means the numbers a signed-off report
    cited cannot be recovered, and somebody who is told "the previous copy is
    kept" will not go looking for the backup while they still have one.
    """
    print(f"  @{summary['key']}  {summary['headline']}")
    old = summary["old_sha"]
    print(
        f"  sha256 {(old or 'unregistered')[:12]} → {summary['new_sha'][:12]}"
    )
    if summary["archived"]:
        if summary.get("archived_is_previous"):
            print(f"  the previous copy is kept as {summary['archived']}")
        else:
            print(f"  this version is kept as {summary['archived']}")
    previous = summary.get("previous_kept")
    if previous and previous != summary["archived"]:
        print(f"  the version it cited before is kept as {previous}")
    elif old and not previous:
        print(
            "\n  The bytes this report cited until now were overwritten outside "
            "the engine and no dated copy of them exists. Recover them from your "
            "own backup or version control if you need to show what the earlier "
            "figure rested on — `data revise` can only keep what it is handed."
        )
    print(
        "\n  Re-read every table and sentence that cites "
        f"@{summary['key']} — the numbers under them have moved."
    )
    return 0
