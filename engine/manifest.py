"""out/manifest.json — the machine-readable index of the workspace.

The engine emits no HTML and starts no server: whatever consumes these reports
— a site build, an agent, a CI job, an upload script — reads this file and finds
every report, its metadata, and the artefacts that currently exist on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import diagrams
from .config import Config
from .workspace import Report, reports


def entry(cfg: Config, report: Report) -> dict:
    meta = report.meta()
    pages_index = report.pages_dir / "pages.json"
    pages = None
    if pages_index.exists():
        with pages_index.open(encoding="utf-8") as handle:
            pages = json.load(handle)

    return {
        "slug": report.slug,
        "meta": meta,
        "source": {
            "main": str(report.main.relative_to(cfg.root)),
            "sources": (
                str(report.sources.relative_to(cfg.root))
                if report.sources.exists()
                else None
            ),
            "diagrams": [
                str(p.relative_to(cfg.root))
                for p in sorted(report.diagrams.glob("*.mmd"))
            ],
        },
        "artifacts": {
            "pdf": str(report.pdf.relative_to(cfg.root)) if report.pdf.exists() else None,
            "pages": (
                str(report.pages_dir.relative_to(cfg.root)) if pages else None
            ),
            "page_count": pages["count"] if pages else None,
        },
        "state": {
            "built": report.pdf.exists(),
            "stale": report.is_stale(),
            "diagrams_unrendered": [
                str(p.relative_to(cfg.root)) for p in diagrams.missing_svgs(report)
            ],
        },
    }


def build(cfg: Config, slug: str | None = None) -> Path:
    found = reports(cfg, slug)
    data = {
        "workspace": str(cfg.root),
        "count": len(found),
        "reports": [entry(cfg, r) for r in found],
    }
    cfg.out.mkdir(parents=True, exist_ok=True)
    target = cfg.out / "manifest.json"
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  → {target.relative_to(cfg.root)} ({len(found)} reports)")
    return target
