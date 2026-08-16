"""Workspace discovery and configuration.

A *workspace* is any directory containing `report-maker.toml`. Everything the
engine touches is resolved from that file, so the engine itself holds no paths
and can be vendored, symlinked, or installed anywhere.

    [workspace]
    reports   = "reports"    # report folders, nested as deep as you like
    templates = "templates"  # designs, grouped by folder
    brand     = "brand"      # brand packs — brand.json + assets
    out       = "out"        # PDFs, page images, manifest

Generated theme files always land in `.build/`, which is not configurable: the
Typst library imports them by a literal path, and a Typst import cannot be
computed at compile time.

    [typst]
    bin   = "typst"
    paper = "a4"

    [diagrams]
    mermaid = "^11.4.2"

    [pages]
    ppi = 110
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "report-maker.toml"

ENGINE_DIR = Path(__file__).resolve().parent

DEFAULTS: dict = {
    "workspace": {
        "reports": "reports",
        "templates": "templates",
        "brand": "brand",
        "out": "out",
    },
    "typst": {"bin": "typst", "paper": "a4"},
    "diagrams": {"mermaid": "^11.4.2"},
    "pages": {"ppi": 110},
}


class ConfigError(RuntimeError):
    pass


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Config:
    """Resolved absolute paths for one workspace."""

    root: Path
    data: dict = field(default_factory=dict)

    # ── directories

    @property
    def reports(self) -> Path:
        return self.root / self.data["workspace"]["reports"]

    @property
    def templates(self) -> Path:
        return self.root / self.data["workspace"]["templates"]

    @property
    def brand(self) -> Path:
        return self.root / self.data["workspace"]["brand"]

    @property
    def out(self) -> Path:
        return self.root / self.data["workspace"]["out"]

    @property
    def build(self) -> Path:
        # Fixed, not configurable — engine/typst/theme.typ imports the generated
        # tokens from "/.build/brand/tokens.typ", and Typst import paths are literal.
        return self.root / ".build"

    @property
    def engine(self) -> Path:
        return ENGINE_DIR

    # ── tools

    @property
    def typst(self) -> str:
        return os.environ.get("TYPST_BIN") or self.data["typst"]["bin"]

    @property
    def paper(self) -> str:
        return self.data["typst"]["paper"]

    @property
    def mermaid_version(self) -> str:
        return self.data["diagrams"]["mermaid"]

    @property
    def ppi(self) -> int:
        return int(self.data["pages"]["ppi"])

    # ── project-absolute Typst paths
    #
    # Typst resolves a leading "/" against `--root`, which is always the
    # workspace root here. Report sources therefore reference the engine, the
    # generated theme, and their own sources.yml by these paths, and never by a
    # relative one — a relative path in a report would break the moment the
    # report folder moved.

    def project_path(self, path: Path) -> str:
        rel = path.resolve().relative_to(self.root.resolve())
        return "/" + rel.as_posix()

    def design_path(self, template_id: str) -> str:
        """Where a report imports its design from. Staged, so the engine itself
        can live outside the vault — see library.py."""
        return f"/.build/design/{template_id}"


def find_root(start: Path | None = None) -> Path:
    """Walk upwards from `start` (default: cwd) looking for report-maker.toml."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    raise ConfigError(
        f"no {CONFIG_NAME} found in {here} or any parent directory. "
        "Run `report-maker init` to create a workspace here."
    )


def load(start: Path | None = None) -> Config:
    root = find_root(start)
    with (root / CONFIG_NAME).open("rb") as handle:
        data = tomllib.load(handle)
    return Config(root=root, data=_merge(DEFAULTS, data))
