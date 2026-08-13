#!/usr/bin/env python3
"""
Build a national county layer from the Return A bulk file.

Source: openICPSR 100707, "Jacob Kaplan's Concatenated Files: Uniform
Crime Reporting Program Data: Offenses Known and Clearances by Arrest
(Return A), 1960-2024" - the CSV/yearly variant, left zipped in
.cache/bulk/.

Return A is agency-level (ORI) annual offence counts. It runs past the
retirement of SRS because the FBI converts NIBRS submissions into Return A
summary equivalents, so a single file covers both eras and no second
source is needed.

    python scripts/inspect_bulk.py        # ALWAYS run this first
    python scripts/import_bulk.py
    python scripts/import_bulk.py --no-impute      # measured data only
    python scripts/import_bulk.py --from-year 1990

This file is now an orchestrator; the work lives in three modules that
can be tested without a 74 MB download:

    returna.py    reads the CSV into a columnar agency-year table, and
                  derives the UCR-county -> FIPS crosswalk from it
    aggregate.py  folds agency-years into county-years, with the
                  covered-agency and multi-county corrections
    impute.py     estimates what was never filed, and calibrates the
                  estimates against published state totals
    fips.py       county identity: renames, merged units, what we serve

COLUMN CHOICES THAT ARE NOT OBVIOUS
-----------------------------------
Each was verified against API-built data for VT/NH/ME; none should be
changed on the strength of a column name. See returna.py for the full
set - the ones that cost the most to get wrong:

  * aggravated assault is `actual_assault_aggravated`, NOT
    `actual_assault_total`, which bundles simple assault and reads about
    ten times too high.
  * population is `population`, not `population_group` (a size band).
  * completeness is `number_of_months_reported`, not `..._missing`.
  * `population_N_county` is UCR's own sequential county number, NOT a
    FIPS code. Jefferson AL is UCR 37 and FIPS 073.

Coordinates in this file are county centroids - every agency in a county
shares one point - so it cannot place agency pillars, only counties.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aggregate import EST, POP, REP, aggregate, totals_for  # noqa: E402
from common import (  # noqa: E402
    CACHE, CRIMES, DATA, die, human_size, ok, step, warn, write_json,
)
from fips import merged_name  # noqa: E402
from impute import DEFAULT_MAX_GAP, DEFAULT_MIN_MONTHS, impute  # noqa: E402
from returna import load_resolved  # noqa: E402

BULK = CACHE / "bulk"

CRIME_ORDER = [c["id"] for c in CRIMES]
COVERAGE_MIN = 80


def find_source(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            die("No such file: %s" % p)
        return p
    if not BULK.exists():
        die("No %s - put the Return A CSV download there." % BULK)
    cands = [f for f in BULK.iterdir()
             if f.suffix.lower() in (".zip", ".csv", ".gz")]
    if not cands:
        die("No .zip/.csv/.gz in %s" % BULK)
    if len(cands) > 1:
        warn("%d candidates in %s; using %s" % (len(cands), BULK, cands[0].name))
    return cands[0]


def to_payload(acc: dict, counties_ix, first: int, last: int,
               stats: dict, imputed: bool) -> dict:
    """Compact encoding: one flat array per county-year.

    [pop, cov, imp, ...totals in meta.crimeOrder]

    The verbose per-record objects the API path emits cost ~360 bytes; at
    3,141 counties x 65 years that is 74 MB, which no browser should be
    asked to parse. Rates are derived in the viewer from totals and pop.

    `imp` is the share of the county-year's offences that nobody filed
    for that county - allocated across a multi-county reporter, or
    estimated outright. `cov` keeps its old meaning, the share of
    population that reported, and is never used to rescale a rate.
    """
    counties: dict = {}
    dropped = 0
    for (cty, year), rec in acc.items():
        pop = rec[POP]
        if pop <= 0 or (rec[REP] <= 0 and rec[EST] <= 0):
            # No population to divide by, or nothing at all to show -
            # neither a filed figure nor an estimate. Omitted, never
            # rendered as zero crime. The old build kept rows whose only
            # reporters had zero population, which is how Philadelphia
            # 2019 rendered as 7 violent crimes for 1.59M people.
            #
            # A county-year with no coverage but a full set of estimates
            # is kept deliberately: that is the honest shape of a place
            # nobody filed for, and `cov` and `imp` both say so.
            dropped += 1
            continue
        tot = totals_for(rec)
        n = sum(tot[c] for c in tot if c not in ("violent", "property"))
        counties.setdefault(counties_ix[cty], {})[str(year)] = [
            int(round(pop)),
            round(min(100.0, rec[REP] / pop * 100), 2),
            round(min(1.0, rec[EST] / n) if n > 0 else 0.0, 4),
        ] + [tot[c] for c in CRIME_ORDER]

    years = sorted({int(y) for c in counties.values() for y in c})
    meta = {
        "generated": datetime.datetime.now(datetime.timezone.utc)
                      .isoformat(timespec="seconds"),
        "format": "compact-2",
        "record": ["pop", "cov", "imp"] + CRIME_ORDER,
        "crimeOrder": CRIME_ORDER,
        "yearRange": [years[0], years[-1]] if years else [first, last],
        "coverageMin": COVERAGE_MIN,
        "imputed": imputed,
        "source": ("openICPSR 100707 - UCR Offenses Known and Clearances "
                   "by Arrest (Return A), 1960-2024"),
        "counties": len(counties),
        "countyYears": sum(len(v) for v in counties.values()),
        "merged": {k: merged_name(k) for k in counties if merged_name(k)},
        "stats": stats,
        "dropped": {"no_reporter": dropped},
        "caveat": (
            "County figures are summed from agency reports. Overlapping "
            "jurisdictions and non-reporting agencies distort them; see "
            "https://ucrbook.com/county-level-data.html. Agencies covering "
            "more than one county are split by population share. Agencies "
            "whose crime is filed by a parent agency count as covered, not "
            "as missing. Agencies with no county of their own are routed to "
            "the counties they file for. `cov` is the share of population "
            "that reported and is never used to rescale a rate; `imp` is "
            "the share of offences that were allocated or estimated rather "
            "than filed for that county. Aggravated assault is the "
            "aggravated column only. Violent and property totals are summed "
            "from their components."
        ),
    }
    return {"meta": meta, "counties": counties}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="path to the download; default .cache/bulk/*")
    ap.add_argument("--from-year", type=int, default=1960)
    ap.add_argument("--to-year", type=int, default=datetime.date.today().year)
    ap.add_argument("--out", default=str(DATA / "counties.json"))
    ap.add_argument("--no-impute", action="store_true",
                    help="measured and allocated figures only; no estimates")
    ap.add_argument("--impute-min-months", type=int, default=DEFAULT_MIN_MONTHS,
                    help="fewest reported months worth scaling to a full year")
    ap.add_argument("--impute-max-gap", type=int, default=DEFAULT_MAX_GAP,
                    help="longest run of missing years to bridge from an "
                         "agency's own history")
    args = ap.parse_args()

    src = find_source(args.file)
    t, stats = load_resolved(src, args.from_year, args.to_year)
    if not len(t):
        die("No rows in range %d-%d." % (args.from_year, args.to_year))

    step("Read")
    ok("%d agency-years, %d agencies, %d counties"
       % (len(t), len(t.oris), len(t.counties)))
    ok("%d placed in one county, %d split across counties (%d via crosswalk)"
       % (stats["single"], stats["multi"], stats["via_xwalk"]))
    if stats["negatives"]:
        warn("%d negative offence counts clamped to zero (unfounded "
             "adjustments)" % stats["negatives"])
    if stats["unplaceable"]:
        warn("%d agency-years have no county of their own - routed where "
             "they file for others" % stats["unplaceable"])

    est_rows = {}
    if not args.no_impute:
        step("Imputation")
        est_rows, istats = impute(t, min_months=args.impute_min_months,
                                  max_gap=args.impute_max_gap)
        stats.update(istats)
        for tier, n in sorted(istats.get("tiers", {}).items()):
            ok("tier %s: %d agency-years" % (tier, n))

    step("Aggregating")
    acc, astats = aggregate(t, est_rows)
    stats.update(astats)
    if astats["routed"]:
        ok("%d county-less agency-years routed to the counties they file for"
           % astats["routed"])
    if astats["spread"]:
        ok("%d county-less agency-years spread over their state's unserved "
           "population" % astats["spread"])
    if astats["routed_lost"]:
        warn("%d county-less agency-years could not be placed at all"
             % astats["routed_lost"])

    payload = to_payload(acc, t.counties.values, args.from_year, args.to_year,
                         stats, not args.no_impute)
    m = payload["meta"]

    ok("%d counties, %d county-years, %d-%d"
       % (m["counties"], m["countyYears"], m["yearRange"][0], m["yearRange"][1]))
    warn("%d county-years dropped: no agency reported" % m["dropped"]["no_reporter"])

    low = sum(1 for c in payload["counties"].values()
              for r in c.values() if r[1] < COVERAGE_MIN)
    tot = max(1, m["countyYears"])
    warn("%d of %d county-years (%.0f%%) below %d%% coverage - de-saturated"
         % (low, tot, low / tot * 100, COVERAGE_MIN))
    est = sum(1 for c in payload["counties"].values()
              for r in c.values() if r[2] >= 0.5)
    if est:
        warn("%d of %d county-years (%.0f%%) are majority estimated"
             % (est, tot, est / tot * 100))

    p = write_json(Path(args.out), payload)
    step("Wrote %s (%s)" % (p.name, human_size(p)))


if __name__ == "__main__":
    main()
