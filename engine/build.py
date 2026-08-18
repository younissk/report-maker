"""Typst compilation.

`typst compile --root <vault>` is the whole build. `--root` matters: report
sources reference the engine library, the generated theme and their own
`sources.yml` by project-absolute paths, and Typst resolves those against the
root. Compiling with the wrong root fails on the first import.

One thing happens before typst is invoked: the facts of the build are written to
`.build/facts/<report-id>.json`, so a design that prints a colophon has something
to read. It goes here rather than in a command of its own because a colophon is
only true of the run that produced the PDF beside it — generated a step earlier
or a step later, it is a description of a different build, which is exactly the
drift the colophon exists to make impossible. See `facts.py`. It cannot fail a
build: a fact we could not gather degrades to "unknown", and a facts file we
could not write is reported and stepped over.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import facts, library
from .config import Config
from .workspace import Report, reports


class BuildError(RuntimeError):
    pass


def _require_typst(cfg: Config) -> str:
    binary = shutil.which(cfg.typst)
    if binary is None:
        raise BuildError(
            f"typst not found on PATH (looked for {cfg.typst!r}).\n"
            "Install it — `brew install typst` — or set typst.bin in report-maker.toml."
        )
    return binary


def write_facts(cfg: Config, report: Report) -> None:
    """Write `.build/facts/<report-id>.json`, and never let that stop a build.

    Deliberately after the staleness check and before typst: a report that was
    skipped keeps the facts of the build that actually produced its PDF, rather
    than acquiring a colophon dated today for a document compiled last week. The
    facts file is not one of `report.inputs()` either, so writing it cannot make
    the report stale and send the next build round again.
    """
    try:
        facts.write(cfg, report)
    except Exception as exc:  # noqa: BLE001 — a fact is never worth a failed build
        print(f"  · no build facts for {report.id}: {exc}")


def compile_report(
    cfg: Config, report: Report, force: bool = False, *, with_facts: bool = True
) -> Path:
    if not force and not report.is_stale():
        print(f"  · {report.pdf.relative_to(cfg.root)} (up to date)")
        return report.pdf

    binary = _require_typst(cfg)
    if with_facts:
        write_facts(cfg, report)
    report.pdf.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            binary, "compile",
            "--root", str(cfg.root),
            str(report.main),
            str(report.pdf),
        ],
        cwd=str(cfg.root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BuildError(
            f"typst failed on {report.main.relative_to(cfg.root)}\n"
            + (result.stdout + result.stderr).rstrip()
        )
    if result.stderr.strip():
        # Warnings — unknown fonts, unresolved citations. Worth seeing, not fatal.
        print(result.stderr.rstrip())
    print(f"  → {report.pdf.relative_to(cfg.root)}")
    return report.pdf


def build(
    cfg: Config,
    target: str | None = None,
    force: bool = False,
    *,
    with_facts: bool = True,
) -> list[Path]:
    """Compile reports to PDF.

    `with_facts` is on by default and exists so a build can be told not to write
    `.build/facts/`: gathering them reads every snapshot record and hashes every
    registered CSV, which is a cost worth skipping on a vault of hundreds of
    reports where no design prints a colophon. A report that *does* print one and
    is built with `with_facts=False` fails in typst on a missing file, which is
    the correct and legible outcome — the alternative is a document quietly
    stating the facts of some earlier build.
    """
    library.stage(cfg)
    return [
        compile_report(cfg, r, force, with_facts=with_facts) for r in reports(cfg, target)
    ]


def watch(cfg: Config, target: str) -> int:
    """Live rebuild of one report. The only long-running command in the engine.

    The facts are written once, at the start. Typst recompiles on every keystroke
    and re-reads the file each time, so a colophon under `watch` states the facts
    of the session rather than of the last save — which is the right trade for a
    working preview, and is why `build` remains the command whose output is the
    one to hand over.
    """
    library.stage(cfg)
    binary = _require_typst(cfg)
    report = reports(cfg, target)[0]
    write_facts(cfg, report)
    report.pdf.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [
            binary, "watch",
            "--root", str(cfg.root),
            str(report.main),
            str(report.pdf),
        ],
        cwd=str(cfg.root),
    ).returncode
