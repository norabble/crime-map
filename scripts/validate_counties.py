#!/usr/bin/env python3
"""
Check the built county layer against everything that has silently broken
it before.

    make validate

Each check here corresponds to a defect that shipped and went unnoticed,
because it produced a plausible-looking map rather than an error:

  * county keys with no geometry           17 years of Oglala Lakota
  * county-years with reporters but no
    reporting population                   Philadelphia 2019 at 7 violent
                                           crimes for 1.59 million people
  * negative offence counts                unfounded-offence adjustments
  * violent != its own components          18% of county-years
  * population discontinuities             Fairfax 459,322 -> 17,508
  * rate discontinuities at full coverage  Baltimore City 2,070 -> 10

WHY THE STATE-SUM CHECK IS NOT THE MAIN ONE
-------------------------------------------
Calibration scales estimates so that measured + estimated equals the
published state total for 1960-2019. That makes "county sums match state
totals" true by construction over those years: worth asserting as an
identity, because failing it means the pipeline is broken, but incapable
of finding anything.

The diagnostic that replaces it is the distribution of `k`, the
calibration factor. Near 1 means the estimates were already close and the
anchor barely moved them. Far from 1 means the donor cells are wrong for
that state-year, or something upstream mis-attributed crime - a defect
the sum check can no longer see. Tails are named, not just counted, and
the honest pre-calibration ratio is reported alongside so it stays
possible to say how much the repairs did versus how much the estimates
did.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA, ok, step, warn  # noqa: E402

VIOLENT_PARTS = ("murder", "rape", "robbery", "assault")
PROPERTY_PARTS = ("burglary", "larceny", "motor")

# Budgets, not absolutes. Some of these are real features of UCR data -
# counties genuinely do get re-districted - so the check is that they stay
# rare, and a rise in the count is the signal.
#
# 80 rather than the 50 the pre-repair build scored, and the extra is
# accounted for rather than waved through. Placing county-less agencies
# added 22 discontinuities: agencies appear in and vanish from Return A
# between years, and UCR moves population between a state force and local
# ones, so the population attributed to a county moves with them. That is
# 22 county-years in 200,000, against fixing denominators that had Alaska
# summing to 63% of its own population. Smoothing the spread weight over
# five years already took this from 87 down to 66 (see UNSERVED_WINDOW).
#
# If this check fails, find out which counties are new before touching the
# number - the last increase was a real defect and the one before it was
# not.
MAX_POP_JUMPS = 80

# A county recording more offences than it has residents is not a tail, it
# is a defect. The 99.9th percentile of agency-years is 0.31 per resident,
# so this sits well clear of anything real.
MAX_PER_RESIDENT = 1.0
MAX_RATE_SWINGS = 600
POP_JUMP_MIN = 20000
CALIBRATED_LAST = 2019

FAIL = 0


def check(cond: bool, msg: str) -> None:
    global FAIL
    if cond:
        ok(msg)
    else:
        FAIL += 1
        print("  FAIL " + msg)


def load(counties_path: Path, geo_path: Path, states_path: Path):
    d = json.loads(counties_path.read_text())
    geo = json.loads(geo_path.read_text())
    states = json.loads(states_path.read_text())["states"]
    return d, geo, states


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--counties", default=str(DATA / "counties.json"))
    ap.add_argument("--geo", default=str(DATA / "geo-counties.json"))
    ap.add_argument("--states", default=str(DATA / "states.json"))
    ap.add_argument("--geo-states", default=str(DATA / "geo-states.json"))
    args = ap.parse_args()

    d, geo, states = load(Path(args.counties), Path(args.geo), Path(args.states))
    meta = d["meta"]
    c = d["counties"]
    rec_slots = meta["record"]
    ix = {name: i for i, name in enumerate(rec_slots)}
    gs = json.loads(Path(args.geo_states).read_text())
    f2n = {v["fips"]: k for k, v in gs.items()}

    step("Build")
    ok("%s, %d counties, %d county-years, %d-%d"
       % (meta["format"], meta["counties"], meta["countyYears"],
          meta["yearRange"][0], meta["yearRange"][1]))
    ok("imputation: %s" % ("on" if meta.get("imputed") else "off"))

    # --- identity -----------------------------------------------------
    step("Geometry join")
    dk, gk = set(c), set(geo)
    check(not (dk - gk),
          "every county key has geometry (%d unmapped)" % len(dk - gk))
    if dk - gk:
        warn("  unmapped: " + ", ".join(sorted(dk - gk)[:12]))
    empty = sorted(gk - dk)
    ok("%d polygons carry no data: %s" % (len(empty), ", ".join(empty[:8]) or "none"))
    for key, name in (meta.get("merged") or {}).items():
        check(key in dk and key in gk, "merged unit %s (%s) has data and geometry"
              % (key, name))

    # --- per-row invariants -------------------------------------------
    step("Row invariants")
    neg = badv = badp = zerocov_with_crime = 0
    for f, yy in c.items():
        for y, r in yy.items():
            if any(v < 0 for v in r):
                neg += 1
            if r[ix["violent"]] != sum(r[ix[p]] for p in VIOLENT_PARTS):
                badv += 1
            if r[ix["property"]] != sum(r[ix[p]] for p in PROPERTY_PARTS):
                badp += 1
            # The Philadelphia-2019 shape: no coverage, no estimate, and
            # yet a crime figure on screen.
            if r[ix["cov"]] == 0 and r[ix.get("imp", 0)] == 0 \
                    and r[ix["violent"]] + r[ix["property"]] > 0:
                zerocov_with_crime += 1
    # No population floor, deliberately. The rate-swing check below has
    # one, and that is exactly how Coffee County GA got through: it went
    # 2,440 -> 162,988 per 100k at full coverage, and was skipped because
    # its population of 19,302 missed the 20,000 floor by 698 people. More
    # offences than residents is impossible at any size, so this needs no
    # floor and catches the whole class.
    impossible = []
    for f, yy in c.items():
        for y, r in yy.items():
            pop = r[ix["pop"]]
            if pop <= 0:
                continue
            per = (r[ix["violent"]] + r[ix["property"]]) / pop
            if per > MAX_PER_RESIDENT:
                impossible.append((round(per, 2), f, y))
    check(not impossible,
          "no county-year records more offences than residents (%d)"
          % len(impossible))
    for per, f, y in sorted(impossible, reverse=True)[:6]:
        warn("  %s %s: %.2f offences per resident" % (f, y, per))

    check(neg == 0, "no negative counts (%d)" % neg)
    check(badv == 0, "violent equals its components everywhere (%d bad)" % badv)
    check(badp == 0, "property equals its components everywhere (%d bad)" % badp)
    check(zerocov_with_crime == 0,
          "no county-year has crime with neither coverage nor estimate (%d)"
          % zerocov_with_crime)

    # --- discontinuities ----------------------------------------------
    step("Discontinuities")
    pop_jumps = rate_swings = 0
    for f, yy in c.items():
        ys = sorted(int(y) for y in yy)
        for a, b in zip(ys, ys[1:]):
            if b - a != 1:
                continue
            ra, rb = yy[str(a)], yy[str(b)]
            pa, pb = ra[ix["pop"]], rb[ix["pop"]]
            if pa >= POP_JUMP_MIN and (pb < pa * 0.6 or pb > pa * 1.6):
                pop_jumps += 1
            if ra[ix["cov"]] < 80 or rb[ix["cov"]] < 80 or pa < POP_JUMP_MIN:
                continue
            va = ra[ix["violent"]] / pa * 1e5
            vb = rb[ix["violent"]] / max(1, pb) * 1e5
            if va > 50 and (vb < va * 0.3 or vb > va * 3):
                rate_swings += 1
    check(pop_jumps <= MAX_POP_JUMPS,
          "population jumps >40%% year-on-year: %d (budget %d)"
          % (pop_jumps, MAX_POP_JUMPS))
    check(rate_swings <= MAX_RATE_SWINGS,
          "rate swings >3x at full coverage: %d (budget %d)"
          % (rate_swings, MAX_RATE_SWINGS))

    # --- estimation ---------------------------------------------------
    step("Estimation")
    by_year_imp: dict = defaultdict(lambda: [0.0, 0])
    fully = 0
    for f, yy in c.items():
        for y, r in yy.items():
            imp = r[ix["imp"]] if "imp" in ix else 0
            e = by_year_imp[int(y)]
            e[0] += imp
            e[1] += 1
            if imp >= 0.999:
                fully += 1
    worst = sorted(((v[0] / v[1], y) for y, v in by_year_imp.items()), reverse=True)
    ok("%d county-years are wholly estimated (%.1f%%)"
       % (fully, fully / max(1, meta["countyYears"]) * 100))
    ok("most-estimated years: " +
       ", ".join("%d %.0f%%" % (y, s * 100) for s, y in worst[:6]))

    st = meta.get("stats") or {}
    if st.get("collapsed"):
        ok("%d full-year filings retracted as implausible collapses"
           % st["collapsed"])
    if st.get("k_capped"):
        warn("%d calibration factors hit the cap; residual left unallocated"
             % st["k_capped"])
        for row in (st.get("unallocated") or [])[:5]:
            warn("  %s %s %s: %s offences unallocated" % tuple(row))
    for row in (st.get("k_extreme") or [])[:5]:
        warn("  extreme k: %s %s %s = %s" % tuple(row))
    for row in (st.get("overcount") or [])[:5]:
        warn("  over-count (k clamped to 0): %s %s %s, filed %s vs published %s"
             % tuple(row))

    # --- state sums ----------------------------------------------------
    step("State sums")
    agg: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for f, yy in c.items():
        for y, r in yy.items():
            a = agg[f[:2]][int(y)]
            a[0] += sum(r[ix[p]] for p in VIOLENT_PARTS)
            a[1] += r[ix["pop"]]
    cal, uncal, popr = [], [], []
    for sf, by in agg.items():
        nm = f2n.get(sf)
        if not nm or nm not in states:
            continue
        for y, cr in by.items():
            sr = states[nm].get(str(y))
            if not sr:
                continue
            target = sum((sr.get("totals") or {}).get(p, 0) for p in VIOLENT_PARTS)
            if target < 100:
                continue
            if sr.get("pop"):
                popr.append(cr[1] / sr["pop"])
            (cal if y <= CALIBRATED_LAST else uncal).append((cr[0] / target, nm, y))

    if cal:
        cal.sort()
        near = sum(1 for r in cal if abs(r[0] - 1) < 0.01)
        ok("calibrated years: %d/%d within 1%% of the published state total"
           % (near, len(cal)))
        low = [r for r in cal if r[0] < 0.8]
        if low:
            warn("%d calibrated state-years still below 80%%: %s" %
                 (len(low), ", ".join("%s %d (%.2f)" % (n, y, v) for v, n, y in low[:6])))
    if uncal:
        uncal.sort()
        med = uncal[len(uncal) // 2][0]
        ok("uncalibrated years (2020+): median ratio %.3f, %d below 80%%"
           % (med, sum(1 for r in uncal if r[0] < 0.8)))
    if popr:
        popr.sort()
        med = popr[len(popr) // 2]
        check(0.95 <= med <= 1.05,
              "county population sums to %.3f of state population (median)" % med)

    print("\n" + ("FAILED %d check(s)" % FAIL if FAIL else "ALL CHECKS PASSED"))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
