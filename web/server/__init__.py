"""The web server: HTTP in, `report-maker` subprocesses out.

Six modules, and the split between them is the same one `app/` makes. `app.py`
speaks HTTP and knows nothing about vaults; `routes.py` knows which engine
command answers which question and nothing about sockets; `engine.py` is the
only door to a subprocess; `security.py` holds every guard; `sessions.py` owns
the store; `github.py` and `share.py` own the two things that reach outward.

Nothing here parses a report, evaluates the citation rule, or computes anything
a CLI command already prints. That is the rule `app/README.md` states for the
desktop shell, and it is the same rule here for the same reason: a second
answer to "what does this vault contain" is the answer that drifts.

Kept import-light on purpose. `python3 -m web` imports this package before it
has parsed a single flag, so a module-level side effect here is a side effect
that happens before `--help`.
"""
