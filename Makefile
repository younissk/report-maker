# report-maker — engine + desktop app + web version
#
# This repository is the tool, not a vault. A vault is a folder somewhere on your
# disk holding report-maker.toml; every report command runs against one:
#
#   make build V=~/Documents/Reports          one vault
#   make V=~/Documents/Reports                everything, that vault
#
# V defaults to examples/demo-vault, the sample vault this repo ships for
# development and for the app's smoke test.
#
#   make new T="Title" V=<vault>   scaffold a report   (G=folder  TPL=design)
#   make list V=<vault>            reports, grouped by folder
#   make templates V=<vault>       designs, grouped by folder
#   make design ID=<id> V=<vault>  create an editable design (FROM=<id>)
#   make design-install URL=<url>  fetch somebody else's design (ID=<id>  REF=<ref>)
#   make check V=<vault>           enforce the citation rule  (SCORE=1 adds density)
#   make cite R=<report> URL=<url> archive a source and add it to sources.yml
#   make verify V=<vault>          re-fetch archived sources, report drift
#   make score V=<vault>           evidence density per report
#   make diff R=<report>           what changed since a revision (REV=<rev>)
#   make html V=<vault>            report + evidence → one self-contained .html
#   make data V=<vault>            the CSVs a report registered, with checksums
#   make data-check V=<vault>      the data rules alone: stale, unread, degenerate
#   make find Q=<query> V=<vault>  search prose, sources, snapshots and diagrams
#   make index V=<vault>           build or refresh the search index
#   make todos V=<vault>           the pad, across the vault  (OPEN=1  R=<report>)
#   make notes R=<report>          that report's notes.md
#   make brand-preview V=<vault>   render the brand specimen (PACK=<name>)
#   make sync V=<vault>            commit the vault  (PUSH=1  M="message")
#   make mcp V=<vault>             serve the vault to an agent over MCP
#   make watch R=<target>          live rebuild while writing
#   make doctor                    what is installed, what is missing
#   make version                   which engine this is
#   make test                      engine unit tests
#   make app                       the desktop app, dev mode
#   make open V=<vault>            build the app and open it on that vault
#   make app-smoke                 build the app, drive it, screenshot each screen
#   make app-dist                  package the app (macOS dmg + zip, unsigned)
#   make web                       the web version, dev: API + Vite together
#   make web-build                 build the frontend the API serves
#   make web-docker                run the container (docker compose up --build)
#   make web-test                  the web server's own suite
#   make install                   CLI on PATH, app in /Applications  (macOS)
#   make uninstall                 take both of those away again
#   make clean V=<vault>           remove that vault's out/ and generated .build/
#
# Every target is a thin call into engine/ — the Makefile adds no behaviour, so
# CI and agents can call `report-maker` directly and get identical results.

V   ?= examples/demo-vault
CLI ?= ./bin/report-maker
PY  ?= python3

# The web version binds loopback on this port; the Vite dev server proxies to it.
# One variable so the two halves cannot disagree — a proxy pointed at a port
# nothing is listening on fails as a blank page rather than as an error.
WEB_PORT ?= 8787

# `RM` is a GNU make built-in (rm -f) and cannot be reused here — a recipe
# written as `$(VAULT) doctor` silently expands to `rm -f doctor`.
VAULT = $(CLI) -C $(V)

.PHONY: all new list templates design design-install stage diagrams build pages \
        manifest check cite verify score diff html data data-check find index \
        todos notes sync brand-preview mcp \
        watch doctor version test app open app-build app-smoke app-deps \
        web web-build web-docker web-test web-deps clean help

all:
	@$(VAULT) all $(R)

new:
	@test -n "$(T)" || { echo 'usage: make new T="Report title" [V=<vault>] [G=clients/acme] [TPL=brief]'; exit 1; }
	@$(VAULT) new "$(T)" $(if $(G),--into $(G),) $(if $(TPL),--template $(TPL),)

list:
	@$(VAULT) list $(R)

templates:
	@$(VAULT) templates

design:
	@test -n "$(ID)" || { echo 'usage: make design ID=audits/company [FROM=base] [V=<vault>]'; exit 1; }
	@$(VAULT) template new $(ID) $(if $(FROM),--from $(FROM),)

# An installed design is code that runs at build time, so the install records
# where it came from: `template update` and `template uninstall` refuse to touch
# a design without that record.
design-install:
	@test -n "$(URL)" || { echo 'usage: make design-install URL=https://github.com/you/house-style [ID=<id>] [REF=<ref>] [V=<vault>]'; exit 1; }
	@$(VAULT) template install "$(URL)" $(if $(ID),--id $(ID),) $(if $(REF),--ref $(REF),)

stage:
	@$(VAULT) stage

diagrams:
	@$(VAULT) diagrams $(R) $(if $(FORCE),--force,)

# KEEP=1 carries on past a report that will not compile and reports the failures
# at the end. It still exits non-zero — one broken report must not make a vault
# of two hundred unbuildable, and it must not go green either.
build:
	@$(VAULT) build $(R) $(if $(FORCE),--force,) $(if $(KEEP),--keep-going,)

pages:
	@$(VAULT) pages $(R) $(if $(PPI),--ppi $(PPI),) $(if $(FORCE),--force,)

manifest:
	@$(VAULT) manifest

check:
	@$(VAULT) check $(R) $(if $(SCORE),--score,)

# ── evidence ─────────────────────────────────────────────────────────────────
#
# A source is fetched, archived beside the report, and written into sources.yml
# by one command; everything after that reads the archive rather than the web.

cite:
	@test -n "$(R)" -a -n "$(URL)" || { echo 'usage: make cite R=<report> URL=https://… [KEY=<key>] [V=<vault>]'; exit 1; }
	@$(VAULT) cite $(R) "$(URL)" $(if $(KEY),--key $(KEY),)

# OFFLINE=1 reports the archive without dialling out; REFRESH=1 re-archives a
# changed page, keeping the previous copy.
verify:
	@$(VAULT) verify $(R) $(if $(OFFLINE),--offline,) $(if $(REFRESH),--refresh,)

score:
	@$(VAULT) score $(R)

diff:
	@test -n "$(R)" || { echo 'usage: make diff R=<report> [REV=HEAD~1] [V=<vault>]'; exit 1; }
	@$(VAULT) diff $(R) $(if $(REV),--rev $(REV),)

# The pages are inlined, so they have to exist: run `make pages` first.
html:
	@$(VAULT) html $(R)

# ── numbers ──────────────────────────────────────────────────────────────────
#
# Registering a CSV and revising one are deliberately not here. `data add` takes
# a path from outside the vault and `data revise` moves a recorded checksum —
# both are decisions to take in front of the diff they cause, not conveniences to
# wrap. `report-maker data add|revise <report> <csv>` says what it is doing.

data:
	@$(VAULT) data list $(R)

data-check:
	@$(VAULT) data check $(R)

# ── reading the vault back ───────────────────────────────────────────────────

find:
	@test -n "$(Q)" || { echo 'usage: make find Q="pricing kind:source" [LIMIT=50] [V=<vault>]'; exit 1; }
	@$(VAULT) find "$(Q)" $(if $(LIMIT),--limit $(LIMIT),)

# Only the files that changed are re-read; FORCE=1 rebuilds the whole index.
index:
	@$(VAULT) index $(if $(FORCE),--force,)

# The pad: todos.md, notes.md, and the // TODO: comments left in the source.
# Never compiled, never cited — see "The pad" in CLAUDE.md.
todos:
	@$(VAULT) todos $(R) $(if $(OPEN),--open,)

notes:
	@test -n "$(R)" || { echo 'usage: make notes R=<report> [V=<vault>]'; exit 1; }
	@$(VAULT) notes $(R)

# ── the vault as a whole ─────────────────────────────────────────────────────

brand-preview:
	@$(VAULT) brand preview $(if $(PACK),--pack $(PACK),) $(if $(PPI),--ppi $(PPI),)

# Commits only. PUSH=1 also pushes, and refuses rather than forces — see the
# refusal list at the top of engine/gitsync.py.
sync:
	@$(VAULT) sync $(if $(M),-m "$(M)",) $(if $(PUSH),--push,)

# Speaks JSON-RPC on stdin/stdout, so nothing here may print. Useful mostly for
# checking the server starts; an agent launches it from its own MCP config.
mcp:
	@$(VAULT) mcp

watch:
	@test -n "$(R)" || { echo 'usage: make watch R=<report> [V=<vault>]'; exit 1; }
	@$(VAULT) watch $(R)

doctor:
	@$(VAULT) doctor

# No vault needed: this is a fact about the engine, not about a folder.
version:
	@$(CLI) --version

test:
	@$(PY) -m unittest discover -s tests -v

# The desktop app is optional: it shells out to the same CLI, so nothing else
# here depends on it. `npm install` runs on first use only.
app: app-deps
	@cd app && npm run dev

# Launch the built app on a vault. The app takes the vault as an argument, so
# this is the same thing as double-clicking it and picking the folder.
open: app-build
	@cd app && npm run open -- "$(abspath $(V))"

app-build: app-deps
	@cd app && npm run build

app-smoke: app-deps
	@cd app && npm run smoke

# A distributable carries the engine with it: electron-builder copies engine/
# and bin/ into the bundle's resources, which is where the app already looks.
# Unsigned by design — see the comment in app/electron-builder.yml for what a
# signed, notarised build needs in the environment.
.PHONY: app-dist
app-dist: app-deps
	@cd app && npm run dist

app-deps:
	@test -d app/node_modules || (cd app && npm install --no-audit --no-fund)

# ── the web version ──────────────────────────────────────────────────────────
#
# Two halves: a standard-library Python server that shells out to the same CLI,
# and a Vite frontend it serves. The server needs no install at all — `python3 -m
# web` is the whole deployment story — so only the frontend has a deps step.
#
# Dev runs both. Vite is in the foreground and the API behind it, sharing one
# origin through Vite's proxy so the session cookie behaves here exactly as it
# does in production; VITE_API_ORIGIN is passed rather than assumed, because a
# proxy aimed at the wrong port fails as a blank page and not as an error.
# Stopping Vite takes the API with it.
web: web-deps
	@RM_WEB_PORT=$(WEB_PORT) $(PY) -m web & \
	  api=$$!; trap 'kill $$api 2>/dev/null' EXIT INT TERM; \
	  cd web/client && VITE_API_ORIGIN=http://127.0.0.1:$(WEB_PORT) npm run dev

# Typechecks, then bundles into web/client/dist — the directory the server hands
# out verbatim. Without it the API still answers; there is just no page.
web-build: web-deps
	@cd web/client && npm run build

# The container, through compose rather than a `docker run` written out here.
# Every decision that matters is in docker-compose.yml with the reason beside
# it — the volume that keeps share links alive across a rebuild, the memory and
# pid limits, and the `127.0.0.1:` on the published port, which is the one
# character that decides whether a casual `up` puts this on the network. A
# second copy of that command in a Makefile is a second place for one of them
# to go missing. The image builds the frontend itself, so this does not depend
# on `web-build`: a container needing a local build first is a container that
# behaves differently on a machine without Node.
#
#   docker compose down      stop it        down -v   also delete every vault
web-docker:
	@docker compose up --build

# Its own suite, on the same runner as the engine's — no pytest, nothing to
# install. It drives a real server against a real engine, so it is slower than
# `make test` and catches the half that unit tests cannot.
web-test:
	@$(PY) -m unittest discover -s web/tests -v

web-deps:
	@test -d web/client/node_modules || (cd web/client && npm install --no-audit --no-fund)

# ── installing it on this machine ────────────────────────────────────────────
#
# `make install` is the morning command: prerequisites, the CLI symlinked into
# ~/.local/bin, the app built, packaged with electron-builder's `dir` target and
# copied into /Applications. It deliberately does not depend on app-deps — the
# script decides whether `npm install` is needed and says so, because the point
# of it is that every step announces itself.
#
# The logic lives in the script rather than here because it writes outside the
# repository, and that needs guards a recipe cannot express: a bundle already at
# the destination is identified by its CFBundleIdentifier before anything is
# removed. Never sudo. See INSTALL.md.
.PHONY: install uninstall
install:
	@./scripts/install-app.sh

uninstall:
	@./scripts/install-app.sh --uninstall

clean:
	@$(VAULT) clean

help:
	@$(CLI) --help
