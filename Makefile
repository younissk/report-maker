# report-maker
#
#   make                    theme, diagrams, PDFs, page images, manifest, check
#   make new T="Title"      scaffold a report
#   make build R=<slug>     one report only (R works on most targets)
#   make check              enforce the citation rule
#   make watch R=<slug>     live rebuild while writing
#   make doctor             what is installed, what is missing
#   make test               engine unit tests
#   make clean              remove out/ and .build/
#
# Every target is a thin call into engine/ — the Makefile adds no behaviour, so
# CI and agents can call `report-maker` directly and get identical results.

RM  ?= ./bin/report-maker
PY  ?= python3

.PHONY: all new list brand diagrams build pages manifest check watch doctor test clean help

all:
	@$(RM) all $(R)

new:
	@test -n "$(T)" || { echo 'usage: make new T="Report title"'; exit 1; }
	@$(RM) new "$(T)"

list:
	@$(RM) list

brand:
	@$(RM) brand

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
	@test -n "$(R)" || { echo 'usage: make watch R=<slug>'; exit 1; }
	@$(RM) watch $(R)

doctor:
	@$(RM) doctor

test:
	@$(PY) -m unittest discover -s tests -v

clean:
	@$(RM) clean

help:
	@$(RM) --help
