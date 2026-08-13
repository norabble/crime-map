.PHONY: help setup data states geo counties discover inspect import merge serve test validate regions compare-regions search clean distclean

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
	@echo "  make validate   check the built county layer for known defects"
	@echo "  make merge      re-apply fips.py merges to existing geometry"
	@echo ""
	@echo "  Region layer (groups of low-population counties):"
	@echo "  make regions          build every variant from the county layer"
	@echo "  make compare-regions  volatility and shape, one row per variant"
	@echo ""
	@echo "  make search     place gazetteer for the viewer's search box"
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

# Every check here stands for a defect that shipped and was invisible on
# the map. Run it after any change to the import path; a passing build
# that fails this is a build that looks right and is not.
validate:
	$(PY) scripts/validate_counties.py

# Re-applies the merged units in fips.py to the geometry already in data/,
# without re-downloading it. Needed when fips.py gains a merge, and safe
# to re-run: it is idempotent.
merge:
	$(PY) scripts/fetch_geometry.py --merge-only

# Region layers, built from the county layer. Membership is committed and
# reused, so this is safe to re-run: it will NOT move a boundary unless you
# pass REDEFINE=1, and moving one invalidates every trend on the map.
#
# The cz variants need one CSV from USDA ERS; `floor` needs no network.
REGION_VARIANTS ?= floor cz cz-floor
regions:
	@for v in $(REGION_VARIANTS); do \
	  $(PY) scripts/build_regions.py --variant $$v $(if $(REDEFINE),--redefine,) || exit 1; \
	done

compare-regions:
	$(PY) scripts/compare_regions.py

# The viewer's search box. States and counties come from the geometry;
# cities come from police agency names in Return A, which carry a county
# FIPS and so are a gazetteer nobody had to download. Needs counties.json
# and geo-*.json to exist first.
search:
	$(PY) scripts/build_search.py

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
