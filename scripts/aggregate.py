#!/usr/bin/env python3
"""
Fold a resolved agency-year table into county-years.

Split out of `import_bulk.py` because it is no longer a loop with a sum
in it. Four corrections live here, each of which was silently wrong
before, and each of which is testable in isolation:

  1. An agency's population and counts are split across the counties it
     covers, by population share.
  2. An agency whose crime is filed by a parent (`covered_by_ori`) counts
     as *covered*, not as non-reporting. Its population joins the
     reporting share; its counts stay zero, because they are already in
     the parent's row.
  3. An agency with no county of its own - state police, multi-county
     districts - is placed rather than discarded. 27,956 agency-years
     used to be dropped here, taking Alaska's State Troopers and their
     270,000 residents with them, which is why Alaska's county population
     summed to 63% of the state's.

     Two mechanisms, in order. Where the agency files for named agencies
     (`covered_by_ori`) those counties are exact. That relation turns out
     to be thin - the Troopers name only 8-10 agencies a year, and just
     557 of 8,337 reporting county-less agency-years name anyone - so the
     fallback carries most of the weight: spread across the state's
     counties in proportion to the population each county did NOT cover
     with a local agency. That is who a state police force serves.
     Weighting by total population instead would put trooper crime in
     the cities they do not patrol.
  4. `violent` and `property` are summed from their components rather
     than read from the file's index columns, so the breakdown always
     reconciles with the headline.

WHAT IS AN ESTIMATE AND WHAT IS NOT
-----------------------------------
Correction 3 places crime in the right *set* of counties, but splitting
it within that set is allocation, not measurement. Those offences are
accumulated into `est` alongside `totals`, so the share of a county-year
that nobody actually filed for that county is carried forward rather than
being laundered into a hard number. Imputation adds to the same
accumulator later.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from returna import (  # noqa: E402
    BASE_CRIMES, PROPERTY_PARTS, VIOLENT_PARTS, AgencyTable,
)

NC = len(BASE_CRIMES)

# Slot layout of an accumulator row. Kept as a flat list because there is
# one per county-year and there are ~200,000 of them.
POP, REP, EST = 0, 1, 2
C0 = 3                      # counts start here, NC of them


def _blank() -> list:
    return [0.0, 0.0, 0.0] + [0.0] * NC


def build_indexes(t: AgencyTable) -> tuple[dict, dict]:
    """(ori, year) -> row, and coverer row -> rows it covers that year."""
    row_of: dict[tuple[int, int], int] = {}
    for i in range(len(t)):
        row_of[(t.ori[i], t.year[i])] = i

    covers: dict[int, list[int]] = {}
    for i in range(len(t)):
        c = t.cover[i]
        if c < 0:
            continue
        parent = row_of.get((c, t.year[i]))
        if parent is not None and parent != i:
            covers.setdefault(parent, []).append(i)
    return row_of, covers


def coverage_targets(t: AgencyTable, covers: dict, row: int
                     ) -> list[tuple[int, float]]:
    """Where an unplaceable agency's crime belongs, by covered population.

    The set of counties is exact - these are the agencies this one files
    for. The split across them is population-weighted, which is the only
    signal the file carries.
    """
    weights: dict[int, float] = {}
    for child in covers.get(row, ()):
        pop = max(1, t.pop[child])
        for cty, w in t.shares(child):
            weights[cty] = weights.get(cty, 0.0) + pop * w
    total = sum(weights.values())
    if not total:
        return []
    return [(c, w / total) for c, w in weights.items()]


def state_of_county(t: AgencyTable, acc: dict) -> dict[int, str]:
    """State-name index -> two-digit state FIPS, learned from placed rows.

    Derived rather than tabulated: the only thing that matters is which
    counties an unplaceable agency's own state contains, and the placed
    rows already say so.
    """
    votes: dict[int, dict[str, int]] = {}
    for i in range(len(t)):
        c = t.cty[i]
        if c < 0:
            continue
        votes.setdefault(t.state[i], {})
        pre = t.counties.values[c][:2]
        votes[t.state[i]][pre] = votes[t.state[i]].get(pre, 0) + 1
    return {s: max(v.items(), key=lambda kv: kv[1])[0] for s, v in votes.items()}


# Years either side of the target used to smooth the unserved-population
# weight. Without it, one local agency skipping a single year hands its
# county a share of the state force's population for that year only, and
# the county's denominator jumps 40% and back - which is precisely the
# discontinuity the validator counts.
UNSERVED_WINDOW = 2


def unserved_index(acc: dict, counties_ix) -> dict:
    """(state, year) -> [(county, weight)], weights summing to 1.

    Weighted by the population no local agency covered, smoothed across a
    few years so the split tracks where policing is genuinely thin rather
    than who happened to file. Falls back to total population for a state
    that reported in full - a state force still exists there, it just has
    no unserved population to key on.
    """
    unserved: dict = {}
    total: dict = {}
    for (cty, y), rec in acc.items():
        st = counties_ix[cty][:2]
        gap = rec[POP] - rec[REP]
        if gap > 0:
            unserved.setdefault((st, y), {})[cty] = gap
        if rec[POP] > 0:
            total.setdefault((st, y), {})[cty] = rec[POP]

    out: dict = {}
    for key in set(unserved) | set(total):
        st, y = key
        smooth: dict = {}
        for o in range(y - UNSERVED_WINDOW, y + UNSERVED_WINDOW + 1):
            for cty, w in unserved.get((st, o), {}).items():
                smooth[cty] = smooth.get(cty, 0.0) + w
        picks = smooth or total.get(key, {})
        s = sum(picks.values())
        if s > 0:
            out[key] = [(c, w / s) for c, w in picks.items()]
    return out


def aggregate(t: AgencyTable, est_rows: dict | None = None) -> tuple[dict, dict]:
    """county-year -> accumulator.

    `est_rows` maps a row index to the fraction of its counts that are
    estimated rather than filed; imputation supplies it. Absent, every
    filed count is treated as measured.

    Two phases: agencies that know their own county are accumulated
    first, because placing the ones that do not depends on knowing how
    much population each county was already short of.
    """
    est_rows = est_rows or {}
    row_of, covers = build_indexes(t)
    acc: dict[tuple[int, int], list] = {}
    stats = {"placed": 0, "covered": 0, "routed": 0, "spread": 0,
             "routed_lost": 0, "not_reporting": 0}
    deferred: list[int] = []

    for i in range(len(t)):
        year = t.year[i]
        pop = t.pop[i]
        shares = t.shares(i)
        est_frac = est_rows.get(i, 0.0)
        # Two separate questions, and conflating them is what made `cov`
        # read ~100% everywhere the moment imputation was switched on.
        #   has_counts: is there a figure to add at all, filed or invented
        #   filed_full: did this agency actually file a complete year,
        #               which is the only thing `cov` is allowed to count
        has_counts = t.reported(i) or est_frac > 0
        filed_full = t.reported(i)

        if not shares:
            deferred.append(i)
            continue

        covered_by = t.cover[i]
        is_covered = False
        if not filed_full and covered_by >= 0:
            parent = row_of.get((covered_by, year))
            is_covered = parent is not None and t.reported(parent)

        for cty, w in shares:
            rec = acc.get((cty, year))
            if rec is None:
                rec = acc[(cty, year)] = _blank()
            rec[POP] += pop * w
            if filed_full or is_covered:
                # Covered agencies count toward coverage but add no
                # counts: their crime is inside the parent's row, which
                # is counted in the parent's own county.
                rec[REP] += pop * w
            if has_counts:
                for k in range(NC):
                    v = t.counts[k][i] * w
                    rec[C0 + k] += v
                    if est_frac:
                        rec[EST] += v * est_frac

        if filed_full:
            stats["placed"] += 1
        elif is_covered:
            stats["covered"] += 1
        else:
            stats["not_reporting"] += 1

    # --- phase two: agencies with no county of their own --------------
    st_fips = state_of_county(t, acc)
    spread_ix = unserved_index(acc, t.counties.values)
    for i in deferred:
        year = t.year[i]
        pop = t.pop[i]
        est_frac = est_rows.get(i, 0.0)
        has_counts = t.reported(i) or est_frac > 0
        filed_full = t.reported(i)

        targets = coverage_targets(t, covers, i)
        exact = bool(targets)
        if not exact:
            sf = st_fips.get(t.state[i])
            if sf is None:
                stats["routed_lost"] += 1
                continue
            targets = spread_ix.get((sf, year))
            if not targets:
                stats["routed_lost"] += 1
                continue
        stats["routed" if exact else "spread"] += 1

        for cty, w in targets:
            rec = acc.get((cty, year))
            if rec is None:
                rec = acc[(cty, year)] = _blank()
            # These are real people in these counties with no local
            # agency of their own, so the population belongs in the
            # denominator whether or not the agency filed.
            rec[POP] += pop * w
            if filed_full:
                rec[REP] += pop * w
            if not has_counts:
                continue
            for k in range(NC):
                v = t.counts[k][i] * w
                rec[C0 + k] += v
                # Exact where the covered agencies name the counties;
                # allocated otherwise. Either way nobody filed this
                # figure for this county, so all of it is estimated.
                rec[EST] += v

    return acc, stats


def totals_for(rec: list) -> dict[str, int]:
    """Nine offence totals, with the two index figures derived."""
    out = {cid: int(round(rec[C0 + k])) for k, cid in enumerate(BASE_CRIMES)}
    out["violent"] = sum(out[c] for c in VIOLENT_PARTS)
    out["property"] = sum(out[c] for c in PROPERTY_PARTS)
    return out
