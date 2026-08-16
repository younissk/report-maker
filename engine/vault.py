"""The vault: designs and brand packs, discovered from folders.

There is no database and no registry file. A template is a folder; its id is its
path under `templates/`, so nesting *is* grouping:

    templates/audits/company/          → id "audits/company",  group "audits"
    templates/proposals/short-form/    → id "proposals/short-form", group "proposals"
    templates/memo/                    → id "memo",            group ""

The engine ships its own templates in `engine/templates/`. A vault template with
the same id shadows the built-in one entirely, which is how a design gets edited:
copy it into the vault (`report-maker template new`) and change it there.

A template is a *design* — the Typst that decides what a report looks like — plus
a *starter*, the skeleton `report-maker new` copies. Both parts are optional:

    templates/<id>/
      template.toml     title, description, extends, brand   (optional)
      report.typ        the design               (optional — inherited if absent)
      components.typ    extra components         (optional — inherited if absent)
      theme.typ         token helpers            (optional — inherited if absent)
      starter/          main.typ, sources.yml, diagrams/*.mmd

Brand packs are folders too: `brand/brand.json` is the default pack, and any
`brand/<name>/brand.json` is a named one a template can ask for.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config import ENGINE_DIR, Config

BUILTIN_TEMPLATES = ENGINE_DIR / "templates"

BASE = "base"

DESIGN_FILES = ("theme.typ", "report.typ", "components.typ")


class VaultError(RuntimeError):
    pass


@dataclass
class Template:
    id: str
    folder: Path
    builtin: bool
    data: dict = field(default_factory=dict)

    @property
    def group(self) -> str:
        return self.id.rsplit("/", 1)[0] if "/" in self.id else ""

    @property
    def name(self) -> str:
        return self.id.rsplit("/", 1)[-1]

    @property
    def title(self) -> str:
        return self.data.get("title", self.name.replace("-", " ").capitalize())

    @property
    def description(self) -> str:
        return self.data.get("description", "")

    @property
    def extends(self) -> str | None:
        """Which design to inherit missing files from. `extends = ""` inherits
        nothing, which is what the base template itself does."""
        value = self.data.get("extends", None if self.id == BASE else BASE)
        return value or None

    @property
    def brand_pack(self) -> str:
        return self.data.get("brand", "default")

    @property
    def kind(self) -> str | None:
        return self.data.get("kind")

    @property
    def starter(self) -> Path:
        return self.folder / "starter"

    def design_files(self) -> dict[str, Path]:
        return {p.name: p for p in sorted(self.folder.glob("*.typ"))}


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _scan(root: Path, builtin: bool) -> dict[str, Template]:
    """Every folder under `root` that looks like a template. A folder is one when
    it holds a template.toml, a *.typ design, or a starter/."""
    found: dict[str, Template] = {}
    if not root.is_dir():
        return found
    for folder in sorted(p for p in root.rglob("*") if p.is_dir()):
        if any(part.startswith((".", "_")) for part in folder.relative_to(root).parts):
            continue
        if folder.name == "starter" or folder.parent.name == "starter":
            continue
        is_template = (
            (folder / "template.toml").is_file()
            or any(folder.glob("*.typ"))
            or (folder / "starter").is_dir()
        )
        if not is_template:
            continue
        tid = folder.relative_to(root).as_posix()
        found[tid] = Template(
            id=tid,
            folder=folder,
            builtin=builtin,
            data=_read_toml(folder / "template.toml"),
        )
    return found


def templates(cfg: Config) -> dict[str, Template]:
    """Built-ins first, then the vault's own — same id means the vault wins."""
    found = _scan(BUILTIN_TEMPLATES, builtin=True)
    found.update(_scan(cfg.templates, builtin=False))
    return dict(sorted(found.items()))


def template(cfg: Config, tid: str) -> Template:
    found = templates(cfg)
    tid = tid.strip("/")
    if tid in found:
        return found[tid]
    # A bare name is enough when it is unambiguous: "company" finds
    # "audits/company" as long as no other group holds one.
    matches = [t for t in found.values() if t.name == tid]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise VaultError(
            f"{tid!r} is ambiguous: " + ", ".join(t.id for t in matches)
        )
    raise VaultError(f"no such template: {tid}\n  known: " + ", ".join(found) )


def lineage(cfg: Config, tpl: Template) -> list[Template]:
    """A template and its ancestors, oldest first. Later files win on merge."""
    chain: list[Template] = []
    seen: set[str] = set()
    current: Template | None = tpl
    while current is not None:
        if current.id in seen:
            raise VaultError(f"template inheritance loops at {current.id}")
        seen.add(current.id)
        chain.append(current)
        parent = current.extends
        current = template(cfg, parent) if parent else None
    return list(reversed(chain))


def groups(cfg: Config) -> dict[str, list[Template]]:
    out: dict[str, list[Template]] = {}
    for tpl in templates(cfg).values():
        out.setdefault(tpl.group, []).append(tpl)
    return dict(sorted(out.items()))


# ── brand packs ──────────────────────────────────────────────────────────────


def brand_packs(cfg: Config) -> dict[str, Path]:
    """Name → brand.json. `brand/brand.json` is "default"; a subfolder holding a
    brand.json is a named pack."""
    packs: dict[str, Path] = {}
    if (cfg.brand / "brand.json").is_file():
        packs["default"] = cfg.brand / "brand.json"
    if cfg.brand.is_dir():
        for folder in sorted(p for p in cfg.brand.iterdir() if p.is_dir()):
            if (folder / "brand.json").is_file():
                packs[folder.name] = folder / "brand.json"
    return packs


def brand_pack(cfg: Config, name: str) -> Path | None:
    """The pack's brand.json, or None to mean "engine defaults only"."""
    packs = brand_packs(cfg)
    if name in packs:
        return packs[name]
    if name == "default":
        return None
    raise VaultError(
        f"no such brand pack: {name}\n  known: " + (", ".join(packs) or "none")
    )
