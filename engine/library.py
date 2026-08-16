"""Staging designs into the vault.

Typst can only import files under `--root`, and `--root` has to be the vault
(that is where the reports and their sources live). A design that lived in the
engine, or that a report referenced by a relative path, would break the moment
the engine moved or the report folder did.

So every design is *staged*: for each template, its Typst files — its own, plus
whatever it inherits from the template it extends — are assembled into

    .build/design/<template-id>/
        tokens.typ       generated from that design's brand pack
        theme.typ        \\
        report.typ        }  the template's own files, or its ancestors'
        components.typ   /

and reports import `/.build/design/<template-id>/report.typ`. Staging is a file
copy, so every design is staged on every build: it costs nothing, and it means a
report can name any design in the vault and always resolve.
"""

from __future__ import annotations

from pathlib import Path

from . import brand, vault
from .config import Config


def design_dir(cfg: Config, template_id: str) -> Path:
    return cfg.build / "design" / template_id


def stage_template(cfg: Config, tpl: vault.Template) -> list[Path]:
    """Assemble one design: inherited files first, the template's own on top."""
    target = design_dir(cfg, tpl.id)
    target.mkdir(parents=True, exist_ok=True)

    files: dict[str, Path] = {}
    for ancestor in vault.lineage(cfg, tpl):
        files.update(ancestor.design_files())

    written = []
    for name, src in files.items():
        if brand.write_if_changed(target / name, src.read_text(encoding="utf-8")):
            written.append(target / name)

    tokens = brand.tokens_typ(brand.load(cfg, tpl.brand_pack))
    if brand.write_if_changed(target / "tokens.typ", tokens):
        written.append(target / "tokens.typ")

    # A design that inherits nothing and defines nothing would compile to an
    # empty module and fail with a confusing "unknown variable" at the report.
    if "report.typ" not in files:
        raise vault.VaultError(
            f"template {tpl.id!r} has no report.typ and inherits none — "
            "add one, or set extends in its template.toml"
        )
    return written


def stage(cfg: Config, verbose: bool = False) -> list[Path]:
    written: list[Path] = []
    for tpl in vault.templates(cfg).values():
        written += stage_template(cfg, tpl)
    if verbose:
        for path in written:
            print(f"  → {path.relative_to(cfg.root)}")
    return written
