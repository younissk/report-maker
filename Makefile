# report-maker
#
#   make                       stage, diagrams, PDFs, page images, manifest, check
#   make new T="Title"         scaffold a report      (G=folder  TPL=design)
#   make list                  reports, grouped by folder
#   make templates             designs, grouped by folder
#   make design ID=<id>        create an editable design (FROM=<id>)
#   make build R=<target>      one report, a folder, or all (R works on most targets)
#   make check                 enforce the citation rule
#   make watch R=<target>      live rebuild while writing
#   make doctor                what is installed, what is missing
#   make test                  engine unit tests
#   make app                   the desktop shell (editor, viewer, file tree)
#   make app-smoke             build the app, screenshot it, exit
#   make clean                 remove out/ and generated .build/ files
#
# Every target is a thin call into engine/ — the Makefile adds no behaviour, so
# CI and agents can call `report-maker` directly and get identical results.

RM  ?= ./bin/report-maker
PY  ?= python3

.PHONY: all new list templates design stage diagrams build pages manifest check watch doctor test app app-build app-smoke app-deps clean help

all:
	@$(RM) all $(R)

new:
	@test -n "$(T)" || { echo 'usage: make new T="Report title" [G=clients/acme] [TPL=brief]'; exit 1; }
	@$(RM) new "$(T)" $(if $(G),--into $(G),) $(if $(TPL),--template $(TPL),)

list:
	@$(RM) list $(R)

templates:
	@$(RM) templates

design:
	@test -n "$(ID)" || { echo 'usage: make design ID=audits/company [FROM=base]'; exit 1; }
	@$(RM) template new $(ID) $(if $(FROM),--from $(FROM),)

stage:
	@$(RM) stage

diagrams:
	@$(RM) diagrams $(R) $(if $(FORCE),--force,)

build:
	@$(RM) build $(R) $(if $(FORCE),--force,)

pages:
	@$(RM) pages $(R) $(if $(PPI),--ppi $(PPI),) $(if $(FORCE),--force,)

manifest:
	@$(RM) manifest

check:
	@$(RM) check $(R)

watch:
	@test -n "$(R)" || { echo 'usage: make watch R=<report>'; exit 1; }
	@$(RM) watch $(R)

doctor:
	@$(RM) doctor

test:
	@$(PY) -m unittest discover -s tests -v

# The desktop shell is optional: it shells out to the same CLI, so nothing here
# depends on it. `npm install` runs on first use only.
app: app-deps
	@cd app && npm run dev

app-build: app-deps
	@cd app && npm run build

app-smoke: app-deps
	@cd app && npm run smoke

app-deps:
	@test -d app/node_modules || (cd app && npm install --no-audit --no-fund)

clean:
	@$(RM) clean

help:
	@$(RM) --help
