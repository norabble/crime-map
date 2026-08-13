# US Crime Rates per Capita — 3D Geographic Explorer

Interactive map of FBI crime data. Each geographic area is extruded as a
pillar whose **height** is the per-capita rate for the selected crime
type, shaded on a gradient by **population density**.

Data is downloaded once by the build scripts and baked into static JSON.
The viewer then loads instantly, needs no API key, and works offline.

**Live: https://norabble.github.io/crime-map/**

---

## Quick start

```bash
make setup                     # creates .env from the template
# paste a free key into .env  →  https://api.data.gov/signup/
make data                      # downloads + builds (~5 min cold)
npm install                    # one-time, for the viewer toolchain
npm run dev                    # http://127.0.0.1:8000/crime-map/
```

Two test suites, both offline and neither needing a key: `make test`
covers the Python pipeline, `npm test` covers the viewer.

### Why a key is needed

Years 2020 onward come from the FBI Crime Data Explorer API, which sits
behind api.data.gov:

| Key | Limit | Verdict |
|-----|-------|---------|
| `DEMO_KEY` | 30 requests/hour | unusable — a full build needs ~460 |
| free key | 1,000 requests/hour | fine |

Signup is instant and needs only an email address. The key is used at
**build time only** — it never reaches the viewer.

Years 1960–2019 need no key. `make states` accepts `--no-nibrs` to build
that era alone:

```bash
python scripts/build_states.py --no-nibrs
```

---

## Publishing

The live site is built and deployed by
[`.github/workflows/pages.yml`](.github/workflows/pages.yml) on every push
to `main`, and can be re-run against the same commit with:

```bash
gh workflow run "Deploy to GitHub Pages"
```

Three things about that pipeline are worth knowing before changing it.

**Pages must be set to "GitHub Actions".** Settings → Pages → Source. With
the default "Deploy from a branch" the workflow builds and tests
perfectly and then dies at `actions/configure-pages`, which is exactly
what happened on the first deploy here.

**CI never builds the data.** `data/*.json` is committed, and the workflow
only reads it. `counties.json` needs a Return A file placed in
`.cache/bulk/` by hand from an authenticated openICPSR download, so no
workflow could rebuild it anyway — and keeping the build local means the
API key stays a local secret instead of a repository secret. `make data`
and `make states` are local, keyed operations by design.

**The build is a gate, not a formality.** It runs `npm run typecheck`,
`npm test` and `make test` before publishing, asserts that `base` in
`vite.config.ts` matches the repository name (Project Pages serve from
`/<repo>/`, and a mismatch renders as a blank page rather than an error),
and checks that all four `data/*.json` files reached `dist/` non-empty.
A site that shipped without its data would look fine in CI and fail only
in a browser.

---

## What gets built

```
data/
  geo-states.json     51 state polygons + precomputed land areas
  geo-counties.json   ~3,140 county polygons + areas
  states.json         1960–present, 9 crime types, population, coverage
  counties.json       3,135 counties x 1960-2024; see "County layer"
```

Every response is cached under `.cache/`, so re-running costs nothing
and an interrupted run resumes where it stopped. `make clean` removes
the built data but keeps the cache; `make distclean` removes both.

---

## The data, and what is wrong with it

This matters more than the code. Three things will mislead you if you
don't know about them.

### 1. There is a seam at 2019/2020

Two different collection systems are stitched together:

| Years | System | Source |
|-------|--------|--------|
| 1960–2019 | Summary Reporting System (SRS) | [CORGIS State Crime dataset](https://corgis-edu.github.io/corgis/csv/state_crime/) |
| 2020– | NIBRS, via the [FBI Crime Data Explorer](https://cde.ucr.cjis.gov/) API | live API |

The viewer marks the boundary on the year slider and warns when you
cross it. Changes across that line partly reflect a change in how crime
is counted, not only in how much occurred.

### 2. Reporting coverage collapsed during the NIBRS transition

Rates are computed on **total** population, but only agencies that
actually reported contribute offences. National coverage was roughly
**75–79% through 2021**, against ~96% by 2023. So 2021 looks like a
dramatic crime drop and largely isn't.

The API publishes coverage per state per month; the pipeline preserves
it and the viewer renders anything under 80% de-saturated and
semi-transparent, with the figure on hover. **A washed-out 2021 map is
the honest signal, not a bug.**

### 3. Partial years are dropped, not shown

The API currently holds data past the end of the last complete year.
Summing those months would render the current year as a catastrophic
decline that never happened. Any year without all twelve months
reported is omitted entirely.

### Also worth knowing

- **Rape definition changed in 2013.** Figures either side are not
  comparable.
- **Reporting is voluntary.** A low number can mean low crime *or* low
  reporting. Early years are far less complete.
- **Density uses true spherical polygon area** (land + water), so it
  differs slightly from Census land-only figures.

---

## County layer — national, 1960–2024

The county layer covers **3,135 counties across 65 years** (182,000
county-years, 8.4 MB). It is built from a single bulk download rather
than the API: measured, VT+NH+ME alone cost 3,852 requests and 5.6 hours
under the 900/hour throttle, so the ~19,000 agencies nationally would be
about **248 hours**.

### Source

[openICPSR 100707](https://www.openicpsr.org/openicpsr/project/100707) —
*UCR Program Data: Offenses Known and Clearances by Arrest (Return A),
1960–2024*, the **CSV / yearly** variant. Return A is agency-level annual
offence counts, and it continues past the retirement of SRS because the
FBI converts NIBRS submissions into Return A summary equivalents, so one
file spans both eras.

Download it (free account), then:

```bash
mkdir -p .cache/bulk
# ...put the zip there, no need to unpack...
make inspect     # read the column report before trusting anything
make import      # ~2 min -> data/counties.json
```

**CSV only.** This project has no pandas, so Stata/SPSS/R/Parquet
exports cannot be read; `make inspect` will say so rather than failing
obscurely.

### Read the inspect report before changing the mapping

`inspect_bulk.py` is read-only. It streams the file and reports every
column, which ones carry ORI / year / state / county FIPS / population,
and which map onto the nine crime ids. Three of those choices are not
obvious and were each verified against API-built data before being fixed
in `import_bulk.py`:

- **Aggravated assault is `actual_assault_aggravated`,** not
  `actual_assault_total`. The "total" bundles simple assault, which is
  ~10x larger: for VT/NH/ME in 2023 it read 25,912 against a true 2,493.
  Nothing downstream would have caught that.
- **Population is `population`,** not `population_group` — a size band
  ("city 250,000 thru 499,999"), not a number.
- **Completeness is `number_of_months_reported`,** not
  `number_of_months_missing`, which is left `NA` on rows that do record a
  month count.

The inspector also refuses to offer clearance, arrest and rate columns as
offence counts. A clearance is an arrest, not a crime.

### How the county figures are built

Population is summed over **every** agency in the county, reported or
not, so rates use total population as the FBI does; under-reporting
surfaces as `cov` rather than being corrected by shrinking the
denominator.

Agencies are placed by `fips_state_county_code`, with three corrections
the file supports and an earlier build ignored:

- **Agencies spanning counties** are split by population share, using
  `population_1/2/3` and `population_1/2/3_county`. That last column is
  UCR's own sequential county number, not FIPS — Jefferson AL is UCR 37
  and FIPS 073 — and the crosswalk between them is derived from the file
  itself rather than guessed.
- **Agencies whose crime is filed by a parent** (`covered_by_ori`) count
  as covered, not as missing. They report zero months, so treating them
  as non-reporting understated coverage for about 5 million people in
  2023 alone.
- **Agencies with no county of their own** — state police, multi-county
  districts — are placed rather than discarded. Where they name the
  agencies they file for, those counties are exact; otherwise they are
  spread over the population each county did *not* cover with a local
  agency, which is who a state force serves. Dropping them had Alaska's
  counties summing to 63% of the state's population, inflating every
  Alaskan rate by about 1.6x.

`violent` and `property` are summed from their components rather than
read from the file's index columns, which disagreed with their own parts
in 18% of county-years.

### Estimated figures, and how to tell

Roughly 337,000 agency-years were never filed in full. They are now
estimated rather than dropped, in three tiers — scaling a part-year to
twelve months, filling from the agency's own neighbouring years, and
failing those, borrowing from agencies of the same type and size in the
same state and year. For 1960–2019 the estimates are then **calibrated**
so that filed + estimated equals the FBI's published state total.

This is a deliberate change of policy. Earlier builds refused to
fabricate any figure the FBI never published. The rule now is narrower
and, we think, more useful: **nothing is fabricated invisibly.**

- `cov` — share of population whose agency filed a full year. Unchanged
  in meaning, and never used to rescale a rate.
- `imp` — share of the offences on screen that nobody filed for that
  county.

The two are independent. A county nobody reported for has `cov` 0 and
`imp` 1; it is drawn at full height, tinted violet, and says so in the
tooltip. About **25% of county-years fall below 80% coverage** and are
drawn de-saturated; about **14% are majority estimated**.

Calibration is **not** applied from 2020 on. There, `states.json` carries
a participation sum rather than an FBI estimate — California 2021 covers
26% of the state — so anchoring to it would assert a known undercount as
ground truth. Those years keep their uncalibrated estimates, and
`make validate` reports them separately.

`make validate` checks all of this against the defects that shipped
before, and reports the calibration factor `k` per state-year-crime.
Where `k` would exceed 5 it is capped and the remainder left
unallocated: Illinois' published burglary totals sit far above anything
its agencies filed, and closing that gap by scaling a few small
departments would put Chicago's crime on small towns.

### Sanity check

National 2023 works out to 346.1 violent offences per 100,000 against the
FBI's published ~364. The ~5% gap is expected: Return A applies the
hierarchy rule (only the most serious offence per incident) while the CDE
summarized figures do not. Compared directly on the same VT/NH/ME
agencies, Return A ran 5.1% below the API for 2023.

### Per-state county builds via the API

Still supported, and useful for fresher NIBRS-era data than a yearly bulk
release carries. It emits the older verbose record format, which the
viewer still reads:

```bash
make discover                # once
make counties STATES=VT      # per-agency requests, ~4s each
```

### County caveats are worse than state caveats

Jacob Kaplan's [chapter on county-level UCR data](https://ucrbook.com/county-level-data.html)
recommends against using these aggregates at all. The specific hazards:

- **Overlapping jurisdictions.** City police and county sheriffs operate
  in the same county. UCR assigns population to the most local agency
  and zero to the overlapping one, so summing works — but only if every
  agency reports.
- **Non-reporting is indistinguishable from zero crime.** This is what
  `cov` and `imp` exist to separate, and neither is a substitute for
  knowing that the underlying figure was never filed.
- **Agencies straddle county lines.** Split by population share where
  the file records the split; a single-county agency in the wrong county
  stays in the wrong county.

Two places are drawn as one unit because one agency reports for all of
them, and no honest split exists: **New York City** (NYPD files one
return for 8.2 million people across five counties — attributed to
Manhattan, it made that pillar carry all five boroughs while the other
four sat empty in every year) and **Puerto Rico**. American Samoa, Guam,
the Northern Marianas and the US Virgin Islands have between 8 and 88
agency-years each across the whole 65-year span, and are not drawn at
all rather than left as a dozen permanent holes.

**Puerto Rico's figures after 2020 are not trustworthy.** Its agencies
file twelve months and report almost nothing — 466 violent offences for
3.2 million people in 2023, against 6,479 in 2019. Neither defence
catches it: the implausible-collapse check compares an agency against its
own neighbouring years and the collapse is sustained across all of them,
and PR has no published state total to calibrate against. It is shown as
filed, and it is wrong. The same entry is used for State and County mode,
so at least the two agree.

County codes are mapped onto current geometry — Shannon SD to Oglala
Lakota, Bedford City VA into Bedford County, and four more. The viewer
used to drop any county key with no polygon without a word, which is how
17 years of one and 64 of the other went missing; it now counts them and
says so in the Status panel.

In the bulk import, agencies are placed by the **county FIPS the file
already carries**, so no geocoding is involved and nothing is inferred.
The per-state API path has no such column and falls back to
point-in-polygon against real county geometry, then to county-name
matching; that build prints how many resolved each way, and a large
name-matched share is a reason for suspicion.

Note that Return A's own `latitude`/`longitude` are **county centroids** —
every agency in a county shares one point — which is why this project
draws counties rather than agency points.

---

## Layout

```
Makefile                 build targets
.env.example             API key template
.github/workflows/
  pages.yml              test, build and deploy to GitHub Pages
scripts/
  common.py              HTTP w/ cache, throttle, retry; geometry maths
  fetch_geometry.py      state + county polygons, precomputed areas
  build_states.py        SRS + NIBRS -> data/states.json
  discover_sources.py    probe agency-level routes (run before the two below)
  inspect_bulk.py        read-only schema report for a bulk download
  import_bulk.py         Return A bulk file -> data/counties.json
  build_counties.py      agency -> county aggregation
  sync-data.mjs          copies data/ into web/public/data before dev/build
tests/
  test_pipeline.py       offline self-tests for the Python pipeline
web/
  index.html             markup, styles and the entry <script>
  src/                   the viewer, as TypeScript modules
    main.ts              boot
    load.ts              fetch helpers; absent (404) vs failed
    map.ts               MapLibre + deck overlay, basemap watchdog
    data.ts              joins data/*.json into the render stores
    counties.ts          the county layer, fetched on first click
    render.ts            deck.gl layer construction
    scales.ts            colour ramps, fixed domains, fade threshold
    status.ts            the "what is working right now" panel
    ui.ts                controls and their event wiring
    *.test.ts            vitest, run with npm test
```

The viewer needs to be **served**, not opened as a file — browsers block
local file reads from `file://` origins. `npm run dev` handles it.

deck.gl and MapLibre are npm dependencies bundled into the build, not CDN
script tags, so the only host contacted at runtime is the basemap
(`basemaps.cartocdn.com`). Everything else is same-origin.

---

## Controls

| Control | Effect |
|---------|--------|
| Geographic area | State, or County — the county layer (4.2 MB gzipped) is fetched on first click, not at startup |
| Crime type | 9 types: violent/property totals, homicide, rape, robbery, aggravated assault, burglary, larceny, motor vehicle theft |
| Year | 1960–present; orange tick marks the SRS/NIBRS boundary |
| Height exaggeration | Scales pillar height |
| Height scale | *Per crime type* (peak for that type) or *Absolute* (peak across all types) |
| Density colours | Green→red, or two colour-blind-safe ramps |

Both height and colour use **fixed domains computed across every year**,
so dragging the year slider shows genuine change instead of re-scaling
underneath you. The legend reports where the selected year sits within
the fixed range.

---

## Sources

- [FBI Crime Data Explorer](https://cde.ucr.cjis.gov/) — NIBRS-era data
- [CORGIS State Crime dataset](https://corgis-edu.github.io/corgis/csv/state_crime/) — SRS 1960–2019
- [us-atlas](https://github.com/topojson/us-atlas) — TopoJSON geometry
- [Decoding FBI Crime Data](https://ucrbook.com/) — Jacob Kaplan; read
  chapter 10 before trusting anything county-level

Crime data is informational only. It reflects offences *reported to
police*, which is not the same as offences committed.
