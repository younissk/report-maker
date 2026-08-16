# engine/

The whole system. Python standard library only, no state, no I/O beyond files.

```
config.py      workspace discovery, report-maker.toml → absolute paths
brand.py       brand.json → Typst tokens + mermaid theme + mermaid CSS
library.py     stages engine/typst/*.typ into <workspace>/.build/typst/
workspace.py   which reports exist, their metadata, whether they are stale
diagrams.py    mermaid .mmd → branded .svg (headless Chrome via mermaid-cli)
build.py       typst compile → PDF
pages.py       typst compile --format png → page images + pages.json
manifest.py    out/manifest.json
check.py       the citation rule, enforced
scaffold.py    new workspaces, new reports
cli.py         the command line over all of the above
typst/         the Typst library that reports import
brand/         the default brand pack, underlying every workspace brand
templates/     what `new` writes
```

## Using it as a library

Every command is a function on a `Config`. Nothing prompts, nothing serves,
nothing blocks — which is the point: an agent or a CI job drives the same code
path a human does.

```python
from engine import config, library, diagrams, build, pages, manifest, check

cfg = config.load("/path/to/workspace")     # or None to search upwards from cwd

library.stage(cfg)                           # theme + Typst library into .build/
diagrams.build(cfg)                          # .mmd → .svg
build.build(cfg, slug="2026-08-12-example")  # → out/<slug>.pdf
pages.build(cfg, ppi=144)                    # → out/pages/<slug>/
manifest.build(cfg)                          # → out/manifest.json

findings = check.check(cfg)
errors = [f for f in findings if f.level == "error"]
```

## Three design decisions worth knowing

**Everything derived is generated.** A colour exists once, in `brand.json`. The
Typst tokens, the mermaid theme variables, the mermaid stylesheet and the
mermaid `classDef`s are all produced from it. Hand-maintaining those four copies
is what makes a diagram drift from the report around it.

**The Typst library is staged, not imported in place.** Typst can only import
files under `--root`, and `--root` must be the workspace. So `engine/typst/*.typ`
is copied into `.build/typst/` on every build and reports import
`/.build/typst/report.typ`. The engine can then live anywhere — a sibling
checkout, a symlink on `PATH`, a submodule.

**The citation rule is a build step, not a convention.** `check.py` reads the
Typst source and the bibliography and fails the build on an uncited figure, a
bare `image()`, or a `@key` with no entry. A rule nobody enforces is a rule that
is already false somewhere in the back half of a long report.

## Adding a rule to the checker

Rules live in `check_report()` and share one scanner: `scrub()` blanks comments
and code blocks while preserving offsets, `calls(src, name)` returns every
`name(…)` call with its argument text, and `add(level, code, index, message)`
records a finding at the right line. Add the rule, then add a test in
`tests/test_engine.py` that fails without it — a linter with an untested rule is
a linter that quietly stops firing.
