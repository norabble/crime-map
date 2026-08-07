#!/usr/bin/env python3
"""
Report what is actually inside a bulk UCR download. Read-only: it prints
and exits, and never writes to data/.

RUN THIS BEFORE WRITING ANY IMPORT MAPPING.

The schema of these files is not guessable. Earlier in this project the
CDE agency roster was assumed to be a flat list, was actually an object
keyed by county name, and the parser silently reported zero agencies -
which read as "this route is dead" rather than "this parser is wrong".
The same mistake against a 400 MB file would be worse, so the mapping in
import_bulk.py gets written from this output, not from assumption.

    python scripts/inspect_bulk.py                  # everything in .cache/bulk/
    python scripts/inspect_bulk.py path/to/file.csv
    python scripts/inspect_bulk.py --max-rows 200000

What it answers:

  * is this file even readable with the stdlib (no pandas here)
  * what are the columns, and which ones carry ORI / year / state /
    county FIPS / population
  * which columns map onto the nine crime ids in common.py
  * is there a months-reported column - the thing that decides whether
    the twelve-month completeness rule can survive the move to annual
    data (see fold_monthly)
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CACHE, CRIMES, die, log, ok, step, warn,
)

BULK = CACHE / "bulk"

# Formats ICPSR and the FBI actually ship. The binary ones are not
# readable without third-party packages that this project deliberately
# does not depend on, so they are named explicitly rather than failing
# with a decode error.
BINARY_HINTS = {
    ".dta": "Stata — re-download as CSV, or convert with pandas/pyreadstat elsewhere",
    ".sav": "SPSS — re-download as CSV",
    ".rds": "R serialised — re-download as CSV",
    ".rda": "R workspace — re-download as CSV",
    ".rdata": "R workspace — re-download as CSV",
    ".parquet": "Parquet — needs pyarrow; re-download as CSV",
    ".xlsx": "Excel — re-download as CSV",
    ".xls": "Excel — re-download as CSV",
}

# Substrings that identify the columns the importer needs. Ordered: the
# first match wins, so put the specific ones first.
ROLE_HINTS = [
    ("ORI",             ("ori9", "ori_9", "originating", "ori")),
    ("year",            ("year", "data_year")),
    ("state",           ("state_abb", "state_abbr", "state_postal", "state")),
    # 5-digit state+county joins straight onto geo-counties.json keys;
    # a bare 3-digit county code does not.
    ("county FIPS",     ("fips_state_county_code", "county_fips",
                         "fips_county_code", "fips_county")),
    # No bare "county" hint: it matches things like population_1_county,
    # which holds an index, not a name. Missing is better than wrong —
    # geo-counties.json supplies names from the FIPS join anyway.
    ("county name",     ("county_name", "counties")),
    ("agency name",     ("agency_name", "pub_agency_name", "name")),
    ("agency type",     ("agency_type", "pub_agency_unit")),
    # "population" must beat "population_group", which is a size *band*
    # ("city 250,000 thru 499,999") and not a number at all.
    ("population",      ("population", "total_pop", "pop")),
    ("latitude",        ("latitude", "lat")),
    ("longitude",       ("longitude", "long", "lon")),
    # Prefer a positive count of months reported: Return A leaves
    # number_of_months_missing as "NA" on rows where it still records
    # number_of_months_reported = 12.
    ("months reported", ("number_of_months_reported", "months_reported",
                         "number_of_months_missing", "months_missing",
                         "last_month_reported", "month_included_in")),
]

# Crime id -> substrings that would name that offence in a bulk file.
# Order matters: each column is claimed by at most one id, most specific
# first. "motor" must precede "larceny" or actual_motor_vehicle_theft is
# swallowed by the bare "theft" hint.
CRIME_HINTS = [
    ("motor",    ("motor_vehicle_theft_total", "motor_vehicle", "mtr_vehicle",
                  "vehicle_theft", "auto_theft")),
    # UCR "murder" is murder and non-negligent manslaughter. Return A
    # carries *negligent* manslaughter as a separate offence, so do not
    # let a manslaughter hint claim this slot.
    ("murder",   ("murder", "homicide")),
    ("rape",     ("rape_total", "rape")),
    ("robbery",  ("robbery_total", "robbery")),
    # assault_total bundles simple assault, which is ~10x aggravated and
    # is not one of the nine. Take the explicit aggravated column.
    ("assault",  ("assault_aggravated", "agg_assault", "aggravated_assault",
                  "assault_total")),
    ("burglary", ("burglary_total", "burglary", "breaking")),
    ("larceny",  ("theft_total", "larceny", "theft")),
    ("violent",  ("index_violent", "violent")),
    ("property", ("index_property", "property")),
]

# Columns that count something other than reported offences. A clearance
# is an arrest, not a crime; importing one as an offence count would be
# wrong in a way no downstream check would catch.
NOT_OFFENCES = ("clearance", "cleared", "unfounded", "rate_per", "_rate",
                "arrest", "employee", "officer", "civilian")


def find_files(target: str | None) -> list[Path]:
    if target:
        p = Path(target)
        if not p.exists():
            die("No such file: %s" % p)
        return [p]
    if not BULK.exists():
        die("No %s directory.\n"
            "Create it and put the download there:\n"
            "    mkdir -p %s\n"
            "It sits under .cache/, which is gitignored." % (BULK, BULK))
    files = sorted(f for f in BULK.rglob("*") if f.is_file())
    if not files:
        die("%s is empty - nothing to inspect." % BULK)
    return files


def open_tabular(path: Path):
    """Yield (label, binary stream) for each tabular member of a file.

    Streams throughout: these downloads run to hundreds of megabytes and
    must never be read into memory whole.
    """
    suf = path.suffix.lower()
    if suf == ".zip":
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            warn("%s is not a valid zip" % path.name)
            return
        for member in zf.namelist():
            if member.endswith("/"):
                continue
            msuf = Path(member).suffix.lower()
            if msuf in BINARY_HINTS:
                warn("  %s: %s" % (member, BINARY_HINTS[msuf]))
                continue
            if msuf in (".csv", ".tsv", ".txt", ".dat"):
                yield "%s :: %s" % (path.name, member), zf.open(member)
        return
    if suf == ".gz":
        yield path.name, gzip.open(path, "rb")
        return
    if suf in BINARY_HINTS:
        warn("%s: %s" % (path.name, BINARY_HINTS[suf]))
        return
    if suf in (".csv", ".tsv", ".txt", ".dat"):
        yield path.name, path.open("rb")
        return
    warn("%s: unrecognised extension, skipping" % path.name)


def sniff(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t|;").delimiter
    except csv.Error:
        # Fall back to whichever candidate appears most in the header.
        first = sample.splitlines()[0] if sample.splitlines() else ""
        return max(",\t|;", key=first.count)


def _pick(columns, lower, used, hints):
    """First column matching the most specific hint available.

    Tries exact name, then suffix, then substring, per hint in order.
    Loose substring matching alone is how `population` lands on
    `population_group` (a size band, not a number) and how `assault`
    lands on `assault_total` (which bundles simple assault, ~10x the
    aggravated figure). Specificity first, always.
    """
    for hint in hints:
        for test in (lambda c, h=hint: lower[c] == h,
                     lambda c, h=hint: lower[c].endswith(h),
                     lambda c, h=hint: h in lower[c]):
            hit = next((c for c in columns if c not in used and test(c)), None)
            if hit:
                return hit
    return None


def classify(columns: list[str]) -> tuple[dict, dict, list]:
    lower = {c: c.lower().strip().strip('"') for c in columns}
    roles: dict = {}
    used: set = set()
    for role, hints in ROLE_HINTS:
        hit = _pick(columns, lower, used, hints)
        if hit:
            roles[role] = hit
            used.add(hit)

    # Set aside anything that is not a count of reported offences before
    # matching, so a clearance column can never be offered as one.
    excluded = [c for c in columns
                if c not in used and any(n in lower[c] for n in NOT_OFFENCES)]
    used.update(excluded)

    crimes: dict = {}
    for cid, hints in CRIME_HINTS:
        chosen = _pick(columns, lower, used, hints)
        if not chosen:
            continue
        used.add(chosen)
        # Sibling columns for the same offence — breakdowns like
        # assault_simple or robbery_with_a_gun. Reported so the choice is
        # visible, but not imported.
        siblings = [c for c in columns
                    if c not in used and any(h in lower[c] for h in hints)]
        used.update(siblings)
        crimes[cid] = [chosen] + siblings

    unmapped = [c for c in columns if c not in used]
    return roles, crimes, unmapped, excluded


def inspect_stream(label: str, raw, max_rows: int) -> None:
    step(label)
    text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
    head = text.read(64 * 1024)
    if not head.strip():
        warn("empty file")
        return
    delim = sniff(head)
    text.seek(0) if text.seekable() else None
    if not text.seekable():
        # Streams from zip members cannot rewind; re-wrap what we read.
        text = io.StringIO(head + text.read())
    reader = csv.reader(text, delimiter=delim)

    try:
        columns = next(reader)
    except StopIteration:
        warn("no header row")
        return

    log("  delimiter %r, %d columns" % (delim, len(columns)))

    rows = 0
    years: Counter = Counter()
    nonnull: Counter = Counter()
    samples: dict = {}
    roles, crimes, unmapped, excluded = classify(columns)
    year_idx = columns.index(roles["year"]) if "year" in roles else None

    for row in reader:
        rows += 1
        if year_idx is not None and year_idx < len(row):
            y = row[year_idx].strip()
            if y:
                years[y] += 1
        for i, c in enumerate(columns):
            if i < len(row) and row[i].strip() not in ("", "NA", "NULL", "."):
                nonnull[c] += 1
                if c not in samples:
                    samples[c] = row[i].strip()
        if rows >= max_rows:
            warn("  stopped at --max-rows %d; counts below are partial" % max_rows)
            break

    log("  %d data rows scanned" % rows)
    if years:
        ys = sorted(years)
        log("  years: %s .. %s (%d distinct)" % (ys[0], ys[-1], len(ys)))

    log("")
    log("  columns the importer needs:")
    for role, _ in ROLE_HINTS:
        col = roles.get(role)
        if col:
            fill = nonnull.get(col, 0) / rows * 100 if rows else 0
            ok("    %-16s %-34s %5.1f%% filled  e.g. %r"
               % (role, col, fill, samples.get(col, "")))
        else:
            warn("    %-16s NOT FOUND" % role)

    log("")
    log("  offence columns matched to common.py CRIMES:")
    for c in CRIMES:
        hits = crimes.get(c["id"])
        if not hits:
            warn("    %-9s no candidate column" % c["id"])
            continue
        chosen, siblings = hits[0], hits[1:]
        ok("    %-9s %s" % (c["id"], chosen))
        if siblings:
            log("    %-9s   not used: %s" % ("", ", ".join(siblings[:6])
                                             + (" ..." if len(siblings) > 6 else "")))

    if excluded:
        log("")
        log("  %d column(s) held back as not-an-offence (clearances, rates,"
            % len(excluded))
        log("  arrests, staffing): %s" % ", ".join(excluded[:12]))

    if unmapped:
        log("")
        log("  %d unmapped columns (first 25): %s"
            % (len(unmapped), ", ".join(unmapped[:25])))

    log("")
    if "latitude" not in roles or "longitude" not in roles:
        warn("  No coordinates. The county layer is fine (it joins on FIPS),")
        warn("  but agency pillars are points and need lon/lat. Join the CDE")
        warn("  roster by ORI to supply them - one cheap request per state,")
        warn("  via fetch_agencies() in build_counties.py.")
        log("")
    if "months reported" in roles:
        ok("  Completeness column present (%s). The twelve-month rule can be"
           % roles["months reported"])
        ok("  reimplemented against it - see fold_monthly in common.py.")
    else:
        warn("  NO completeness column found. Annual totals here cannot be")
        warn("  checked for partial years, which is the guard that stops a")
        warn("  part-year sum rendering as a crash. Record this in meta and")
        warn("  the README rather than importing silently.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="file to inspect; default .cache/bulk/*")
    ap.add_argument("--max-rows", type=int, default=500000,
                    help="stop scanning after this many rows (default 500000)")
    args = ap.parse_args()

    files = find_files(args.path)
    log("Inspecting %d file(s). Nothing is written." % len(files))
    log("")
    found = False
    for f in files:
        for label, raw in open_tabular(f):
            found = True
            with raw:
                inspect_stream(label, raw, args.max_rows)
            log("")
    if not found:
        die("No readable tabular data found. This project has no pandas, so\n"
            "CSV (optionally zipped or gzipped) is the format to download.")


if __name__ == "__main__":
    main()
