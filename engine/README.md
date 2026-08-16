# engine/

The whole system. Python standard library only, no state, no I/O beyond files.

```
config.py      vault discovery, report-maker.toml → absolute paths
vault.py       designs and brand packs, discovered from folders
brand.py       a brand pack → Typst tokens + mermaid theme + mermaid CSS
library.py     stages each design into <vault>/.build/design/<id>/
workspace.py   which reports exist (nested), their metadata, their staleness
diagrams.py    mermaid .mmd → branded .svg (headless Chrome via mermaid-cli)
build.py       typst compile → PDF
pages.py       typst compile --format png → page images + pages.json
manifest.py    out/manifest.json
check.py       the citation rule, enforced
scaffold.py    new vaults, new reports, new designs
cli.py         the command line over all of the above
typst-less:    templates/ ships the built-in designs, brand/ the default pack
```

## Using it as a library

Every command is a function on a `Config`. Nothing prompts, nothing serves,
nothing blocks — which is the point: an agent or a CI job drives the same code
path a human does.

```python
from engine import config, library, vault, diagrams, build, pages, manifest, check

cfg = config.load("/path/to/vault")          # or None to search upwards from cwd

vault.templates(cfg)                          # {id: Template}, built-ins + vault
vault.groups(cfg)                             # {group: [Template]}

library.stage(cfg)                            # designs → .build/design/<id>/
diagrams.build(cfg)                           # .mmd → .svg, per design's brand
build.build(cfg, "clients/acme")              # a report, a folder, or all of them
pages.build(cfg, ppi=144)                     # → out/pages/<report-id>/
manifest.build(cfg)                           # → out/manifest.json

findings = check.check(cfg)
errors = [f for f in findings if f.level == "error"]
```

## Four design decisions worth knowing

**Folders are the data model.** A report is a folder with a `main.typ`; its path
under `reports/` is its id and everything above the last segment is its group. A
design is a folder under `templates/`; nesting groups it. A brand pack is a
folder with a `brand.json`. There is no index, no registry and no database to
drift from what is on disk — `rglob` is the query engine.

**Everything derived is generated.** A colour exists once, in a brand pack. The
Typst tokens, the mermaid theme variables, the mermaid stylesheet and the
mermaid `classDef`s are all produced from it. Hand-maintaining those four copies
is what makes a diagram drift from the report around it.

**Designs are staged, not imported in place.** Typst can only import files under
`--root`, and `--root` must be the vault. So each design — its own Typst files,
plus whatever it inherits, plus `tokens.typ` for its brand pack — is assembled
into `.build/design/<id>/`, and reports import `/.build/design/<id>/report.typ`.
The engine can then live anywhere, and inheritance costs nothing at compile time.

**The citation rule is a build step, not a convention.** `check.py` reads the
Typst source and the bibliography and fails the build on an uncited figure, a
bare `image()`, or a `@key` with no entry. A rule nobody enforces is a rule that
is already false somewhere in the back half of a long report.

## Adding a design

`templates/<id>/report.typ` defines a `report(…)` show-rule function; anything it
does not define is inherited from the template named in `extends`. The staged
directory is flat, so a design's own files import their siblings by name
(`#import "theme.typ"`), and can reach another design by its staged path
(`#import "/.build/design/base/report.typ": running-header`) — which is how
`brief` borrows the base running chrome instead of copying it.

## Adding a rule to the checker

Rules live in `check_report()` and share one scanner: `scrub()` blanks comments
and code blocks while preserving offsets, `calls(src, name)` returns every
`name(…)` call with its argument text, and `add(level, code, index, message)`
records a finding at the right line. Add the rule, then add a test in
`tests/test_engine.py` that fails without it — a linter with an untested rule is
a linter that quietly stops firing.
