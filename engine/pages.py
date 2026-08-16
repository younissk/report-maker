"""Page images.

A PDF is the deliverable, but a lot of consumers cannot render one: embedded
browser panes, chat clients, vision models, image-only review tools. Rendering
each page to a PNG makes the document readable anywhere, and gives an agent
something it can actually look at.

Output is data, not a viewer:

    out/pages/<report-id>/page-1.png …
    out/pages/<report-id>/pages.json   { id, ppi, count, pages: [...] }
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import library
from .build import _require_typst
from .config import Config
from .workspace import Report, reports


class PagesError(RuntimeError):
    pass


def is_fresh(report: Report) -> bool:
    index = report.pages_dir / "pages.json"
    if not index.exists() or not report.pdf.exists():
        return False
    return index.stat().st_mtime >= report.pdf.stat().st_mtime


def render(cfg: Config, report: Report, ppi: int) -> list[Path]:
    binary = _require_typst(cfg)
    target = report.pages_dir
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    result = subprocess.run(
        [
            binary, "compile",
            "--root", str(cfg.root),
            "--format", "png",
            "--ppi", str(ppi),
            str(report.main),
            str(target / "page-{0p}.png"),
        ],
        cwd=str(cfg.root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PagesError(
            f"typst failed rendering pages for {report.id}\n"
            + (result.stdout + result.stderr).rstrip()
        )
    # Typst does not zero-pad, so page-10 sorts before page-2 lexically. The
    # manifest is read by machines that trust the order, so sort numerically.
    return sorted(
        target.glob("page-*.png"),
        key=lambda p: int(re.sub(r"\D", "", p.stem) or 0),
    )


def build_one(cfg: Config, report: Report, ppi: int, force: bool = False) -> Path:
    index = report.pages_dir / "pages.json"
    if not force and is_fresh(report):
        print(f"  · {index.relative_to(cfg.root)} (up to date)")
        return index

    files = render(cfg, report, ppi)
    index.write_text(
        json.dumps(
            {
                "id": report.id,
                "slug": report.slug,
                "ppi": ppi,
                "count": len(files),
                "pages": [f.name for f in files],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"  → {report.pages_dir.relative_to(cfg.root)} ({len(files)} pages @ {ppi} ppi)")
    return index


def build(
    cfg: Config, target: str | None = None, ppi: int | None = None, force: bool = False
) -> list[Path]:
    ppi = ppi or cfg.ppi
    library.stage(cfg)
    return [build_one(cfg, r, ppi, force) for r in reports(cfg, target)]
