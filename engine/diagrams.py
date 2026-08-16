"""mermaid source in, branded SVG out.

Every `.mmd` under a report's `diagrams/` folder becomes a sibling `.svg`, styled
with the generated theme in `.build/brand/mermaid/`. The SVG is what Typst
embeds; the `.mmd` is what a human edits and what shows up in a diff, so both
belong in the repository.

mermaid-cli is installed on demand into `.build/mermaid/` and driven headlessly
against a system Chrome — it never downloads its own browser, and nothing here
opens a window.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import brand, vault
from .config import Config
from .workspace import Report, reports

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


class DiagramError(RuntimeError):
    pass


def find_chrome() -> str | None:
    env = os.environ.get("PUPPETEER_EXECUTABLE_PATH") or os.environ.get("CHROME_PATH")
    if env and Path(env).exists():
        return env
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    return shutil.which("google-chrome") or shutil.which("chromium")


def cli_dir(cfg: Config) -> Path:
    return cfg.build / "mermaid"


def mmdc(cfg: Config) -> Path:
    return cli_dir(cfg) / "node_modules" / ".bin" / "mmdc"


def ensure_cli(cfg: Config) -> Path:
    binary = mmdc(cfg)
    if binary.exists():
        return binary
    if not shutil.which("npm"):
        raise DiagramError(
            "mermaid-cli is not installed and npm was not found.\n"
            "Install Node.js, then re-run. Reports without diagrams build fine "
            "without it."
        )
    target = cli_dir(cfg)
    target.mkdir(parents=True, exist_ok=True)
    (target / "package.json").write_text(
        json.dumps(
            {
                "name": "report-maker-mermaid",
                "private": True,
                "description": "Local mermaid-cli, installed on demand. Safe to delete.",
                "devDependencies": {"@mermaid-js/mermaid-cli": cfg.mermaid_version},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Installing mermaid-cli into {target.relative_to(cfg.root)} (first run only)…")
    env = dict(
        os.environ,
        PUPPETEER_SKIP_DOWNLOAD="1",
        PUPPETEER_SKIP_CHROMIUM_DOWNLOAD="1",
    )
    subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund"],
        cwd=str(target),
        env=env,
        check=True,
    )
    return binary


def puppeteer_config(cfg: Config) -> Path | None:
    """Point mermaid-cli at the system browser, since its download is skipped."""
    chrome = find_chrome()
    if chrome is None:
        return None
    path = cli_dir(cfg) / "puppeteer.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"executablePath": chrome, "args": ["--no-sandbox", "--disable-gpu"]},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


# ── emphasis classes ─────────────────────────────────────────────────────────
#
# mermaid puts presentation into inline `style` attributes on each node, and
# Typst's SVG renderer honours those over any rule in the stylesheet — which is
# why a `.em-accent` CSS rule alone paints nothing in the PDF. The colours have
# to arrive as mermaid `classDef`s instead.
#
# Writing those classDefs by hand would mean a hex code in every .mmd file, and
# a colour that drifts the first time the brand changes. So they are generated
# from the same brand.json as everything else and injected at render time: a
# diagram references `em-accent`, and never a colour.

EMPHASIS_ROLES = ("accent", "muted", "good", "ghost")

CLASSDEF_DIAGRAMS = ("flowchart", "graph", "stateDiagram", "classDiagram")


def emphasis_classdefs(brand_data: dict) -> dict[str, str]:
    c = brand_data["colors"]
    return {
        "em-accent": f"fill:{c['accent']},stroke:{c['accent-deep']},color:{c['surface']}",
        "em-muted": f"fill:{c['surface-alt']},stroke:{c['rule']},color:{c['ink-soft']}",
        "em-good": f"fill:{c['positive-tint']},stroke:{c['positive']},color:{c['positive-deep']}",
        "em-ghost": (
            f"fill:{c['surface']},stroke:{c['rule']},color:{c['ink-muted']},"
            "stroke-dasharray:3 3"
        ),
    }


def _supports_classdef(text: str) -> bool:
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("%%"):
            continue
        return line.split()[0].split("-")[0] in CLASSDEF_DIAGRAMS
    return False


def prepare(cfg: Config, src: Path, brand_data: dict) -> Path:
    """Return the file to hand mermaid: the source, plus any classDefs it needs."""
    text = src.read_text(encoding="utf-8")
    if not _supports_classdef(text):
        return src

    wanted = [
        name
        for name, style in emphasis_classdefs(brand_data).items()
        if re.search(rf"(?::::|\bclass\b[^\n]*?){re.escape(name)}\b", text)
        and not re.search(rf"^\s*classDef\s+{re.escape(name)}\b", text, re.M)
    ]
    if not wanted:
        return src

    defs = emphasis_classdefs(brand_data)
    injected = (
        text.rstrip("\n")
        + "\n\n%% classDefs generated by report-maker from brand/brand.json\n"
        + "\n".join(f"  classDef {name} {defs[name]}" for name in wanted)
        + "\n"
    )
    # One flat staging directory, so the name has to carry the report path.
    rel = src.relative_to(cfg.root).as_posix().replace("/", "--")
    staged = cfg.build / "mermaid" / "src" / rel
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(injected, encoding="utf-8")
    return staged


def sources(cfg: Config, slug: str | None) -> list[Path]:
    return [
        path
        for report in reports(cfg, slug)
        for path in sorted(report.diagrams.glob("*.mmd"))
    ]


def _is_fresh(src: Path, theme: Path) -> bool:
    out = src.with_suffix(".svg")
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        return False
    newest_theme = max(
        (p.stat().st_mtime for p in theme.glob("*") if p.is_file()), default=0
    )
    return out.stat().st_mtime >= newest_theme


def render(
    cfg: Config,
    src: Path,
    binary: Path,
    puppeteer: Path | None,
    force: bool,
    brand_data: dict,
    theme: Path,
) -> str:
    out = src.with_suffix(".svg")
    if not force and _is_fresh(src, theme):
        return "up to date"

    staged = prepare(cfg, src, brand_data)
    cmd = [
        str(binary),
        "--input", str(staged),
        "--output", str(out),
        "--configFile", str(theme / "config.json"),
        "--cssFile", str(theme / "style.css"),
        "--backgroundColor", "transparent",
        "--quiet",
    ]
    if puppeteer is not None:
        cmd += ["--puppeteerConfigFile", str(puppeteer)]

    result = subprocess.run(cmd, cwd=str(cfg.root), capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise DiagramError(f"mermaid failed on {src.relative_to(cfg.root)}")

    # Labels rendered inside <foreignObject> are invisible to Typst's SVG
    # renderer. Catch it here rather than in a silently wordless diagram.
    if "<foreignObject" in out.read_text(encoding="utf-8"):
        raise DiagramError(
            f"{out.relative_to(cfg.root)} contains <foreignObject>, which Typst "
            "cannot render. htmlLabels must be false in the generated mermaid config."
        )
    return "rendered"


def build(cfg: Config, target: str | None = None, force: bool = False) -> list[Path]:
    """Render every diagram, each with the theme of its own report's design.

    Two reports built from two designs can sit on two different brand packs, so
    the theme is resolved per report rather than once for the vault.
    """
    work: list[tuple[Report, list[Path]]] = [
        (report, sorted(report.diagrams.glob("*.mmd"))) for report in reports(cfg, target)
    ]
    work = [(report, files) for report, files in work if files]
    if not work:
        where = f"{target}/diagrams/" if target else "any report's diagrams/ folder"
        print(f"  no .mmd files in {where} — nothing to render")
        return []

    binary = ensure_cli(cfg)
    puppeteer = puppeteer_config(cfg)
    packs: dict[str, tuple[dict, Path]] = {}
    rendered = []

    for report, files in work:
        pack = vault.template(cfg, report.template_id()).brand_pack
        if pack not in packs:
            packs[pack] = (brand.load(cfg, pack), brand.sync_mermaid(cfg, pack))
        brand_data, theme = packs[pack]
        for src in files:
            status = render(cfg, src, binary, puppeteer, force, brand_data, theme)
            out = src.with_suffix(".svg")
            print(f"  → {out.relative_to(cfg.root)} ({status})")
            if status == "rendered":
                rendered.append(out)
    return rendered


def missing_svgs(report: Report) -> list[Path]:
    return [
        src
        for src in sorted(report.diagrams.glob("*.mmd"))
        if not src.with_suffix(".svg").exists()
    ]
