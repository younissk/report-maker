"""Installing a design from a git repository, so a house style can be shared.

A design is already just a folder — `templates/<id>/` holding a `template.toml`,
some Typst, and a starter. Sharing one between vaults therefore needs no package
format and no registry: clone the repository, take the folder, write down where
it came from. This module is that, plus the part that makes it safe.

Provenance lives with the design, in `.installed.json` beside its
`template.toml`:

    templates/house/
      .installed.json    url, ref, resolved sha, subdir, installed_at, engine
      template.toml
      report.typ
      starter/…

That file is the whole registry. A design that has one was installed and can be
updated or uninstalled; a design without one was written here by hand, and
`update` and `uninstall` refuse to touch it — losing somebody's own design to a
command they aimed at a third-party one is not a recoverable mistake.

Everything here assumes the repository is hostile, because it is a stranger's:

  - the clone lands in a temp directory, never in the vault, so a failed or
    malicious install cannot leave anything behind;
  - `core.hooksPath=/dev/null` and `--no-recurse-submodules` mean cloning cannot
    execute code, which by default it otherwise can;
  - only the file types a design legitimately has are copied, and each one is
    written as a fresh 0644 file — no scripts, no symlinks, no dotfiles;
  - every path is checked to land inside `templates/` before anything is
    written, because a `..` in a subdir or an id is a write to an arbitrary
    place on the user's disk;
  - the design has to parse and resolve before the install counts as done, and
    when it does not, the vault is put back exactly as it was.

None of that makes an installed design harmless. Typst runs at build time, and
the warning printed on install says so.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import scaffold, vault
from .config import Config

RECORD = ".installed.json"

# What a design is allowed to be made of. Anything else is refused rather than
# skipped: a design carrying a shell script is not a design with a stray file in
# it, it is something else pretending to be a design.
ROOT_FILES = {"template.toml"}
ROOT_SUFFIXES = {".typ"}
STARTER_SUFFIXES = {".typ", ".yml", ".yaml", ".mmd"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}

# Repository furniture that is not part of the design and is not evidence of a
# hostile one. Skipped silently — the clean case is a repository whose root *is*
# the design, and every such repository has a README and a licence.
IGNORED_SUFFIXES = {".md", ".txt", ".rst"}
IGNORED_NAMES = {"LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS"}

WARNING = """  A design is Typst, and Typst runs when you build a report. It cannot reach the
  network or the shell, but it can read any file inside this vault and put what
  it reads into the PDF. Install designs only from sources you trust.
"""


class InstallError(RuntimeError):
    pass


@dataclass
class Installed:
    """One design and where it came from — the `.installed.json` record, plus the
    folder it describes and, after an update, the sha it moved from."""

    id: str
    url: str
    ref: str | None
    sha: str
    subdir: str | None
    installed_at: str
    folder: Path
    previous_sha: str | None = None

    @property
    def moved(self) -> bool:
        return self.previous_sha is not None and self.previous_sha != self.sha

    @property
    def short(self) -> str:
        return self.sha[:9]


# ── git ──────────────────────────────────────────────────────────────────────

# The transports a design may arrive over. The list is short on purpose: git's
# `<helper>::<address>` syntax hands the address to an external program, and
# `ext::sh -c "…"` is therefore a remote-code-execution URL wearing a URL's
# clothes. Modern git refuses `ext` by default, but that default is a config
# setting a user can have turned off, so it is not something to rely on.
ALLOWED_SCHEMES = {"https", "http", "ssh", "git", "file"}


def _safe_remote(url: str) -> str:
    """A repository URL that git will read as a URL and nothing else.

    Two things are rejected here, and both are code execution rather than a
    typo. A leading `-` makes git parse the argument as an *option* — see
    `_safe_ref` — and a `helper::address` URL makes it run the helper.
    """
    if url.startswith("-"):
        raise InstallError(f"bad repository URL: {url!r} — a URL may not begin with '-'")
    head = url.split("/", 1)[0]
    if "::" in head:
        helper = head.split("::", 1)[0]
        raise InstallError(
            f"bad repository URL: {url!r}\n"
            f"  '{helper}::' is a git transport helper — it runs a program rather than "
            "fetching a repository, and report-maker will not do that"
        )
    if "://" in url:
        scheme = url.split("://", 1)[0].lower()
        if scheme not in ALLOWED_SCHEMES:
            raise InstallError(
                f"bad repository URL: {url!r} — {scheme}:// is not a transport a design "
                "may arrive over (" + ", ".join(sorted(ALLOWED_SCHEMES)) + ")"
            )
    return url


def _safe_ref(ref: str | None) -> str | None:
    """A branch, tag or commit that cannot turn into a git option.

    This is the one that bites. `--branch` consumes its value safely, but the
    commit-sha fallback ends in `git fetch … origin <ref>`, and `git fetch`
    parses options *after* positionals — so a ref of `--upload-pack=<command>`
    runs that command and then reports the ref as not found. A ref name cannot
    legally start with `-` or hold whitespace or control characters anyway, so
    refusing those costs nothing real.
    """
    if ref is None:
        return None
    if ref.startswith("-"):
        raise InstallError(
            f"bad ref: {ref!r} — a branch, tag or commit may not begin with '-'"
        )
    if any(ch.isspace() or ord(ch) < 0x20 for ch in ref):
        raise InstallError(f"bad ref: {ref!r} — a ref name holds no whitespace")
    return ref


def _git(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run git with hooks disabled.

    `core.hooksPath` is the one thing that turns `git clone` from a download into
    an execution: a repository can ship hooks, and some git operations run them.
    Pointing it at /dev/null on every invocation costs nothing and closes that.

    `protocol.ext.allow=never` is the same move for the other execution path.
    Current git already defaults to refusing `ext::`, but the default lives in
    the user's config and a config is a thing that gets changed; saying it here
    means this module's guarantee does not depend on the machine it runs on.
    """
    exe = shutil.which("git")
    if exe is None:
        raise InstallError("git is not installed — it is what fetches a design")
    proc = subprocess.run(
        [exe, "-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never", *args],
        cwd=str(cwd) if cwd else None,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise InstallError(f"git {args[0]} failed:\n  " + "\n  ".join(detail[-4:]))
    return proc


def _clone(url: str, ref: str | None, dest: Path) -> str:
    """Shallow-clone `url` at `ref` into `dest`, and return the resolved sha.

    The guards are re-applied here rather than trusted from the caller: this is
    the only place either value reaches git, so it is the only place where being
    wrong about them costs anything.
    """
    url, ref = _safe_remote(url), _safe_ref(ref)
    base = ["clone", "--depth", "1", "--no-recurse-submodules", "--quiet"]
    args = [*base, *(["--branch", ref] if ref else []), "--", url, str(dest)]
    proc = _git(args, check=False)
    if proc.returncode != 0:
        if ref is None:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            raise InstallError(f"cannot clone {url}:\n  " + "\n  ".join(detail[-4:]))
        # --branch knows branches and tags only, so a commit sha lands here.
        shutil.rmtree(dest, ignore_errors=True)
        _git([*base, url, str(dest)])
        # `--end-of-options` so the ref cannot be read as a flag even if the
        # guard above is ever loosened: `git fetch` parses options after its
        # positionals, which is what made a ref of `--upload-pack=…` executable.
        fetched = _git(
            ["fetch", "--depth", "1", "--quiet", "--end-of-options", "origin", ref],
            cwd=dest,
            check=False,
        )
        if fetched.returncode != 0:
            raise InstallError(f"{url} has no branch, tag or commit {ref!r}")
        _git(["checkout", "--detach", "--quiet", "FETCH_HEAD"], cwd=dest)
    return _git(["rev-parse", "HEAD"], cwd=dest).stdout.strip()


# ── what the user asked for ──────────────────────────────────────────────────


def split_url(url: str, ref: str | None = None, subdir: str | None = None) -> tuple[str, str | None, str | None]:
    """`url#subdir` and `url@ref`, with explicit arguments winning.

    The `@` is only read when it falls after the last `/`, so the `@` in
    `git@github.com:org/designs.git` is left where it belongs.
    """
    raw = url.strip()
    if "#" in raw:
        raw, _, fragment = raw.partition("#")
        subdir = subdir or (fragment.strip() or None)
    if "@" in raw.rsplit("/", 1)[-1]:
        head, _, tail = raw.rpartition("@")
        if head and tail:
            raw, ref = head, ref or tail
    if not raw:
        raise InstallError("no repository URL")
    return _safe_remote(raw), _safe_ref(ref), subdir


def _default_id(url: str, subdir: str | None) -> str:
    """The design's id when the user did not name one: the subdir's last part,
    or the repository name without its `.git`."""
    name = PurePosixPath(subdir).name if subdir else ""
    if not name:
        name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
    return scaffold.slugify(name)


def clean_id(tid: str) -> str:
    """A template id that is a relative path of ordinary folder names, or an
    error. Everything else — a leading `/`, a `..`, a `.hidden` segment the vault
    would never scan — is a design landing somewhere the user did not ask for."""
    raw = tid.strip()
    if not raw or raw.startswith("/") or "\\" in raw or PurePosixPath(raw).is_absolute():
        raise InstallError(f"bad template id: {tid!r} — it must be a path under templates/")
    tid = raw.rstrip("/")
    for part in PurePosixPath(tid).parts:
        if part in (".", "..") or part.startswith((".", "_")) or "/" in part:
            raise InstallError(
                f"bad template id: {tid!r}\n"
                "  a segment may not be '.', '..', or start with '.' or '_'"
            )
    return tid


def _template_folder(cfg: Config, tid: str) -> Path:
    """Where a template id lands, refusing any id that leaves `templates/`."""
    folder = Path(os.path.normpath(cfg.templates / clean_id(tid)))
    if not folder.is_relative_to(cfg.templates) or folder == cfg.templates:
        raise InstallError(f"template id {tid!r} escapes {cfg.templates}")
    return folder


def _resolve_subdir(clone: Path, subdir: str | None) -> Path:
    """The design folder inside the clone. A `..`, an absolute path, or a symlink
    pointing out of the clone is a write to somewhere else on the disk."""
    if not subdir:
        return clone
    rel = PurePosixPath(subdir.strip("/"))
    if rel.is_absolute() or ".." in rel.parts or "\\" in subdir or subdir.startswith("/"):
        raise InstallError(f"bad subdir: {subdir!r} — it must be a path inside the repository")
    target = clone / rel
    root = clone.resolve()
    if target.is_symlink() or any(p.is_symlink() for p in target.parents if p.is_relative_to(clone)):
        raise InstallError(f"bad subdir: {subdir!r} — it is a symlink")
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise InstallError(f"bad subdir: {subdir!r} — it resolves outside the repository")
    if not resolved.is_dir():
        raise InstallError(f"{subdir!r} is not a folder in this repository")
    return target


# ── what may be copied ───────────────────────────────────────────────────────


def _classify(rel: PurePosixPath, is_dir: bool) -> str:
    """Verdict on one entry: `copy`, `skip`, or the sentence saying why it is
    refused. Anything that is neither design nor repository furniture is a
    refusal, because a design is a known, small set of file types."""
    if is_dir:
        if rel.parts[0] == "starter":
            return "copy"
        return "a design's only folder is starter/"
    name, suffix = rel.name, rel.suffix.lower()
    if suffix in IGNORED_SUFFIXES or name in IGNORED_NAMES or name.split(".")[0] in IGNORED_NAMES:
        return "skip"
    if rel.parts[0] == "starter":
        if suffix in STARTER_SUFFIXES or suffix in IMAGE_SUFFIXES:
            return "copy"
        return "a starter may hold only .typ, .yml, .mmd and images"
    if len(rel.parts) == 1 and (name in ROOT_FILES or suffix in ROOT_SUFFIXES):
        return "copy"
    return "a design may hold only template.toml, *.typ and a starter/"


def plan(source: Path) -> list[PurePosixPath]:
    """Every file we are willing to copy out of `source`, relative to it.

    Refuses rather than filters: a repository holding a file a design has no
    business holding is reported, not quietly cleaned up on the way in.
    """
    files: list[PurePosixPath] = []
    refused: list[str] = []
    stack: list[tuple[Path, PurePosixPath]] = [(source, PurePosixPath())]
    while stack:
        folder, prefix = stack.pop()
        for entry in sorted(folder.iterdir(), key=lambda p: p.name):
            rel = prefix / entry.name
            if entry.name.startswith("."):
                # Dotfiles are never copied — .git, .github, and the upstream's
                # own .installed.json included. Ours is written afterwards.
                continue
            if entry.is_symlink():
                refused.append(f"{rel} (symlink — a copy that escapes its folder)")
                continue
            verdict = _classify(rel, entry.is_dir())
            if verdict == "skip":
                continue
            if verdict != "copy":
                refused.append(f"{rel} ({verdict})")
                continue
            if entry.is_dir():
                stack.append((entry, rel))
            elif entry.is_file():
                files.append(rel)
            else:
                refused.append(f"{rel} (not a regular file)")
    if refused:
        raise InstallError(
            "this folder holds files a design may not have:\n  "
            + "\n  ".join(sorted(refused))
            + "\n  nothing was copied. If the design sits deeper in the repository, "
            "point at it with --subdir."
        )
    if not files:
        raise InstallError("no design here — the folder holds no template.toml and no Typst")
    return sorted(files)


# ── the record ───────────────────────────────────────────────────────────────


def _read_record(folder: Path) -> dict | None:
    path = folder / RECORD
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("url") else None


def _to_installed(tid: str, folder: Path, data: dict) -> Installed:
    return Installed(
        id=tid,
        url=str(data.get("url", "")),
        ref=data.get("ref"),
        sha=str(data.get("sha", "")),
        subdir=data.get("subdir"),
        installed_at=str(data.get("installed_at", "")),
        folder=folder,
    )


def installed(cfg: Config) -> list[Installed]:
    """Every design in this vault that came from a repository."""
    if not cfg.templates.is_dir():
        return []
    out: list[Installed] = []
    for path in sorted(cfg.templates.rglob(RECORD)):
        data = _read_record(path.parent)
        if data is None:
            continue
        tid = path.parent.relative_to(cfg.templates).as_posix()
        out.append(_to_installed(tid, path.parent, data))
    return sorted(out, key=lambda i: i.id)


def _find(cfg: Config, tid: str) -> Installed | None:
    tid = tid.strip("/")
    records = installed(cfg)
    exact = [r for r in records if r.id == tid]
    if exact:
        return exact[0]
    by_name = [r for r in records if r.id.rsplit("/", 1)[-1] == tid]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise InstallError(f"{tid!r} is ambiguous: " + ", ".join(r.id for r in by_name))
    return None


# ── placing it in the vault ──────────────────────────────────────────────────


def _validate(cfg: Config, tid: str) -> None:
    """The design has to be a design before the install counts as done."""
    folder = cfg.templates / tid
    toml = folder / "template.toml"
    if not toml.is_file():
        raise InstallError(f"{tid}: no template.toml — this folder is not a design")
    try:
        with toml.open("rb") as handle:
            tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise InstallError(f"{tid}: template.toml does not parse — {exc}") from exc
    try:
        tpl = vault.template(cfg, tid)
        chain = vault.lineage(cfg, tpl)
    except vault.VaultError as exc:
        raise InstallError(f"{tid}: {exc}") from exc
    if not any((a.folder / "report.typ").is_file() for a in chain):
        raise InstallError(
            f"{tid}: no report.typ, and its extends chain provides none — "
            "the design would not compile"
        )


def _missing_ancestors(base: Path, folder: Path) -> list[Path]:
    """Folders between `base` and `folder` that do not exist yet, deepest first —
    what a rollback has to remove so a failed install leaves no empty groups."""
    out: list[Path] = []
    current = folder.parent
    while current != base.parent and not current.exists():
        out.append(current)
        current = current.parent
    return out


def _place(cfg: Config, tid: str, source: Path, files: list[PurePosixPath], record: dict, force: bool) -> Path:
    folder = _template_folder(cfg, tid)
    # The design being replaced is moved aside inside the vault's own .build/,
    # so both the move and the restore are renames on one filesystem: a rollback
    # that fails halfway would be the one way to lose somebody's design.
    cfg.build.mkdir(parents=True, exist_ok=True)
    holding = Path(tempfile.mkdtemp(prefix="replaced-", dir=cfg.build))
    backup: Path | None = None
    try:
        if folder.exists():
            if not force:
                raise InstallError(
                    f"{tid} already exists at {folder}\n"
                    "  install over it with --force, or give it another id with --id"
                )
            backup = holding / "previous"
            shutil.move(str(folder), str(backup))
        created = _missing_ancestors(cfg.templates, folder)
        try:
            folder.mkdir(parents=True)
            for rel in files:
                dest = folder / Path(*rel.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                # copyfile, not copy: a mode carried over from the repository is
                # the repository deciding what is executable in this vault.
                shutil.copyfile(source / Path(*rel.parts), dest)
                os.chmod(dest, 0o644)
            (folder / RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            _validate(cfg, tid)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            for stale in created:
                if stale.is_dir() and not any(stale.iterdir()):
                    stale.rmdir()
            if backup is not None:
                folder.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(backup), str(folder))
                backup = None
            raise
    finally:
        shutil.rmtree(holding, ignore_errors=True)
    return folder


# ── the commands ─────────────────────────────────────────────────────────────


def install(
    cfg: Config,
    url: str,
    *,
    id: str | None = None,
    ref: str | None = None,
    subdir: str | None = None,
    force: bool = False,
    quiet: bool = False,
) -> Installed:
    """Fetch a design from a git repository and put it in this vault."""
    url, ref, subdir = split_url(url, ref, subdir)
    tid = clean_id(id or _default_id(url, subdir))
    _template_folder(cfg, tid)  # fail before the network, not after
    if not quiet:
        print(WARNING)
        print(f"  fetching {url}" + (f" @ {ref}" if ref else ""))

    with tempfile.TemporaryDirectory(prefix="report-maker-install-") as tmp:
        clone = Path(tmp) / "repo"
        sha = _clone(url, ref, clone)
        source = _resolve_subdir(clone, subdir)
        files = plan(source)
        record = {
            "url": url,
            "ref": ref,
            "sha": sha,
            "subdir": subdir,
            "installed_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "engine": _engine_version(),
        }
        folder = _place(cfg, tid, source, files, record, force=force)

    item = _to_installed(tid, folder, record)
    if not quiet:
        print(f"  → {folder} ({len(files)} file(s), {item.short})")
        print(f"\nNext: report-maker stage, then report-maker new \"Title\" --template {tid}")
    return item


def update(cfg: Config, id: str | None = None, *, quiet: bool = False) -> list[Installed]:
    """Re-fetch installed designs at the ref they were installed from."""
    if id is None:
        targets = installed(cfg)
        if not targets:
            raise InstallError("no installed designs in this vault")
    else:
        found = _find(cfg, id)
        if found is None:
            tid = id.strip("/")
            if (cfg.templates / tid).is_dir():
                raise InstallError(
                    f"{tid} is a local design — it did not come from a repository, "
                    "so there is nothing to update"
                )
            raise InstallError(f"no installed design {tid!r}")
        targets = [found]

    out: list[Installed] = []
    for previous in targets:
        fresh = install(
            cfg,
            previous.url,
            id=previous.id,
            ref=previous.ref,
            subdir=previous.subdir,
            force=True,
            quiet=True,
        )
        fresh.previous_sha = previous.sha
        out.append(fresh)
        if not quiet:
            if fresh.moved:
                print(f"  → {fresh.id}  {previous.sha[:9]} → {fresh.short}")
            else:
                print(f"  · {fresh.id}  unchanged ({fresh.short})")
    return out


def uninstall(cfg: Config, id: str, *, quiet: bool = False) -> None:
    """Remove an installed design. A local design is never removed this way."""
    tid = id.strip("/")
    folder = _template_folder(cfg, tid)
    if not folder.is_dir():
        found = _find(cfg, tid)
        if found is None:
            raise InstallError(f"no design at {cfg.templates / tid}")
        folder, tid = found.folder, found.id
    if _read_record(folder) is None:
        raise InstallError(
            f"{tid} is a local design — it was written here, not installed.\n"
            f"  report-maker will not delete it; remove {folder} yourself if you mean to."
        )
    shutil.rmtree(folder)
    # An empty group folder left behind would still list as a group.
    parent = folder.parent
    while parent != cfg.templates and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
    if not quiet:
        print(f"  removed {folder}")


# ── output ───────────────────────────────────────────────────────────────────


def _engine_version() -> str:
    from . import __version__

    return __version__


def record_json(item: Installed) -> dict:
    return {
        "id": item.id,
        "url": item.url,
        "ref": item.ref,
        "sha": item.sha,
        "subdir": item.subdir,
        "installed_at": item.installed_at,
        "folder": str(item.folder),
        "previous_sha": item.previous_sha,
        "moved": item.moved,
    }


def to_json(items: Installed | Iterable[Installed]) -> dict:
    if isinstance(items, Installed):
        items = [items]
    return {"installed": [record_json(i) for i in items]}


def report_installed(cfg: Config, items: list[Installed]) -> int:
    if not items:
        print("  no installed designs — report-maker template install <url>")
        return 0
    for item in items:
        where = f"{item.url}#{item.subdir}" if item.subdir else item.url
        print(f"  {item.id:<28} {item.short}  {where}" + (f" @ {item.ref}" if item.ref else ""))
    return 0
