"""report-maker — the command line over the engine.

Every command is non-interactive and writes files: no server, no browser, no
editor, nothing that waits for a human. `watch` is the single exception, and it
only exists because writing a report is nicer with a live rebuild.

    report-maker init                 make the current directory a workspace
    report-maker new "Title"          scaffold a report folder
    report-maker list [--json]        what reports exist
    report-maker brand                regenerate the theme and stage the Typst library
    report-maker diagrams [slug]      mermaid .mmd → branded .svg
    report-maker build [slug]         Typst → PDF
    report-maker pages [slug]         PDF pages → PNG + pages.json
    report-maker manifest             out/manifest.json
    report-maker check [slug]         enforce the citation rule
    report-maker all                  stage, diagrams, build, pages, manifest, check
    report-maker watch <slug>         live rebuild while writing
    report-maker doctor               what is installed, what is missing
    report-maker clean                remove out/ and .build/
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
from . import build as build_mod
from . import check as check_mod
from . import diagrams as diagrams_mod
from . import manifest as manifest_mod
from . import pages as pages_mod
from . import scaffold
from .config import CONFIG_NAME, Config, ConfigError, load
from .workspace import reports


def _config(args) -> Config:
    return load(Path(args.workspace) if args.workspace else None)


# ── commands ─────────────────────────────────────────────────────────────────


def cmd_init(args) -> int:
    scaffold.init(Path(args.workspace or ".").resolve(), force=args.force)
    print(f"\nWorkspace ready. Next: report-maker new \"My first report\"")
    return 0


def cmd_new(args) -> int:
    cfg = _config(args)
    date = dt.date.fromisoformat(args.date) if args.date else None
    folder = scaffold.new_report(
        cfg,
        title=args.title,
        slug=args.slug,
        date=date,
        kind=args.kind,
        author=args.author,
        with_diagram=args.with_diagram,
    )
    print(f"\nNext: edit {folder / 'sources.yml'} first, then {folder / 'main.typ'}")
    return 0


def cmd_list(args) -> int:
    cfg = _config(args)
    found = reports(cfg)
    if args.json:
        print(
            json.dumps(
                [{"slug": r.slug, "built": r.pdf.exists(), **r.meta()} for r in found],
                indent=2,
            )
        )
        return 0
    if not found:
        print(f"  no reports in {cfg.reports.relative_to(cfg.root)}/")
        return 0
    for report in found:
        meta = report.meta()
        state = "built" if report.pdf.exists() else "unbuilt"
        if report.pdf.exists() and report.is_stale():
            state = "stale"
        print(f"  {report.slug:<48} {state:<8} {meta.get('title', '')}")
    return 0


def cmd_brand(args) -> int:
    cfg = _config(args)
    written = brand_mod.sync(cfg, verbose=True) + library_mod.sync(cfg)
    if not written:
        print("  theme and library up to date")
    return 0


def cmd_diagrams(args) -> int:
    cfg = _config(args)
    diagrams_mod.build(cfg, args.slug, force=args.force)
    return 0


def cmd_build(args) -> int:
    cfg = _config(args)
    build_mod.build(cfg, args.slug, force=args.force)
    return 0


def cmd_pages(args) -> int:
    cfg = _config(args)
    pages_mod.build(cfg, args.slug, ppi=args.ppi, force=args.force)
    return 0


def cmd_manifest(args) -> int:
    cfg = _config(args)
    manifest_mod.build(cfg)
    return 0


def cmd_check(args) -> int:
    cfg = _config(args)
    findings = check_mod.check(cfg, args.slug)
    code = check_mod.report_findings(cfg, findings)
    return 0 if args.warn_only else code


def cmd_all(args) -> int:
    cfg = _config(args)
    print("stage")
    library_mod.stage(cfg, verbose=True)
    print("diagrams")
    try:
        diagrams_mod.build(cfg, args.slug, force=args.force)
    except diagrams_mod.DiagramError as exc:
        # A workspace with no diagrams should not need Node installed at all.
        print(f"  skipped: {exc}", file=sys.stderr)
    print("build")
    build_mod.build(cfg, args.slug, force=args.force)
    if not args.no_pages:
        print("pages")
        pages_mod.build(cfg, args.slug, force=args.force)
    print("manifest")
    manifest_mod.build(cfg)
    print("check")
    findings = check_mod.check(cfg, args.slug)
    code = check_mod.report_findings(cfg, findings)
    return 0 if args.warn_only else code


def cmd_watch(args) -> int:
    cfg = _config(args)
    return build_mod.watch(cfg, args.slug)


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
        print(f"  workspace   {cfg.root}")
        print(f"  reports     {len(reports(cfg))} in {cfg.reports.relative_to(cfg.root)}/")
    except ConfigError as exc:
        print(f"  workspace   none ({exc.args[0].splitlines()[0]})")
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


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="report-maker",
        description="Headless report engine: Typst in, cited PDFs and page images out.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n", 2)[2],
    )
    ap.add_argument(
        "-C", "--workspace", metavar="DIR",
        help=f"directory to run in (any parent holding {CONFIG_NAME})",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="make a directory into a workspace")
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new", help="scaffold a report folder")
    p.add_argument("title")
    p.add_argument("--slug", help="folder name (default: YYYY-MM-DD-title)")
    p.add_argument("--date", help="ISO date for the report (default: today)")
    p.add_argument("--kind", help='e.g. "Company Audit", "Proposal"')
    p.add_argument("--author")
    p.add_argument("--with-diagram", action="store_true", help="include an example mermaid diagram")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("list", help="list reports")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("brand", help="regenerate the theme and stage the Typst library")
    p.set_defaults(func=cmd_brand)

    p = sub.add_parser("diagrams", help="render mermaid .mmd to branded .svg")
    p.add_argument("slug", nargs="?")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_diagrams)

    p = sub.add_parser("build", help="compile reports to PDF")
    p.add_argument("slug", nargs="?")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("pages", help="render page PNGs plus pages.json")
    p.add_argument("slug", nargs="?")
    p.add_argument("--ppi", type=int)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_pages)

    p = sub.add_parser("manifest", help="write out/manifest.json")
    p.set_defaults(func=cmd_manifest)

    p = sub.add_parser("check", help="enforce the citation rule")
    p.add_argument("slug", nargs="?")
    p.add_argument("--warn-only", action="store_true", help="never fail, just report")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("all", help="stage, diagrams, build, pages, manifest, check")
    p.add_argument("slug", nargs="?")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-pages", action="store_true", help="skip page images")
    p.add_argument("--warn-only", action="store_true")
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("watch", help="live rebuild one report")
    p.add_argument("slug")
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
        brand_mod.BrandError,
        build_mod.BuildError,
        diagrams_mod.DiagramError,
        pages_mod.PagesError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
