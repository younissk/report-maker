"""Scratch-pad tests.

Two properties carry this module, and neither is about markdown.

The first is that `toggle` is surgical. The app calls it from a checkbox click
while the same file may be open in an editor, so a rewrite that reflowed a line,
normalised a line ending or dropped a trailing space would quietly eat somebody's
words. The assertions below therefore compare *every other line byte for byte*
rather than checking that the tick appeared — the tick appearing is the easy
half, and it is not the half that loses work.

The second is that the list is complete. A `// TODO:` left in `main.typ` is a
note somebody wrote and expects to see again; if it is missing from the list,
the list is worse than no list, because it looks authoritative. So the harvest
is tested for the line numbers it reports and for the two things it must not
mistake for a task — a `//` inside a URL, and a line that merely says "todo".

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import notes  # noqa: E402
from engine.config import load  # noqa: E402
from engine.workspace import reports  # noqa: E402

VAULT_TOML = """[vault]
reports = "reports"
"""

# Line numbers are asserted against this, so the comment markers sit on lines
# that are easy to count to: the TODO on 8, the FIXME on 12.
MAIN = """#import "/.build/design/base/report.typ": report

#show: report.with(
  title: "Notes",
  sources: "/reports/2026-08-18-notes/sources.yml",
)

// TODO: ask which pricing page is current

= Findings

A fact about the world @alpha. // FIXME: this number is from the old export

Read it at https://TODO: which is a URL, not a task.

The word todo in a sentence is not a task either.
"""

# Written the way a checklist actually accumulates: indented sub-items, a star
# bullet from whatever editor autocompleted it, prose in between, one malformed
# box that is not a task, and trailing whitespace nobody meant to leave — which
# `toggle` must nevertheless hand back untouched.
TODOS = """# Todo — 2026-08-18-notes

- [ ] ask the client which page is current #evidence #pricing @2026-09-01
- [x] draft the scorecard
  - [ ] and the sub-item under it #later  
* [ ] a star bullet, because markdown allows one
Not a task at all.
- [] no space in the box, so not a task
"""

NOTES = """# Notes

The pricing page changed under us last quarter, which is why the archive
matters more than the link does.

- [ ] a task written where the thought happened
"""


class VaultCase(unittest.TestCase):
    """A real vault on disk — every function here reads or writes the filesystem."""

    report_id = "2026-08-18-notes"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "report-maker.toml").write_text(VAULT_TOML, encoding="utf-8")
        self.folder = self.root / "reports" / self.report_id
        self.folder.mkdir(parents=True)
        (self.folder / "main.typ").write_text(MAIN, encoding="utf-8")
        self.cfg = load(self.root)
        self.report = reports(self.cfg, self.report_id)[0]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────────

    def write_todos(self, text: str = TODOS) -> Path:
        path = self.folder / "todos.md"
        path.write_text(text, encoding="utf-8")
        return path

    def write_notes(self, text: str = NOTES) -> Path:
        path = self.folder / "notes.md"
        path.write_text(text, encoding="utf-8")
        return path

    def second_report(self, rid: str = "2026-08-18-quiet") -> None:
        folder = self.root / "reports" / rid
        folder.mkdir(parents=True)
        (folder / "main.typ").write_text("= Nothing to note\n", encoding="utf-8")

    def from_(self, source: str) -> list[notes.Todo]:
        return [t for t in notes.todos(self.report) if t.source == source]


class Checklist(VaultCase):
    def test_reads_both_marks(self) -> None:
        self.write_todos()
        found = self.from_("todos.md")
        self.assertEqual([t.done for t in found], [False, True, False, False])

    def test_an_uppercase_x_is_also_done(self) -> None:
        self.write_todos("- [X] shouted, but done\n")
        self.assertTrue(self.from_("todos.md")[0].done)

    def test_indentation_is_tolerated(self) -> None:
        # Nesting arrives as indentation, and a nested task is still a task.
        self.write_todos()
        found = self.from_("todos.md")
        self.assertEqual(found[2].text, "and the sub-item under it #later")
        self.assertEqual(found[2].line, 5)

    def test_a_star_bullet_is_a_bullet(self) -> None:
        self.write_todos()
        found = self.from_("todos.md")
        self.assertEqual(found[3].text, "a star bullet, because markdown allows one")
        self.assertEqual(found[3].line, 6)

    def test_prose_and_malformed_boxes_are_not_tasks(self) -> None:
        self.write_todos()
        texts = [t.text for t in self.from_("todos.md")]
        self.assertNotIn("Not a task at all.", texts)
        self.assertFalse(any("no space in the box" in text for text in texts))

    def test_line_numbers_are_the_editor_line_numbers(self) -> None:
        self.write_todos()
        self.assertEqual([t.line for t in self.from_("todos.md")], [3, 4, 5, 6])

    def test_a_checklist_in_notes_is_read_too(self) -> None:
        self.write_notes()
        found = self.from_("notes.md")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].line, 6)


class TagsAndDates(VaultCase):
    def test_tags_come_out_in_order_without_repeats(self) -> None:
        self.assertEqual(
            notes.tags_in("chase #evidence then #pricing then #evidence again"),
            ["evidence", "pricing"],
        )

    def test_a_hash_inside_a_word_is_not_a_tag(self) -> None:
        # Otherwise every colour and every URL fragment in a note becomes a tag.
        self.assertEqual(notes.tags_in("the accent is #2E5A88 in issue no#4"), [])

    def test_a_due_date_is_read_off_the_text(self) -> None:
        self.write_todos()
        first = self.from_("todos.md")[0]
        self.assertEqual(first.due, "2026-09-01")
        self.assertEqual(first.tags, ["evidence", "pricing"])

    def test_a_task_with_no_date_has_no_due(self) -> None:
        self.write_todos()
        self.assertIsNone(self.from_("todos.md")[1].due)

    def test_an_impossible_date_is_not_a_due_date(self) -> None:
        # A typo in a scratch file should cost the date, not the task.
        self.assertIsNone(notes.due_in("ready by @2026-13-40"))

    def test_a_date_glued_to_a_word_is_not_a_due_date(self) -> None:
        self.assertIsNone(notes.due_in("see acme@2026-09-01"))


class Harvest(VaultCase):
    def test_todo_and_fixme_come_out_of_the_report_source(self) -> None:
        found = self.from_("main.typ")
        self.assertEqual(
            [t.text for t in found],
            [
                "TODO: ask which pricing page is current",
                "FIXME: this number is from the old export",
            ],
        )

    def test_the_line_numbers_point_at_the_comment(self) -> None:
        self.assertEqual([t.line for t in self.from_("main.typ")], [8, 12])

    def test_a_harvested_marker_is_never_done(self) -> None:
        self.assertTrue(all(not t.done for t in self.from_("main.typ")))

    def test_the_slashes_in_a_url_do_not_open_a_comment(self) -> None:
        # `check.scrub` already knows this, which is the reason to reuse it
        # rather than write a second comment parser that has to learn it again.
        self.assertFalse(any("URL" in t.text for t in self.from_("main.typ")))

    def test_the_word_todo_in_prose_is_not_a_task(self) -> None:
        self.assertFalse(any("sentence" in t.text for t in self.from_("main.typ")))

    def test_a_marker_carries_its_tags_and_date(self) -> None:
        found = notes.harvest("// TODO: rewrite this #prose @2026-10-05\n")
        self.assertEqual(found[0].tags, ["prose"])
        self.assertEqual(found[0].due, "2026-10-05")


class Toggling(VaultCase):
    def lines(self, path: Path) -> list[bytes]:
        return path.read_bytes().split(b"\n")

    def test_ticking_changes_one_character_on_one_line(self) -> None:
        path = self.write_todos()
        before = self.lines(path)
        notes.toggle(self.report, 3, True)
        after = self.lines(path)

        self.assertEqual(len(before), len(after))
        for number, (was, now) in enumerate(zip(before, after), 1):
            if number == 3:
                self.assertEqual(was.replace(b"[ ]", b"[x]", 1), now)
            else:
                # Byte for byte. This is the whole contract with a concurrent
                # editor, and it is why the file is not reflowed or rewritten.
                self.assertEqual(was, now, f"line {number} changed")

    def test_unticking_puts_the_space_back(self) -> None:
        path = self.write_todos()
        notes.toggle(self.report, 4, False)
        self.assertIn("- [ ] draft the scorecard", path.read_text(encoding="utf-8"))

    def test_crlf_line_endings_survive(self) -> None:
        # An editor on another machine wrote this file. Rewriting it as LF would
        # turn a one-character change into a whole-file diff.
        path = self.folder / "todos.md"
        path.write_bytes(b"# Todo\r\n\r\n- [ ] one\r\n- [ ] two\r\n")
        notes.toggle(self.report, 3, True)
        self.assertEqual(path.read_bytes(), b"# Todo\r\n\r\n- [x] one\r\n- [ ] two\r\n")

    def test_toggling_to_the_state_it_is_already_in_writes_nothing(self) -> None:
        # `scan` reports the mtime and a watcher rebuilds on it, so a double
        # click must not look like an edit.
        path = self.write_todos()
        stamp = path.stat().st_mtime_ns
        notes.toggle(self.report, 4, True)
        self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_a_task_in_notes_can_be_ticked_too(self) -> None:
        path = self.write_notes()
        notes.toggle(self.report, 6, True, source="notes.md")
        self.assertIn("- [x] a task written", path.read_text(encoding="utf-8"))

    def test_it_refuses_a_marker_in_the_report_source(self) -> None:
        self.write_todos()
        with self.assertRaises(notes.NotesError) as caught:
            notes.toggle(self.report, 8, True, source="main.typ")
        message = str(caught.exception)
        # The refusal has to say where the task *can* be ticked, or the app has
        # nothing to show but "no".
        self.assertIn("main.typ", message)
        self.assertIn("todos.md", message)
        self.assertEqual((self.folder / "main.typ").read_text(encoding="utf-8"), MAIN)

    def test_it_refuses_a_line_that_is_not_a_task(self) -> None:
        path = self.write_todos()
        with self.assertRaises(notes.NotesError):
            notes.toggle(self.report, 7, True)
        self.assertEqual(path.read_text(encoding="utf-8"), TODOS)

    def test_it_refuses_a_line_that_is_not_there(self) -> None:
        self.write_todos()
        with self.assertRaises(notes.NotesError):
            notes.toggle(self.report, 900, True)

    def test_it_refuses_when_there_is_no_file(self) -> None:
        with self.assertRaises(notes.NotesError):
            notes.toggle(self.report, 1, True)


class Adding(VaultCase):
    def test_it_creates_the_file_with_a_heading(self) -> None:
        todo = notes.add(self.report, "chase the pricing page @2026-09-30")
        path = self.folder / "todos.md"
        text = path.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("# Todo — 2026-08-18-notes"))
        self.assertTrue(text.endswith("- [ ] chase the pricing page @2026-09-30\n"))
        self.assertEqual(todo.due, "2026-09-30")
        self.assertEqual(todo.source, "todos.md")

    def test_the_returned_line_is_where_the_task_landed(self) -> None:
        todo = notes.add(self.report, "first")
        found = notes.todos(self.report)
        self.assertEqual([t.line for t in found if t.source == "todos.md"], [todo.line])

    def test_a_second_task_appends(self) -> None:
        first = notes.add(self.report, "first")
        second = notes.add(self.report, "second")
        self.assertEqual(second.line, first.line + 1)
        self.assertEqual(
            [t.text for t in notes.todos(self.report) if t.source == "todos.md"],
            ["first", "second"],
        )

    def test_it_appends_to_a_file_with_no_trailing_newline(self) -> None:
        (self.folder / "todos.md").write_text("- [ ] one", encoding="utf-8")
        notes.add(self.report, "two")
        self.assertEqual(
            (self.folder / "todos.md").read_text(encoding="utf-8"),
            "- [ ] one\n- [ ] two\n",
        )

    def test_a_pasted_bullet_is_not_doubled(self) -> None:
        todo = notes.add(self.report, "- [ ] pasted out of another file")
        self.assertEqual(todo.text, "pasted out of another file")
        self.assertIn(
            "- [ ] pasted out of another file\n",
            (self.folder / "todos.md").read_text(encoding="utf-8"),
        )

    def test_an_empty_task_is_refused(self) -> None:
        with self.assertRaises(notes.NotesError):
            notes.add(self.report, "   ")

    def test_a_paragraph_is_refused_rather_than_mangled(self) -> None:
        with self.assertRaises(notes.NotesError):
            notes.add(self.report, "one line\nand another")


class Reading(VaultCase):
    def test_notes_are_none_when_there_are_none(self) -> None:
        self.assertIsNone(notes.notes(self.report))

    def test_notes_come_back_whole(self) -> None:
        self.write_notes()
        note = notes.notes(self.report)
        self.assertEqual(note.report, self.report_id)
        self.assertEqual(note.text, NOTES)
        self.assertEqual(note.lines, len(NOTES.splitlines()))
        self.assertTrue(note.modified)


class Scanning(VaultCase):
    def test_counts_every_source_together(self) -> None:
        self.write_todos()
        self.write_notes()
        row = notes.scan(self.cfg)[0]
        # 3 open in todos.md + 1 in notes.md + 2 harvested from main.typ.
        self.assertEqual((row["open"], row["done"]), (6, 1))
        self.assertEqual(len(row["todos"]), 7)
        self.assertTrue(row["has_notes"])
        self.assertTrue(row["modified"])

    def test_a_report_with_nothing_on_its_pad_is_left_out(self) -> None:
        self.second_report()
        self.assertEqual([row["id"] for row in notes.scan(self.cfg)], [self.report_id])

    def test_notes_alone_are_enough_to_appear(self) -> None:
        quiet = self.root / "reports" / "2026-08-18-quiet"
        quiet.mkdir(parents=True)
        (quiet / "main.typ").write_text("= Quiet\n", encoding="utf-8")
        (quiet / "notes.md").write_text("Just a thought.\n", encoding="utf-8")
        row = [r for r in notes.scan(self.cfg) if r["id"] == "2026-08-18-quiet"][0]
        self.assertEqual((row["open"], row["done"], row["todos"]), (0, 0, []))
        self.assertTrue(row["has_notes"])

    def test_open_only_filters_the_list_but_not_the_counts(self) -> None:
        self.write_todos()
        row = notes.scan(self.cfg, open_only=True)[0]
        self.assertTrue(all(not todo["done"] for todo in row["todos"]))
        # The counts describe the report; the list describes the filter.
        self.assertEqual((row["open"], row["done"]), (5, 1))

    def test_a_target_narrows_to_one_report(self) -> None:
        self.write_todos()
        self.second_report()
        self.assertEqual(len(notes.scan(self.cfg, self.report_id)), 1)

    def test_the_json_row_carries_what_a_checkbox_needs(self) -> None:
        self.write_todos()
        todo = notes.scan(self.cfg)[0]["todos"][0]
        self.assertEqual(
            sorted(todo), ["done", "due", "line", "source", "tags", "text"]
        )


class Targeting(VaultCase):
    def test_a_slug_resolves_to_its_report(self) -> None:
        self.second_report()
        self.assertEqual(notes.one(self.cfg, self.report_id).id, self.report_id)

    def test_a_folder_matching_two_reports_is_refused(self) -> None:
        # `--add` writes. Writing into whichever report sorted first is the kind
        # of helpfulness nobody can undo.
        for name in ("clients/2026-08-18-one", "clients/2026-08-18-two"):
            self.second_report(name)
        with self.assertRaises(notes.NotesError):
            notes.one(self.cfg, "clients")

    def test_an_empty_vault_says_how_to_get_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "report-maker.toml").write_text(VAULT_TOML, encoding="utf-8")
            with self.assertRaises(notes.NotesError) as caught:
                notes.one(load(root), None)
        self.assertIn("report-maker new", str(caught.exception))


class Printing(VaultCase):
    def output(self, rows) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = notes.report_todos(self.cfg, rows)
        self.assertEqual(code, 0, "an open task is not a failed build")
        return buffer.getvalue()

    def test_an_empty_vault_says_where_to_start(self) -> None:
        self.assertIn("todos.md", self.output([]))

    def test_every_task_is_printed_with_where_it_came_from(self) -> None:
        self.write_todos()
        self.write_notes()
        text = self.output(notes.scan(self.cfg))
        self.assertIn("todos.md:3", text)
        self.assertIn("notes.md:6", text)
        self.assertIn("main.typ:8", text)
        self.assertIn("6 open, 1 done", text)

    def test_an_overdue_task_says_so(self) -> None:
        self.write_todos("- [ ] this one slipped @2001-01-01\n")
        self.assertIn("overdue", self.output(notes.scan(self.cfg)))

    def test_a_finished_task_is_never_overdue(self) -> None:
        self.write_todos("- [x] this one landed @2001-01-01\n")
        self.assertNotIn("overdue", self.output(notes.scan(self.cfg)))


class Seeding(VaultCase):
    def test_the_starter_is_a_checklist_a_person_can_tick(self) -> None:
        text = notes.starter_text(self.report_id)
        (self.folder / "todos.md").write_text(text, encoding="utf-8")
        found = self.from_("todos.md")
        self.assertTrue(found)
        self.assertTrue(all(not todo.done for todo in found))

    def test_the_starter_says_the_rule_does_not_apply_here(self) -> None:
        # A reader who has internalised the house rule will otherwise wonder why
        # this file is exempt, and the answer belongs in the file itself.
        self.assertIn("citation rule does not apply", notes.starter_text("x"))


if __name__ == "__main__":
    unittest.main()
