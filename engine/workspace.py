"""What reports exist, and what each one says about itself.

A report is a folder under the workspace's reports directory containing a
`main.typ`. Folders whose name starts with `_` are ignored, which is how the
starter template stays out of every build.

Metadata is read out of the `#show: report.with(…)` call by regex rather than by
compiling the document, because the manifest has to work for a report that does
not currently compile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config

FIELDS = (
    "title",
    "subtitle",
    "kind",
    "author",
    "role",
    "subject",
    "doc-id",
    "version",
    "classification",
)

MONTHS = (
    "January February March April May June July "
    "August September October November December"
).split()


@dataclass
class Report:
    slug: str
    folder: Path
    cfg: Config

    @property
    def main(self) -> Path:
        return self.folder / "main.typ"

    @property
    def sources(self) -> Path:
        return self.folder / "sources.yml"

    @property
    def diagrams(self) -> Path:
        return self.folder / "diagrams"

    @property
    def pdf(self) -> Path:
        return self.cfg.out / f"{self.slug}.pdf"

    @property
    def pages_dir(self) -> Path:
        return self.cfg.out / "pages" / self.slug

    def meta(self) -> dict[str, str]:
        src = self.main.read_text(encoding="utf-8")
        meta: dict[str, str] = {}
        for field in FIELDS:
            match = re.search(
                rf'^\s*{re.escape(field)}:\s*"((?:[^"\\]|\\.)*)"', src, re.M
            )
            if match:
                meta[field] = match.group(1).replace('\\"', '"')
        match = re.search(
            r"date:\s*datetime\(\s*year:\s*(\d+),\s*month:\s*(\d+),\s*day:\s*(\d+)", src
        )
        if match:
            year, month, day = (int(x) for x in match.groups())
            meta["date"] = f"{year:04d}-{month:02d}-{day:02d}"
            meta["date-display"] = f"{day} {MONTHS[month - 1]} {year}"
        return meta

    def is_stale(self) -> bool:
        """True when the PDF is missing or older than anything it is built from."""
        if not self.pdf.exists():
            return True
        built = self.pdf.stat().st_mtime
        for path in self.inputs():
            if path.stat().st_mtime > built:
                return True
        return False

    def inputs(self) -> list[Path]:
        paths = [self.main]
        if self.sources.exists():
            paths.append(self.sources)
        paths += sorted(self.folder.rglob("*.svg"))
        paths += sorted(self.folder.rglob("*.png"))
        paths += sorted((self.cfg.engine / "typst").glob("*.typ"))
        paths += sorted((self.cfg.build / "brand").glob("*.typ"))
        paths += sorted(self.cfg.brand.rglob("*")) if self.cfg.brand.exists() else []
        return [p for p in paths if p.is_file()]


def reports(cfg: Config, slug: str | None = None) -> list[Report]:
    if not cfg.reports.is_dir():
        return []
    found = []
    for folder in sorted(cfg.reports.iterdir()):
        if not folder.is_dir() or folder.name.startswith("_") or folder.name.startswith("."):
            continue
        if not (folder / "main.typ").is_file():
            continue
        found.append(Report(slug=folder.name, folder=folder, cfg=cfg))
    if slug is None:
        return found
    slug = slug.rstrip("/")
    matches = [r for r in found if r.slug == slug]
    if not matches:
        known = ", ".join(r.slug for r in found) or "none"
        raise SystemExit(f"no such report: {slug}\n  known reports: {known}")
    return matches
