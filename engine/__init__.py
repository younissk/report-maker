"""The report engine.

Headless by construction: every entry point here is a function that reads files
and writes files. Nothing serves, nothing opens a browser, nothing prompts. That
is what makes the same code usable from a shell, a Makefile, CI, or an agent.

    from engine import config, build, check

    cfg = config.load()
    build.build(cfg)
    findings = check.check(cfg)

Modules:

    config      vault discovery, report-maker.toml
    brand       brand.json → Typst tokens + mermaid theme (generated)
    workspace   which reports exist, and their metadata
    diagrams    mermaid .mmd → branded .svg
    build       Typst → PDF
    pages       PDF → page PNGs + pages.json
    manifest    out/manifest.json
    check       the citation rule, enforced
    scaffold    new vaults, new reports, new designs
    cli         the command line over all of the above
"""

__version__ = "0.1.0"
