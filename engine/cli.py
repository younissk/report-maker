"""report-maker — the command line over the engine.

Every command is non-interactive and writes files: no server, no browser, no
editor, nothing that waits for a human. `watch` is the single exception, and it
only exists because writing a report is nicer with a live rebuild.

    report-maker init                 make the current directory a vault
    report-maker new "Title"          scaffold a report folder
    report-maker list [--json]        what reports exist, by folder
    report-maker templates [--json]   what designs exist, by group
    report-maker template new <id>    create an editable design
    report-maker stage                regenerate every design into .build/
    report-maker diagrams [target]    mermaid .mmd → branded .svg
    report-maker build [target]       Typst → PDF
    report-maker pages [target]       PDF pages → PNG + pages.json
    report-maker manifest             out/manifest.json
    report-maker check [target]       enforce the citation rule
    report-maker all [target]         stage, diagrams, build, pages, manifest, check
    report-maker watch <target>       live rebuild while writing
    report-maker doctor               what is installed, what is missing
    report-maker clean                remove out/ and .build/

Every command runs against one vault: the folder holding report-maker.toml, found
by walking up from the working directory, or named outright with -C/--vault.

A target is a report id, a bare slug, or a folder — `build clients/acme` builds
every report filed under it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

from . import brand as brand_mod
from . import library as library_mod
from . import vault as vault_mod
from . import build as build_mod
from . import check as check_mod
from . import diagrams as diagrams_mod
from . import manifest as manifest_mod
from . import pages as pages_mod
from . import scaffold
from .config import CONFIG_NAME, Config, ConfigError, load
from .workspace import reports


def _config(args) -> Config:
    return load(Path(args.vault) if args.vault else None)


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


def cmd_stage(args) -> int:
    cfg = _config(args)
    if not library_mod.stage(cfg, verbose=True):
        print("  designs up to date")
    return 0


def cmd_diagrams(args) -> int:
    cfg = _config(args)
    diagrams_mod.build(cfg, args.target, force=args.force)
    return 0


def cmd_build(args) -> int:
    cfg = _config(args)
    build_mod.build(cfg, args.target, force=args.force)
    return 0


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
    code = check_mod.report_findings(cfg, findings)
    return 0 if args.warn_only else code


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
    build_mod.build(cfg, args.target, force=args.force)
    if not args.no_pages:
        print("pages")
        pages_mod.build(cfg, args.target, force=args.force)
    print("manifest")
    manifest_mod.build(cfg)
    print("check")
    findings = check_mod.check(cfg, args.target)
    code = check_mod.report_findings(cfg, findings)
    return 0 if args.warn_only else code


def cmd_watch(args) -> int:
    cfg = _config(args)
    return build_mod.watch(cfg, args.target)


def cmd_clean(args) -> int:
    cfg = _config(args)
    # The installed mermaid-cli also lives under .build/, and reinstalling it
    # costs half a minute of npm. Generated output goes; tooling stays unless
    # --all says otherwise.
    targets = [cfg.out, cfg.build / "brand", cfg.build / "typst", cfg.build / "mermaid" / "src"]
    if args.all:
        targets = [cfg.out, cfg.build]
    for path in targets:
        if path.exists():
            shutil.rmtree(path)
            print(f"  removed {path.relative_to(cfg.root)}/")
    return 0


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
    if cfg is not None:
        mmdc = diagrams_mod.mmdc(cfg)
        print(f"  mermaid-cli {mmdc if mmdc.exists() else 'not installed (installed on first use)'}")
    return 0 if typst else 1


# ── parser ───────────────────────────────────────────────────────────────────


TARGET_HELP = (
    "a report id (acme/2026-08-12-audit), a bare slug when unambiguous, "
    "or a folder to take everything under it"
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

    p = sub.add_parser("template", help="create or inspect a design")
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

    p = sub.add_parser("stage", help="regenerate every design into .build/design/")
    p.set_defaults(func=cmd_stage)

    p = target(sub.add_parser("diagrams", help="render mermaid .mmd to branded .svg"))
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_diagrams)

    p = target(sub.add_parser("build", help="compile reports to PDF"))
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_build)

    p = target(sub.add_parser("pages", help="render page PNGs plus pages.json"))
    p.add_argument("--ppi", type=int)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_pages)

    p = sub.add_parser("manifest", help="write out/manifest.json")
    p.set_defaults(func=cmd_manifest)

    p = target(sub.add_parser("check", help="enforce the citation rule"))
    p.add_argument("--warn-only", action="store_true", help="never fail, just report")
    p.set_defaults(func=cmd_check)

    p = target(sub.add_parser("all", help="stage, diagrams, build, pages, manifest, check"))
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-pages", action="store_true", help="skip page images")
    p.add_argument("--warn-only", action="store_true")
    p.set_defaults(func=cmd_all)

    p = target(sub.add_parser("watch", help="live rebuild one report"), required=True)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("clean", help="remove generated output")
    p.add_argument("--all", action="store_true", help="also remove the installed mermaid-cli")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("doctor", help="report what is installed and what is missing")
    p.set_defaults(func=cmd_doctor)

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
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
