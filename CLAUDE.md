# Project notes for Claude Code

Interactive 3D map of FBI crime data. Pillar height = per-capita crime
rate; colour = population density.

## Architecture

Data is a **build artifact**, not a runtime fetch. `scripts/` (Python)
download and normalise into `data/*.json`; the viewer under `web/` only
reads those files. The API key is build-time only and must never be
referenced from the viewer.

Two toolchains, one boundary: Python builds the data, Node serves and
builds the site. They meet at `data/*.json` and nowhere else.

`data/*.json` is **committed** despite being a build artifact, because
Pages has to serve it and CI cannot rebuild it — `counties.json` needs a
Return A file placed in `.cache/bulk/` by hand from openICPSR 100707,
behind an authenticated download. That trade is deliberate: it keeps the
API key a local secret rather than a repository secret. Git is
content-addressed, so rebuilding with identical output adds nothing to
history.

## This repository is public

`norabble/crime-map` was made public in August 2026 and is live at
https://norabble.github.io/crime-map/. The threat model changed then, and
the old warnings were written for a different one.

**Anything committed is world-readable, immediately and permanently, and
so is every blob in history.** Leaking `.env` used to take a misconfigured
local server; now it takes one `git add -f`. The `.gitignore` entry is the
whole defence. This was verified before publishing: `.env` has never been
committed on any branch, the key appears in no reachable blob, and the only
key-shaped string in the tree is the deliberately fake `SECRET` fixture in
`tests/test_pipeline.py`. Anything added to the tree from here on is
published the moment it lands on `main`.

`make data` and `make states` stay local, keyed operations. Do not move
the API key into GitHub Actions secrets to automate them — the openICPSR
step cannot be automated anyway, so the only thing that would buy is a
copy of the key in a second place.

## Commands

    npm run dev      http://127.0.0.1:8000/crime-map/
    npm run build    -> dist/  (what GitHub Pages serves)
    npm test         vitest, viewer logic
    npm run typecheck

    make test        offline self-tests for the Python pipeline
    make data        full build (geometry + states)
    make inspect     schema report for a bulk file in .cache/bulk/
    make import      national counties 1960-2024 from Return A
    make validate    check the built county layer for known defects
    make merge       re-apply fips.py merges to existing geometry
    make discover    probe CDE agency routes (for the API path)
    make counties    STATES=VT -> per-state counties via the API

Pushing to `main` deploys the site; `gh workflow run "Deploy to GitHub
Pages"` republishes the same commit. Pages is set to the **GitHub
Actions** source — with "Deploy from a branch" the workflow tests and
builds cleanly and then fails at `configure-pages`, which is how the
first deploy failed. See "Publishing" in the README.

`make serve` is a thin alias for `npm run dev`. There is no hand-written
server here any more, and there should not be one again: the last one was
single-threaded and stalled whenever a browser opened a preconnect socket
("first load fails, reload works"), and its stated reason for existing was
wrong anyway — `python -m http.server` has had `--bind` and `--directory`
for years.

**`server.fs.allow` in `vite.config.ts` is load-bearing.** Setting
`root: 'web'` does *not* confine the dev server; a request for
`/crime-map/../.git/config` resolves straight out of the web root and was
served until `fs.allow` was narrowed. `.env` holds the API key and `.git`
can hold credentials, so both are also in `fs.deny`. Verify with a
traversal probe after touching that config, not by reading it.

`npm run dev` and `npm run build` both run `scripts/sync-data.mjs` first,
which copies `data/` into `web/public/data/`. A copy, not a symlink, so
dev and build behave identically. Never edit `web/public/data/` — it is
overwritten.

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
  de-saturate.
- **Coverage below 80% is de-saturated,** not hidden and not corrected.
- **Nothing is fabricated invisibly.** This *replaces* the older rule
  that nothing may be fabricated at all. The county layer now estimates
  agency-years nobody filed (`scripts/impute.py`, three tiers, calibrated
  to published state totals for 1960–2019). What makes that legitimate is
  the labelling, so the labelling is the invariant:
  - `cov` is measured reporting only. It must never move because an
    estimate was added. Crediting imputed rows to the reporting share
    once made `cov` read ~100% everywhere the moment imputation was
    switched on — the field became meaningless and nothing failed.
  - `imp` is the share of offences nobody filed for that county. It is a
    *separate channel* from `cov`, not a refinement of it: a county
    nobody reported for is `cov` 0 **and** `imp` 1, and both readings
    have to survive to the pixel.
  - `--no-impute` must reproduce the measured build. It is the only
    cheap proof that estimation is a layer rather than a rewrite.
- **Calibration stops at 2019.** Before that, `states.json` totals are
  FBI *estimates* — complete by construction, and the right anchor. From
  2020 the same field is a participation sum (California 2021 covers
  26%), so calibrating to it would assert a known undercount as truth.
  `k` is recorded as 1 for those years so the two regimes are
  distinguishable in the data, not just in prose.
- **`k` is capped at 5.** A huge residual over a tiny imputed base lands
  a city's missing crime on a handful of small agencies. Cap it, leave
  the remainder unallocated, and report it — Illinois' burglary and
  Chicago's non-compliant filings are the standing example.
- **Geometry must be lon/lat.** us-atlas ships pre-projected "albers"
  builds too; `fetch_geometry.py` validates before accepting a source.
- **Antimeridian.** Longitude deltas wrap to ±180 or Alaska's area is
  wrong by an order of magnitude.

## The viewer must never fail silently

Three bugs shared one cause: the viewer degraded invisibly, so "it's
broken" and "it's fine" looked identical. Each fix has tests; all three
were verified by reintroducing the bug and watching the suite go red.

- **Boot never waits on the basemap.** Everything used to hang off
  `map.on('load')`, which never fires if the cartocdn style stalls — the
  splash then span forever, and a reload "fixed" it because the style was
  cached. `map.ts` now starts on load *or* a watchdog, whichever comes
  first, gated by `once()`. `interleaved:false` is what makes that legal:
  deck draws to its own canvas and needs no loaded style. If the basemap
  turns up late, the status flips back to ready by itself.
- **`loadOptional` distinguishes absent from failed.** A 404 means the
  build legitimately lacks that layer — stay quiet. Everything else means
  it should have worked. The old single `optional` flag collapsed both to
  `null`, which is why a failed 8.5 MB `counties.json` produced no signal
  anywhere while the County button claimed no such dataset exists. Don't
  reunify these.
- **The county layer loads on click, not at boot.** It is 4.2 MB gzipped
  against 0.45 MB for everything else; eager loading put first paint
  behind the slowest request on the site. Tying it to a click is also what
  makes a failure attributable, reportable and retryable.
- **The Status panel is the contract.** Every layer is visibly working or
  visibly not. `absent` is `warn`, never `bad` — an unbuilt layer is a
  choice, and crying wolf about it teaches people to ignore the panel.
  `degraded` was added for the same reason: county data that joined to no
  geometry means the layer draws but something in it is lost, which is
  neither `ready` nor `failed`.
- **County keys with no geometry are counted, not skipped.** `buildCounties`
  used to `continue` past them in silence; that is how 17 years of Oglala
  Lakota and 64 of Bedford City disappeared. `fips.py` should keep the
  count at zero, but fixing the crosswalk without keeping the detector
  just hides the next boundary change instead.

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
1960-2024). `import_bulk.py` is now an orchestrator over four modules:
`returna.py` (CSV -> columnar agency-year table + the UCR county
crosswalk), `aggregate.py` (agency-years -> county-years),
`impute.py` (estimates + calibration), `fips.py` (county identity).

Column choices that are load-bearing, each verified against API-built
data:

- aggravated assault is `actual_assault_aggravated`; `actual_assault_total`
  bundles simple assault and reads ~10x too high
- population is `population`, not `population_group` (a size band)
- completeness is `number_of_months_reported`, not `..._missing`
- **`population_N_county` is UCR's own sequential county number, not
  FIPS.** Jefferson AL is UCR 37 and FIPS 073. The crosswalk is derived
  from the file in `build_crosswalk` and never guessed. Only *secondary*
  components of multi-county agencies go through it — a handful of rows
  carry a `population_1_county` that contradicts their own FIPS, and
  routing single-county agencies through the crosswalk would move them.
- **`covered_by_ori`** names the agency that files on this one's behalf.
  Unread, it cost ~5 million people's coverage in 2023 alone.

`counties.json` uses a **compact encoding**, now `compact-2`:
`[pop, cov, imp, ...totals]` per county-year. **Read slot positions from
`meta.record`, never from a constant** — `decodeCountyRec` hardcoded
totals at index 2, so adding `imp` in front of them would have silently
relabelled every offence in the file. It reads `compact-1`, `compact-2`
and the verbose object form the per-state API path still emits.

`fips.py` owns county identity and is imported by both the data build and
`fetch_geometry.py`, so geometry and data cannot disagree about what a
unit contains. It holds the vintage renames (Shannon SD -> Oglala Lakota,
Bedford City -> Bedford County, four more), the merged units (New York
City, Puerto Rico), and the territories too thin to draw. `make merge`
re-applies merges to existing geometry without re-downloading it, and is
idempotent.

Run `make validate` after anything that touches the import path. Every
check in it stands for a defect that shipped and looked fine on the map.

Puerto Rico is one unit (`72999`), built from Return A and written into
`states.json` by `build_states.py --territories-only` — a keyless step, so
State and County mode read the same number from one code path. Its
post-2020 figures are known-bad (466 violent offences for 3.2M in 2023):
the agencies file twelve months of near-nothing for several consecutive
years, so the collapse detector has no healthy baseline to compare
against, and there is no published state total to calibrate to. Do not
"fix" this by loosening `COLLAPSE_*` — that would start deleting genuine
long-run declines everywhere else.

## Data caveats

See README. The short version: there is a methodology seam at 2019/2020,
2021 coverage was ~75% so its apparent crime drop is largely an artifact,
and county-level aggregation is inherently unreliable
(https://ucrbook.com/county-level-data.html).
