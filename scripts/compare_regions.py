#!/usr/bin/env python3
"""
Compare the region variants against each other and against counties.

    make compare-regions

The layer exists to make trends readable, so the column that decides it
is volatility: the median absolute year-on-year change in a unit's rate.
Everything else - unit count, compactness, how many units still fall
under the floor - is a cost or a constraint on that.

Read it with the Poisson floor in mind. Rate error is
1/sqrt(expected events), so a 100,000-person unit expects about 6.5
murders and cannot do better than ~39% on murder however it is drawn. No
variant will beat that, and one that appears to has a bug.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adjacency import perimeter_mi  # noqa: E402
from common import DATA, ok, step, warn  # noqa: E402

FIRST, LAST = 2000, 2024
CRIMES = ("violent", "property", "murder")


def volatility(series_by_unit: dict) -> dict:
    """Median |year-on-year % change| per crime, over units and years."""
    out = {}
    for cid in CRIMES:
        swings = []
        for rates in series_by_unit.values():
            years = sorted(rates)
            for a, b in zip(years, years[1:]):
                if b - a != 1:
                    continue
                ra, rb = rates[a].get(cid), rates[b].get(cid)
                if ra is None or rb is None or ra <= 0:
                    continue
                swings.append(abs(rb - ra) / ra)
        out[cid] = statistics.median(swings) * 100 if swings else float("nan")
    return out


def rates_from(doc: dict, unit_key: str) -> dict:
    ix = {n: i for i, n in enumerate(doc["meta"]["record"])}
    out = {}
    for unit, years in doc[unit_key].items():
        per = {}
        for y, r in years.items():
            y = int(y)
            if not (FIRST <= y <= LAST):
                continue
            pop = r[ix["pop"]]
            if pop <= 0:
                continue
            per[y] = {c: r[ix[c]] / pop * 1e5 for c in CRIMES}
        if per:
            out[unit] = per
    return out


def describe(label: str, doc: dict, unit_key: str, geo: dict | None,
             members: dict | None, floor: int) -> dict:
    ix = {n: i for i, n in enumerate(doc["meta"]["record"])}
    vol = volatility(rates_from(doc, unit_key))

    pops = []
    for unit, years in doc[unit_key].items():
        v = sorted(r[ix["pop"]] for r in years.values() if r[ix["pop"]] > 0)
        if v:
            pops.append(v[len(v) // 2])
    pops.sort()

    sizes = [len(m) for m in members.values()] if members else [1] * len(pops)
    cross = 0
    if members:
        cross = sum(1 for m in members.values() if len({f[:2] for f in m}) > 1)

    compact, worst_area = [], 0.0
    if geo:
        for k, entry in geo.items():
            a = entry.get("area_sq_mi") or 0.0
            worst_area = max(worst_area, a)
            p = perimeter_mi(entry["geometry"])
            if p > 0 and a > 0:
                # Polsby-Popper on a sphere-projected outline: 1.00 is a
                # circle, 0.79 a square. Comparable between variants
                # because every one is measured the same way.
                compact.append(4 * math.pi * a / (p * p))

    return {
        "label": label,
        "units": len(pops),
        "mean_size": sum(sizes) / max(1, len(sizes)),
        "max_size": max(sizes) if sizes else 1,
        "p10": pops[len(pops) // 10] if pops else 0,
        "median": pops[len(pops) // 2] if pops else 0,
        "below": sum(1 for p in pops if p < floor),
        "cross": cross,
        "compact": statistics.median(compact) if compact else float("nan"),
        "worst_area": worst_area,
        "inexact": doc["meta"].get("inexactGeometry", 0),
        **vol,
    }


def main() -> None:
    counties = json.loads((DATA / "counties.json").read_text())
    floor = 100000
    rows = [describe("counties (today)", counties, "counties", None, None, floor)]

    root = DATA / "regions"
    variants = sorted(p.name for p in root.iterdir()
                      if (p / "regions.json").exists()) if root.exists() else []
    if not variants:
        warn("No region variants built. Run: make regions")
    for v in variants:
        doc = json.loads((root / v / "regions.json").read_text())
        geo = json.loads((root / v / "geo.json").read_text())
        keys = json.loads((root / v / "members.json").read_text())
        members: dict = {}
        for f, k in keys.items():
            members.setdefault(k, []).append(f)
        rows.append(describe(v, doc, "regions", geo, members,
                             doc["meta"].get("floor", floor)))

    step("Volatility — median |year-on-year change| in the rate, %d-%d"
         % (FIRST, LAST))
    print("  %-16s %7s %6s %5s | %8s %9s %7s | %9s %8s %6s | %7s %8s"
          % ("variant", "units", "mean", "max", "violent", "property", "murder",
             "pop p10", "median", "<floor", "compact", "max area"))
    for r in rows:
        print("  %-16s %7d %6.1f %5d | %7.0f%% %8.0f%% %6.0f%% | %9s %8s %6d | %7.2f %8s"
              % (r["label"], r["units"], r["mean_size"], r["max_size"],
                 r["violent"], r["property"], r["murder"],
                 f"{r['p10']:,}", f"{r['median']:,}", r["below"],
                 r["compact"], f"{r['worst_area']:,.0f}"))

    print()
    for r in rows[1:]:
        notes = []
        if r["cross"]:
            notes.append("%d regions span two states" % r["cross"])
        if r["inexact"]:
            notes.append("%d regions kept internal borders" % r["inexact"])
        if notes:
            warn("%s: %s" % (r["label"], "; ".join(notes)))

    ok("Poisson floor: a 100,000-person unit expects ~6.5 murders, so ~39% "
       "error on murder is the best any sub-state geography can do.")
    ok("Compactness: 1.00 is a circle, 0.79 a square. Higher is rounder.")


if __name__ == "__main__":
    main()
