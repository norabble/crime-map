#!/usr/bin/env python3
"""
Build data/agencies.json - crime rates for individual reporting agencies.

WHY AGENCIES RATHER THAN COUNTIES
---------------------------------
The agency is the unit UCR actually collects. A city police department and
the county sheriff around it are separate reporters, each with its own
served population. Rendering them directly shows the urban/rural contrast
without inventing a geographic aggregation, which sidesteps the objections
in https://ucrbook.com/county-level-data.html rather than inheriting them:
nothing here sums across overlapping jurisdictions.

The trade is a different set of hazards, all of which this script measures
and records in `meta` rather than hiding:

  1. Zero-population agencies. UCR assigns population to the most local
     agency, so state police, university and transit forces are credited
     with 0 residents. Their rate is undefined, not infinite. Dropped.

  2. Tiny-population noise. A 500-resident town with two assaults reads as
     400/100k and would dominate a fixed height scale shared with cities.
     --min-pop drops these; the threshold is recorded in `meta`.

  3. Non-reporting is indistinguishable from zero crime, exactly as at
     state level. `cov` is carried per agency-year and the viewer
     de-saturates anything below 80%.

  4. Agencies straddle county lines. The county assignment here is only
     used for the density colour channel, never to sum offences.

    python scripts/discover_sources.py          # run this FIRST
    python scripts/build_agencies.py --states VT
    python scripts/build_agencies.py --states VT,NH,ME --crimes violent,property
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CRIMES, DATA, NIBRS_FIRST, Progress, die, fetch, fold_monthly,
    human_size, ok, require_api_key, step, warn, write_json,
)
from build_counties import (  # noqa: E402
    assign_counties, fetch_agencies, load_sources,
)


def fetch_agency_offenses(key, agencies, crimes, first, last, max_requests):
    """Pull per-agency offence counts. len(agencies) x len(crimes) calls."""
    total = len(agencies) * len(crimes)
    step("Agency offence counts (%d requests)" % total)
    if total > max_requests:
        die("%d requests exceeds --max-requests (%d).\n"
            "Narrow it, e.g.:\n"
            "  --states VT\n"
            "  --crimes violent,property\n"
            "At the built-in 900/hour throttle that would take about %.1f hours."
            % (total, max_requests, total * 4.0 / 3600))

    out: dict = {}
    p = Progress(total, "offences")
    for c in crimes:
        for a in agencies:
            q = urllib.parse.urlencode({
                "from": "01-%d" % first, "to": "12-%d" % last, "API_KEY": key})
            url = ("https://api.usa.gov/crime/fbi/cde/summarized/agency/%s/%s?%s"
                   % (a["ori"], c["cde"], q))
            try:
                folded = fold_monthly(fetch(url, expect_json=True))
            except Exception:  # noqa: BLE001
                folded = None
            if folded and folded["years"]:
                for row in folded["years"]:
                    (out.setdefault(a["ori"], {})
                        .setdefault(row["year"], {})[c["id"]]) = row
            p.tick()
    p.done()
    ok("%d agencies returned data" % len(out))
    return out


def assemble(agencies, per_agency, counties, min_pop):
    """Turn per-agency folds into the viewer payload, dropping what cannot
    honestly be rendered and counting every drop."""
    step("Assembling")
    drops = {"no_data": 0, "zero_pop": 0, "below_min_pop": 0, "unplaced": 0}
    out: dict = {}

    for a in agencies:
        years_src = per_agency.get(a["ori"])
        if not years_src:
            drops["no_data"] += 1
            continue

        years: dict = {}
        for year, by_crime in years_src.items():
            # Population is identical across crimes for a given agency-year;
            # take it from whichever offence came back.
            pop = next((r["pop"] for r in by_crime.values() if r.get("pop")), 0)
            if not pop or pop <= 0:
                drops["zero_pop"] += 1
                continue
            if pop < min_pop:
                drops["below_min_pop"] += 1
                continue
            covs = [r["cov"] for r in by_crime.values() if r.get("cov") is not None]
            years[str(year)] = {
                "pop": int(pop),
                "cov": round(sum(covs) / len(covs), 2) if covs else None,
                "src": "NIBRS",
                "rates": {cid: r["rate"] for cid, r in by_crime.items()},
                "totals": {cid: r["total"] for cid, r in by_crime.items()},
            }

        if not years:
            continue
        if not a.get("fips"):
            drops["unplaced"] += 1

        county = counties.get(a.get("fips") or "")
        out[a["ori"]] = {
            "name": a["name"],
            "type": a.get("type") or "",
            "state": a["state"],
            "lat": a["lat"],
            "lon": a["lon"],
            "fips": a.get("fips"),
            "county": county["name"] if county else None,
            "years": years,
        }

    _attach_density(out, counties)
    ok("%d agencies with usable years" % len(out))
    # Two different units are being counted here, so they are labelled
    # separately rather than lumped together as one number.
    if drops["no_data"]:
        warn("%d agencies returned no complete year" % drops["no_data"])
    if drops["unplaced"]:
        warn("%d agencies had data but no county (colour falls back to none)"
             % drops["unplaced"])
    if drops["zero_pop"]:
        warn("%d agency-years dropped: zero served population" % drops["zero_pop"])
    if drops["below_min_pop"]:
        warn("%d agency-years dropped: served population below --min-pop"
             % drops["below_min_pop"])
    return out, drops


def _attach_density(out, counties):
    """Density for the colour channel.

    An agency has a served population but no area of its own, so its own
    density is not computable. UCR assigns population to the most local
    agency, which makes the sum of agency populations within a county a
    reasonable stand-in for county population - the same property
    build_counties.py relies on. That sum over the county's area is what
    colours the pillar. It is an approximation, and it is only ever a
    colour: no offence total is aggregated this way.
    """
    pop_by: dict = {}
    for rec in out.values():
        if not rec["fips"]:
            continue
        for year, yr in rec["years"].items():
            pop_by.setdefault((rec["fips"], year), 0)
            pop_by[(rec["fips"], year)] += yr["pop"]

    for rec in out.values():
        for year, yr in rec["years"].items():
            c = counties.get(rec["fips"] or "")
            area = (c or {}).get("area_sq_mi") or 0
            tot = pop_by.get((rec["fips"], year), 0)
            yr["dens"] = round(tot / area, 3) if area > 0 and tot else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default="VT,NH,ME",
                    help="comma-separated abbreviations; default VT,NH,ME")
    ap.add_argument("--crimes", default="",
                    help="comma-separated crime ids; default all")
    ap.add_argument("--from-year", type=int, default=NIBRS_FIRST)
    ap.add_argument("--to-year", type=int, default=datetime.date.today().year)
    ap.add_argument("--min-pop", type=int, default=1000,
                    help="drop agency-years serving fewer than this many "
                         "residents; per-capita rates are meaningless below "
                         "it (default 1000)")
    ap.add_argument("--max-requests", type=int, default=8000)
    args = ap.parse_args()

    sources = load_sources()
    roster = sources.get("agency_roster") or {}
    if not roster.get("template"):
        die("No working agency roster route in .cache/sources.json.\n"
            "Re-run: python scripts/discover_sources.py")

    geo_path = DATA / "geo-counties.json"
    if not geo_path.exists():
        die("Missing data/geo-counties.json - run: python scripts/fetch_geometry.py")
    counties = json.loads(geo_path.read_text(encoding="utf-8"))
    ok("%d county polygons loaded" % len(counties))

    states = [s.strip().upper() for s in args.states.split(",") if s.strip()]
    crimes = [c for c in CRIMES
              if not args.crimes or c["id"] in
              {x.strip() for x in args.crimes.split(",")}]
    if not crimes:
        die("No crime ids matched --crimes.")

    key = require_api_key()
    agencies = fetch_agencies(key, roster["template"], states)
    if not agencies:
        die("No agencies returned - cannot continue.")

    assign_counties(agencies, counties)
    per_agency = fetch_agency_offenses(
        key, agencies, crimes, args.from_year, args.to_year, args.max_requests)
    final, drops = assemble(agencies, per_agency, counties, args.min_pop)
    if not final:
        die("No agency produced usable data.")

    payload = {
        "meta": {
            "generated": datetime.datetime.now(datetime.timezone.utc)
                          .isoformat(timespec="seconds"),
            "states": states,
            "crimes": [c["id"] for c in crimes],
            "yearRange": [args.from_year, args.to_year],
            "coverageMin": 80,
            "minPop": args.min_pop,
            "agenciesInRoster": len(agencies),
            "agenciesWithData": len(final),
            "dropped": drops,
            "caveat": (
                "One pillar is one reporting agency, not an area. Population "
                "is the residents that agency serves, so a city force and the "
                "sheriff around it are separate. Agencies serving fewer than "
                "%d residents are omitted because per-capita rates are "
                "meaningless at that size, as are agencies credited with zero "
                "population (state police, campus and transit forces)."
                % args.min_pop
            ),
        },
        "agencies": final,
    }
    p = write_json(DATA / "agencies.json", payload)
    step("Wrote %s (%s)" % (p.name, human_size(p)))


if __name__ == "__main__":
    main()
