#!/usr/bin/env python3
"""
Build data/counties.json by aggregating agency-level crime up to counties.

READ THIS BEFORE TRUSTING THE OUTPUT
------------------------------------
County-level UCR data is genuinely problematic, and this script does not
make it less so. Jacob Kaplan's chapter on it recommends against using
county aggregates at all:

    https://ucrbook.com/county-level-data.html

The specific hazards, all of which this script measures and reports
rather than hides:

  1. Jurisdictions overlap. A city police department and the county
     sheriff both operate inside the same county. UCR handles this by
     assigning population to the most local agency and zero to the
     overlapping one, so summing agency populations is roughly right -
     but only if every agency reports.

  2. Non-reporting looks identical to zero crime. An agency that
     reported nothing and an agency with no crime are the same row. The
     coverage ratio below is the only defence.

  3. Agencies straddle county lines. State police, university police
     and multi-county districts get assigned to a single county here.

  4. Coverage varies enormously by county and year, far more than at
     state level.

Every county-year therefore carries `cov`, the share of the county's
population covered by agencies that actually reported. The viewer
de-saturates anything under 80%. Counties with no reporting agency at
all are omitted, not rendered as zero.

    python scripts/discover_sources.py      # run this FIRST
    python scripts/build_counties.py
    python scripts/build_counties.py --states VT,NH,ME   # small test run
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
    CACHE, CRIMES, DATA, NIBRS_FIRST, STATE_ABBR, ABBR_TO_NAME,
    Progress, bbox_of, die, fetch, fold_monthly, human_size, log, ok,
    roster_items,
    point_in_geometry, require_api_key, step, warn, write_json,
)

STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15",
    "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56",
}


# ---------------------------------------------------------------------
# agency roster
# ---------------------------------------------------------------------
def load_sources() -> dict:
    p = CACHE / "sources.json"
    if not p.exists():
        die("No .cache/sources.json - run:  python scripts/discover_sources.py")
    return json.loads(p.read_text(encoding="utf-8"))


def get_field(d: dict, *names):
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    lower = {k.lower(): v for k, v in d.items()}
    for n in names:
        v = lower.get(n.lower())
        if v not in (None, ""):
            return v
    return None


def fetch_agencies(key: str, template: str, states: list) -> list:
    """Pull the agency roster for each state. 51 cheap requests."""
    step("Agency roster")
    agencies = []
    p = Progress(len(states), "roster  ")
    for abbr in states:
        url = template.format(st=abbr, key=key)
        try:
            data = fetch(url, expect_json=True)
        except Exception as e:  # noqa: BLE001
            warn("%s roster failed: %s" % (abbr, e))
            p.tick()
            continue
        if not data:
            p.tick()
            continue
        items = roster_items(data)
        for a in items:
            if not isinstance(a, dict):
                continue
            ori = get_field(a, "ori", "ori9", "ORI")
            if not ori:
                continue
            agencies.append({
                "ori": ori,
                "state": abbr,
                "name": get_field(a, "agency_name", "name") or "",
                "type": get_field(a, "agency_type_name", "agency_type") or "",
                "county_name": get_field(a, "counties", "county_name", "county") or "",
                "lat": _f(get_field(a, "latitude", "lat")),
                "lon": _f(get_field(a, "longitude", "longitude_", "lon", "lng")),
                # The roster carries no population. It arrives per year with
                # the offence payload instead, so this stays None here.
                "pop": _i(get_field(a, "population", "agency_population")),
            })
        p.tick("%s %d" % (abbr, len(items)))
    p.done()
    ok("%d agencies" % len(agencies))
    return agencies


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------
# agency -> county assignment
# ---------------------------------------------------------------------
def assign_counties(agencies: list, counties: dict) -> dict:
    """Place each agency in a county.

    Coordinates first (unambiguous), falling back to county-name matching
    within the agency's state. Reports how many resolved each way, and
    how many could not be placed at all.
    """
    step("Assigning agencies to counties")

    # Index counties by state FIPS, with bounding boxes for pre-filtering.
    by_state: dict = {}
    for fips, c in counties.items():
        by_state.setdefault(c["state_fips"], []).append(
            (fips, c, bbox_of(c["geometry"])))

    # Name index for the fallback path.
    name_idx: dict = {}
    for fips, c in counties.items():
        key = (c["state_fips"], _norm_county(c["name"]))
        name_idx.setdefault(key, fips)

    stats = {"geo": 0, "name": 0, "unplaced": 0}
    p = Progress(len(agencies), "assign  ")
    for a in agencies:
        sf = STATE_FIPS.get(a["state"])
        a["fips"] = None
        if not sf:
            stats["unplaced"] += 1
            p.tick()
            continue

        # 1. geometric placement
        if a["lat"] is not None and a["lon"] is not None:
            x, y = a["lon"], a["lat"]
            for fips, c, bb in by_state.get(sf, ()):
                if bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]:
                    if point_in_geometry(x, y, c["geometry"]):
                        a["fips"] = fips
                        stats["geo"] += 1
                        break

        # 2. name fallback
        if not a["fips"] and a["county_name"]:
            # A few agencies list several counties, comma separated;
            # the first is treated as primary.
            first = a["county_name"].split(",")[0]
            a["fips"] = name_idx.get((sf, _norm_county(first)))
            if a["fips"]:
                stats["name"] += 1

        if not a["fips"]:
            stats["unplaced"] += 1
        p.tick()
    p.done()

    total = max(1, len(agencies))
    ok("placed by coordinates: %d (%.1f%%)" % (stats["geo"], stats["geo"] / total * 100))
    ok("placed by county name: %d (%.1f%%)" % (stats["name"], stats["name"] / total * 100))
    if stats["unplaced"]:
        warn("unplaced: %d (%.1f%%) - excluded from county totals"
             % (stats["unplaced"], stats["unplaced"] / total * 100))
    return stats


def _norm_county(s: str) -> str:
    s = (s or "").strip().lower()
    for suffix in (" county", " parish", " borough", " census area",
                   " city and borough", " municipality", " city"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s.replace(".", "").replace("'", "").replace("-", " ").strip()


# ---------------------------------------------------------------------
# offence counts per agency
# ---------------------------------------------------------------------
def fetch_agency_offenses(key: str, agencies: list, crimes: list,
                          first: int, last: int) -> dict:
    """Pull per-agency offence counts through the CDE API.

    Deliberately gated: this is len(agencies) x len(crimes) requests. For
    all ~19,000 agencies that is six figures of calls, which no key
    allows. Intended for a handful of states at a time.
    """
    total = len(agencies) * len(crimes)
    step("Agency offence counts (%d requests)" % total)
    if total > 8000:
        die("%d requests is beyond a sane budget.\n"
            "Narrow it with --states, e.g.:\n"
            "  python scripts/build_counties.py --states VT,NH,ME\n"
            "or supply a bulk file (see discover_sources.py)." % total)

    out: dict = {}
    p = Progress(total, "offences")
    for c in crimes:
        for a in agencies:
            q = urllib.parse.urlencode({
                "from": "01-%d" % first, "to": "12-%d" % last, "API_KEY": key})
            url = ("https://api.usa.gov/crime/fbi/cde/summarized/agency/%s/%s?%s"
                   % (a["ori"], c["cde"], q))
            try:
                payload = fetch(url, expect_json=True)
                folded = fold_monthly(payload)
            except Exception:  # noqa: BLE001
                folded = None
            if folded and folded["years"]:
                for row in folded["years"]:
                    out.setdefault(a["ori"], {}).setdefault(row["year"], {})[c["id"]] = {
                        "total": row["total"], "pop": row["pop"], "cov": row["cov"]}
            p.tick()
    p.done()
    return out


# ---------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------
def aggregate(agencies, per_agency, counties):
    """Sum agency counts into counties and compute coverage."""
    step("Aggregating to counties")

    # County population reference from the agencies themselves. UCR
    # assigns population to the most local agency and zero to overlapping
    # ones, so summing over a county approximates its population.
    #
    # The roster carries no population field, so it has to come from the
    # offence payloads: each agency's largest population seen across the
    # year range. Taking it from the roster instead would leave every
    # reference at zero, the denominator would collapse to "population
    # that reported", and coverage would read a reassuring 100% for every
    # county in every year - the one safeguard this module has, silently
    # disabled. Agencies that never reported at all contribute nothing,
    # so this reference is an upper bound on coverage, not an exact one.
    agency_pop: dict = {}
    for ori, rows in per_agency.items():
        best = 0
        for by_crime in rows.values():
            for v in by_crime.values():
                if v.get("pop") and v["pop"] > best:
                    best = v["pop"]
        agency_pop[ori] = best

    county_ref: dict = {}
    for a in agencies:
        if not a.get("fips"):
            continue
        county_ref.setdefault(a["fips"], {"pop": 0, "agencies": 0})
        county_ref[a["fips"]]["agencies"] += 1
        county_ref[a["fips"]]["pop"] += agency_pop.get(a["ori"], 0) or 0

    out: dict = {}
    for a in agencies:
        fips = a.get("fips")
        if not fips:
            continue
        rows = per_agency.get(a["ori"]) or {}
        for year, by_crime in rows.items():
            rec = out.setdefault(fips, {}).setdefault(year, {
                "totals": {}, "pop": 0, "reported_pop": 0, "agencies": 0,
                "reporting": 0})
            rec["agencies"] = county_ref[fips]["agencies"]
            for cid, v in by_crime.items():
                rec["totals"][cid] = rec["totals"].get(cid, 0) + v["total"]
            # Population credited to agencies that actually reported.
            any_pop = next((v["pop"] for v in by_crime.values() if v.get("pop")), 0)
            rec["reported_pop"] += any_pop or 0
            rec["reporting"] += 1

    # Finalise: rates, coverage, drop empties.
    final: dict = {}
    dropped = 0
    for fips, by_year in out.items():
        ref_pop = county_ref.get(fips, {}).get("pop", 0)
        for year, rec in by_year.items():
            pop = ref_pop or rec["reported_pop"]
            if pop <= 0:
                dropped += 1
                continue
            cov = min(100.0, rec["reported_pop"] / pop * 100) if pop else 0
            rates = {cid: round(t / pop * 100000, 3)
                     for cid, t in rec["totals"].items()}
            final.setdefault(fips, {})[str(year)] = {
                "pop": int(pop),
                "cov": round(cov, 2),
                "src": "NIBRS",
                "rates": rates,
                "totals": rec["totals"],
                "agencies": rec["agencies"],
                "reporting": rec["reporting"],
            }

    if dropped:
        warn("%d county-years dropped for zero population" % dropped)

    low = sum(1 for c in final.values() for r in c.values() if r["cov"] < 80)
    tot = sum(len(c) for c in final.values())
    ok("%d counties, %d county-years" % (len(final), tot))
    if tot:
        warn("%d of %d county-years (%.0f%%) below 80%% coverage - "
             "de-saturated in the viewer" % (low, tot, low / tot * 100))
    return final


# ---------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default="",
                    help="comma-separated abbreviations, e.g. VT,NH,ME")
    ap.add_argument("--crimes", default="",
                    help="comma-separated crime ids; default all")
    ap.add_argument("--from-year", type=int, default=NIBRS_FIRST)
    ap.add_argument("--to-year", type=int, default=datetime.date.today().year)
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

    states = [s.strip().upper() for s in args.states.split(",") if s.strip()] \
        or list(STATE_ABBR)
    crimes = [c for c in CRIMES
              if not args.crimes or c["id"] in
              {x.strip() for x in args.crimes.split(",")}]

    key = require_api_key()
    agencies = fetch_agencies(key, roster["template"], states)
    if not agencies:
        die("No agencies returned - cannot continue.")

    assign_counties(agencies, counties)
    placed = [a for a in agencies if a.get("fips")]

    per_agency = fetch_agency_offenses(
        key, placed, crimes, args.from_year, args.to_year)
    final = aggregate(placed, per_agency, counties)

    payload = {
        "meta": {
            "generated": datetime.datetime.now(datetime.timezone.utc)
                          .isoformat(timespec="seconds"),
            "states": states,
            "crimes": [c["id"] for c in crimes],
            "yearRange": [args.from_year, args.to_year],
            "coverageMin": 80,
            "caveat": (
                "County figures are summed from agency reports. Overlapping "
                "jurisdictions, agencies straddling county lines, and "
                "non-reporting agencies all distort them. See "
                "https://ucrbook.com/county-level-data.html"
            ),
        },
        "counties": final,
    }
    p = write_json(DATA / "counties.json", payload)
    step("Wrote %s (%s)" % (p.name, human_size(p)))


if __name__ == "__main__":
    main()
