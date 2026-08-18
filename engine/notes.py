"""The thinking that is not the report.

Every report accumulates material that will never appear in it: the question to
put to the client, the paragraph that has to be rewritten once the pricing page
is archived, the reminder that the scorecard is still a guess. It has to live
somewhere, and the two bad answers are a separate app — where it is immediately
divorced from the report it belongs to — and the report itself, where it either
ships to the reader or gets deleted before it can.

So it lives beside the report, in two optional files:

    reports/<id>/notes.md    free prose. Anything.
    reports/<id>/todos.md    a markdown checklist.

Both are plain markdown on purpose. They are readable in any editor, diffable in
any review, greppable from any shell, and there is no schema to keep in sync with
anything — the same reasoning that makes folders the data model everywhere else
in this engine.

**Neither file is ever compiled into the PDF, and the citation rule does not
apply to either of them.** That is not an oversight, it is the point. The rule
exists so that nothing a *reader* sees can sit between a cited fact and a marked
opinion; a scratch pad has no reader but the author, and a half-formed thought
that had to be cited before it could be written down would simply not get
written down. `check` never opens these files, and `build` never sees them.

One thing does leak in the other direction, deliberately. A `// TODO:` or
`// FIXME:` left in a comment in `main.typ` is the same kind of note, written in
the place the thought occurred, and a list that omitted it would be a list you
could not trust to be complete — so they are harvested into the same view. They
are read-only here: `toggle` refuses on them, because a checkbox in a Typst
comment is prose, not state.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import check
from .config import Config
from .workspace import Report, reports

TODOS_NAME = "todos.md"
NOTES_NAME = "notes.md"

# Where a task can have come from. Two of the three are writable; a marker in
# the report source is not, which `toggle` enforces.
SOURCES = (TODOS_NAME, NOTES_NAME, "main.typ")
WRITABLE = (TODOS_NAME, NOTES_NAME)

# A checklist item, as markdown actually gets written: any indentation (which is
# how nesting will arrive), any of the three bullet characters, and either mark.
# The indentation is captured and thrown away — this module reports a flat list,
# because a todo's parent is not information a checkbox click needs.
CHECKBOX = re.compile(r"^[ \t]*[-*+][ \t]+\[(?P<mark>[ xX])\][ \t]*(?P<text>.*)$")

# `// TODO:` and `// FIXME:` in the report source. Case-insensitive because a
# note written in a hurry is written in a hurry; the colon is required, so that
# a sentence merely containing the word does not become a task.
MARKER = re.compile(r"//+[ \t]*(?P<kind>TODO|FIXME)[ \t]*:[ \t]*(?P<text>[^\n]*)", re.I)

# `#tag` — a word, not a colour code and not a Typst directive. Only at a word
# boundary, so a URL fragment in a note is not a tag.
TAG = re.compile(r"(?:(?<=\s)|^)#(?P<tag>[A-Za-z][\w-]*)")

# `@2026-09-01` — the same sigil Typst uses for a citation, which is safe here
# precisely because these files are never compiled.
DUE = re.compile(r"(?:(?<=\s)|^)@(?P<date>\d{4}-\d{2}-\d{2})\b")

HEADING = """# Todo — {title}

Scratch, not evidence. This file is never compiled into the report and the
citation rule does not apply to it — that is what it is for.
"""

# What `report-maker new` seeds. The first item is the one piece of advice that
# changes how a report goes: the bibliography before the prose.
SEEDED = """
- [ ] Draft sources.yml before the prose.
- [ ] Decide what in this report is evidence and what is assessment.
"""


class NotesError(RuntimeError):
    """A refusal. The message names the fix, because a refusal that only says no
    teaches people to work around the tool."""


@dataclass
class Todo:
    """One task, wherever it was written."""

    text: str
    done: bool
    line: int
    tags: list[str] = field(default_factory=list)
    due: str | None = None
    source: str = TODOS_NAME  # "todos.md" | "main.typ" | "notes.md"


@dataclass
class Note:
    """`notes.md`, whole. There is nothing to parse — it is prose."""

    report: str
    path: Path
    text: str
    lines: int
    modified: str


# ── paths ────────────────────────────────────────────────────────────────────


def todos_file(report: Report) -> Path:
    return report.folder / TODOS_NAME


def notes_file(report: Report) -> Path:
    return report.folder / NOTES_NAME


def _read(path: Path) -> str | None:
    """The file's text, or None when it is not there.

    Decoding is lenient because these are scratch files: a stray byte pasted out
    of a PDF should cost one mangled character in a list, not an exception that
    takes down `scan` for every other report in the vault. `toggle` decodes
    strictly instead — it writes the result back.
    """
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _stamp(path: Path) -> str:
    return (
        dt.datetime.fromtimestamp(path.stat().st_mtime)
        .replace(microsecond=0)
        .isoformat(sep=" ")
    )


# ── parsing ──────────────────────────────────────────────────────────────────


def tags_in(text: str) -> list[str]:
    """Every `#tag`, in order, without repeats."""
    found: list[str] = []
    for match in TAG.finditer(text):
        tag = match.group("tag")
        if tag not in found:
            found.append(tag)
    return found


def due_in(text: str) -> str | None:
    """The first `@YYYY-MM-DD` that is a real date.

    A token that looks like a date but is not one (`@2026-13-40`) yields None
    rather than an error: it is a typo in a scratch file, and refusing to list
    the item would be a strange punishment for it.
    """
    for match in DUE.finditer(text):
        try:
            return dt.date.fromisoformat(match.group("date")).isoformat()
        except ValueError:
            continue
    return None


def _item(text: str, done: bool, line: int, source: str) -> Todo:
    text = text.rstrip()
    return Todo(
        text=text,
        done=done,
        line=line,
        tags=tags_in(text),
        due=due_in(text),
        source=source,
    )


def parse_checklist(text: str, source: str = TODOS_NAME) -> list[Todo]:
    """Every `- [ ]` / `- [x]` line in a markdown file."""
    found: list[Todo] = []
    for number, line in enumerate(text.split("\n"), 1):
        match = CHECKBOX.match(line)
        if match:
            found.append(
                _item(
                    match.group("text"),
                    done=match.group("mark").lower() == "x",
                    line=number,
                    source=source,
                )
            )
    return found


def harvest(raw: str) -> list[Todo]:
    """`// TODO:` and `// FIXME:` comments in Typst source.

    Whether a `//` opens a comment is a question the linter already answers, and
    answering it twice is how two parsers start disagreeing about what a report
    says. So this reuses `check.scrub`, which blanks comments to spaces while
    keeping every offset: a marker is in a comment exactly when the character
    under it came back blank.

    `scrub` also blanks code blocks, so a `// TODO:` inside one is harvested
    too. That false positive costs one line in a list; a second comment parser
    would cost a permanent divergence from the rule the linter enforces.
    """
    blanked = check.scrub(raw)
    found: list[Todo] = []
    for match in MARKER.finditer(raw):
        if blanked[match.start()] != " ":
            continue  # a `//` in prose, or the one in `https://`
        # The marker word goes into the text: TODO and FIXME do not mean the
        # same thing, and the dataclass has nowhere else to keep the difference.
        kind = match.group("kind").upper()
        rest = match.group("text").strip()
        text = f"{kind}: {rest}" if rest else kind
        found.append(
            _item(text, done=False, line=check.line_of(raw, match.start()), source="main.typ")
        )
    return found


# ── reading one report ───────────────────────────────────────────────────────


def todos(report: Report) -> list[Todo]:
    """Every task attached to a report, in the order a person would look for
    them: the checklist first, then anything in the notes, then the report
    source itself."""
    found: list[Todo] = []
    text = _read(todos_file(report))
    if text is not None:
        found += parse_checklist(text, TODOS_NAME)
    text = _read(notes_file(report))
    if text is not None:
        found += parse_checklist(text, NOTES_NAME)
    if report.main.is_file():
        found += harvest(report.main.read_text(encoding="utf-8", errors="replace"))
    return found


def notes(report: Report) -> Note | None:
    """`notes.md`, or None. An absent scratch pad is not a finding."""
    path = notes_file(report)
    text = _read(path)
    if text is None:
        return None
    return Note(
        report=report.id,
        path=path,
        text=text,
        lines=len(text.splitlines()),
        modified=_stamp(path),
    )


def _modified(report: Report) -> str | None:
    """The most recent of the two files, so the app can sort by "last touched"
    without opening either."""
    stamps = [
        _stamp(path)
        for path in (todos_file(report), notes_file(report))
        if path.is_file()
    ]
    return max(stamps) if stamps else None


# ── reading a vault ──────────────────────────────────────────────────────────


def one(cfg: Config, target: str) -> Report:
    """The single report a write is aimed at.

    Adding a task to eight reports because a folder name matched is not a thing
    anybody meant, so anything that writes goes through here first.
    """
    found = reports(cfg, target)
    if not found:
        raise NotesError(
            'this vault has no reports yet — `report-maker new "Title"` makes one'
        )
    if len(found) != 1:
        raise NotesError(
            f"{target!r} matches {len(found)} reports — name exactly one: "
            + ", ".join(report.id for report in found)
        )
    return found[0]


def scan(cfg: Config, target: str | None = None, *, open_only: bool = False) -> list[dict]:
    """One row per report that has something on its pad.

    Reports with neither file and no harvested marker are left out entirely: a
    list of eighty reports, seventy-six of them empty, is a list nobody reads.

    `open` and `done` always count everything found, even under `open_only` —
    the counts describe the report, and the list describes the filter. Reporting
    `done: 0` for a report with nine finished tasks would be a lie told to make
    a flag look tidy.
    """
    rows: list[dict] = []
    for report in reports(cfg, target):
        found = todos(report)
        note = notes(report)
        shown = [todo for todo in found if not todo.done] if open_only else found
        if not shown and note is None:
            continue
        rows.append(
            {
                "id": report.id,
                "open": sum(1 for todo in found if not todo.done),
                "done": sum(1 for todo in found if todo.done),
                "todos": to_json(shown),
                "has_notes": note is not None,
                "modified": _modified(report),
            }
        )
    return rows


# ── writing ──────────────────────────────────────────────────────────────────


def toggle(report: Report, line: int, done: bool, *, source: str = TODOS_NAME) -> None:
    """Flip one checkbox, rewriting exactly one character on exactly one line.

    Surgical on purpose. This is called from a checkbox click in the app while
    the same file may be open in an editor, and a whole-file rewrite would eat
    whatever the person typed a second earlier. Everything outside the two
    characters between the brackets comes back byte for byte — line endings,
    trailing whitespace, the lot — and when the box is already in the requested
    state nothing is written at all, so a double click does not bump the mtime a
    watcher is keyed on.
    """
    if source == "main.typ":
        raise NotesError(
            "a `// TODO:` in main.typ is prose, not state — it has no checkbox to "
            "flip.\n"
            "  Edit the line in the report, or move the task to "
            f"{TODOS_NAME} where it can be ticked."
        )
    if source not in WRITABLE:
        raise NotesError(f"a tickable task lives in {' or '.join(WRITABLE)}, not in {source!r}")

    path = report.folder / source
    # Messages name the file the way the vault does — an absolute path from a
    # subprocess is noise in an app's error toast.
    where = check.relative(path, report.cfg.root)
    if not path.is_file():
        raise NotesError(f"{where} does not exist — add a task first")

    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        # Lenient decoding is fine for reading; writing back a replaced
        # character would silently destroy whatever was really there.
        raise NotesError(f"{where} is not valid UTF-8 ({exc.reason}) — fix it by hand") from exc

    # Split on "\n" rather than with splitlines(), which also breaks on \x0b,
    # \x0c and U+2028, and would therefore number lines differently from every
    # other tool that has ever reported a line in this file.
    lines = text.split("\n")
    if not 1 <= line <= len(lines):
        raise NotesError(f"{where} has {len(lines)} lines — there is no line {line}")

    original = lines[line - 1]
    match = CHECKBOX.match(original)
    if not match:
        raise NotesError(
            f"{where}:{line} is not a checklist item: {original.strip()!r}\n"
            "  A task is a line like `- [ ] the thing to do`."
        )

    start, end = match.span("mark")
    mark = "x" if done else " "
    if original[start:end] == mark:
        return
    lines[line - 1] = original[:start] + mark + original[end:]
    path.write_bytes("\n".join(lines).encode("utf-8"))


def add(report: Report, text: str) -> Todo:
    """Append a task, creating `todos.md` with its heading if it is not there."""
    text = text.strip()
    if not text:
        raise NotesError("a task needs some words")
    if "\n" in text:
        raise NotesError(
            "a task is one line — split it into two tasks, or put the paragraph in "
            f"{NOTES_NAME}"
        )
    # Somebody pasting a line out of a markdown file will bring the bullet with
    # it; appending it verbatim would produce `- [ ] - [ ] …`.
    text = re.sub(r"^[-*+][ \t]+(\[[ xX]\][ \t]*)?", "", text)

    path = todos_file(report)
    existing = _read(path)
    if existing is None:
        existing = HEADING.format(title=report.id) + "\n"
    elif existing and not existing.endswith("\n"):
        existing += "\n"

    line = existing.count("\n") + 1
    path.write_text(existing + f"- [ ] {text}\n", encoding="utf-8")
    return _item(text, done=False, line=line, source=TODOS_NAME)


def starter_text(report_id: str) -> str:
    """What `report-maker new` writes into a fresh report folder."""
    return HEADING.format(title=report_id) + SEEDED


# ── output ───────────────────────────────────────────────────────────────────


def to_json(items: Sequence[Todo]) -> list[dict]:
    return [
        {
            "text": todo.text,
            "done": todo.done,
            "line": todo.line,
            "tags": todo.tags,
            "due": todo.due,
            "source": todo.source,
        }
        for todo in items
    ]


def note_json(note: Note | None, *, root: Path | None = None) -> dict | None:
    if note is None:
        return None
    return {
        "report": note.report,
        "path": check.relative(note.path, root) if root else note.path.as_posix(),
        "text": note.text,
        "lines": note.lines,
        "modified": note.modified,
    }


def _flag(todo: dict, today: dt.date) -> str:
    """Overdue and due-today, said out loud. A due date nobody surfaces is a
    comment, and this is the whole of what the date is for."""
    if todo["done"] or not todo["due"]:
        return ""
    due = dt.date.fromisoformat(todo["due"])
    if due < today:
        return "overdue"
    if due == today:
        return "due today"
    return ""


def report_todos(cfg: Config, rows: Sequence[dict]) -> int:
    """Print the pad, grouped by report.

    Always exits 0. An open task is not a broken build — it is the note that
    says the build is not finished yet, which is a different thing, and a
    command that failed on one would train people to stop writing them down.
    """
    if not rows:
        print(
            "  nothing on the pad\n"
            f"  `report-maker todos <report> --add \"…\"` starts reports/<id>/{TODOS_NAME}"
        )
        return 0

    today = dt.date.today()
    labels = [
        f"[{'x' if todo['done'] else ' '}] {todo['text']}"
        for row in rows
        for todo in row["todos"]
    ]
    width = min(max((len(label) for label in labels), default=0), 64)

    for row in rows:
        print(f"  {(cfg.reports / row['id']).relative_to(cfg.root)}")
        for todo in row["todos"]:
            label = f"[{'x' if todo['done'] else ' '}] {todo['text']}"
            where = f"{todo['source']}:{todo['line']}"
            print(f"    {label:<{width}}  {where:<16}  {_flag(todo, today)}".rstrip())
        if row["has_notes"]:
            print(f"    {NOTES_NAME}  (last touched {row['modified']})")

    total_open = sum(row["open"] for row in rows)
    total_done = sum(row["done"] for row in rows)
    noted = sum(1 for row in rows if row["has_notes"])
    summary = f"\n  {total_open} open, {total_done} done across {len(rows)} report(s)"
    if noted:
        summary += f" · {noted} with notes"
    print(summary)
    return 0
