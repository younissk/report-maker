"""Staging the Typst library into the workspace.

Typst can only import files under `--root`, and `--root` has to be the workspace
(that is where the reports and their sources live). An engine installed anywhere
else — a sibling checkout, a symlink on PATH, a vendored copy — would therefore
be unimportable.

So the library is staged: `engine/typst/*.typ` is copied into `.build/typst/`
before every build, and reports import it from `/.build/typst/report.typ`. The
engine can then live wherever it likes, and the workspace holds no editable copy
of it — `.build/` is generated, gitignored, and rebuilt on demand.
"""

from __future__ import annotations

from pathlib import Path

from . import brand
from .config import Config

SOURCE = Path(__file__).resolve().parent / "typst"

# Project-absolute, because that is how a report has to reference it: a relative
# import would break the moment the report folder moved.
LIB_PATH = "/.build/typst"


def sync(cfg: Config) -> list[Path]:
    target = cfg.build / "typst"
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for src in sorted(SOURCE.glob("*.typ")):
        dst = target / src.name
        text = src.read_text(encoding="utf-8")
        if not dst.exists() or dst.read_text(encoding="utf-8") != text:
            dst.write_text(text, encoding="utf-8")
            written.append(dst)
    return written


def stage(cfg: Config, verbose: bool = False) -> None:
    """Everything Typst needs that is generated rather than authored."""
    for path in brand.sync(cfg) + sync(cfg):
        if verbose:
            print(f"  → {path.relative_to(cfg.root)}")
