#!/usr/bin/env python3
"""
Offline self-tests for the pipeline's pure logic. No network required.

    python tests/test_pipeline.py

Covers the parts where a silent error would corrupt the output without
crashing: the monthly-to-annual fold, spherical area, point-in-polygon
agency placement, TopoJSON decoding, and county-name normalisation.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from common import (  # noqa: E402
    fold_monthly, geo_area_sq_mi, point_in_geometry, redact_url,
    topo_to_features,
)

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS " + msg)
    else:
        FAIL += 1
        print("  FAIL " + msg)


def section(name):
    print("\n[" + name + "]")


# ---------------------------------------------------------------------
def mk_response(name, years):
    """Build a payload shaped exactly like the live CDE response.

    `months` truncates the year to that many keys. `reported` is the shape
    the live API actually returns for the current year: all twelve keys
    present, with null in the months nobody has reported yet.
    """
    act, pop, cov = {}, {}, {}
    for year, cfg in years.items():
        months = cfg.get("months", 12)
        reported = cfg.get("reported")
        for m in range(1, months + 1):
            k = "%02d-%d" % (m, year)
            act[k] = None if (reported is not None and m > reported) else cfg["per"]
            pop[k] = cfg["pop"]
            if cfg.get("cov") is not None:
                cov[k] = cfg["cov"]
    return {
        "offenses": {"actuals": {name + " Offenses": act, name + " Clearances": {}}},
        "populations": {"population": {name: pop}},
        "tooltips": {"Percent of Population Coverage": {name: cov}},
    }


section("fold_monthly: annual rollup")
f = fold_monthly(mk_response("Vermont", {2023: {"per": 100, "pop": 1000000, "cov": 99}}))
ok(f and f["name"] == "Vermont", "state name parsed from the ' Offenses' key")
ok(f["years"][0]["total"] == 1200, "12 x 100 = 1200")
ok(abs(f["years"][0]["rate"] - 120) < 1e-6, "rate 120 per 100k")
ok(abs(f["years"][0]["cov"] - 99) < 1e-9, "coverage averaged to 99%")

section("fold_monthly: real published figures")
# Vermont 2023 violent crime, straight from the live API.
vt = [104, 105, 117, 113, 134, 112, 132, 127, 135, 114, 108, 97]
payload = {
    "offenses": {"actuals": {"Vermont Offenses":
                             {"%02d-2023" % (i + 1): v for i, v in enumerate(vt)}}},
    "populations": {"population": {"Vermont":
                                   {"%02d-2023" % (i + 1): 647464 for i in range(12)}}},
    "tooltips": {},
}
f = fold_monthly(payload)
ok(f["years"][0]["total"] == 1398, "violent total 1398 matches the API")
ok(abs(f["years"][0]["rate"] - 215.9) < 0.05,
   "rate %.1f matches published 215.9" % f["years"][0]["rate"])
ok(f["years"][0]["cov"] is None, "absent coverage block yields None, not a crash")

section("fold_monthly: partial years are dropped")
f = fold_monthly(mk_response("X", {
    2024: {"per": 100, "pop": 1000000, "cov": 99},
    2025: {"per": 100, "pop": 1000000, "cov": 99, "months": 7},
}))
ok(len(f["years"]) == 1 and f["years"][0]["year"] == 2024,
   "7-month 2025 dropped, complete 2024 kept")
ok(7 * 100 / 1000000 * 100000 < 120,
   "(kept, it would have read 70 vs 120 - a fabricated 42% drop)")

section("fold_monthly: null months are not reported months")
# The live API does not omit unreported months - it returns all twelve keys
# with null in the ones nobody has filed yet. Counting keys instead of
# reported values is what let 2026 through as a 60% crash.
f = fold_monthly(mk_response("X", {
    2025: {"per": 100, "pop": 1000000, "cov": 99},
    2026: {"per": 100, "pop": 1000000, "cov": 99, "reported": 7},
}))
ok(len(f["years"]) == 1 and f["years"][0]["year"] == 2025,
   "twelve keys with five nulls dropped, complete 2025 kept")

section("fold_monthly: a reported zero is data, not a gap")
# The counterpart trap: skipping on falsiness rather than `is None` would
# discard genuine zero-crime months and silently drop quiet years.
f = fold_monthly(mk_response("X", {
    2023: {"per": 0, "pop": 1000000, "cov": 99},
}))
ok(len(f["years"]) == 1, "twelve reported zeros kept as a complete year")
ok(f["years"][0]["total"] == 0 and f["years"][0]["rate"] == 0,
   "a genuinely crime-free year reads as 0, not as missing")

section("fold_monthly: a partly-null year with a real zero still drops")
f = fold_monthly(mk_response("X", {
    2026: {"per": 0, "pop": 1000000, "cov": 99, "reported": 6},
}))
ok(f["years"] == [], "six reported zeros + six nulls is still a partial year")

section("fold_monthly: degrades safely")
ok(fold_monthly(None) is None, "None -> None")
ok(fold_monthly({}) is None, "empty dict -> None")
ok(fold_monthly({"offenses": {"actuals": {}}}) is None, "no offences key -> None")
f = fold_monthly(mk_response("Y", {2023: {"per": 5, "pop": 0, "cov": 90}}))
ok(f["years"] == [], "zero population dropped, no ZeroDivisionError")

section("fold_monthly: rate uses total population")
# The FBI divides by full population even when coverage is partial.
# That is exactly why low-coverage years understate; we must not
# silently rescale, only surface the coverage figure.
f = fold_monthly(mk_response("Z", {2021: {"per": 50, "pop": 1000000, "cov": 60}}))
ok(abs(f["years"][0]["rate"] - 60) < 1e-6, "rate 60 from full population")
ok(f["years"][0]["cov"] == 60, "coverage 60% surfaced separately")

section("spherical area")
sphere = 4 * math.pi * 3958.7613 ** 2
ok(abs(sphere - 196_940_000) / 196_940_000 < 0.001,
   "full sphere %.0f sq mi (~196,940,000)" % sphere)
box = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}
analytic = (10 * math.pi / 180) * math.sin(10 * math.pi / 180) * 3958.7613 ** 2
ok(abs(geo_area_sq_mi(box) - analytic) / analytic < 1e-9, "10x10 degree box matches analytic")
co = {"type": "Polygon", "coordinates": [[[-109.05, 37], [-102.05, 37],
                                          [-102.05, 41], [-109.05, 41], [-109.05, 37]]]}
a = geo_area_sq_mi(co)
ok(abs(a - 104094) / 104094 < 0.02,
   "Colorado %.0f sq mi vs published 104,094" % a)
am = {"type": "Polygon", "coordinates": [[[175, 50], [-175, 50],
                                          [-175, 52], [175, 52], [175, 50]]]}
exp = (10 * math.pi / 180) * (math.sin(52 * math.pi / 180) -
                              math.sin(50 * math.pi / 180)) * 3958.7613 ** 2
ok(abs(geo_area_sq_mi(am) - exp) / exp < 1e-9,
   "antimeridian span read as 10 degrees, not 350 (Aleutians)")
hole = {"type": "Polygon", "coordinates": [
    [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
    [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]]}
ok(geo_area_sq_mi(hole) < geo_area_sq_mi(box), "holes subtract")
ok(geo_area_sq_mi({"type": "Point", "coordinates": [0, 0]}) == 0, "Point -> 0")

section("point_in_geometry: agency placement")
sq = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}
ok(point_in_geometry(5, 5, sq), "interior point inside")
ok(not point_in_geometry(15, 5, sq), "exterior point outside")
ok(not point_in_geometry(3, 3, hole), "point inside the hole (3,3) counts as outside")
ok(point_in_geometry(7, 7, hole), "point outside the hole counts as inside")
ok(point_in_geometry(5, 3, hole), "point beside the hole (5,3) is still inside")
mp = {"type": "MultiPolygon", "coordinates": [
    [[[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]],
    [[[20, 20], [25, 20], [25, 25], [20, 25], [20, 20]]]]}
ok(point_in_geometry(22, 22, mp), "second polygon of a MultiPolygon matches")
ok(not point_in_geometry(10, 10, mp), "gap between parts is outside")

section("TopoJSON decoding")
topo = {
    "type": "Topology",
    "transform": {"scale": [0.001, 0.001], "translate": [-100.0, 40.0]},
    "arcs": [[[0, 0], [1000, 0], [0, 1000], [-1000, 0], [0, -1000]]],
    "objects": {"units": {"type": "GeometryCollection", "geometries": [
        {"type": "Polygon", "arcs": [[0]], "id": "01", "properties": {"name": "Test"}}]}},
}
feats = topo_to_features(topo, "units")
ok(len(feats) == 1, "one feature decoded")
ring = feats[0]["geometry"]["coordinates"][0]
ok(abs(ring[0][0] - (-100.0)) < 1e-9 and abs(ring[0][1] - 40.0) < 1e-9,
   "delta decoding + transform applied: %s" % ring[0])
ok(abs(ring[1][0] - (-99.0)) < 1e-9, "cumulative deltas accumulate correctly")
ok(all(abs(p[0]) <= 180 and abs(p[1]) <= 90 for p in ring), "output is lon/lat")
try:
    topo_to_features(topo, "nope")
    ok(False, "missing object should raise")
except KeyError:
    ok(True, "missing object raises KeyError naming what is available")

section("county name normalisation")
sys.path.insert(0, str(ROOT / "scripts"))
from build_counties import _norm_county  # noqa: E402
ok(_norm_county("Orange County") == "orange", "'County' stripped")
ok(_norm_county("St. Louis city") == "st louis", "'city' stripped, punctuation dropped")
ok(_norm_county("Orleans Parish") == "orleans", "Louisiana parishes")
ok(_norm_county("Prince of Wales-Hyder Census Area") == "prince of wales hyder",
   "Alaska census areas, hyphens normalised")
ok(_norm_county("DeKalb") == "dekalb", "plain names pass through")
ok(_norm_county("") == "", "empty string safe")
ok(_norm_county(None) == "", "None safe")

section("county identity: renames, merges, territories")
from fips import canonical, merged_name, merged_parts  # noqa: E402
ok(canonical("50001") == "50001", "an ordinary county passes through")
ok(canonical("46113") == "46102", "Shannon SD renamed to Oglala Lakota")
ok(canonical("51515") == "51019", "Bedford City folded into Bedford County")
ok(canonical("57999") is None, "a code that is not a county is dropped")
ok(canonical("66010") is None, "Guam is not served")
ok(canonical("36061") == "36998" and canonical("36047") == "36998",
   "every NYC borough resolves to one unit")
ok(canonical("72127") == "72999", "any PR municipio resolves to one unit")
ok(canonical("2020") == "02020", "a 4-digit code is zero-padded, not discarded")
ok(canonical(None) is None and canonical("") is None, "empty input safe")
ok(merged_name("36998") == "New York City", "merged units are named")
# The state-prefix merge selects on a prefix its own key shares, so a
# second pass over already-merged geometry must not dissolve it again.
parts = merged_parts({"72001", "72003", "72999", "36998", "50001"})
ok("72999" not in parts.get("72999", []), "a merged unit is not a part of itself")
ok(sorted(parts["72999"]) == ["72001", "72003"], "PR parts are its municipios")
ok("36998" not in parts, "a merge with no remaining parts is not re-run")

section("aggregation: coverage, splits and county-less agencies")
from aggregate import C0, EST, POP, REP, aggregate, totals_for  # noqa: E402
from returna import BASE_CRIMES, AgencyTable  # noqa: E402


def mk_table(rows):
    """rows: (ori, year, fips, pop, months, cover_ori, {crime: n})"""
    t = AgencyTable()
    for ori, year, fips, pop, months, cover, counts in rows:
        t.ori.append(t.oris.index(ori))
        t.year.append(year)
        t.state.append(t.states.index("teststate"))
        t.atype.append(t.types.index("local police department"))
        t.group.append(t.groups.index("band"))
        t.pop.append(pop)
        t.months.append(months)
        for k, cid in enumerate(BASE_CRIMES):
            t.counts[k].append(counts.get(cid, 0))
        t.cty.append(t.counties.index(fips) if fips else -1)
        t.wgt.append(1.0)
        t._cover_raw.append(cover or "")
    for cb in t._cover_raw:
        t.cover.append(t.oris.get(cb) if cb else -1)
    t._cover_raw = []
    return t


# A covered agency's crime is inside its parent's row. Counting it as
# non-reporting is what drove ~5 million people's coverage to zero and
# produced counties at cov 0 whose crime was filed all along.
t = mk_table([
    ("parent", 2023, "10001", 8000, 12, None, {"murder": 4}),
    ("child",  2023, "10001", 2000,  0, "parent", {}),
])
acc, st = aggregate(t)
r = acc[(t.counties.get("10001"), 2023)]
ok(r[POP] == 10000, "a covered agency's population counts in the denominator")
ok(r[REP] == 10000, "and in the reporting share, so coverage reads 100%")
ok(totals_for(r)["murder"] == 4, "but adds no counts of its own - no double count")
ok(st["covered"] == 1, "and is reported as covered rather than missing")

# The same child with a parent that did NOT report is genuinely missing.
t = mk_table([
    ("parent", 2023, "10001", 8000, 0, None, {}),
    ("child",  2023, "10001", 2000, 0, "parent", {}),
])
acc, st = aggregate(t)
ok(acc[(t.counties.get("10001"), 2023)][REP] == 0,
   "a covered agency whose parent filed nothing is not covered")

# An agency with no county of its own is spread over the population its
# state's counties did not cover - not over their total population, which
# would put state-police crime in the cities they do not patrol.
t = mk_table([
    ("rural", 2023, "10001", 1000,  0, None, {}),          # nobody covers it
    ("city",  2023, "10002", 9000, 12, None, {"murder": 9}),
    ("state", 2023, None,    5000, 12, None, {"murder": 6}),
])
acc, st = aggregate(t)
rural = acc[(t.counties.get("10001"), 2023)]
city = acc[(t.counties.get("10002"), 2023)]
ok(st["spread"] == 1, "the county-less agency is placed, not discarded")
ok(rural[POP] == 6000 and city[POP] == 9000,
   "its population lands entirely on the unserved county")
ok(totals_for(rural)["murder"] == 6, "and so do its offences")
ok(rural[EST] == 6, "all of which are marked estimated - nobody filed them here")
ok(city[REP] == 9000 and rural[REP] == 5000,
   "the state force's own residents count as covered - it did file for them")
ok(rural[REP] < rural[POP],
   "but the county's unpoliced 1,000 still read as uncovered, not as 100%")

section("imputation tiers")
from impute import _collapsed, _series, impute  # noqa: E402

# Tier 1 scales a part-year to twelve months and records how much of the
# result was invented.
t = mk_table([("a", 2023, "10001", 12000, 6, None, {"murder": 5})])
est, st = impute(t, states_path=Path("/nonexistent"))
ok(t.counts[BASE_CRIMES.index("murder")][0] == 10, "6 months of 5 scales to 12 months of 10")
ok(abs(est[0] - 0.5) < 1e-9, "half of that figure is an estimate")
ok(st["tiers"][1] == 1, "counted as tier 1")

# Tier 2 uses the agency's own rate from adjacent years, never a peer's.
t = mk_table([
    ("a", 2021, "10001", 10000, 12, None, {"murder": 10}),
    ("a", 2022, "10001", 10000,  0, None, {}),
    ("a", 2023, "10001", 10000, 12, None, {"murder": 10}),
])
est, st = impute(t, states_path=Path("/nonexistent"))
ok(t.counts[BASE_CRIMES.index("murder")][1] == 10,
   "the gap year takes the agency's own rate")
ok(st["tiers"][2] == 1 and st["tiers"][3] == 0, "counted as tier 2, not donor")
ok(est[1] == 1.0, "and is wholly an estimate")

# A twelve-month filing reporting almost nothing against its own history
# is retracted - NYPD 2021 filed 636 offences for 8.5 million people.
t = mk_table([
    ("a", 2021, "10001", 500000, 12, None, {"murder": 400}),
    ("a", 2022, "10001", 500000, 12, None, {"murder": 2}),
    ("a", 2023, "10001", 500000, 12, None, {"murder": 400}),
])
ok(_collapsed(t, _series(t)) == [1], "an implausible full-year filing is spotted")

# The opposite mistake deletes honest quiet years. Orange County Sheriff VT
# filed twelve genuine zeros in 2023 and must survive.
t = mk_table([
    ("a", 2022, "10001", 9000, 12, None, {"murder": 1}),
    ("a", 2023, "10001", 9000, 12, None, {}),
    ("a", 2024, "10001", 9000, 12, None, {"murder": 1}),
])
ok(_collapsed(t, _series(t)) == [], "a small quiet county is left alone")

section("calibration to state margins")
import json as _json  # noqa: E402
import tempfile  # noqa: E402

# Published state total 100; agencies filed 60; the estimate is scaled so
# the two meet, rather than being left wherever the donor cell put it.
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    _json.dump({"states": {"Alabama": {
        "2000": {"pop": 1000, "totals": {"murder": 100}},
        "2021": {"pop": 1000, "totals": {"murder": 100}},
    }}}, fh)
    spath = Path(fh.name)


def cal_table(year):
    t = mk_table([
        ("filed",   year, "01001", 10000, 12, None, {"murder": 60}),
        ("missing", year, "01002", 10000,  0, None, {}),
        ("donor",   year, "01003", 10000, 12, None, {"murder": 20}),
    ])
    t.states.values[t.state[0]] = "alabama"
    return t


t = cal_table(2000)
est, st = impute(t, states_path=spath)
m = BASE_CRIMES.index("murder")
ok(t.counts[m][1] == 20,
   "the estimated agency is scaled to close the state's residual (got %d)"
   % t.counts[m][1])
ok(t.counts[m][0] == 60, "filed figures are never rescaled")

# 2020 onward the published total is a participation sum, not an estimate.
# Calibrating to it would assert a known undercount as ground truth.
t = cal_table(2021)
est, st = impute(t, states_path=spath)
# The donor cell's population-weighted rate is (60+20)/20000 = 0.004, so
# the estimate is 40 - left exactly where the donor put it. Calibrating it
# to the same published total would have pulled it to 20, asserting a
# participation sum as if it were a complete count.
ok(t.counts[m][1] == 40, "NIBRS-era estimates are left uncalibrated (k = 1)")
ok(st["uncalibrated"] >= 1, "and are reported as uncalibrated")
spath.unlink()

section("API key redaction")
# Every one of these is a URL that could reach a log, an exception message
# or a cache filename. The assertion that matters is not "the output looks
# right" but "the secret is not in it", so each case checks for the absence
# of the key itself rather than for an expected string.
SECRET = "abcdef0123456789abcdef0123456789abcdef01"
for label, url in [
    ("key last in the query", "https://api.usa.gov/x?from=01-2020&API_KEY=" + SECRET),
    ("key first in the query", "https://api.usa.gov/x?API_KEY=" + SECRET),
    ("lowercase api_key", "https://api.usa.gov/x?api_key=" + SECRET),
    ("apikey, no separator", "https://api.usa.gov/x?apikey=" + SECRET),
    ("hyphenated api-key", "https://api.usa.gov/x?api-key=" + SECRET),
    ("mixed case Api_Key", "https://api.usa.gov/x?Api_Key=" + SECRET),
    ("trailing fragment", "https://api.usa.gov/x?API_KEY=" + SECRET + "#frag"),
    ("key followed by another param",
     "https://api.usa.gov/x?API_KEY=" + SECRET + "&to=12-2024"),
]:
    ok(SECRET not in redact_url(url), label)

# Redaction must not eat anything else: a truncated URL is a debugging
# problem of its own, and a cache path keyed on an over-redacted URL would
# collide across genuinely different requests.
ok(redact_url("https://api.usa.gov/x?from=01-2020&API_KEY=" + SECRET + "&to=12-2024")
   == "https://api.usa.gov/x?from=01-2020&API_KEY=REDACTED&to=12-2024",
   "surrounding query parameters survive intact")
ok(redact_url("https://api.usa.gov/x?API_KEY=" + SECRET + "#frag")
   .endswith("#frag"), "fragment survives intact")
ok(redact_url("https://corgis-edu.github.io/state_crime.csv")
   == "https://corgis-edu.github.io/state_crime.csv", "key-free URL unchanged")
ok(redact_url("") == "" and redact_url(None) == "", "empty and None safe")

print("\n" + ("FAILED %d" % FAIL if FAIL else "ALL PASSED") + "  (%d assertions)" % PASS)
sys.exit(1 if FAIL else 0)
