"""What reports exist, and what each one says about itself.

A report is any folder under the reports directory that contains a `main.typ`.
Folders nest as deep as you like, and the nesting *is* the filing system:

    reports/acme/2026-08-12-audit/          → id "acme/2026-08-12-audit"
    reports/internal/q3/2026-08-01-review/  → id "internal/q3/2026-08-01-review"
    reports/2026-08-16-example/             → id "2026-08-16-example"

The id is the path, the group is everything above the last segment, and `out/`
mirrors the same shape — so a vault of eighty reports stays navigable in a file
manager, with no index to keep in sync.

Metadata is read out of the `#show: report.with(…)` call by regex rather than by
compiling, because the manifest has to work for a report that does not currently
compile.
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

DEFAULT_TEMPLATE = "base"


@dataclass
class Report:
    id: str
    folder: Path
    cfg: Config

    # Kept so older call sites and messages can keep saying "slug" for the
    # last path segment, which is what a person actually types.
    @property
    def slug(self) -> str:
        return self.id.rsplit("/", 1)[-1]

    @property
    def group(self) -> str:
        return self.id.rsplit("/", 1)[0] if "/" in self.id else ""

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
        return self.cfg.out / f"{self.id}.pdf"

    @property
    def pages_dir(self) -> Path:
        return self.cfg.out / "pages" / self.id

    def template_id(self) -> str:
        """Which design this report imports. Read from the import line, so the
        report itself is the record of what it was built with."""
        match = re.search(
            r'#import\s+"/\.build/design/([^"]+?)/report\.typ"',
            self.main.read_text(encoding="utf-8"),
        )
        return match.group(1) if match else DEFAULT_TEMPLATE

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
        return any(path.stat().st_mtime > built for path in self.inputs())

    def inputs(self) -> list[Path]:
        design = self.cfg.build / "design" / self.template_id()
        paths = [self.main]
        if self.sources.exists():
            paths.append(self.sources)
        paths += sorted(self.folder.rglob("*.svg"))
        paths += sorted(self.folder.rglob("*.png"))
        paths += sorted(design.glob("*.typ"))
        paths += sorted(self.cfg.brand.rglob("*")) if self.cfg.brand.exists() else []
        return [p for p in paths if p.is_file()]


def _hidden(rel: Path) -> bool:
    return any(part.startswith((".", "_")) for part in rel.parts)


def reports(cfg: Config, target: str | None = None) -> list[Report]:
    """Every report, or the one matching `target`.

    `target` may be a full id (`acme/2026-08-12-audit`), a bare slug when it is
    unambiguous, or a folder to build everything underneath (`acme`).
    """
    found: list[Report] = []
    if cfg.reports.is_dir():
        for main in sorted(cfg.reports.rglob("main.typ")):
            rel = main.parent.relative_to(cfg.reports)
            if _hidden(rel):
                continue
            found.append(Report(id=rel.as_posix(), folder=main.parent, cfg=cfg))

    if target is None:
        return found

    target = target.strip("/")
    exact = [r for r in found if r.id == target]
    if exact:
        return exact
    under = [r for r in found if r.id.startswith(target + "/")]
    if under:
        return under
    by_slug = [r for r in found if r.slug == target]
    if len(by_slug) == 1:
        return by_slug
    if len(by_slug) > 1:
        raise SystemExit(
            f"{target!r} is ambiguous — it matches: " + ", ".join(r.id for r in by_slug)
        )
    known = ", ".join(r.id for r in found) or "none"
    raise SystemExit(f"no such report: {target}\n  known reports: {known}")


def groups(cfg: Config) -> dict[str, list[Report]]:
    out: dict[str, list[Report]] = {}
    for report in reports(cfg):
        out.setdefault(report.group, []).append(report)
    return dict(sorted(out.items()))
