"""Creating things: a workspace, and a report inside it.

Both are template expansions rather than copies, because the paths a report
needs — the engine library, its own sources.yml — are project-absolute and only
known once the workspace layout is known.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

from .config import CONFIG_NAME, ENGINE_DIR, Config
from .library import LIB_PATH

TEMPLATES = ENGINE_DIR / "templates"

WORKSPACE_TOML = """# report-maker workspace.
#
#   report-maker new "Title"   scaffold a report
#   report-maker all           theme, diagrams, PDFs, page images, manifest
#   report-maker check         enforce the citation rule
#
# Generated theme files land in .build/ and are never edited by hand.

[workspace]
reports = "{reports}"
brand   = "{brand}"
out     = "{out}"

[typst]
bin   = "typst"
paper = "a4"

[diagrams]
mermaid = "^11.4.2"

[pages]
ppi = 110
"""

GITIGNORE = """out/
.build/
"""

BRAND_STUB = {
    "$comment": [
        "House style. Any key left out falls back to the engine default in",
        "engine/brand/brand.json. Everything derived from this file — the Typst",
        "tokens, the mermaid theme, the mermaid stylesheet — is generated into",
        ".build/brand/, so a colour lives in exactly one place.",
        "",
        "Drop a logo in brand/assets/ and point org.logo at it as a project-absolute",
        "path, e.g. \"/brand/assets/logo.svg\". With no logo, covers and running heads",
        "fall back to the organisation name in display type.",
    ],
    "org": {
        "name": "Your Organisation",
        "url": "example.com",
        "logo": None,
    },
    "colors": {
        "accent": "#2E5A88",
        "accent-deep": "#1C3D60",
        "accent-bright": "#2F6FB2",
        "accent-tint": "#EAF0F7",
    },
}


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "report"


def _write(path: Path, text: str, force: bool = False) -> bool:
    if path.exists() and not force:
        print(f"  · {path.name} (exists, kept)")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"  → {path}")
    return True


def init(root: Path, force: bool = False) -> None:
    """Turn a directory into a workspace. Safe to re-run: nothing is overwritten."""
    root.mkdir(parents=True, exist_ok=True)
    _write(
        root / CONFIG_NAME,
        WORKSPACE_TOML.format(reports="reports", brand="brand", out="out"),
        force,
    )
    _write(root / "brand" / "brand.json", json.dumps(BRAND_STUB, indent=2) + "\n", force)
    (root / "brand" / "assets").mkdir(parents=True, exist_ok=True)
    (root / "reports").mkdir(parents=True, exist_ok=True)
    _write(root / "reports" / ".gitkeep", "", force)

    ignore = root / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    missing = [line for line in GITIGNORE.splitlines() if line not in existing]
    if missing:
        ignore.write_text(
            (existing.rstrip("\n") + "\n" if existing else "") + "\n".join(missing) + "\n",
            encoding="utf-8",
        )
        print(f"  → {ignore} ({', '.join(missing)})")


def _expand(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def new_report(
    cfg: Config,
    title: str,
    slug: str | None = None,
    date: dt.date | None = None,
    kind: str | None = None,
    author: str | None = None,
    with_diagram: bool = False,
) -> Path:
    date = date or dt.date.today()
    slug = slug or f"{date.isoformat()}-{slugify(title)}"
    folder = cfg.reports / slug
    if folder.exists():
        raise SystemExit(f"{folder} already exists")

    brand_defaults = json.loads((ENGINE_DIR / "brand" / "brand.json").read_text())["defaults"]
    values = {
        "slug": slug,
        "title": title,
        "kind": kind or brand_defaults["kind"],
        "author": author or "Author Name",
        "date": f"datetime(year: {date.year}, month: {date.month}, day: {date.day})",
        "doc_id": f"RM-{date.year}-{len(list(cfg.reports.glob('[0-9]*'))) + 1:03d}",
        "lib": LIB_PATH,
        "sources": cfg.project_path(folder / "sources.yml"),
        "report_path": cfg.project_path(folder),
    }

    folder.mkdir(parents=True)
    for name in ("main.typ", "sources.yml"):
        _write(folder / name, _expand((TEMPLATES / "report" / name).read_text(encoding="utf-8"), values))

    if with_diagram:
        (folder / "diagrams").mkdir()
        shutil.copy(
            TEMPLATES / "report" / "diagrams" / "example-flow.mmd",
            folder / "diagrams" / "example-flow.mmd",
        )
        print(f"  → {folder / 'diagrams' / 'example-flow.mmd'}")
        print("    (run `report-maker diagrams` before building — an unrendered .mmd fails `check`)")

    return folder
