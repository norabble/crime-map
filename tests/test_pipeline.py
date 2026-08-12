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
