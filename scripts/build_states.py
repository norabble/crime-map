#!/usr/bin/env python3
"""
Build data/states.json — the complete state series, 1960 to present.

Two eras, merged:
  1960-2019  FBI Summary Reporting System, via the CORGIS state crime CSV
  2020+      FBI Crime Data Explorer API (NIBRS era)

Roughly 460 API calls on a cold run (9 crime types x 51 states), each
covering every year at once. Responses are cached, so a re-run is free
and an interrupted run resumes.

    python scripts/build_states.py
    python scripts/build_states.py --no-nibrs     # SRS only, no key needed
    python scripts/build_states.py --refresh      # ignore cache
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CRIMES, DATA, NIBRS_FIRST, SRS_LAST, STATE_ABBR, ABBR_TO_NAME,
    Progress, cde_state_url, die, fetch, fold_monthly, human_size, log, ok,
    parse_csv, require_api_key, step, warn, write_json,
)

SRS_CSV_URL = "https://corgis-edu.github.io/corgis/datasets/csv/state_crime/state_crime.csv"


def load_srs(refresh: bool) -> dict:
    """Parse the CORGIS CSV into {state: {year: record}}."""
    step("Fetching SRS series 1960-%d" % SRS_LAST)
    text = fetch(SRS_CSV_URL, cache=not refresh, throttle=False)
    if not text:
        die("Could not download the CORGIS state crime CSV.")

    rows = parse_csv(text)
    if not rows:
        die("CORGIS CSV parsed to zero rows - the format may have changed.")

    out: dict = {}
    skipped_rollup = 0
    for r in rows:
        state = (r.get("State") or "").strip()
        if not state:
            continue
        # The file carries a rolled-up national row; it is not a state.
        if state == "United States":
            skipped_rollup += 1
            continue
        try:
            year = int(r.get("Year") or 0)
        except ValueError:
            continue
        if not year or year > SRS_LAST:
            continue

        rates, totals = {}, {}
        for c in CRIMES:
            try:
                rates[c["id"]] = float(r[c["srs_rate"]])
            except (KeyError, TypeError, ValueError):
                pass
            try:
                totals[c["id"]] = int(float(r[c["srs_total"]]))
            except (KeyError, TypeError, ValueError):
                pass
        try:
            pop = int(float(r.get("Data.Population") or 0))
        except ValueError:
            pop = 0
        if pop <= 0:
            continue

        out.setdefault(state, {})[year] = {
            "pop": pop, "cov": None, "src": "SRS",
            "rates": rates, "totals": totals,
        }

    years = sorted({y for st in out.values() for y in st})
    ok("%d states, %d-%d (%d rollup rows skipped)"
       % (len(out), years[0], years[-1], skipped_rollup))
    return out


def load_nibrs(merged: dict, refresh: bool) -> dict:
    """Pull every crime type for every state from the CDE API and merge."""
    key = require_api_key()
    last_year = datetime.date.today().year
    step("Fetching NIBRS series %d-%d from the Crime Data Explorer"
         % (NIBRS_FIRST, last_year))
    log("  %d crime types x %d states = %d requests (cached after the first run)"
        % (len(CRIMES), len(STATE_ABBR), len(CRIMES) * len(STATE_ABBR)))

    stats = {"ok": 0, "empty": 0, "failed": 0}
    unmatched: set = set()

    for c in CRIMES:
        p = Progress(len(STATE_ABBR), c["id"].ljust(9))
        for abbr in STATE_ABBR:
            url = cde_state_url(abbr, c["cde"], key, NIBRS_FIRST, last_year)
            try:
                payload = fetch(url, cache=not refresh, expect_json=True)
            except Exception as e:  # noqa: BLE001
                warn("%s/%s failed: %s" % (abbr, c["id"], e))
                stats["failed"] += 1
                p.tick()
                continue

            folded = fold_monthly(payload)
            if not folded or not folded["years"]:
                stats["empty"] += 1
                p.tick()
                continue

            name = folded["name"]
            # Prefer our own abbreviation mapping if the API's label drifts.
            if name not in merged and ABBR_TO_NAME.get(abbr) in merged:
                name = ABBR_TO_NAME[abbr]
            if name not in merged:
                unmatched.add(name)
                merged[name] = {}

            for row in folded["years"]:
                rec = merged[name].setdefault(row["year"], {
                    "pop": row["pop"], "cov": row["cov"], "src": "NIBRS",
                    "rates": {}, "totals": {},
                })
                rec["pop"] = row["pop"]
                rec["src"] = "NIBRS"
                if row["cov"] is not None:
                    rec["cov"] = row["cov"]
                rec["rates"][c["id"]] = row["rate"]
                rec["totals"][c["id"]] = row["total"]

            stats["ok"] += 1
            p.tick(name[:18])
        p.done()

    if unmatched:
        warn("names present in CDE but not in the SRS series: %s"
             % ", ".join(sorted(unmatched)))
    ok("%d ok, %d empty, %d failed" % (stats["ok"], stats["empty"], stats["failed"]))
    return merged


def summarise(merged: dict) -> dict:
    """Precompute the fixed scale domains the viewer needs."""
    crime_max: dict = {c["id"]: 0.0 for c in CRIMES}
    years: set = set()
    nibrs_years: set = set()
    low_cov = 0
    total_recs = 0

    for state, by_year in merged.items():
        for year, rec in by_year.items():
            years.add(year)
            total_recs += 1
            if rec["src"] == "NIBRS":
                nibrs_years.add(year)
            if rec.get("cov") is not None and rec["cov"] < 80:
                low_cov += 1
            for cid, v in rec["rates"].items():
                if v > crime_max.get(cid, 0):
                    crime_max[cid] = v

    return {
        "years": sorted(years),
        "nibrsYears": sorted(nibrs_years),
        "crimeMax": {k: round(v, 3) for k, v in crime_max.items()},
        "records": total_recs,
        "lowCoverageRecords": low_cov,
    }


def territories_from_counties() -> dict:
    """State-layer entries for places the CDE has no state figures for.

    Puerto Rico is in the geometry and in Return A, but not in the CDE
    state series, so State mode drew it as a permanent hole while County
    mode had 3.2 million people in it. Rather than a second source, the
    entry is read straight back off the county layer's own merged unit -
    one code path, so the two modes cannot disagree about the number.

    Requires data/counties.json, which is why this is a separate,
    keyless step: `make states` needs an API key, and this does not.
    """
    path = DATA / "counties.json"
    if not path.exists():
        warn("No counties.json - skipping territory entries")
        return {}
    doc = json.loads(path.read_text())
    ix = {name: i for i, name in enumerate(doc["meta"]["record"])}
    order = doc["meta"]["crimeOrder"]
    out: dict = {}
    for key, name in (doc["meta"].get("merged") or {}).items():
        # Only whole-territory merges belong in the state layer. New York
        # City is a merged county unit, not a state.
        if key not in ("72999",):
            continue
        by_year = {}
        for y, r in doc["counties"].get(key, {}).items():
            pop = r[ix["pop"]]
            if pop <= 0:
                continue
            totals = {c: r[ix[c]] for c in order}
            by_year[y] = {
                "pop": pop,
                "cov": r[ix["cov"]],
                "imp": r[ix["imp"]] if "imp" in ix else 0,
                "src": "NIBRS" if int(y) > SRS_LAST else "SRS",
                "rates": {c: round(v / pop * 100000, 3) for c, v in totals.items()},
                "totals": totals,
            }
        if by_year:
            out[name] = dict(sorted(by_year.items()))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-nibrs", action="store_true",
                    help="skip the API pull; build SRS 1960-2019 only")
    ap.add_argument("--refresh", action="store_true",
                    help="ignore the on-disk cache and refetch")
    ap.add_argument("--territories-only", action="store_true",
                    help="add territory entries to an existing states.json "
                         "from the county layer; no API key needed")
    args = ap.parse_args()

    if args.territories_only:
        out = DATA / "states.json"
        payload = json.loads(out.read_text())
        extra = territories_from_counties()
        payload["states"].update(extra)
        payload["states"] = dict(sorted(payload["states"].items()))
        write_json(out, payload)
        step("Added %d territory entries: %s"
             % (len(extra), ", ".join(extra) or "none"))
        return

    merged = load_srs(args.refresh)
    if not args.no_nibrs:
        merged = load_nibrs(merged, args.refresh)
    else:
        warn("--no-nibrs: output will stop at %d" % SRS_LAST)

    meta = summarise(merged)

    # Serialise years as strings; JSON object keys must be strings anyway,
    # and this keeps the viewer's parsing unambiguous.
    payload = {
        "meta": {
            "generated": datetime.datetime.now(datetime.timezone.utc)
                          .isoformat(timespec="seconds"),
            "srsLast": SRS_LAST,
            "nibrsFirst": NIBRS_FIRST,
            "coverageMin": 80,
            "crimes": [{"id": c["id"], "label": c["label"]} for c in CRIMES],
            "years": meta["years"],
            "nibrsYears": meta["nibrsYears"],
            "crimeMax": meta["crimeMax"],
            "sources": {
                "srs": SRS_CSV_URL,
                "nibrs": "https://api.usa.gov/crime/fbi/cde/ (FBI Crime Data Explorer)",
            },
        },
        "states": {
            state: {str(y): rec for y, rec in sorted(by_year.items())}
            for state, by_year in sorted(merged.items())
        },
    }

    out = DATA / "states.json"
    write_json(out, payload)

    step("Wrote %s" % out.relative_to(out.parent.parent))
    log("  %d states, years %d-%d, %d records"
        % (len(payload["states"]), meta["years"][0], meta["years"][-1],
           meta["records"]))
    log("  %d records below 80%% reporting coverage (flagged in the viewer)"
        % meta["lowCoverageRecords"])
    log("  %s" % human_size(out))


if __name__ == "__main__":
    main()
