#!/usr/bin/env python3
"""
Published county groupings, used as a baseline for our own regions.

    python scripts/fetch_sources.py --list
    python scripts/fetch_sources.py cz

The point of carrying an external scheme at all is comparison: it says
what a body that does this professionally arrived at, so our own
algorithm has something to be measured against rather than only against
itself.

WHAT IS HERE, AND WHAT IS NOT
-----------------------------
**Commuting Zones (USDA ERS, 2020).** 3,222 counties into 598 labour
markets, built from commuting flows. Published as a real CSV, which this
project requires - there is no pandas here and no pip.

**NCHS Health Service Areas are NOT here.** They were the better
theoretical fit, being built specifically so that small-area *rates* are
stable, which is our exact problem. But the definitions were published in
a 1991 Vital and Health Statistics report (Series 2, No. 112) and no
machine-readable county crosswalk is distributed. Extracting them would
mean parsing a scanned PDF, and a hand-transcribed crosswalk is a silent
error waiting to happen. Dartmouth Atlas "Hospital Service Areas" are a
different thing - ZIP-based and about 3,400 of them - and are not a
substitute.

MATCHING OUR COUNTY CODES
-------------------------
The crosswalk is keyed through `fips.canonical()`, so the merged units
this project draws resolve on their own: the five NYC boroughs all sit in
one commuting zone and collapse to it cleanly.

Two mismatches are handled, and both are properties of the source rather
than bugs:

  * Connecticut abolished counties in 2022, so the 2020 file lists nine
    planning regions the geometry has never heard of. Every one of them
    is in the same commuting zone, so mapping Connecticut by state is
    exact, not an approximation.
  * Puerto Rico is one unit here and 78 municipios across 10 commuting
    zones there. It cannot map to one zone, so it stays its own group.

Anything still unmatched becomes a group of one, which the floor merge
then absorbs. That is reported, never silent.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import die, fetch, ok, step, warn  # noqa: E402
from fips import canonical  # noqa: E402

SOURCES = {
    "cz": {
        "label": "USDA ERS 2020 Commuting Zones",
        "urls": [
            "https://www.ers.usda.gov/media/6968/2020-commuting-zones.csv",
        ],
        "fips_col": "FIPStxt",
        "group_col": "CZ2020",
        "name_col": "CZName",
        "min_rows": 3000,
    },
}


def _read_csv(name: str) -> list:
    spec = SOURCES[name]
    last = None
    for url in spec["urls"]:
        text = fetch(url, throttle=False, timeout=120)
        if not text:
            last = "empty or 404"
            warn("%s -> %s" % (url.split("/")[-1], last))
            continue
        rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
        # Validate before accepting, the same way fetch_geometry.py refuses
        # a pre-projected basemap: a wrong file that parses is worse than
        # one that does not.
        missing = [c for c in (spec["fips_col"], spec["group_col"])
                   if not rows or c not in rows[0]]
        if missing:
            last = "missing columns %s" % missing
            warn("%s -> %s" % (url.split("/")[-1], last))
            continue
        if len(rows) < spec["min_rows"]:
            last = "only %d rows, expected >= %d" % (len(rows), spec["min_rows"])
            warn("%s -> %s" % (url.split("/")[-1], last))
            continue
        ok("%s -> %d rows" % (url.split("/")[-1], len(rows)))
        return rows
    die("No usable %s source. Last problem: %s" % (name, last))


def load_crosswalk(name: str, known: set | None = None) -> tuple[dict, dict]:
    """Return (fips -> group id, group id -> label).

    `known` is the set of county keys we actually draw. Supplying it turns
    the Connecticut and Puerto Rico mismatches into reported facts rather
    than quietly missing rows.
    """
    if name not in SOURCES:
        die("Unknown source %r; have %s" % (name, ", ".join(SOURCES)))
    spec = SOURCES[name]
    rows = _read_csv(name)

    group: dict = {}
    labels: dict = {}
    by_state: dict = {}
    for r in rows:
        raw = (r.get(spec["fips_col"]) or "").strip().zfill(5)
        gid = (r.get(spec["group_col"]) or "").strip()
        if not raw or not gid:
            continue
        labels.setdefault(gid, (r.get(spec["name_col"]) or "").strip() or gid)
        by_state.setdefault(raw[:2], Counter())[gid] += 1
        c = canonical(raw)
        if c is None:
            continue
        # A merged unit inherits the zone its parts agree on.
        group.setdefault(c, Counter())[gid] += 1

    resolved = {f: cnt.most_common(1)[0][0] for f, cnt in group.items()}

    if known:
        gaps = sorted(known - set(resolved))
        rescued = []
        for f in gaps:
            zones = by_state.get(f[:2])
            # Only when the whole state is one zone is a state-level
            # answer exact rather than a guess. Connecticut is.
            if zones and len(zones) == 1:
                resolved[f] = next(iter(zones))
                rescued.append(f)
        if rescued:
            ok("%d counties matched via a single-zone state (Connecticut's "
               "planning regions): %s" % (len(rescued), ", ".join(rescued[:6])))
        still = sorted(known - set(resolved))
        if still:
            warn("%d counties absent from %s; each becomes its own group: %s"
                 % (len(still), name, ", ".join(still[:8])))
    return resolved, labels


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", default="cz")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        for k, v in SOURCES.items():
            print("  %-8s %s" % (k, v["label"]))
        return
    step(SOURCES[args.source]["label"])
    xw, labels = load_crosswalk(args.source)
    ok("%d counties -> %d groups" % (len(xw), len(set(xw.values()))))


if __name__ == "__main__":
    main()
