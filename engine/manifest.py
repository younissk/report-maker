"""out/manifest.json — the machine-readable index of the vault.

The engine emits no HTML and starts no server: whatever consumes these reports
— a site build, an agent, a CI job, an upload script — reads this file and finds
every report, its metadata, and the artefacts that currently exist on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import diagrams, vault
from .config import Config
from .workspace import Report, reports


def entry(cfg: Config, report: Report) -> dict:
    meta = report.meta()
    pages_index = report.pages_dir / "pages.json"
    pages = None
    if pages_index.exists():
        with pages_index.open(encoding="utf-8") as handle:
            pages = json.load(handle)

    template = report.template_id()
    return {
        "id": report.id,
        "slug": report.slug,
        "group": report.group,
        "template": template,
        "brand": vault.template(cfg, template).brand_pack,
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


def build(cfg: Config, target: str | None = None) -> Path:
    found = reports(cfg, target)
    entries = [entry(cfg, r) for r in found]
    data = {
        "vault": str(cfg.root),
        "count": len(found),
        # Folders are the filing system, so the manifest publishes them: a
        # consumer can rebuild the tree without walking the disk itself.
        "groups": sorted({e["group"] for e in entries}),
        "templates": {
            tid: {
                "title": tpl.title,
                "group": tpl.group,
                "description": tpl.description,
                "brand": tpl.brand_pack,
                "builtin": tpl.builtin,
            }
            for tid, tpl in vault.templates(cfg).items()
        },
        "reports": entries,
    }
    cfg.out.mkdir(parents=True, exist_ok=True)
    path = cfg.out / "manifest.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  → {path.relative_to(cfg.root)} ({len(found)} reports)")
    return path
