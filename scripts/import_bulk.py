#!/usr/bin/env python3
"""
Build a national county layer from the Return A bulk file.

Source: openICPSR 100707, "Jacob Kaplan's Concatenated Files: Uniform
Crime Reporting Program Data: Offenses Known and Clearances by Arrest
(Return A), 1960-2024" - the CSV/yearly variant, left zipped in
.cache/bulk/.

Return A is agency-level (ORI) annual offence counts. It runs past the
retirement of SRS because the FBI converts NIBRS submissions into Return A
summary equivalents, so a single file covers both eras and no second
source is needed.

    python scripts/inspect_bulk.py        # ALWAYS run this first
    python scripts/import_bulk.py
    python scripts/import_bulk.py --from-year 1990

COLUMN CHOICES THAT ARE NOT OBVIOUS
-----------------------------------
Each was verified against the API-built data for VT/NH/ME before being
committed to; none should be changed on the strength of a column name.

  * aggravated assault is `actual_assault_aggravated`, NOT
    `actual_assault_total`. The "total" bundles simple assault, which is
    roughly ten times larger - for VT/NH/ME in 2023 it read 25,912
    against a true 2,493. That mistake would have made every assault
    pillar in the country about 10x too tall, and nothing downstream
    would have caught it.
  * population is `population`, not `population_group`, which is a size
    band ("city 250,000 thru 499,999") rather than a number.
  * the county key is `fips_state_county_code` (5 digits, e.g. "50001"),
    which joins directly onto data/geo-counties.json. `fips_county_code`
    is only the 3-digit part.
  * completeness is `number_of_months_reported`, not
    `number_of_months_missing`, which is left "NA" on rows that do record
    a month count.

Coordinates in this file are county centroids - every agency in a county
shares one point - so it cannot place agency pillars, only counties.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CACHE, CRIMES, DATA, Progress, die, human_size, ok, step, warn, write_json,
)

BULK = CACHE / "bulk"

# Crime id -> Return A column. Order here defines the order of the totals
# array in the compact record, and is written into meta.crimeOrder so the
# viewer never has to guess it.
COLUMNS = {
    "violent":  "actual_index_violent",
    "property": "actual_index_property",
    "murder":   "actual_murder",
    "rape":     "actual_rape_total",
    "robbery":  "actual_robbery_total",
    "assault":  "actual_assault_aggravated",
    "burglary": "actual_burglary_total",
    "larceny":  "actual_theft_total",
    "motor":    "actual_motor_vehicle_theft_total",
}
CRIME_ORDER = [c["id"] for c in CRIMES if c["id"] in COLUMNS]

# The twelve-month rule, carried over from fold_monthly. Annual data has
# no monthly nulls to count, so completeness rests entirely on this
# column; without it a part-year sum would render as a crash in the rate.
FULL_YEAR = 12


def find_source(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            die("No such file: %s" % p)
        return p
    if not BULK.exists():
        die("No %s - put the Return A CSV download there." % BULK)
    cands = [f for f in BULK.iterdir()
             if f.suffix.lower() in (".zip", ".csv", ".gz")]
    if not cands:
        die("No .zip/.csv/.gz in %s" % BULK)
    if len(cands) > 1:
        warn("%d candidates in %s; using %s" % (len(cands), BULK, cands[0].name))
    return cands[0]


def open_rows(path: Path):
    """Stream (row dict) from a zipped or plain CSV without loading it."""
    if path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path)
        members = [m for m in zf.namelist() if m.lower().endswith(".csv")]
        if not members:
            die("No .csv inside %s" % path.name)
        step("Reading %s :: %s" % (path.name, members[0]))
        with zf.open(members[0]) as f:
            yield from csv.DictReader(
                io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""))
        return
    step("Reading %s" % path.name)
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        yield from csv.DictReader(f)


def _int(v) -> int:
    if v is None:
        return 0
    v = v.strip()
    if not v or v in ("NA", "NULL", "."):
        return 0
    try:
        return int(float(v))
    except ValueError:
        return 0


def aggregate(path: Path, first: int, last: int) -> tuple[dict, dict]:
    """Sum agencies into county-years.

    Population is summed over *every* agency in the county, reported or
    not, because UCR assigns population to the most local agency and zero
    to the overlapping one. Rates therefore use total population, matching
    FBI method, and under-reporting is surfaced as `cov` rather than
    corrected by rescaling the denominator.
    """
    acc: dict = {}
    stats = {"rows": 0, "no_fips": 0, "out_of_range": 0,
             "partial_year": 0, "counted": 0}

    for row in open_rows(path):
        stats["rows"] += 1
        if stats["rows"] % 100000 == 0:
            print("  %d rows..." % stats["rows"], end="\r", flush=True)

        year = _int(row.get("year"))
        if not (first <= year <= last):
            stats["out_of_range"] += 1
            continue
        fips = (row.get("fips_state_county_code") or "").strip()
        if not fips or fips == "NA":
            # State police and multi-county agencies carry no single
            # county. They cannot be placed, so they are excluded from
            # both numerator and denominator rather than distorting one.
            stats["no_fips"] += 1
            continue
        fips = fips.zfill(5)

        pop = _int(row.get("population"))
        rec = acc.setdefault((fips, year), {
            "pop": 0, "rep_pop": 0, "agencies": 0, "reporting": 0,
            "totals": [0] * len(CRIME_ORDER)})
        rec["pop"] += pop
        rec["agencies"] += 1

        if _int(row.get("number_of_months_reported")) < FULL_YEAR:
            stats["partial_year"] += 1
            continue

        rec["rep_pop"] += pop
        rec["reporting"] += 1
        for i, cid in enumerate(CRIME_ORDER):
            rec["totals"][i] += _int(row.get(COLUMNS[cid]))
        stats["counted"] += 1

    print(" " * 40, end="\r")
    return acc, stats


def to_payload(acc: dict, first: int, last: int, stats: dict) -> dict:
    """Compact encoding: one flat array per county-year.

    [pop, cov, ...totals in meta.crimeOrder]

    The verbose per-record objects the API path emits cost ~360 bytes;
    at 3,141 counties x 65 years that is 74 MB, which no browser should
    be asked to parse. This form is roughly a fifth of that. Rates are
    derived in the viewer from totals and pop.
    """
    counties: dict = {}
    dropped = 0
    for (fips, year), rec in acc.items():
        if rec["reporting"] == 0 or rec["pop"] <= 0:
            # No agency filed a complete year, or no population to divide
            # by. Omitted, never rendered as zero crime.
            dropped += 1
            continue
        cov = round(rec["rep_pop"] / rec["pop"] * 100, 2)
        counties.setdefault(fips, {})[str(year)] = \
            [rec["pop"], cov] + rec["totals"]

    years = sorted({int(y) for c in counties.values() for y in c})
    return {
        "meta": {
            "generated": datetime.datetime.now(datetime.timezone.utc)
                          .isoformat(timespec="seconds"),
            "format": "compact-1",
            "record": ["pop", "cov"] + CRIME_ORDER,
            "crimeOrder": CRIME_ORDER,
            "yearRange": [years[0], years[-1]] if years else [first, last],
            "coverageMin": 80,
            "source": ("openICPSR 100707 - UCR Offenses Known and Clearances "
                       "by Arrest (Return A), 1960-2024"),
            "counties": len(counties),
            "countyYears": sum(len(v) for v in counties.values()),
            "dropped": {"no_county": stats["no_fips"],
                        "partial_year": stats["partial_year"],
                        "no_complete_reporter": dropped},
            "caveat": (
                "County figures are summed from agency reports. Overlapping "
                "jurisdictions, agencies straddling county lines, and "
                "non-reporting agencies all distort them; see "
                "https://ucrbook.com/county-level-data.html. Agency-years "
                "without all twelve months reported are excluded, and the "
                "share of each county's population that did report is "
                "carried as cov. Agencies with no single county - state "
                "police, multi-county districts - are excluded entirely. "
                "Aggravated assault is the aggravated column only; simple "
                "assault is not included."
            ),
        },
        "counties": counties,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="path to the download; default .cache/bulk/*")
    ap.add_argument("--from-year", type=int, default=1960)
    ap.add_argument("--to-year", type=int, default=datetime.date.today().year)
    ap.add_argument("--out", default=str(DATA / "counties.json"))
    args = ap.parse_args()

    src = find_source(args.file)
    acc, stats = aggregate(src, args.from_year, args.to_year)
    if not acc:
        die("No rows in range %d-%d." % (args.from_year, args.to_year))

    payload = to_payload(acc, args.from_year, args.to_year, stats)
    m = payload["meta"]

    step("Aggregated")
    ok("%d rows read" % stats["rows"])
    ok("%d counties, %d county-years, %d-%d"
       % (m["counties"], m["countyYears"], m["yearRange"][0], m["yearRange"][1]))
    warn("%d agency-years dropped: fewer than 12 months reported"
         % stats["partial_year"])
    warn("%d agency-years dropped: no single county" % stats["no_fips"])
    warn("%d county-years dropped: no agency filed a complete year"
         % m["dropped"]["no_complete_reporter"])

    low = sum(1 for c in payload["counties"].values()
              for r in c.values() if r[1] < 80)
    tot = m["countyYears"]
    warn("%d of %d county-years (%.0f%%) below 80%% coverage - de-saturated"
         % (low, tot, low / max(1, tot) * 100))

    p = write_json(Path(args.out), payload)
    step("Wrote %s (%s)" % (p.name, human_size(p)))


if __name__ == "__main__":
    main()
