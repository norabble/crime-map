#!/usr/bin/env python3
"""
Build a regional layer: contiguous groups of low-population counties.

    python scripts/build_regions.py --variant floor
    python scripts/build_regions.py --variant floor --redefine
    make regions

WHY
---
Small counties are unreadable. Measured over 2000-2024, the median county
under 10,000 people saw its violent rate move 45% year on year and its
murder rate 100%, with 89% of years recording no murders at all. 41% of
counties hold under 20,000 people but only 3.7% of the population, so
most of the map is small-number noise while most of the people are not.

Grouping to a 100,000 floor takes violent volatility from 19% to 9%.
Murder goes from 55% to 37% and stops there: rate noise is Poisson, so
the error is 1/sqrt(expected events), and 100,000 people expect 6.5
murders. Reaching 10% error on murder needs 1.5 million people, which is
a state. That is a floor, not a tuning problem - the multi-year window in
the viewer is the other half of the answer.

STABILITY IS THE POINT
----------------------
A boundary that moves between builds destroys every trend on the map, so:

  * membership is decided from each county's MEDIAN population across the
    whole series, not from any single year
  * membership is written to members.json and COMMITTED, and is reused on
    every subsequent build unless --redefine is passed
  * ties break on FIPS, so the result never depends on dict ordering

Regions never cross a state line: reporting practice and the calibration
in impute.py are both per-state, and the state layer is the parent unit.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adjacency import build_adjacency, connected, perimeter_mi, union  # noqa: E402
from common import (  # noqa: E402
    CRIMES, DATA, die, human_size, ok, step, warn, write_json,
)

CRIME_ORDER = [c["id"] for c in CRIMES]
# The seven counted offences; `violent` and `property` are sums of these,
# so including them in a total would double-count.
BASE = ("murder", "rape", "robbery", "assault", "burglary", "larceny", "motor")

DEFAULT_FLOOR = 100000

# Three ways to draw the same layer, built side by side so they can be
# compared on the map and in `make compare-regions` rather than argued
# about:
#
#   floor     our own greedy merge to a population floor, no outside input
#   cz        USDA commuting zones exactly as published, floor ignored
#   cz-floor  commuting zones as a seed, then merged up to the floor
#
# NCHS Health Service Areas were the intended third and are not here:
# they exist only in a 1991 PDF with no machine-readable crosswalk. See
# fetch_sources.py.
VARIANTS = ("floor", "cz", "cz-floor")


def out_dir(variant: str) -> Path:
    return DATA / "regions" / variant


def reference_population(counties: dict, ix: dict) -> dict:
    """Median population per county across its whole series.

    The median rather than the latest year, so one bad year - a coverage
    collapse, a boundary change - cannot move a region boundary and
    invalidate the trend the layer exists to show.
    """
    ref = {}
    for fips, years in counties.items():
        v = sorted(r[ix["pop"]] for r in years.values() if r[ix["pop"]] > 0)
        if v:
            ref[fips] = v[len(v) // 2]
    return ref


def centroids(geo: dict) -> dict:
    from adjacency import rings
    out = {}
    for fips, entry in geo.items():
        pts = [p for r in rings(entry.get("geometry")) for p in r]
        if pts:
            out[fips] = (sum(p[0] for p in pts) / len(pts),
                         sum(p[1] for p in pts) / len(pts))
    return out


def agglomerate(pop: dict, adj: dict, cent: dict, floor: int,
                seed: dict | None = None) -> dict:
    """Greedy merge to a population floor. Returns fips -> group id.

    Repeatedly takes the least-populous group still under the floor and
    merges it into an adjacent same-state group, preferring one that is
    also under the floor. Without that preference the smallest county
    always merges into its largest neighbour and one blob swallows a
    state; with it, groups pair off and settle near the floor.

    Compactness is the tie-break, not the objective. Yukon-Koyukuk is
    146,881 square miles with 506 residents - no objective function
    produces a tidy shape there, and pretending otherwise would only
    distort the rest of Alaska.

    `seed` optionally pre-groups counties (a published scheme), in which
    case this only merges whatever is still under the floor.
    """
    group = dict(seed) if seed else {f: f for f in pop}
    members: dict = {}
    for f, g in group.items():
        members.setdefault(g, set()).add(f)

    gpop = {g: sum(pop.get(f, 0) for f in ms) for g, ms in members.items()}
    gcent = {}
    for g, ms in members.items():
        pts = [cent[f] for f in ms if f in cent]
        w = [max(1, pop.get(f, 1)) for f in ms if f in cent]
        if pts:
            tw = sum(w)
            gcent[g] = (sum(p[0] * q for p, q in zip(pts, w)) / tw,
                        sum(p[1] * q for p, q in zip(pts, w)) / tw)
    gadj: dict = {}
    for g, ms in members.items():
        near = set()
        for f in ms:
            for n in adj.get(f, ()):
                ng = group.get(n)
                if ng is not None and ng != g:
                    near.add(ng)
        gadj[g] = near

    locked = set()
    while True:
        cands = sorted((p, g) for g, p in gpop.items()
                       if p < floor and g not in locked)
        if not cands:
            break
        _, g = cands[0]
        state = next(iter(sorted(members[g])))[:2]
        opts = [n for n in gadj[g] if n in gpop and n != g
                and next(iter(sorted(members[n])))[:2] == state]
        if not opts:
            # An island, or the last group left in its state. Both are
            # real and neither is fixable by merging.
            locked.add(g)
            continue
        small = [n for n in opts if gpop[n] < floor]
        pool = small or opts
        here = gcent.get(g, (0.0, 0.0))
        best = min(pool, key=lambda n: (math.dist(here, gcent.get(n, (0.0, 0.0))), n))

        tot = gpop[g] + gpop[best]
        if g in gcent and best in gcent and tot > 0:
            w = gpop[g] / tot
            gcent[best] = (gcent[g][0] * w + gcent[best][0] * (1 - w),
                           gcent[g][1] * w + gcent[best][1] * (1 - w))
        gpop[best] = tot
        members[best] |= members[g]
        for f in members[g]:
            group[f] = best
        gadj[best] |= gadj[g]
        gadj[best].discard(best)
        gadj[best].discard(g)
        for n in list(gadj[g]):
            if n in gadj:
                gadj[n].discard(g)
                gadj[n].add(best)
        del members[g], gpop[g], gadj[g]
        gcent.pop(g, None)
    return group


def key_for(state: str, n: int) -> str:
    return "R%s%03d" % (state, n)


def assign_keys(group: dict) -> dict:
    """fips -> stable region key. Numbered by sorted member FIPS."""
    by_group: dict = {}
    for f, g in group.items():
        by_group.setdefault(g, []).append(f)
    out = {}
    per_state: dict = {}
    for g in sorted(by_group, key=lambda g: sorted(by_group[g])[0]):
        ms = sorted(by_group[g])
        state = ms[0][:2]
        per_state[state] = per_state.get(state, 0) + 1
        k = key_for(state, per_state[state])
        for f in ms:
            out[f] = k
    return out


def region_name(members: list, geo: dict, pop: dict) -> str:
    """Named for its largest member, so it is findable on a map."""
    best = max(members, key=lambda f: (pop.get(f, 0), f))
    name = (geo.get(best) or {}).get("name") or best
    return name if len(members) == 1 else "%s +%d" % (name, len(members) - 1)


def aggregate_regions(counties: dict, ix: dict, keys: dict) -> tuple:
    """Sum county-years into region-years, in the compact-2 layout."""
    acc: dict = {}
    partial = 0
    sizes: dict = {}
    for f, k in keys.items():
        sizes[k] = sizes.get(k, 0) + 1
    seen: dict = {}

    for fips, years in counties.items():
        k = keys.get(fips)
        if k is None:
            continue
        for y, r in years.items():
            slot = acc.setdefault(k, {}).setdefault(y, [0.0, 0.0, 0.0] + [0] * len(CRIME_ORDER))
            pop = r[ix["pop"]]
            n = sum(r[ix[c]] for c in BASE)
            slot[0] += pop
            slot[1] += r[ix["cov"]] / 100.0 * pop      # reporting population
            slot[2] += r[ix["imp"]] * n                # estimated offences
            for i, cid in enumerate(CRIME_ORDER):
                slot[3 + i] += r[ix[cid]]
            seen.setdefault(k, {}).setdefault(y, 0)
            seen[k][y] += 1

    out: dict = {}
    for k, years in acc.items():
        for y, slot in years.items():
            pop = slot[0]
            if pop <= 0:
                continue
            n = sum(slot[3 + CRIME_ORDER.index(c)] for c in BASE)
            if seen[k][y] < sizes[k]:
                partial += 1
            out.setdefault(k, {})[y] = [
                int(round(pop)),
                round(min(100.0, slot[1] / pop * 100), 2),
                round(min(1.0, slot[2] / n) if n > 0 else 0.0, 4),
            ] + [int(slot[3 + i]) for i in range(len(CRIME_ORDER))]
    return out, partial


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="floor", choices=VARIANTS)
    ap.add_argument("--floor", type=int, default=DEFAULT_FLOOR)
    ap.add_argument("--redefine", action="store_true",
                    help="recompute membership instead of reusing the "
                         "committed members.json")
    args = ap.parse_args()

    cdoc = json.loads((DATA / "counties.json").read_text())
    counties = cdoc["counties"]
    ix = {name: i for i, name in enumerate(cdoc["meta"]["record"])}
    geo = json.loads((DATA / "geo-counties.json").read_text())

    pop = reference_population(counties, ix)
    adj = build_adjacency(geo)
    cent = centroids(geo)

    d = out_dir(args.variant)
    mpath = d / "members.json"
    if mpath.exists() and not args.redefine:
        keys = json.loads(mpath.read_text())
        step("Reusing committed membership (%d counties)" % len(keys))
        ok("pass --redefine to recompute; boundaries moving between builds "
           "would invalidate every trend on the map")
    else:
        step("Computing membership: %s, floor %s" % (args.variant, f"{args.floor:,}"))
        seed = None
        if args.variant.startswith("cz"):
            seed = load_seed(set(pop))
        if args.variant == "cz":
            # As published. Commuting zones cross state lines and carry no
            # population floor, which is exactly what the comparison is
            # meant to expose, so neither is enforced here.
            group = seed
        else:
            # Split each zone at state boundaries before merging. A labour
            # market genuinely spans the Hudson; a crime rate built from
            # two states' reporting practice and two calibration targets
            # does not mean anything.
            seed = {f: "%s@%s" % (g, f[:2]) for f, g in seed.items()}
            group = agglomerate(pop, adj, cent, args.floor, seed)
        keys = assign_keys(group)

    members: dict = {}
    for f, k in keys.items():
        members.setdefault(k, []).append(f)
    for k in members:
        members[k].sort()

    # --- geometry -----------------------------------------------------
    step("Dissolving %d regions" % len(members))
    geo_out: dict = {}
    inexact = 0
    for k, ms in sorted(members.items()):
        g, exact = union(geo, ms)
        if g is None:
            continue
        if not exact:
            inexact += 1
        area = sum((geo.get(f) or {}).get("area_sq_mi", 0.0) for f in ms)
        geo_out[k] = {
            "fips": k,
            "name": region_name(ms, geo, pop),
            "state_fips": ms[0][:2],
            "area_sq_mi": round(area, 2),
            "members": ms,
            "geometry": g,
        }
    if inexact:
        warn("%d regions could not be stitched cleanly and kept visible "
             "internal borders" % inexact)

    # --- data ---------------------------------------------------------
    regions, partial = aggregate_regions(counties, ix, keys)
    pops = sorted(sum(pop.get(f, 0) for f in ms) for ms in members.values())
    below = sum(1 for p in pops if p < args.floor)

    payload = {
        "meta": {
            "generated": datetime.datetime.now(datetime.timezone.utc)
                          .isoformat(timespec="seconds"),
            "format": "compact-2",
            "record": ["pop", "cov", "imp"] + CRIME_ORDER,
            "crimeOrder": CRIME_ORDER,
            "variant": args.variant,
            "floor": args.floor,
            "regions": len(regions),
            "regionYears": sum(len(v) for v in regions.values()),
            "inexactGeometry": inexact,
            "partialYears": partial,
            "coverageMin": cdoc["meta"].get("coverageMin", 80),
            "imputed": cdoc["meta"].get("imputed", False),
            "source": cdoc["meta"].get("source"),
            "caveat": (
                "Regions are contiguous groups of counties within one "
                "state, merged until they reach a population floor so that "
                "a handful of offences stops moving the rate. Membership is "
                "fixed across all years and committed, so a rebuild cannot "
                "move a boundary. Murder remains noisy at any sub-state "
                "size: a 100,000-person region expects about 6.5 murders a "
                "year, so its murder rate carries roughly 39% error."
            ),
        },
        "regions": regions,
    }

    d.mkdir(parents=True, exist_ok=True)
    write_json(mpath, keys)
    p1 = write_json(d / "regions.json", payload)
    p2 = write_json(d / "geo.json", geo_out)

    step("Wrote %s" % d.relative_to(DATA.parent))
    ok("%d regions from %d counties (mean %.1f, max %d)"
       % (len(members), len(keys), len(keys) / max(1, len(members)),
          max(len(m) for m in members.values())))
    ok("population p10 %s, median %s" % (f"{pops[len(pops)//10]:,}",
                                         f"{pops[len(pops)//2]:,}"))
    if below:
        warn("%d regions remain under the floor (islands and single-region "
             "states)" % below)
    if partial:
        warn("%d region-years are missing at least one member county" % partial)
    ok("%s + %s" % (human_size(p1), human_size(p2)))


def load_seed(known: set) -> dict:
    """Pre-grouping from commuting zones; unmatched counties stand alone."""
    from fetch_sources import load_crosswalk
    xw, _ = load_crosswalk("cz", known)
    return {f: xw.get(f, f) for f in known}


if __name__ == "__main__":
    main()
