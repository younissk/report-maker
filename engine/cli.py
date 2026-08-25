"""report-maker — the command line over the engine.

Every command is non-interactive and writes files: no server, no browser, no
editor, nothing that waits for a human. `watch` and `mcp` are the two exceptions,
and both only wait because something else is driving them — a writer saving a
file, an agent speaking JSON-RPC down a pipe.

    report-maker init                 make the current directory a vault
    report-maker new "Title"          scaffold a report folder
    report-maker list [--json]        what reports exist, by folder
    report-maker templates [--json]   what designs exist, by group
    report-maker template new <id>    create an editable design
    report-maker template list --installed   designs fetched from a repository
    report-maker template install <url>      fetch a design from a git repository
    report-maker template update [id]        re-fetch them at their recorded ref
    report-maker template uninstall <id>     remove an installed design
    report-maker stage                regenerate every design into .build/
    report-maker diagrams [target]    mermaid .mmd → branded .svg
    report-maker diagrams --prepare <mmd>    the exact source mermaid is handed
    report-maker build [target]       Typst → PDF (--keep-going for the whole vault)
    report-maker pages [target]       PDF pages → PNG + pages.json
    report-maker manifest             out/manifest.json
    report-maker check [target]       enforce the citation rule, data rules included
    report-maker sources <target>     the report's bibliography, as data
    report-maker cite <target> <url>  archive a source and add it to sources.yml
    report-maker verify [target]      re-fetch archived sources, report drift
    report-maker score [target]       evidence density per report
    report-maker todos [target]       the pad beside a report: tasks, and // TODO:
    report-maker notes <target>       a report's notes.md
    report-maker diff <target>        what changed since a git revision
    report-maker html [target]        report + evidence → one self-contained .html
    report-maker data add <target> <csv>  register a CSV so a table can cite it
    report-maker data revise <target> <csv>  re-register it, keeping the old copy
    report-maker data revisions <target> <csv>  the dated copies kept of one file
    report-maker data status <target> <csv>  recorded checksum vs the bytes on disk
    report-maker data absence <target> <corpus> <query>  a search that found nothing
    report-maker find <query>         search reports, sources, snapshots, diagrams
    report-maker index                build or refresh the search index
    report-maker all [target]         stage, diagrams, build, pages, manifest, check
    report-maker watch <target>       live rebuild while writing
    report-maker sync [--push]        commit the vault to git, and optionally push
    report-maker brand <sub>          brand packs: list, show, new, set, preview
    report-maker mcp                  serve the vault to an agent (MCP over stdio)
    report-maker doctor               what is installed, what is missing
    report-maker clean                remove out/ and .build/
    report-maker --version            the engine version, and nothing else

Every command runs against one vault: the folder holding report-maker.toml, found
by walking up from the working directory, or named outright with -C/--vault.

A target is a report id, a bare slug, or a folder — `build clients/acme` builds
every report filed under it.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import ipaddress
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from . import brand as brand_mod
from . import brandcmd as brandcmd_mod
from . import library as library_mod
from . import vault as vault_mod
from . import build as build_mod
from . import check as check_mod
from . import cite as cite_mod
from . import data as data_mod
from . import datarev as datarev_mod
from . import diagrams as diagrams_mod
from . import diffing as diffing_mod
from . import gitsync as gitsync_mod
from . import html as html_mod
from . import install as install_mod
from . import manifest as manifest_mod
from . import mcp as mcp_mod
from . import notes as notes_mod
from . import pages as pages_mod
from . import scaffold
from . import score as score_mod
from . import search as search_mod
from . import snapshot as snapshot_mod
from . import sources as sources_mod
from . import verify as verify_mod
from .config import CONFIG_NAME, Config, ConfigError, load
from .workspace import reports


def _config(args) -> Config:
    return load(Path(args.vault) if args.vault else None)


@contextlib.contextmanager
def _quiet():
    """Collect whatever a module prints, so `--json` can keep stdout pure.

    Every `--json` form in this CLI promises that the protocol is the whole of
    stdout — a caller that has to skip a friendly line before `JSON.parse` is a
    caller that will one day skip a line that mattered. The collected text is
    not thrown away; the caller puts it on stderr, where it is still readable
    from a terminal and invisible to a pipe.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer


# ── commands ─────────────────────────────────────────────────────────────────


def cmd_init(args) -> int:
    scaffold.init(Path(args.vault or ".").resolve(), force=args.force)
    print("\nVault ready. Next: report-maker new \"My first report\"")
    return 0


def cmd_new(args) -> int:
    cfg = _config(args)
    date = dt.date.fromisoformat(args.date) if args.date else None
    folder = scaffold.new_report(
        cfg,
        title=args.title,
        slug=args.slug,
        into=args.into,
        template=args.template,
        date=date,
        kind=args.kind,
        author=args.author,
        with_diagram=args.with_diagram,
    )
    print(f"\nNext: edit {folder / 'sources.yml'} first, then {folder / 'main.typ'}")
    return 0


def cmd_list(args) -> int:
    cfg = _config(args)
    found = reports(cfg, args.target)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": r.id,
                        "group": r.group,
                        "template": r.template_id(),
                        "built": r.pdf.exists(),
                        "stale": r.is_stale(),
                        **r.meta(),
                    }
                    for r in found
                ],
                indent=2,
            )
        )
        return 0
    if not found:
        print(f"  no reports in {cfg.reports.relative_to(cfg.root)}/")
        return 0
    # Grouped by folder, because the folder is the filing system.
    grouped: dict[str, list] = {}
    for report in found:
        grouped.setdefault(report.group, []).append(report)
    for group, items in sorted(grouped.items()):
        print(f"\n  {group or '(top level)'}/")
        for report in items:
            state = "built" if report.pdf.exists() else "unbuilt"
            if report.pdf.exists() and report.is_stale():
                state = "stale"
            meta = report.meta()
            print(f"    {report.slug:<44} {state:<8} {meta.get('title', '')}")
    return 0


def cmd_templates(args) -> int:
    cfg = _config(args)
    found = vault_mod.templates(cfg)
    if args.json:
        print(
            json.dumps(
                {
                    tid: {
                        "title": t.title,
                        "group": t.group,
                        "description": t.description,
                        "extends": t.extends,
                        "brand": t.brand_pack,
                        "builtin": t.builtin,
                        "folder": str(t.folder),
                    }
                    for tid, t in found.items()
                },
                indent=2,
            )
        )
        return 0
    for group, items in vault_mod.groups(cfg).items():
        print(f"\n  {group or '(ungrouped)'}/")
        for tpl in items:
            origin = "built-in" if tpl.builtin else "vault"
            print(f"    {tpl.name:<24} {origin:<9} {tpl.title}")
            if tpl.description:
                print(f"      {tpl.description}")
    print("\n  Edit a built-in: report-maker template new <new-id> --from <id>")
    return 0


def cmd_template_new(args) -> int:
    cfg = _config(args)
    folder = scaffold.new_template(
        cfg,
        args.id,
        source=args.from_template,
        title=args.title,
        description=args.description,
        copy_design=not args.thin,
    )
    print(f"\nDesign at {folder}. Use it: report-maker new \"Title\" --template {args.id.strip('/')}")
    return 0


def cmd_template_show(args) -> int:
    cfg = _config(args)
    tpl = vault_mod.template(cfg, args.id)
    chain = " → ".join(t.id for t in vault_mod.lineage(cfg, tpl))
    print(f"  id           {tpl.id}")
    print(f"  title        {tpl.title}")
    print(f"  group        {tpl.group or '(ungrouped)'}")
    print(f"  origin       {'built-in' if tpl.builtin else 'vault'}")
    print(f"  folder       {tpl.folder}")
    print(f"  inherits     {chain}")
    print(f"  brand pack   {tpl.brand_pack}")
    if tpl.description:
        print(f"  description  {tpl.description}")
    own = ", ".join(tpl.design_files()) or "none (all inherited)"
    print(f"  own files    {own}")
    return 0


def cmd_template_list(args) -> int:
    cfg = _config(args)
    if not args.installed:
        return cmd_templates(args)
    items = install_mod.installed(cfg)
    if args.json:
        # A bare array, not the module's `{"installed": […]}` envelope: this is a
        # listing, and every other `--json` listing in this CLI prints the rows.
        print(json.dumps([install_mod.record_json(item) for item in items], indent=2))
        return 0
    return install_mod.report_installed(cfg, items)


def cmd_template_install(args) -> int:
    cfg = _config(args)
    item = install_mod.install(
        cfg,
        args.url,
        id=args.id,
        ref=args.ref,
        subdir=args.subdir,
        force=args.force,
        quiet=args.json,
    )
    if args.json:
        print(json.dumps(install_mod.record_json(item), indent=2))
    return 0


def cmd_template_update(args) -> int:
    cfg = _config(args)
    items = install_mod.update(cfg, args.id, quiet=args.json)
    if args.json:
        print(json.dumps([install_mod.record_json(item) for item in items], indent=2))
    return 0


def cmd_template_uninstall(args) -> int:
    cfg = _config(args)
    install_mod.uninstall(cfg, args.id)
    return 0


def cmd_stage(args) -> int:
    cfg = _config(args)
    if not library_mod.stage(cfg, verbose=True):
        print("  designs up to date")
    return 0


def cmd_diagrams(args) -> int:
    cfg = _config(args)
    if args.prepare:
        # The input rather than the output: what mermaid would be handed for this
        # one diagram, so a renderer that is not mermaid-cli — the app's live
        # editor — can render exactly what the build renders.
        payload = diagrams_mod.prepared_json(
            cfg, diagrams_mod.resolve_source(cfg, args.prepare)
        )
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            sys.stdout.write(payload["source"])
        return 0
    diagrams_mod.build(cfg, args.target, force=args.force)
    return 0


def _build_all(cfg: Config, args) -> list[str]:
    """Compile the target, and say which reports failed rather than which one did.

    Without `--keep-going` this is `build.build`, which raises on the first
    broken report — the right default for one report, and the wrong one for a
    vault of two hundred, where the first failure hides whether the other
    hundred and ninety-nine are fine. With it, every report is attempted and the
    failures are collected; the caller still exits non-zero. Nothing is
    forgiven, only deferred.

    Built here out of `build.compile_report`, which is the same public call
    `build.build` makes, so the two paths cannot compile a report differently.
    """
    if not args.keep_going:
        build_mod.build(cfg, args.target, force=args.force)
        return []
    library_mod.stage(cfg)
    failed: list[str] = []
    for report in reports(cfg, args.target):
        try:
            build_mod.compile_report(cfg, report, args.force)
        except build_mod.BuildError as exc:
            failed.append(report.id)
            print(f"  ✗ {report.id}\n{exc}", file=sys.stderr)
    return failed


def _report_failures(failed: list[str]) -> int:
    """Print the roll-call once, at the end. Returns the exit code."""
    if not failed:
        return 0
    # The roll-call is the last thing anybody should read, and it goes to a
    # different stream from the successes it summarises.
    sys.stdout.flush()
    print(
        f"\n  {len(failed)} report(s) did not compile:\n"
        + "\n".join(f"    {report_id}" for report_id in failed),
        file=sys.stderr,
    )
    return 1


def cmd_build(args) -> int:
    cfg = _config(args)
    return _report_failures(_build_all(cfg, args))


def cmd_pages(args) -> int:
    cfg = _config(args)
    pages_mod.build(cfg, args.target, ppi=args.ppi, force=args.force)
    return 0


def cmd_manifest(args) -> int:
    cfg = _config(args)
    manifest_mod.build(cfg)
    return 0


def cmd_check(args) -> int:
    cfg = _config(args)
    findings = check_mod.check(cfg, args.target)
    scores = score_mod.score(cfg, args.target) if args.score else None
    if args.json:
        # --json implies quiet: the protocol is the whole of stdout.
        payload = check_mod.findings_json(
            cfg, findings, score=score_mod.to_json(scores) if scores else None
        )
        print(json.dumps(payload, indent=2))
        code = 1 if any(f.level == "error" for f in findings) else 0
    else:
        code = check_mod.report_findings(cfg, findings)
        if scores is not None:
            score_mod.report_scores(cfg, scores)
    return 0 if args.warn_only else code


def cmd_sources(args) -> int:
    cfg = _config(args)
    found = reports(cfg, args.target)
    if args.json:
        # A flat array of rows: one report is the normal case, and that is
        # exactly the `SourceRow[]` the app's sources panel reads.
        rows = [
            row
            for report in found
            for row in sources_mod.rows(
                report, snapshots=snapshot_mod.records(report)
            )
        ]
        print(json.dumps(rows, indent=2))
        return 0
    for report in found:
        if len(found) > 1:
            print(f"\n  {report.id}")
        rows = sources_mod.rows(report, snapshots=snapshot_mod.records(report))
        if not rows:
            print("  no sources — start sources.yml before the prose")
            continue
        for row in rows:
            archived = "archived" if row["snapshot"] else "—"
            uses = f"{row['uses']} use(s)" if row["uses"] else "unused (W001)"
            print(f"  {row['key']:<28} {row['type']:<8} {uses:<14} {archived:<9} {row['title']}")
    return 0


def _fetching(args) -> dict:
    """`{"fetch": …}` when an address was pinned, and `{}` when one was not.

    Absent rather than defaulted, on purpose. With no `--pinned-address` these
    commands reach `cite` and `verify` with exactly the call they always made,
    and the fetcher those modules name for themselves is the one that runs —
    which is the difference between an option and a change of policy.
    """
    fetch = snapshot_mod.fetcher(pinned=args.pinned_address)
    return {} if fetch is snapshot_mod.http_fetch else {"fetch": fetch}


def cmd_cite(args) -> int:
    cfg = _config(args)
    # `cite` prints what it added and the key to cite with; nothing to add here.
    cite_mod.cite(
        cfg,
        args.target,
        args.url,
        key=args.key,
        type_=args.type_,
        no_snapshot=args.no_snapshot,
        **_fetching(args),
    )
    return 0


def cmd_verify(args) -> int:
    cfg = _config(args)
    drifts = verify_mod.verify(
        cfg,
        args.target,
        offline=args.offline,
        refresh=args.refresh,
        **_fetching(args),
    )
    if args.json:
        print(json.dumps(verify_mod.to_json(drifts), indent=2))
        # --json is quiet, not lenient: a dead source still fails the command.
        return 1 if any(d.state == "gone" for d in drifts) else 0
    return verify_mod.report_drift(cfg, drifts)


def cmd_score(args) -> int:
    cfg = _config(args)
    scores = score_mod.score(cfg, args.target)
    if args.json:
        print(json.dumps(score_mod.to_json(scores), indent=2))
        return 0
    return score_mod.report_scores(cfg, scores)


def cmd_todos(args) -> int:
    cfg = _config(args)

    # The writing forms take exactly one report. Appending a task to a folder of
    # them, or ticking "line 4" in eight files at once, is not a thing anybody
    # means by a target — `notes.one` refuses an ambiguous one by name.
    if args.add or args.check is not None or args.uncheck is not None:
        report = notes_mod.one(cfg, args.target)
        if args.add:
            todo = notes_mod.add(report, args.add)
            print(f"  → {notes_mod.todos_file(report).relative_to(cfg.root)}:{todo.line}")
            print(f"  - [ ] {todo.text}")
            return 0
        line = args.check if args.check is not None else args.uncheck
        notes_mod.toggle(report, line, args.check is not None, source=args.in_)
        print(f"  → {(report.folder / args.in_).relative_to(cfg.root)}:{line}")
        return 0

    rows = notes_mod.scan(cfg, args.target, open_only=args.open)
    if args.json:
        # Wrapped rather than bare, so the response has somewhere to grow a
        # summary later without breaking every reader of it.
        print(json.dumps({"reports": rows}, indent=2))
        return 0
    return notes_mod.report_todos(cfg, rows)


def cmd_notes(args) -> int:
    cfg = _config(args)
    note = notes_mod.notes(notes_mod.one(cfg, args.target))
    if args.json:
        print(json.dumps(notes_mod.note_json(note, root=cfg.root), indent=2))
        return 0
    if note is None:
        # Not an error: a report nobody has taken notes on is the normal case.
        print(f"  no {notes_mod.NOTES_NAME} here — write anything in it and it exists")
        return 0
    print(note.text, end="" if note.text.endswith("\n") else "\n")
    return 0


def cmd_diff(args) -> int:
    cfg = _config(args)
    diffs = diffing_mod.diff(cfg, args.target, rev=args.rev)
    if args.json:
        print(json.dumps(diffing_mod.to_json(diffs), indent=2))
        return 0
    return diffing_mod.report_diffs(cfg, diffs)


def cmd_html(args) -> int:
    cfg = _config(args)
    html_mod.export(cfg, args.target)
    return 0


def cmd_data_add(args) -> int:
    cfg = _config(args)
    if args.json:
        # --json implies quiet, and `add` narrates the copy on stdout. Reroute
        # its chatter to stderr rather than silencing it: the line saying where
        # the file was copied to is worth keeping, and stdout is the protocol.
        with _quiet() as chatter:
            datafile = data_mod.add(
                cfg, args.target, args.csv, key=args.key, title=args.title
            )
        sys.stderr.write(chatter.getvalue())
        # The key and the sha are the two things a caller cannot compute for
        # itself — `key` may have been chosen here, and the sha is what E011
        # will compare against. Scraping either out of the prose below would be
        # a second implementation of this command living in the app.
        row = data_mod.to_json([datafile], root=cfg.root)[0]
        row["srctable"] = data_mod.srctable_call(cfg, datafile)
        print(json.dumps(row, indent=2))
        return 0
    datafile = data_mod.add(cfg, args.target, args.csv, key=args.key, title=args.title)
    print(
        f"  {datafile.key:<28} {datafile.rows}×{datafile.columns} "
        f" sha256 {datafile.sha256[:12]}"
    )
    print("\nThe table that reads it:\n")
    print(data_mod.srctable_call(cfg, datafile))
    return 0


def cmd_data_list(args) -> int:
    cfg = _config(args)
    files = data_mod.inventory(cfg, args.target)
    if args.json:
        print(json.dumps(data_mod.to_json(files, root=cfg.root), indent=2))
        return 0
    return data_mod.report_files(cfg, files)


def cmd_data_check(args) -> int:
    cfg = _config(args)
    records = data_mod.check(cfg, args.target)
    if args.json:
        print(json.dumps(data_mod.findings_json(records, root=cfg.root), indent=2))
        return 1 if any(r[0] == "error" for r in records) else 0
    return data_mod.report_findings(cfg, records)


def cmd_data_revise(args) -> int:
    cfg = _config(args)
    report = datarev_mod.one(cfg, args.target)
    summary = datarev_mod.reregister(report, args.csv, note=args.note)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    return datarev_mod.report_change(cfg, summary)


def cmd_data_revisions(args) -> int:
    cfg = _config(args)
    report = datarev_mod.one(cfg, args.target)
    items = datarev_mod.revisions(report, args.csv)
    if args.json:
        print(json.dumps(datarev_mod.to_json(items, root=cfg.root), indent=2))
        return 0
    return datarev_mod.report_revisions(cfg, items)


def cmd_data_status(args) -> int:
    cfg = _config(args)
    state = datarev_mod.status(datarev_mod.one(cfg, args.target), args.csv)
    if args.json:
        print(json.dumps(state, indent=2))
        return 0
    # Printed here rather than routed through a reporter in datarev: this is one
    # question with a five-line answer, and the JSON form is what the app reads.
    print(f"  {state['rel']:<40} {('@' + state['key']) if state['key'] else 'unregistered'}")
    print(
        f"  on disk   sha256 {(state['current_sha'] or 'missing')[:12]}"
        f"  {state['rows']}×{state['columns']}"
    )
    print(f"  recorded  sha256 {(state['recorded_sha'] or 'nothing')[:12]}")
    print(f"  {'matches' if state['matches'] else 'does not match — E011 is firing'}")
    print(f"  {len(state['revisions'])} dated revision(s) kept")
    return 0


def cmd_data_absence(args) -> int:
    """Record a search that returned nothing, so an absence can be cited.

    Registered here because `data.py`'s W007 tells the reader in so many words to
    run `report-maker data absence`, and a warning that names a command which
    does not exist sends somebody looking for a way to state an absence and
    leaves them with nothing but an unmarked sentence — which is the outcome the
    citation rule exists to prevent.
    """
    cfg = _config(args)
    report, source = data_mod.add_absence(
        cfg,
        args.target,
        args.corpus,
        args.query,
        date=args.date,
        note=args.note,
        key=args.key,
    )
    if args.json:
        # The key and the sentence are the two things a caller cannot compute:
        # the key may have been derived here against the keys already taken, and
        # the sentence is what makes the entry worth adding at all.
        print(
            json.dumps(
                {
                    "report": report.id,
                    "key": source.key,
                    "fields": source.fields,
                    "line": data_mod.absence_line(source, args.corpus, args.query),
                },
                indent=2,
            )
        )
        return 0
    return data_mod.report_absence(report, source, args.corpus, args.query)


def cmd_find(args) -> int:
    cfg = _config(args)
    hits = search_mod.find(
        cfg,
        args.query,
        kinds=args.kind,
        limit=args.limit,
        rebuild=not args.no_index,
    )
    if args.json:
        print(json.dumps(search_mod.to_json(hits), indent=2))
        return 0
    return search_mod.report_hits(cfg, hits)


def cmd_index(args) -> int:
    cfg = _config(args)
    index = search_mod.build_index(cfg, force=args.force)
    docs = index["docs"]
    counts = {
        kind: sum(1 for doc in docs.values() if doc["kind"] == kind)
        for kind in search_mod.KINDS
    }
    tally = ", ".join(f"{n} {kind}" for kind, n in counts.items() if n) or "nothing to index"
    print(f"  {search_mod.index_path(cfg).relative_to(cfg.root)}  ({tally})")
    return 0


def cmd_all(args) -> int:
    cfg = _config(args)
    print("stage")
    library_mod.stage(cfg, verbose=True)
    print("diagrams")
    try:
        diagrams_mod.build(cfg, args.target, force=args.force)
    except diagrams_mod.DiagramError as exc:
        # A vault with no diagrams should not need Node installed at all.
        print(f"  skipped: {exc}", file=sys.stderr)
    print("build")
    failed = _build_all(cfg, args)
    if not args.no_pages:
        print("pages")
        pages_mod.build(cfg, args.target, force=args.force)
    if args.html:
        # After the pages, because the bundle inlines them; before check, because
        # check is the gate and stays last.
        print("html")
        html_mod.export(cfg, args.target)
    print("manifest")
    manifest_mod.build(cfg)
    print("check")
    findings = check_mod.check(cfg, args.target)
    code = check_mod.report_findings(cfg, findings)
    # `--warn-only` forgives the citation rule, which is what it says. It does
    # not forgive a report that would not compile: a PDF that does not exist is
    # not a warning about one.
    return _report_failures(failed) or (0 if args.warn_only else code)


def cmd_watch(args) -> int:
    cfg = _config(args)
    return build_mod.watch(cfg, args.target)


def cmd_sync(args) -> int:
    cfg = _config(args)
    if args.log:
        rows = gitsync_mod.log(cfg, args.log, args.limit)
        if args.json:
            print(json.dumps(gitsync_mod.to_json(gitsync_mod.state(cfg), log_rows=rows), indent=2))
            return 0
        return gitsync_mod.report_log(cfg, rows)
    if args.state:
        st = gitsync_mod.state(cfg)
        if args.json:
            print(json.dumps(gitsync_mod.to_json(st), indent=2))
            return 0
        return gitsync_mod.report_state(cfg, st)
    result = gitsync_mod.sync(cfg, message=args.message, do_push=args.push)
    if args.json:
        print(json.dumps(gitsync_mod.to_json(gitsync_mod.state(cfg), result=result), indent=2))
        # --json is quiet, not lenient: a refused push is still a failure.
        return 1 if result["refused"] else 0
    return gitsync_mod.report_sync(cfg, result)


def cmd_brand_list(args) -> int:
    cfg = _config(args)
    packs = brandcmd_mod.list_packs(cfg)
    if args.json:
        print(json.dumps(brandcmd_mod.packs_json(cfg, packs), indent=2))
        return 0
    return brandcmd_mod.report_packs(cfg, packs)


def cmd_brand_show(args) -> int:
    cfg = _config(args)
    shown = brandcmd_mod.show_pack(cfg, args.pack)
    if args.json:
        print(json.dumps(brandcmd_mod.show_json(cfg, shown), indent=2))
        return 0
    return brandcmd_mod.report_pack(cfg, shown)


def cmd_brand_new(args) -> int:
    cfg = _config(args)
    brandcmd_mod.new_pack(cfg, args.name, source=args.source)
    return 0


def cmd_brand_set(args) -> int:
    cfg = _config(args)
    # The value is coerced against the key's current type inside `set_key`, so a
    # size stays a number and a version string stays a string.
    brandcmd_mod.set_key(cfg, args.key, args.value, pack=args.pack)
    return 0


def cmd_brand_preview(args) -> int:
    cfg = _config(args)
    pages = brandcmd_mod.preview(cfg, args.pack, args.ppi)
    if args.json:
        print(json.dumps(brandcmd_mod.preview_json(cfg, args.pack, pages), indent=2))
        return 0
    # The human form prints the PNG paths, and the app's brand studio reads them
    # off stdout. Keep them on their own lines.
    return brandcmd_mod.report_preview(cfg, args.pack, pages)


def cmd_mcp(args) -> int:
    # Nothing may print here. stdout is the transport, and one banner line
    # desynchronises the client's parser for the rest of the session.
    return mcp_mod.serve(args.vault)


def cmd_doctor(args) -> int:
    try:
        cfg = _config(args)
        print(f"  vault       {cfg.root}")
        print(f"  reports     {len(reports(cfg))} in {cfg.reports.relative_to(cfg.root)}/")
    except ConfigError as exc:
        print(f"  vault       none ({exc.args[0].splitlines()[0]})")
        cfg = None

    typst = shutil.which(cfg.typst if cfg else "typst")
    print(f"  typst       {typst or 'MISSING — brew install typst'}")
    npm = shutil.which("npm")
    print(f"  npm         {npm or 'missing — only needed for mermaid diagrams'}")
    chrome = diagrams_mod.find_chrome()
    print(f"  chrome      {chrome or 'missing — only needed for mermaid diagrams'}")
    git = shutil.which("git")
    print(f"  git         {git or 'missing — only needed for sync, diff and template install'}")
    if cfg is not None:
        mmdc = diagrams_mod.mmdc(cfg)
        print(f"  mermaid-cli {mmdc if mmdc.exists() else 'not installed (installed on first use)'}")
    return 0 if typst else 1


def cmd_clean(args) -> int:
    cfg = _config(args)
    # The installed mermaid-cli also lives under .build/, and reinstalling it
    # costs half a minute of npm. Generated output goes; tooling stays unless
    # --all says otherwise.
    #
    # The brand previews are named from `brandcmd.PREVIEW_ROOT` rather than
    # spelled here. This line used to say `"brand"`, which is not a directory the
    # engine ever writes — the previews go to `.build/brand-preview/` — so `clean`
    # silently skipped them and left stale compiled specimens behind while
    # reporting success.
    targets = [
        cfg.out,
        cfg.build / brandcmd_mod.PREVIEW_ROOT,
        cfg.build / "typst",
        cfg.build / "mermaid" / "src",
    ]
    if args.all:
        targets = [cfg.out, cfg.build]
    for path in targets:
        if path.exists():
            shutil.rmtree(path)
            print(f"  removed {path.relative_to(cfg.root)}/")
    return 0


# ── parser ───────────────────────────────────────────────────────────────────


TARGET_HELP = (
    "a report id (acme/2026-08-12-audit), a bare slug when unambiguous, "
    "or a folder to take everything under it"
)


def _ip_literal(value: str) -> str:
    """`--pinned-address` takes an address, not a name — refused at the door.

    `snapshot._ip_literal` says the same thing at the point of use; this one
    exists so the refusal arrives as a usage error before a report is loaded,
    rather than as a failed fetch three steps in.
    """
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not an IP address. A pinned address is the literal to "
            "connect to; a name would have to be resolved, which is the lookup "
            "pinning removes."
        ) from exc


def _pinned_address(p: argparse.ArgumentParser) -> None:
    """Connect to a vetted address, keeping the hostname for TLS and `Host`.

    For a caller that is not the person at the keyboard. A server citing a URL a
    stranger typed resolves the name, judges every address it answers with, and
    then has to hand the URL to this command — which used to resolve the name
    all over again, so a name server willing to answer twice differently chose
    the address for a fetch that was approved against another one. Passing the
    vetted literal here closes that window: the connection goes where the caller
    looked, the certificate is still checked against the hostname, and a
    redirect off that origin is refused rather than followed on its scheme.

    Nobody typing this at a terminal needs it, and without it nothing changes.
    """
    p.add_argument(
        "--pinned-address",
        metavar="IP",
        type=_ip_literal,
        help="connect to this address instead of resolving the hostname, keeping "
             "the hostname for Host, TLS SNI and the certificate check (for a "
             "caller that has already vetted where the name resolves)",
    )


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="report-maker",
        description="Headless report engine: a folder-based vault of reports, designs and brands in; cited PDFs, page images and a manifest out.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n", 2)[2],
    )
    ap.add_argument(
        "-C", "--vault", metavar="DIR",
        help=f"the vault to work in (a folder holding {CONFIG_NAME}); "
             "without it, the nearest vault above the working directory",
    )
    # `action="version"` fires and exits during parse_args, before the required
    # subparser is enforced — so `report-maker --version` needs no vault and no
    # subcommand, which is the whole point of asking it.
    ap.add_argument(
        "--version",
        action="version",
        version=f"report-maker {__version__}",
        help="print the engine version and exit",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    def target(p, required=False):
        if required:
            p.add_argument("target", help=TARGET_HELP)
        else:
            p.add_argument("target", nargs="?", help=TARGET_HELP)
        return p

    p = sub.add_parser("init", help="make a folder into a vault")
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new", help="scaffold a report folder")
    p.add_argument("title")
    p.add_argument("--into", metavar="FOLDER", help="folder under reports/ to file it in, e.g. clients/acme")
    p.add_argument("--template", default="base", metavar="ID", help="design to build it with (default: base)")
    p.add_argument("--slug", help="folder name (default: YYYY-MM-DD-title)")
    p.add_argument("--date", help="ISO date for the report (default: today)")
    p.add_argument("--kind", help='e.g. "Company Audit", "Proposal"')
    p.add_argument("--author")
    p.add_argument("--with-diagram", action="store_true", help="include the template's example diagram")
    p.set_defaults(func=cmd_new)

    p = target(sub.add_parser("list", help="list reports, grouped by folder"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("templates", help="list designs, grouped by folder")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("template", help="create, inspect or install a design")
    tsub = p.add_subparsers(dest="template_command", required=True)

    tp = tsub.add_parser("new", help="create an editable design in the vault")
    tp.add_argument("id", help="template id — nesting groups it, e.g. audits/company")
    tp.add_argument("--from", dest="from_template", default="base", metavar="ID",
                    help="design to seed from (default: base)")
    tp.add_argument("--title")
    tp.add_argument("--description")
    tp.add_argument("--thin", action="store_true",
                    help="do not copy the Typst files — inherit them and override later")
    tp.set_defaults(func=cmd_template_new)

    tp = tsub.add_parser("show", help="what a design is and what it inherits")
    tp.add_argument("id")
    tp.set_defaults(func=cmd_template_show)

    tp = tsub.add_parser("list", help="the designs here, or only the installed ones")
    tp.add_argument("--installed", action="store_true",
                    help="only designs fetched from a repository, with the commit each is at")
    tp.add_argument("--json", action="store_true")
    tp.set_defaults(func=cmd_template_list)

    tp = tsub.add_parser("install", help="fetch a design from a git repository")
    tp.add_argument("url", help="a git URL, optionally #subdir and @ref")
    tp.add_argument("--id", metavar="ID", help="template id to install it as (default: from the URL)")
    tp.add_argument("--ref", help="branch, tag or commit to install")
    tp.add_argument("--subdir", metavar="PATH", help="the design's folder inside the repository")
    tp.add_argument("--force", action="store_true", help="overwrite an existing design of that id")
    tp.add_argument("--json", action="store_true")
    tp.set_defaults(func=cmd_template_install)

    tp = tsub.add_parser("update", help="re-fetch installed designs at their recorded ref")
    tp.add_argument("id", nargs="?", help="one design (default: every installed design)")
    tp.add_argument("--json", action="store_true")
    tp.set_defaults(func=cmd_template_update)

    tp = tsub.add_parser("uninstall", help="remove an installed design")
    tp.add_argument("id")
    tp.set_defaults(func=cmd_template_uninstall)

    p = sub.add_parser("stage", help="regenerate every design into .build/design/")
    p.set_defaults(func=cmd_stage)

    p = target(sub.add_parser("diagrams", help="render mermaid .mmd to branded .svg"))
    p.add_argument("--force", action="store_true")
    p.add_argument(
        "--prepare", metavar="DIAGRAM",
        help="print one diagram's prepared source instead of rendering it — the "
             "author's text with the brand's classDefs injected, exactly as the "
             "build hands it to mermaid. DIAGRAM is a path, a bare file name, or "
             "a report holding a single diagram",
    )
    p.add_argument(
        "--json", action="store_true",
        help="with --prepare: the prepared source plus the generated mermaid "
             "config and stylesheet, paths and contents both",
    )
    p.set_defaults(func=cmd_diagrams)

    p = target(sub.add_parser("build", help="compile reports to PDF"))
    p.add_argument("--force", action="store_true")
    p.add_argument(
        "--keep-going", action="store_true",
        help="compile every report even after one fails, and list the failures "
             "at the end; still exits non-zero",
    )
    p.set_defaults(func=cmd_build)

    p = target(sub.add_parser("pages", help="render page PNGs plus pages.json"))
    p.add_argument("--ppi", type=int)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_pages)

    p = sub.add_parser("manifest", help="write out/manifest.json")
    p.set_defaults(func=cmd_manifest)

    p = target(sub.add_parser("check", help="enforce the citation rule"))
    p.add_argument("--warn-only", action="store_true", help="never fail, just report")
    p.add_argument("--score", action="store_true", help="also report evidence density")
    p.add_argument("--json", action="store_true", help="machine-readable findings (implies quiet)")
    p.set_defaults(func=cmd_check)

    p = target(sub.add_parser("sources", help="the bibliography of a report, with use counts"), required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("cite", help="fetch a URL, archive it, add it to a report's sources.yml")
    p.add_argument("target", help="the report the source belongs to")
    p.add_argument("url")
    p.add_argument("--key", help="citation key to use (default: derived from the title and the site)")
    p.add_argument("--type", dest="type_", metavar="TYPE",
                   help="hayagriva type, e.g. Web, Report, Article (default: Web)")
    p.add_argument("--no-snapshot", action="store_true", help="add the entry without archiving the page")
    _pinned_address(p)
    p.set_defaults(func=cmd_cite)

    p = target(sub.add_parser("verify", help="re-fetch archived sources and report drift"))
    p.add_argument("--offline", action="store_true",
                   help="report archived sources without fetching anything")
    p.add_argument("--refresh", action="store_true",
                   help="re-archive changed pages, keeping the old copy")
    p.add_argument("--json", action="store_true")
    _pinned_address(p)
    p.set_defaults(func=cmd_verify)

    p = target(sub.add_parser("score", help="evidence density per report"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_score)

    p = target(sub.add_parser(
        "todos",
        help="the pad beside a report: todos.md, notes.md, and // TODO: in the source",
    ))
    p.add_argument("--json", action="store_true")
    p.add_argument("--open", action="store_true", help="list only what is still open")
    p.add_argument("--add", metavar="TEXT", help="append a task to reports/<id>/todos.md")
    p.add_argument("--check", type=int, metavar="LINE", help="tick the checkbox on that line")
    p.add_argument("--uncheck", type=int, metavar="LINE", help="untick the checkbox on that line")
    # Not optional: a checklist item sits in notes.md as easily as in todos.md,
    # and a toggle that only knows about one file leaves the other's boxes
    # permanently unclickable.
    p.add_argument("--in", dest="in_", default=notes_mod.TODOS_NAME, metavar="FILE",
                   choices=list(notes_mod.WRITABLE),
                   help=f"which file --check/--uncheck rewrites (default: {notes_mod.TODOS_NAME})")
    p.set_defaults(func=cmd_todos)

    p = target(sub.add_parser("notes", help="the scratch pad beside a report"), required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_notes)

    p = target(sub.add_parser("diff", help="what changed in a report since a git revision"), required=True)
    p.add_argument("--rev", default="HEAD~1", metavar="REV",
                   help="the revision to compare against (default: HEAD~1)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_diff)

    p = target(sub.add_parser("html", help="export a self-contained HTML bundle"))
    p.set_defaults(func=cmd_html)

    p = sub.add_parser("data", help="the CSV files a table reads, registered as sources")
    dsub = p.add_subparsers(dest="data_command", required=True)

    dp = dsub.add_parser("add", help="register a CSV so a table can cite it")
    dp.add_argument("target", help="the report the numbers belong to")
    dp.add_argument("csv", help="path to the .csv/.tsv file")
    dp.add_argument("--key", help="citation key to use (default: data-<filename>)")
    dp.add_argument("--title", help="how the References inventory should name it")
    dp.add_argument("--json", action="store_true",
                    help="the registered file as data, including the key, the "
                         "sha256 E011 will check, and the srctable(…) to paste")
    dp.set_defaults(func=cmd_data_add)

    dp = target(dsub.add_parser("list", help="the data files in a vault, with their checksums"))
    dp.add_argument("--json", action="store_true")
    dp.set_defaults(func=cmd_data_list)

    dp = target(dsub.add_parser("check", help="data rules alone: unregistered, stale or unused files"))
    dp.add_argument("--json", action="store_true")
    dp.set_defaults(func=cmd_data_check)

    dp = dsub.add_parser(
        "revise",
        help="archive the current version, then move the recorded checksum onto it",
    )
    dp.add_argument("target", help="the report the numbers belong to")
    dp.add_argument("csv", help="the data file, e.g. data/prices.csv")
    dp.add_argument("--note", help="why the numbers moved; spliced into the entry's "
                                   "note, beside the checksum")
    dp.add_argument("--json", action="store_true")
    dp.set_defaults(func=cmd_data_revise)

    dp = dsub.add_parser("revisions", help="the dated copies kept of one data file, newest first")
    dp.add_argument("target", help="the report the numbers belong to")
    dp.add_argument("csv", help="the data file, e.g. data/prices.csv")
    dp.add_argument("--json", action="store_true")
    dp.set_defaults(func=cmd_data_revisions)

    dp = dsub.add_parser(
        "status",
        help="what sources.yml records about a data file, against the bytes on disk",
    )
    dp.add_argument("target", help="the report the numbers belong to")
    dp.add_argument("csv", help="the data file, e.g. data/prices.csv")
    dp.add_argument("--json", action="store_true")
    dp.set_defaults(func=cmd_data_status)

    dp = dsub.add_parser(
        "absence",
        help="record a search that returned nothing, so the absence can be cited",
    )
    dp.add_argument("target", help="the report the search belongs to")
    dp.add_argument("corpus", help="what was searched, e.g. 'every page on acme.com'")
    dp.add_argument("query", help="the exact query that was run")
    dp.add_argument("--date", help="when the search was run (default: today)")
    dp.add_argument("--note", help="caveats about the method; recorded before the result")
    dp.add_argument("--key", help="citation key to use (default: absence-<corpus>-<query>); "
                                  "naming one rewrites that entry instead of filing a second search")
    dp.add_argument("--json", action="store_true",
                    help="the entry as data, including the key and the sentence to paste")
    dp.set_defaults(func=cmd_data_absence)

    p = sub.add_parser("find", help="search reports, sources, snapshots and diagrams")
    p.add_argument("query", help='what to look for — "quoted" is a phrase, -word excludes, kind:snapshot filters')
    p.add_argument("--kind", action="append", choices=list(search_mod.KINDS), metavar="KIND",
                   help="restrict to report, source, snapshot or diagram; repeat to allow several")
    p.add_argument("--limit", type=int, default=50, metavar="N", help="most hits to return (default: 50)")
    p.add_argument("--json", action="store_true", help="machine-readable hits; always exits 0")
    p.add_argument("--no-index", action="store_true", help="query the stored index without refreshing it first")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("index", help="build or refresh the search index")
    p.add_argument("--force", action="store_true", help="re-read every file, not only the changed ones")
    p.set_defaults(func=cmd_index)

    p = target(sub.add_parser("all", help="stage, diagrams, build, pages, manifest, check"))
    p.add_argument("--force", action="store_true")
    p.add_argument(
        "--keep-going", action="store_true",
        help="compile every report even after one fails, and list the failures "
             "at the end; still exits non-zero",
    )
    p.add_argument("--no-pages", action="store_true", help="skip page images")
    p.add_argument("--html", action="store_true", help="also export the HTML bundles")
    p.add_argument("--warn-only", action="store_true")
    p.set_defaults(func=cmd_all)

    p = target(sub.add_parser("watch", help="live rebuild one report"), required=True)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("sync", help="commit the vault to git, and optionally push")
    p.add_argument("-m", "--message",
                   help="commit message (default: report-maker: <n> file(s) — <date>)")
    p.add_argument("--push", action="store_true", help="also push the branch to its upstream")
    # --status is the same question said the other way, and the desktop shell
    # asks it with that spelling. Both reach the same flag.
    p.add_argument("--state", "--status", action="store_true",
                   help="print the repository state and stop")
    p.add_argument("--log", metavar="PATH", help="list the commits touching PATH")
    p.add_argument("--limit", type=int, default=50, help="how many commits --log lists (default: 50)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("brand", help="brand packs: list, show, new, set, preview")
    bsub = p.add_subparsers(dest="brand_command", required=True)

    bp = bsub.add_parser("list", help="the brand packs available here")
    bp.add_argument("--json", action="store_true")
    bp.set_defaults(func=cmd_brand_list)

    bp = bsub.add_parser("show", help="one pack, every key, with the source of each value")
    bp.add_argument("pack", nargs="?", default="default")
    bp.add_argument("--json", action="store_true")
    bp.set_defaults(func=cmd_brand_show)

    bp = bsub.add_parser("new", help="create a brand pack, seeded from an existing one")
    bp.add_argument("name")
    bp.add_argument("--from", dest="source", default="default", metavar="PACK")
    bp.set_defaults(func=cmd_brand_new)

    bp = bsub.add_parser("set", help="set one dotted key, e.g. colors.accent")
    bp.add_argument("key")
    bp.add_argument("value")
    bp.add_argument("--pack", default="default")
    bp.set_defaults(func=cmd_brand_set)

    bp = bsub.add_parser("preview", help="build the brand specimen and its page images")
    bp.add_argument("--pack", default="default")
    bp.add_argument("--ppi", type=int, default=None)
    bp.add_argument("--json", action="store_true")
    bp.set_defaults(func=cmd_brand_preview)

    p = sub.add_parser("mcp", help="serve this vault to an agent over MCP (stdio JSON-RPC)")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("doctor", help="report what is installed and what is missing")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("clean", help="remove generated output")
    p.add_argument("--all", action="store_true", help="also remove the installed mermaid-cli")
    p.set_defaults(func=cmd_clean)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except (
        ConfigError,
        vault_mod.VaultError,
        brand_mod.BrandError,
        build_mod.BuildError,
        diagrams_mod.DiagramError,
        pages_mod.PagesError,
        cite_mod.CiteError,
        snapshot_mod.SnapshotError,
        diffing_mod.DiffError,
        html_mod.HtmlError,
        gitsync_mod.GitError,
        search_mod.SearchError,
        data_mod.DataError,
        install_mod.InstallError,
        notes_mod.NotesError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
