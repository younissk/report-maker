# report-maker — engine + desktop app
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
#   make check V=<vault>           enforce the citation rule
#   make watch R=<target>          live rebuild while writing
#   make doctor                    what is installed, what is missing
#   make test                      engine unit tests
#   make app                       the desktop app (opens with no vault)
#   make app-smoke                 build the app, screenshot it, exit
#   make clean V=<vault>           remove that vault's out/ and generated .build/
#
# Every target is a thin call into engine/ — the Makefile adds no behaviour, so
# CI and agents can call `report-maker` directly and get identical results.

V   ?= examples/demo-vault
CLI ?= ./bin/report-maker
PY  ?= python3

# `RM` is a GNU make built-in (rm -f) and cannot be reused here — a recipe
# written as `$(VAULT) doctor` silently expands to `rm -f doctor`.
VAULT = $(CLI) -C $(V)

.PHONY: all new list templates design stage diagrams build pages manifest check watch doctor test app app-build app-smoke app-deps clean help

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

stage:
	@$(VAULT) stage

diagrams:
	@$(VAULT) diagrams $(R) $(if $(FORCE),--force,)

build:
	@$(VAULT) build $(R) $(if $(FORCE),--force,)

pages:
	@$(VAULT) pages $(R) $(if $(PPI),--ppi $(PPI),) $(if $(FORCE),--force,)

manifest:
	@$(VAULT) manifest

check:
	@$(VAULT) check $(R)

watch:
	@test -n "$(R)" || { echo 'usage: make watch R=<report> [V=<vault>]'; exit 1; }
	@$(VAULT) watch $(R)

doctor:
	@$(VAULT) doctor

test:
	@$(PY) -m unittest discover -s tests -v

# The desktop app is optional: it shells out to the same CLI, so nothing else
# here depends on it. `npm install` runs on first use only.
app: app-deps
	@cd app && npm run dev

app-build: app-deps
	@cd app && npm run build

app-smoke: app-deps
	@cd app && npm run smoke

app-deps:
	@test -d app/node_modules || (cd app && npm install --no-audit --no-fund)

clean:
	@$(VAULT) clean

help:
	@$(CLI) --help
