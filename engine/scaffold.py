"""Creating things: a vault, a report inside it, and a design to edit.

Everything is a template expansion rather than a copy, because the paths a
report needs — its design, its own sources.yml — are project-absolute and only
known once the vault layout and the chosen template are known.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

from . import vault
from .config import CONFIG_NAME, ENGINE_DIR, Config

VAULT_TOML = """# report-maker vault.
#
#   report-maker new "Title"            scaffold a report
#   report-maker new "Title" --into acme --template brief
#   report-maker templates              the designs available here
#   report-maker all                    designs, diagrams, PDFs, pages, manifest
#   report-maker check                  enforce the citation rule
#
# Folders are the filing system: reports/ nests as deep as you like, and
# templates/ nests to group designs. Generated files land in .build/ and out/.

[vault]
reports   = "{reports}"
templates = "{templates}"
brand     = "{brand}"
out       = "{out}"

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
        "The default brand pack. Any key left out falls back to the engine default",
        "in engine/brand/brand.json. Everything derived from this file — the Typst",
        "tokens, the mermaid theme, the mermaid stylesheet, the mermaid classDefs —",
        "is generated, so a colour lives in exactly one place.",
        "",
        "For a second pack, make brand/<name>/brand.json and point a template at it",
        "with `brand = \"<name>\"` in its template.toml.",
        "",
        "Drop a logo in brand/assets/ and set org.logo to a project-absolute path,",
        "e.g. \"/brand/assets/logo.svg\". With no logo, covers and running heads fall",
        "back to the organisation name in display type.",
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

TEMPLATE_TOML = '''title = "{title}"
description = "{description}"
kind = "{kind}"

# Inherits every Typst file it does not define itself from this design.
extends = "{extends}"

# Which brand pack under brand/ this design uses.
brand = "{brand}"
'''


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
    """Turn a directory into a vault. Safe to re-run: nothing is overwritten."""
    root.mkdir(parents=True, exist_ok=True)
    _write(
        root / CONFIG_NAME,
        VAULT_TOML.format(
            reports="reports", templates="templates", brand="brand", out="out"
        ),
        force,
    )
    _write(root / "brand" / "brand.json", json.dumps(BRAND_STUB, indent=2) + "\n", force)
    (root / "brand" / "assets").mkdir(parents=True, exist_ok=True)
    for folder in ("reports", "templates"):
        (root / folder).mkdir(parents=True, exist_ok=True)
        _write(root / folder / ".gitkeep", "", force)

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


def _starter(cfg: Config, tpl: vault.Template) -> Path:
    """The nearest starter up the inheritance chain — a design that only changes
    the look does not have to restate the skeleton."""
    for ancestor in reversed(vault.lineage(cfg, tpl)):
        if (ancestor.starter / "main.typ").is_file():
            return ancestor.starter
    raise vault.VaultError(f"template {tpl.id!r} has no starter/main.typ, and inherits none")


def new_report(
    cfg: Config,
    title: str,
    slug: str | None = None,
    into: str | None = None,
    template: str = "base",
    date: dt.date | None = None,
    kind: str | None = None,
    author: str | None = None,
    with_diagram: bool = False,
) -> Path:
    tpl = vault.template(cfg, template)
    date = date or dt.date.today()
    slug = slug or f"{date.isoformat()}-{slugify(title)}"
    group = (into or "").strip("/")
    folder = cfg.reports / group / slug if group else cfg.reports / slug
    if folder.exists():
        raise SystemExit(f"{folder} already exists")

    report_id = f"{group}/{slug}" if group else slug
    starter = _starter(cfg, tpl)
    values = {
        "slug": slug,
        "id": report_id,
        "group": group,
        "title": title,
        "template": tpl.id,
        "kind": kind or tpl.kind or "Report",
        "author": author or "Author Name",
        "date": f"datetime(year: {date.year}, month: {date.month}, day: {date.day})",
        "doc_id": f"RM-{date.year}-{len(list(cfg.reports.rglob('main.typ'))) + 1:03d}",
        "design": cfg.design_path(tpl.id),
        "sources": cfg.project_path(folder / "sources.yml"),
        "report_path": cfg.project_path(folder),
    }

    folder.mkdir(parents=True)
    for name in ("main.typ", "sources.yml"):
        source = starter / name
        if source.is_file():
            _write(folder / name, _expand(source.read_text(encoding="utf-8"), values))

    if with_diagram:
        examples = sorted((starter / "diagrams").glob("*.mmd"))
        if not examples:
            print("    (this template ships no example diagram — skipped)")
        else:
            (folder / "diagrams").mkdir()
            for example in examples:
                shutil.copy(example, folder / "diagrams" / example.name)
                print(f"  → {folder / 'diagrams' / example.name}")
            print("    (run `report-maker diagrams` before building — an unrendered .mmd fails `check`)")

    return folder


def new_template(
    cfg: Config,
    tid: str,
    source: str = "base",
    title: str | None = None,
    description: str | None = None,
    copy_design: bool = True,
) -> Path:
    """Create an editable design in the vault, seeded from an existing one.

    With `copy_design`, the source's Typst files are copied in so every rule is
    visible and editable. Without it, the new template is only a template.toml
    that extends the source — which is the right shape when the change is a
    brand pack or a starter, not the layout.
    """
    tid = tid.strip("/")
    if tid in vault.templates(cfg) and (cfg.templates / tid).exists():
        raise SystemExit(f"template {tid!r} already exists at {cfg.templates / tid}")
    parent = vault.template(cfg, source)

    folder = cfg.templates / tid
    folder.mkdir(parents=True)
    name = tid.rsplit("/", 1)[-1]

    _write(
        folder / "template.toml",
        TEMPLATE_TOML.format(
            title=title or name.replace("-", " ").capitalize(),
            description=description or f"Based on {parent.id}.",
            kind=parent.kind or "Report",
            extends=parent.id,
            brand=parent.brand_pack,
        ),
    )

    if copy_design:
        for source_file in parent.design_files().values():
            _write(folder / source_file.name, source_file.read_text(encoding="utf-8"))

    starter = _starter(cfg, parent)
    target_starter = folder / "starter"
    target_starter.mkdir()
    for item in sorted(starter.iterdir()):
        if item.is_dir():
            shutil.copytree(item, target_starter / item.name)
        else:
            shutil.copy(item, target_starter / item.name)
    print(f"  → {target_starter}/ (starter copied from {parent.id})")

    return folder
