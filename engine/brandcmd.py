"""`report-maker brand` — reading, editing and seeing a brand pack.

`brand.py` turns a pack into artefacts. This module is the other half: the
commands a person or the app's brand studio drives, and the one thing neither of
them can do from a JSON file alone — look at the result.

Three ideas hold it together.

**A pack is a delta, not a fork.** `brand.load` fills the engine default in
underneath every pack, so a file that restates a default value decides nothing
while looking like it does. `set_key` therefore writes back only the keys that
actually differ, and `show_pack` tags every resolved key with the file it was
read from. That is what lets the studio grey out an inherited field and mean it:
"default" says nobody has made a decision here yet.

**A preview is a document, not a swatch sheet.** A colour behaves differently in
a 5mm cover band and behind nine-point body text, so the specimen exercises the
whole component set — see `templates/_preview/specimen.typ`. It is staged and
compiled under `.build/brand-preview/<pack>/` and never, under any argument,
inside `reports/`: a preview is not a report, and a vault that accumulated one
per experiment would be a vault whose citation checks are full of specimens.

**The pack being previewed decides the tokens.** A design staged by `library.py`
carries the tokens of the pack its template names, which is the wrong pack when
you are previewing a different one. So the preview stages its own throwaway copy
of the design with tokens generated from the pack under inspection.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import brand, vault
from .brand import BrandError
from .build import BuildError, _require_typst
from .config import ENGINE_DIR, Config

SPECIMEN_DIR = ENGINE_DIR / "templates" / "_preview"

# The design the specimen is set in. Resolved through `vault.template`, so a
# vault that ships its own `base` previews its own design rather than ours.
PREVIEW_TEMPLATE = "base"

PREVIEW_ROOT = "brand-preview"

# A pack is one folder under brand/, so its name is a folder name — no slashes,
# nothing that walks upwards.
PACK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class BrandPreviewError(BrandError):
    """Raised when a specimen cannot be rendered — usually a missing typst."""


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _default_brand() -> dict:
    """The engine's own pack: the baseline every other pack is a delta over."""
    return _read_json(brand.DEFAULT_BRAND)


def _display_path(cfg: Config, path: Path) -> str:
    """Vault-relative when the file is in the vault, absolute when it is the
    engine's own. The distinction is the point — one is editable, one is not."""
    try:
        return path.resolve().relative_to(cfg.root.resolve()).as_posix()
    except ValueError:
        return str(path)


# ── packs ────────────────────────────────────────────────────────────────────


def _pack_row(cfg: Config, name: str, path: Path, builtin: bool) -> dict:
    resolved = brand.load(cfg, name)
    return {
        "name": name,
        "path": _display_path(cfg, path),
        "builtin": builtin,
        "org": resolved["org"]["name"],
        "accent": resolved["colors"]["accent"],
    }


def list_packs(cfg: Config) -> list[dict]:
    """Every pack this vault can name, `default` first.

    A vault with no `brand/brand.json` still has a default pack — the engine's —
    and it is listed as built-in rather than left out, because it is what every
    report in that vault is currently rendered with.
    """
    packs = vault.brand_packs(cfg)
    rows = []
    if "default" not in packs:
        rows.append(_pack_row(cfg, "default", brand.DEFAULT_BRAND, builtin=True))
    for name, path in packs.items():
        rows.append(_pack_row(cfg, name, path, builtin=False))
    return rows


# ── the resolved pack, key by key ────────────────────────────────────────────


def _flatten(data: Mapping, prefix: str = "") -> dict[str, Any]:
    """Nested mappings to dotted keys. Lists are leaves — a font stack is one
    value, not three, and the studio edits it as one."""
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if key.startswith("$"):  # $comment and friends are notes to a human
            continue
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, prefix=f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


def _tagged(resolved: Mapping, own: Mapping, prefix: str = "") -> dict:
    """The resolved pack with every leaf replaced by `{value, origin}`."""
    out: dict = {}
    for key, value in resolved.items():
        if key.startswith("$"):
            continue
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping):
            out[key] = _tagged(value, own, prefix=f"{dotted}.")
        else:
            out[key] = {"value": value, "origin": "pack" if dotted in own else "default"}
    return out


def show_pack(cfg: Config, pack: str = "default") -> dict:
    """Every key of a resolved pack, each tagged with where its value came from.

    `origin` is `"pack"` when the key is written in the pack's own file and
    `"default"` when it falls through to the engine's. It is decided by presence,
    not by comparison: a pack that writes a value identical to the default has
    still decided it. `set_key` keeps that file a delta, so in practice the two
    readings agree.

    The same answer is given twice on purpose. `values` keeps the shape of a
    brand.json — a form builds its sections from it, and a leaf carries its own
    provenance so an inherited field can be drawn as inherited. `keys` is the
    flat, ordered version, which is what a person reads down a terminal and what
    `brand set` addresses a value by.
    """
    resolved = brand.load(cfg, pack)  # raises VaultError on an unknown pack
    path = vault.brand_pack(cfg, pack)
    own = _flatten(_read_json(path)) if path is not None else {}

    return {
        "pack": pack,
        "path": _display_path(cfg, path) if path is not None else None,
        "builtin": path is None,
        "values": _tagged(resolved, own),
        "keys": [
            {
                "key": key,
                "value": value,
                "origin": "pack" if key in own else "default",
            }
            for key, value in _flatten(resolved).items()
        ],
    }


# ── creating a pack ──────────────────────────────────────────────────────────


def _pack_file(cfg: Config, name: str) -> Path:
    """`default` is `brand/brand.json`; everything else is `brand/<name>/brand.json`."""
    return cfg.brand / "brand.json" if name == "default" else cfg.brand / name / "brand.json"


def _comment(name: str, source: str) -> list[str]:
    return [
        f"Brand pack {name!r}, created by `report-maker brand new` from {source!r}.",
        "",
        "Only the keys that differ from the engine default belong here — everything",
        "left out falls back to engine/brand/brand.json, and `report-maker brand show`",
        "reports which of the two each value came from.",
        "",
        f"Point a design at it with `brand = \"{name}\"` in its template.toml, and see it",
        f"with `report-maker brand preview --pack {name}`.",
    ]


def new_pack(cfg: Config, name: str, source: str = "default") -> Path:
    """Create `brand/<name>/brand.json`, seeded from an existing pack.

    What is copied is the source's *own* file, not its resolved values: a
    duplicate of a delta is a delta. Seeding from the engine's built-in default
    therefore produces an empty pack, which is correct — it decides nothing yet,
    and every field in the studio will show as inherited until one is set.
    """
    name = name.strip().strip("/")
    if not PACK_NAME.match(name):
        raise BrandError(
            f"{name!r} is not a usable pack name — a pack is one folder under "
            "brand/, so use letters, digits, dot, dash or underscore."
        )
    target = _pack_file(cfg, name)
    if target.exists():
        raise BrandError(
            f"brand pack {name!r} already exists at {_display_path(cfg, target)}"
        )

    origin = vault.brand_pack(cfg, source)  # raises VaultError on an unknown source
    data = _read_json(origin) if origin is not None else {}
    data["$comment"] = _comment(name, source)

    _write_pack(cfg, target, data)
    return target


# ── setting one key ──────────────────────────────────────────────────────────


def coerce(text: str, current: Any = None) -> Any:
    """Turn a command-line string into the JSON value the pack should hold.

    The current value is the type hint, and it is doing real work: `1.0` typed
    for `defaults.version` must stay the string `"1.0"`, because a bare number
    reaches Typst unquoted and the template then fails on a version that is not
    content. So a key whose value is a string stays a string, whatever it looks
    like, and only a key that already holds a list, number or boolean is parsed
    as JSON. A key nobody has ever set is a string unless it is written as an
    explicit JSON list or object.
    """
    if not isinstance(text, str):
        return text  # the app sends typed JSON; nothing to guess
    if isinstance(current, str) or (current is None and not text.lstrip().startswith(("[", "{"))):
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        kind = type(current).__name__ if current is not None else "JSON"
        raise BrandError(f"{text!r} is not a valid {kind} value: {exc}") from exc


def set_key(cfg: Config, dotted_key: str, value: Any, pack: str = "default") -> None:
    """Write one value into a pack, keeping the file a delta over the default.

    Keys that match the engine default are dropped on the way out — including
    ones that were already in the file. That is deliberate: the file is the list
    of decisions this pack has made, and a restated default is not one. It hides
    what the pack actually changes, and it makes the studio show an inherited
    field as though someone had chosen it.
    """
    parts = [part for part in dotted_key.split(".") if part]
    baseline = _default_brand()
    sections = [key for key in baseline if not key.startswith("$")]
    if not parts or parts[0].startswith("$"):
        raise BrandError(
            f"{dotted_key!r} is not a brand key — they look like colors.accent, "
            "org.name, sizes.body."
        )
    if parts[0] not in sections:
        raise BrandError(
            f"unknown brand section {parts[0]!r} in {dotted_key!r}\n"
            "  sections: " + ", ".join(sections)
        )

    # Resolving first is the existence check: an unknown named pack raises here,
    # while `default` always resolves — a vault with no brand.json gets one.
    resolved = _flatten(brand.load(cfg, pack))
    target = _pack_file(cfg, pack)
    # Membership, not the value: `org.logo` is null by default, and a key that
    # exists and holds null is not the same thing as a key nobody has heard of.
    known = dotted_key in resolved
    value = coerce(value, resolved.get(dotted_key))

    data = _read_json(target) if target.exists() else {}
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value

    _write_pack(cfg, target, data)
    print(f"    {dotted_key} = {json.dumps(value)}")
    if not known and len(parts) > 1:
        print(f"    ({dotted_key} is a new key — nothing reads it unless a design does)")


# ── writing a pack file ──────────────────────────────────────────────────────


def _delta(data: Mapping, baseline: Mapping) -> dict:
    """`data` minus everything the engine default already says."""
    out: dict = {}
    for key, value in data.items():
        if key.startswith("$"):
            out[key] = value
            continue
        base = baseline.get(key)
        if isinstance(value, Mapping) and isinstance(base, Mapping):
            pruned = _delta(value, base)
            if pruned:
                out[key] = pruned
        elif key not in baseline or value != base:
            out[key] = value
    return out


def _ordered(data: Mapping, baseline: Mapping) -> dict:
    """The default's order, with anything it does not know about kept at the end.

    Brand files are read by people, and a diff that reorders half the file every
    time a colour changes is a diff nobody reads.
    """
    out: dict = {}
    for key in data:
        if key.startswith("$"):
            out[key] = data[key]
    for key in baseline:
        if key in data and not key.startswith("$"):
            value = data[key]
            base = baseline[key]
            out[key] = (
                _ordered(value, base)
                if isinstance(value, Mapping) and isinstance(base, Mapping)
                else value
            )
    for key, value in data.items():
        if key not in out:
            out[key] = value
    return out


def _write_pack(cfg: Config, target: Path, data: Mapping) -> Path:
    baseline = _default_brand()
    # ensure_ascii=False: a brand file is opened and edited by hand, so its
    # punctuation should reach it as itself rather than as an ASCII escape.
    text = (
        json.dumps(_ordered(_delta(data, baseline), baseline), indent=2, ensure_ascii=False)
        + "\n"
    )
    if brand.write_if_changed(target, text):
        print(f"  → {_display_path(cfg, target)}")
    else:
        print(f"  · {_display_path(cfg, target)} (unchanged)")
    return target


# ── preview ──────────────────────────────────────────────────────────────────


def preview_dir(cfg: Config, pack: str = "default") -> Path:
    return cfg.build / PREVIEW_ROOT / pack


def _expand(text: str, values: Mapping[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def stage_preview(cfg: Config, pack: str = "default") -> Path:
    """Assemble the specimen and its own copy of the design. Returns the .typ.

    The design is staged again here rather than reused from `.build/design/`
    because that copy carries the tokens of the pack its *template* names, which
    is precisely the pack we are not previewing.
    """
    root = preview_dir(cfg, pack)
    design = root / "design"

    files: dict[str, Path] = {}
    for ancestor in vault.lineage(cfg, vault.template(cfg, PREVIEW_TEMPLATE)):
        files.update(ancestor.design_files())
    for name, src in files.items():
        brand.write_if_changed(design / name, src.read_text(encoding="utf-8"))
    brand.write_if_changed(design / "tokens.typ", brand.tokens_typ(brand.load(cfg, pack)))

    today = dt.date.today()
    values = {
        "pack": pack,
        "design": cfg.project_path(design),
        "sources": cfg.project_path(root / "sources.yml"),
        "date": f"datetime(year: {today.year}, month: {today.month}, day: {today.day})",
        "today": today.isoformat(),
    }
    for name in ("specimen.typ", "sources.yml"):
        brand.write_if_changed(
            root / name,
            _expand((SPECIMEN_DIR / name).read_text(encoding="utf-8"), values),
        )
    return root / "specimen.typ"


def _typst(cfg: Config, binary: str, source: Path, out: Path, extra: Sequence[str]) -> None:
    result = subprocess.run(
        [binary, "compile", "--root", str(cfg.root), *extra, str(source), str(out)],
        cwd=str(cfg.root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BrandPreviewError(
            "typst failed rendering the brand specimen\n"
            + (result.stdout + result.stderr).rstrip()
        )


def preview(cfg: Config, pack: str = "default", ppi: int | None = None) -> list[Path]:
    """Build the specimen for one pack, and return its page PNGs in order.

    Everything written lands under `.build/brand-preview/<pack>/`: the staged
    design, the specimen, `preview.pdf`, the `page-N.png` images beside it, and
    a `pages.json` in the shape `pages.py` writes for a report. Nothing here
    touches `reports/` — a preview is not a report.
    """
    ppi = ppi or cfg.ppi
    try:
        binary = _require_typst(cfg)
    except BuildError as exc:
        raise BrandPreviewError(
            f"cannot render the brand specimen — {exc}"
        ) from exc

    specimen = stage_preview(cfg, pack)
    root = specimen.parent
    _typst(cfg, binary, specimen, root / "preview.pdf", ())

    # Clear the last render first: a pack whose type shrank the document from six
    # pages to five would otherwise keep page-6.png, and the studio would show a
    # page that is no longer in the document.
    for stale in root.glob("page-*.png"):
        stale.unlink()
    _typst(cfg, binary, specimen, root / "page-{0p}.png", ("--format", "png", "--ppi", str(ppi)))

    # Typst does not zero-pad, so page-10 sorts before page-2 lexically.
    files = sorted(root.glob("page-*.png"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    (root / "pages.json").write_text(
        json.dumps(
            {"pack": pack, "ppi": ppi, "count": len(files), "pages": [f.name for f in files]},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return files


# ── output ───────────────────────────────────────────────────────────────────


def packs_json(cfg: Config, packs: Sequence[Mapping]) -> dict:
    return {"vault": str(cfg.root), "packs": [dict(row) for row in packs]}


def show_json(cfg: Config, shown: Mapping) -> dict:
    return {"vault": str(cfg.root), **shown}


def preview_json(cfg: Config, pack: str, pages: Iterable[Path]) -> dict:
    root = preview_dir(cfg, pack)
    return {
        "vault": str(cfg.root),
        "pack": pack,
        "dir": str(root),
        "pdf": str(root / "preview.pdf"),
        "pages": [str(page) for page in pages],
    }


def report_packs(cfg: Config, packs: Sequence[Mapping]) -> int:
    if not packs:
        print("  no brand pack — this vault has none and the engine default is missing")
        return 0
    name_width = max(len(str(row["name"])) for row in packs)
    org_width = max(len(str(row["org"])) for row in packs)
    for row in packs:
        where = f"{row['path']} (built-in)" if row["builtin"] else str(row["path"])
        print(
            f"  {row['name']:<{name_width}}  {row['accent']}  "
            f"{row['org']:<{org_width}}  {where}"
        )
    return 0


def report_pack(cfg: Config, shown: Mapping) -> int:
    where = shown["path"] or "engine default (this vault has no file for it)"
    print(f"  {shown['pack']} — {where}")
    keys = shown["keys"]
    width = max((len(row["key"]) for row in keys), default=0)
    for row in keys:
        value = row["value"]
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value)
        elif value is None:
            rendered = "none"  # the brand file's null, not Python's repr of it
        else:
            rendered = str(value)
        origin = "" if row["origin"] == "pack" else "  (default)"
        print(f"    {row['key']:<{width}}  {rendered}{origin}")
    return 0


def report_preview(cfg: Config, pack: str, pages: Sequence[Path]) -> int:
    root = preview_dir(cfg, pack)
    print(f"  → {_display_path(cfg, root / 'preview.pdf')}")
    for page in pages:
        print(f"  → {_display_path(cfg, page)}")
    return 0
