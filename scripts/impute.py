#!/usr/bin/env python3
"""
Estimate agency-years that were never filed, and calibrate the estimates
against published state totals.

Everything in this file fabricates numbers. That is its whole purpose,
and the reason it is one file rather than a few lines inside the
aggregation: every invented figure originates here, carries a tier, and
is counted so the viewer can say how much of any pillar was measured.

`import_bulk.py --no-impute` skips this module entirely and must produce
byte-identical output to a run where it does nothing - the cheapest proof
that estimation is a layer over the measured build rather than a rewrite
of it.

THE TIERS
---------
1. **Month-scaling.** An agency that filed 3-11 months has its counts
   scaled to twelve. Within-agency, within-year, and the same adjustment
   the FBI applies. 54,869 agency-years.
2. **Within-agency temporal fill.** An agency that filed nothing, but did
   file within `max_gap` years either side, gets its own interpolated
   per-capita rate applied to its own population. Never borrows from
   another agency.
3. **Donor imputation.** Anything left: the population-weighted mean rate
   of agencies that did report in the same year, state, agency type and
   population band, falling back through coarser cells as they thin out.
   This is the tier Maltz & Targonski (2002) criticised, and the reason
   openICPSR's pre-imputed county file was rejected for this project. It
   is used here only with the labelling that file lacks.

Agencies covered by a reporting parent are never imputed: their crime is
already inside the parent's row, and estimating it again would double it.

Agency-years missing from the file altogether - 22,319, or 1.9% of
agency spans - are not synthesised. They carry no population figure,
so there is nothing to apply a rate to.

CALIBRATION
-----------
Donor imputation extrapolates from peers. Calibration anchors the result
to a figure the FBI actually published: for each state, year and offence,
the estimated portion is scaled so that measured + estimated equals the
published state total.

Only 1960-2019. In those years `states.json` carries no coverage figure
because the totals are FBI *estimates*, complete by construction, which
is exactly the margin this needs. From 2020 the same field is a
participation sum - California 2021 covers 26.1% of the state - and
calibrating to it would assert a known undercount as ground truth. Those
years, and Puerto Rico, keep k = 1 and say so.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ABBR_TO_NAME, DATA, warn  # noqa: E402
from returna import BASE_CRIMES, FULL_YEAR, AgencyTable  # noqa: E402

DEFAULT_MIN_MONTHS = 3
DEFAULT_MAX_GAP = 3

# A filing that claims twelve months and reports almost nothing is not a
# quiet year, it is a broken submission - NYPD's 2021 row carries 636
# offences for 8.5 million people against a neighbouring-year median of
# 207,487. These thresholds are deliberately conservative, because the
# opposite mistake deletes honest quiet years: Orange County Sheriff VT
# filed twelve genuine zeros in 2023 and must survive untouched.
#
#   - big enough that a near-zero year is not plausible noise
#   - judged only against the agency's OWN neighbouring full-year filings
#   - and only where those neighbours are large enough to divide by
COLLAPSE_MIN_POP = 10000
COLLAPSE_MIN_NEIGHBOUR = 50
COLLAPSE_RATIO = 0.1
COLLAPSE_WINDOW = 2

# Past this the anchor is not correcting an estimate, it is inventing one:
# a huge residual divided by a tiny imputed base lands a whole city's
# missing crime on a handful of small agencies. Capped and reported, with
# the remainder left unallocated rather than misplaced.
K_MAX = 5.0

# The state layer's figures stop being FBI estimates and start being
# participation sums here. Same seam the viewer already marks.
CALIBRATION_LAST_YEAR = 2019

# Beyond this the anchor is not correcting the estimate, it is replacing
# it, and something upstream is wrong. Reported, not silently applied.
K_WARN = 5.0

NC = len(BASE_CRIMES)
_NAME_BY_LOWER = {n.lower(): n for n in ABBR_TO_NAME.values()}


def _series(t: AgencyTable) -> dict[int, list[tuple[int, int]]]:
    """ORI -> [(year, row)], ascending."""
    by: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for i in range(len(t)):
        by[t.ori[i]].append((t.year[i], i))
    for v in by.values():
        v.sort()
    return by


def _covered_rows(t: AgencyTable) -> set[int]:
    """Rows whose crime is filed by a parent that reported a full year."""
    row_of = {}
    for i in range(len(t)):
        row_of[(t.ori[i], t.year[i])] = i
    out = set()
    for i in range(len(t)):
        c = t.cover[i]
        if c < 0:
            continue
        p = row_of.get((c, t.year[i]))
        if p is not None and p != i and t.months[p] >= FULL_YEAR:
            out.add(i)
    return out


def _collapsed(t: AgencyTable, series: dict) -> list[int]:
    """Full-year filings that report far too little to be believed.

    Compared only against the same agency's own neighbouring full-year
    filings, so a genuinely low-crime place is never flagged - it is the
    *change* that is implausible, not the level.
    """
    out = []
    for rows in series.values():
        tot = {y: sum(t.counts[k][i] for k in range(NC)) for y, i in rows}
        full = {y for y, i in rows if t.months[i] >= FULL_YEAR}
        for y, i in rows:
            if y not in full or t.pop[i] < COLLAPSE_MIN_POP:
                continue
            nb = [tot[o] for o in range(y - COLLAPSE_WINDOW, y + COLLAPSE_WINDOW + 1)
                  if o != y and o in full]
            if len(nb) < 2:
                continue
            nb.sort()
            med = nb[len(nb) // 2] if len(nb) % 2 else (nb[len(nb) // 2 - 1]
                                                        + nb[len(nb) // 2]) / 2
            if med >= COLLAPSE_MIN_NEIGHBOUR and tot[y] < med * COLLAPSE_RATIO:
                out.append(i)
    return out


def _donor_cells(t: AgencyTable, measured: list[bool]) -> tuple[dict, dict, dict]:
    """Population-weighted mean rates over rows that actually reported.

    Built before any estimate is written, so the donor pool is never
    contaminated by an earlier tier's output.
    """
    fine: dict = defaultdict(lambda: [0.0] + [0.0] * NC)
    mid: dict = defaultdict(lambda: [0.0] + [0.0] * NC)
    coarse: dict = defaultdict(lambda: [0.0] + [0.0] * NC)
    for i in range(len(t)):
        if not measured[i] or t.pop[i] <= 0:
            continue
        y, s, a, g = t.year[i], t.state[i], t.atype[i], t.group[i]
        for cell, key in ((fine, (y, s, a, g)), (mid, (y, s, a)), (coarse, (y, s))):
            rec = cell[key]
            rec[0] += t.pop[i]
            for k in range(NC):
                rec[1 + k] += t.counts[k][i]
    return fine, mid, coarse


def _rate_from(cells, keys):
    for cell, key in zip(cells, keys):
        rec = cell.get(key)
        if rec and rec[0] > 0:
            return [rec[1 + k] / rec[0] for k in range(NC)]
    return None


def impute(t: AgencyTable, min_months: int = DEFAULT_MIN_MONTHS,
           max_gap: int = DEFAULT_MAX_GAP,
           states_path: Path | None = None) -> tuple[dict, dict]:
    """Fill and calibrate in place. Returns (row -> estimated share, stats).

    Counts in `t` are modified. The returned fraction is how much of each
    row's final counts nobody filed, which `aggregate` carries into the
    county-year `imp` figure.
    """
    est: dict[int, float] = {}
    stats = {"tiers": {}, "calibrated": 0, "uncalibrated": 0, "collapsed": 0,
             "overcount": [], "k_extreme": [], "k_capped": 0,
             "unallocated": []}
    tiers = {1: 0, 2: 0, 3: 0}

    series = _series(t)
    # Zeroing the month count is how an unusable filing is retracted: it
    # then fails `reported()` everywhere, so it neither counts toward
    # coverage nor blocks the tiers below from replacing it.
    collapsed = _collapsed(t, series)
    for i in collapsed:
        t.months[i] = 0
    stats["collapsed"] = len(collapsed)

    covered = _covered_rows(t)
    measured = [t.months[i] >= min_months for i in range(len(t))]

    # --- tier 1: scale a part-year to a full one ----------------------
    for i in range(len(t)):
        m = t.months[i]
        if not (min_months <= m < FULL_YEAR):
            continue
        scale = FULL_YEAR / m
        for k in range(NC):
            t.counts[k][i] = int(round(t.counts[k][i] * scale))
        est[i] = 1.0 - m / FULL_YEAR
        tiers[1] += 1

    # Donor pool from measured rows only, before tiers 2 and 3 write.
    cells = _donor_cells(t, measured)

    # --- tiers 2 and 3: rows that filed nothing usable ----------------
    for i in range(len(t)):
        if measured[i] or i in covered or t.pop[i] <= 0:
            continue

        rates = None
        # Tier 2: this agency's own rate, from the nearest years it did
        # report, on either side and within the bound.
        hist = series.get(t.ori[i], ())
        year = t.year[i]
        before = after = None
        for y, r in hist:
            if not measured[r]:
                continue
            if y < year and year - y <= max_gap:
                before = (y, r)
            elif y > year and y - year <= max_gap and after is None:
                after = (y, r)
                break
        picks = [p for p in (before, after) if p]
        if picks:
            rates = [0.0] * NC
            for _, r in picks:
                p = max(1, t.pop[r])
                for k in range(NC):
                    rates[k] += t.counts[k][r] / p
            rates = [v / len(picks) for v in rates]
            tier = 2
        else:
            # Tier 3: peers, from the finest cell that has anyone in it.
            y, s, a, g = year, t.state[i], t.atype[i], t.group[i]
            rates = _rate_from(cells, [(y, s, a, g), (y, s, a), (y, s)])
            tier = 3

        if rates is None:
            continue
        pop = t.pop[i]
        for k in range(NC):
            t.counts[k][i] = int(round(rates[k] * pop))
        est[i] = 1.0
        tiers[tier] += 1

    stats["tiers"] = tiers
    _calibrate(t, est, stats, states_path)
    return est, stats


def _calibrate(t: AgencyTable, est: dict, stats: dict,
               states_path: Path | None) -> None:
    """Scale the estimated portion to meet published state totals."""
    path = states_path or (DATA / "states.json")
    try:
        states = json.loads(path.read_text())["states"]
    except Exception as e:  # noqa: BLE001
        warn("No usable %s (%s) - estimates left uncalibrated" % (path.name, e))
        stats["uncalibrated"] = len(est)
        return

    # (state, year) -> per-crime measured and estimated sums.
    meas: dict = defaultdict(lambda: [0.0] * NC)
    imp: dict = defaultdict(lambda: [0.0] * NC)
    for i, frac in est.items():
        key = (t.state[i], t.year[i])
        m, e = meas[key], imp[key]
        for k in range(NC):
            v = t.counts[k][i]
            m[k] += v * (1.0 - frac)
            e[k] += v * frac
    # Rows with no estimate at all still count toward the measured side.
    for i in range(len(t)):
        if i in est:
            continue
        m = meas[(t.state[i], t.year[i])]
        for k in range(NC):
            m[k] += t.counts[k][i]

    factors: dict = {}
    for (s_ix, year), e in imp.items():
        name = _NAME_BY_LOWER.get(t.states.values[s_ix])
        if name is None or year > CALIBRATION_LAST_YEAR:
            # Territories have no published state total; NIBRS-era totals
            # are participation sums, not estimates. Both keep k = 1.
            continue
        yy = states.get(name, {}).get(str(year))
        if not yy:
            continue
        totals = yy.get("totals") or {}
        m = meas[(s_ix, year)]
        ks = [1.0] * NC
        for k, cid in enumerate(BASE_CRIMES):
            target = totals.get(cid)
            if target is None or e[k] <= 0:
                continue
            residual = target - m[k]
            if residual <= 0:
                # Filed counts already meet or exceed the published
                # total. Nothing to estimate; leave what agencies
                # actually reported alone and report it.
                ks[k] = 0.0
                stats["overcount"].append([name, year, cid, round(m[k]), target])
                continue
            raw = residual / e[k]
            if raw > K_MAX:
                # Chicago's rape figures were never UCR-compliant, so
                # Illinois' published total sits far above anything its
                # agencies filed. Scaling the few imputed agencies by
                # 4,756x to close that would put a city's crime on a
                # handful of small towns. Cap, and say what was left over.
                stats["k_capped"] += 1
                stats["unallocated"].append(
                    [name, year, cid, round(residual - e[k] * K_MAX)])
                raw = K_MAX
            ks[k] = raw
            if raw >= K_MAX or raw < 1.0 / K_WARN:
                stats["k_extreme"].append([name, year, cid, round(raw, 3)])
        factors[(s_ix, year)] = ks

    for i, frac in est.items():
        ks = factors.get((t.state[i], t.year[i]))
        if ks is None:
            stats["uncalibrated"] += 1
            continue
        stats["calibrated"] += 1
        for k in range(NC):
            if ks[k] == 1.0:
                continue
            # Only the estimated portion moves; a part-year's filed months
            # are measurement and stay put.
            t.counts[k][i] = int(round(t.counts[k][i] * (1.0 - frac + frac * ks[k])))

    # These lists exist to be read, not to grow without bound.
    stats["overcount"] = sorted(stats["overcount"], key=lambda r: -r[3])[:40]
    stats["k_extreme"] = sorted(stats["k_extreme"], key=lambda r: -r[3])[:40]
    stats["unallocated"] = sorted(stats["unallocated"], key=lambda r: -r[3])[:40]
