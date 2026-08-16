"""Typst compilation.

`typst compile --root <workspace>` is the whole build. `--root` matters: report
sources reference the engine library, the generated theme and their own
`sources.yml` by project-absolute paths, and Typst resolves those against the
root. Compiling with the wrong root fails on the first import.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import library
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


def compile_report(cfg: Config, report: Report, force: bool = False) -> Path:
    if not force and not report.is_stale():
        print(f"  · {report.pdf.relative_to(cfg.root)} (up to date)")
        return report.pdf

    binary = _require_typst(cfg)
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


def build(cfg: Config, slug: str | None = None, force: bool = False) -> list[Path]:
    library.stage(cfg)
    return [compile_report(cfg, r, force) for r in reports(cfg, slug)]


def watch(cfg: Config, slug: str) -> int:
    """Live rebuild of one report. The only long-running command in the engine."""
    library.stage(cfg)
    binary = _require_typst(cfg)
    report = reports(cfg, slug)[0]
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
