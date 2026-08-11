# Project notes for Claude Code

Interactive 3D map of FBI crime data. Pillar height = per-capita crime
rate; colour = population density.

## Architecture

Data is a **build artifact**, not a runtime fetch. `scripts/` download
and normalise into `data/*.json`; `viewer/index.html` only reads those
files. The API key is build-time only and must never be referenced from
the viewer.

## Commands

    make test        offline self-tests, no network or key needed
    make data        full build (geometry + states)
    make serve       http://localhost:8000
    make inspect     schema report for a bulk file in .cache/bulk/
    make import      national counties 1960-2024 from Return A
    make discover    probe CDE agency routes (for the API path)
    make counties    STATES=VT -> per-state counties via the API

`make serve` runs `scripts/serve.py`, which roots the server at `viewer/`
and symlinks `data/` into it. Do not "fix" this by serving the repo root
— that publishes `.env`. It exists instead of `python -m http.server`
because that module binds every interface and sends no cache headers,
and neither is settable from its CLI. Both replacements are load-bearing:
loopback-only keeps an unbuilt-for-the-internet preview off the LAN, and
`no-store` plus dropped conditional-GET headers is what stops you editing
the viewer, reloading, and debugging the previous copy.

Only those two defaults are wrong, though. **The server must stay
threaded.** `python -m http.server` is (`ThreadingHTTPServer`), and an
earlier rewrite here dropped that: a single-threaded server parks its
accept loop in `readline()` on any connection that never sends a request
line, which is exactly what a browser preconnect is. It presented as "the
first load fails, the reload works", and because the viewer used to
report every failed body read as invalid JSON, it read as a corrupt data
file. `make test` now asserts an idle socket cannot starve a request.

Always run `make test` after touching `scripts/common.py` — the
geometry and fold logic there is what silently corrupts output if wrong.

## Invariants worth preserving

- **Fixed scale domains.** Height and colour are normalised across ALL
  years, not per-year. Per-year normalisation makes the tallest unit
  full height in every year and hides all temporal change. This was a
  real bug once; don't reintroduce it.
- **Partial years are dropped.** `fold_monthly` requires 12 *reported*
  months. A part-year sum renders as a fake crash in the rate. The API
  does not omit unreported months — it returns all 12 keys with `null`
  in the ones nobody has filed yet, so count values, not keys. Test
  `val is None`, never falsiness: a month with a genuine zero count is
  reported data (Orange County Sheriff VT filed 12 real zeros in 2023)
  and dropping it would delete honest quiet years. This shipped broken
  once — 2026 rendered as a 60% national crash — past a test whose
  fixture faked short years by omitting months instead of nulling them.
- **Rates use total population,** matching FBI method. Do NOT rescale by
  participating population — surface `cov` instead and let the viewer
  de-saturate. Rescaling fabricates figures the FBI never published.
- **Coverage below 80% is de-saturated,** not hidden and not corrected.
- **Geometry must be lon/lat.** us-atlas ships pre-projected "albers"
  builds too; `fetch_geometry.py` validates before accepting a source.
- **Antimeridian.** Longitude deltas wrap to ±180 or Alaska's area is
  wrong by an order of magnitude.

## Known-good reference values

Useful for spotting regressions:

- Vermont 2023 violent crime: 1,398 offences, pop 647,464 → 215.9/100k
- Vermont 2023: homicide 21 + rape 228 + robbery 94 + agg assault 1,055
  = 1,398, exactly matching the violent-crime total
- Colorado area: 103,859 sq mi computed vs 104,094 published (0.23% err)
- Earth surface: 4πR² with R=3958.7613 mi ≈ 196.9M sq mi

## Sub-state data

**Counties only.** There was an agency-point layer; it was removed because
UCR coordinates cannot support it — Return A's lat/lon are county
centroids, and the CDE roster stacked 200 of 362 agencies on shared points
(19 NH agencies on one). `scripts/build_agencies.py` is retained on disk
but is no longer wired to anything.

The national county layer comes from openICPSR 100707 (Return A,
1960-2024) via `import_bulk.py`. Three column choices are load-bearing and
were each verified against API-built data:

- aggravated assault is `actual_assault_aggravated`; `actual_assault_total`
  bundles simple assault and reads ~10x too high
- population is `population`, not `population_group` (a size band)
- completeness is `number_of_months_reported`, not `..._missing`

`counties.json` uses a **compact encoding**: `[pop, cov, ...totals]` per
county-year, ordered by `meta.crimeOrder`. The verbose object form would
be 74 MB; this is 8.4 MB. `buildCounties` in the viewer reads both, since
the per-state API path still emits objects.

## Data caveats

See README. The short version: there is a methodology seam at 2019/2020,
2021 coverage was ~75% so its apparent crime drop is largely an artifact,
and county-level aggregation is inherently unreliable
(https://ucrbook.com/county-level-data.html).
