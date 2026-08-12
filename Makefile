.PHONY: help setup data states geo counties discover inspect import serve test clean distclean

PY ?= python3
PORT ?= 8000
STATES ?= VT

help:
	@echo ""
	@echo "  US crime rate map - build targets"
	@echo ""
	@echo "  make setup      one-time: check python, create .env from template"
	@echo "  make geo        download state + county geometry, precompute areas"
	@echo "  make states     build data/states.json  (1960-present, needs API key)"
	@echo "  make data       geo + states  <- the usual first run"
	@echo "  make serve      serve the viewer at http://localhost:$(PORT)"
	@echo "  make test       run offline self-tests (no network)"
	@echo ""
	@echo "  County layer (optional, see README):"
	@echo "  make inspect    schema report for a bulk file in .cache/bulk/"
	@echo "  make import     national counties 1960-2024 from the Return A bulk file"
	@echo "  make discover   probe the CDE agency routes (for the API path)"
	@echo "  make counties   per-state counties via the API (STATES=VT)"
	@echo ""
	@echo "  make clean      remove generated data, keep the download cache"
	@echo "  make distclean  remove data and cache"
	@echo ""

setup:
	@$(PY) --version >/dev/null 2>&1 || { echo "python3 not found"; exit 1; }
	@test -f .env || { cp .env.example .env; echo "Created .env - paste your key into it."; }
	@echo "Get a free key (instant): https://api.data.gov/signup/"

geo:
	$(PY) scripts/fetch_geometry.py

states:
	$(PY) scripts/build_states.py

# Geometry has no key requirement, so it runs first and gives the viewer
# something to show even if the API step fails.
data: geo states
	@echo ""
	@echo "Built. Now run:  make serve"

discover:
	$(PY) scripts/discover_sources.py

# Read-only schema report for a bulk download placed in .cache/bulk/.
# Run this before writing any import mapping - the columns are not
# guessable and a wrong guess fails silently.
inspect:
	$(PY) scripts/inspect_bulk.py $(FILE)

# National county layer, 1960-2024, from the Return A bulk download.
# Run `make inspect` first and read the column report.
import:
	$(PY) scripts/import_bulk.py $(if $(FILE),--file $(FILE),)

# Aggregates the SAME per-agency calls `make agencies` makes, so once a
# state is built the county layer costs nothing but cache reads.
counties:
	$(PY) scripts/build_counties.py --states $(STATES)

# Kept as a thin alias so muscle memory still works; the dev server is
# Vite now. `npm run dev` runs scripts/sync-data.mjs first, which copies
# data/ into web/public/data where the server and the build both find it.
#
# There is no hand-written server here any more, deliberately. The last one
# was single-threaded and stalled on browser preconnects, and its stated
# reason for existing was wrong besides - `python -m http.server` has had
# --bind and --directory for years. See vite.config.ts for the part that
# still matters: fs.allow is what keeps .env and .git out of reach.
serve:
	@npm run dev

test:
	$(PY) tests/test_pipeline.py

clean:
	rm -rf data/*.json
	@echo "Removed data/. The download cache in .cache/ is intact, so a"
	@echo "rebuild will not re-hit the API."

distclean: clean
	rm -rf .cache
