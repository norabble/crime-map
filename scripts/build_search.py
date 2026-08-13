#!/usr/bin/env python3
"""
Build the place gazetteer the viewer searches.

    make search

Three kinds of entry, and the interesting one is free. Every
`local police department` row in Return A carries an `agency_name` and a
`fips_state_county_code`, which between them are a city-to-county
gazetteer: 16,884 distinct (city, state) pairs, from a file already in
the repo. No download, no new dependency.

    states    ["Vermont", "VT", "50"]
    counties  ["Addison", "VT", "50001"]
    cities    ["Chicago", "IL", "17031", 2628298]

Arrays rather than objects because at this row count the field names
would outweigh the data. About 150 KB gzipped, small enough to load at
boot beside states.json.

WHAT IT CANNOT FIND
-------------------
A place with no police force of its own does not appear in Return A -
suburbs policed by a county sheriff, unincorporated communities. Closing
that would mean adding a Census place gazetteer and a download. The
viewer's empty state says "no police agency by that name" rather than
implying the place does not exist, because those are different claims.

CANONICAL FIPS, NOT RAW
-----------------------
Every county code goes through `fips.canonical()` on the way in. Chicago
is 17031 either way, but New York City's agency sits in 36061, and this
project draws the five boroughs as one merged unit 36998. Storing the raw
code would send every New York search to a county the map no longer has.
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
    ABBR_TO_NAME, CACHE, DATA, die, human_size, ok, step, warn, write_json,
)
from fips import canonical  # noqa: E402

BULK = CACHE / "bulk"

# ABBR_TO_NAME is the 50 states plus DC, because that is what the CDE API
# serves. Puerto Rico is drawn as a unit on both the state and county
# layers - built from Return A rather than the API - so it needs an
# abbreviation here or it is the one place on the map search cannot find.
EXTRA_ABBR = {"Puerto Rico": "PR"}
NAME_TO_ABBR = {v: k for k, v in ABBR_TO_NAME.items()}
NAME_TO_ABBR.update(EXTRA_ABBR)

# Agency types that name a settlement. Sheriffs' offices name counties,
# which the geometry already supplies; special jurisdictions are campuses,
# transit systems and parks, which are not places anyone searches a map
# for.
PLACE_TYPES = {"local police department", "constable/marshal"}

# Title-casing lowercase agency names gets a handful wrong. Only the ones
# large enough to be searched are worth a rule; the rest stay imperfect
# rather than growing a table nobody maintains.
CASE_FIXES = {
    "Mcallen": "McAllen", "Mcalester": "McAlester", "Mckinney": "McKinney",
    "Mckeesport": "McKeesport", "Mcminnville": "McMinnville",
    "Mcpherson": "McPherson", "Mchenry": "McHenry", "Mcallendale": "McAllendale",
    "Mcdonough": "McDonough", "Mcfarland": "McFarland", "Mccomb": "McComb",
    "Mccook": "McCook", "Mckees Rocks": "McKees Rocks", "Mcgregor": "McGregor",
    "Desoto": "DeSoto", "Dekalb": "DeKalb", "Depere": "De Pere",
    "Lagrange": "LaGrange", "Laporte": "LaPorte", "Lasalle": "LaSalle",
    "Ofallon": "O'Fallon", "Ofallon City": "O'Fallon",
}

SMALL_WORDS = {"of", "the", "and", "on", "at", "in", "de", "la", "las", "los"}

# Return A names the AGENCY, not the place, and truncates at about 24
# characters: "Suffolk Police Departmen", "Las Vegas Metro Pd",
# "Saint Louis Police Depar". 724 entries carried one of these tails.
# Stripped from the end, repeatedly, so two-word tails go too.
ROLE_TOKENS = {
    "police", "pd", "dept", "depts", "department", "departments",
    "departmen", "departme", "departm", "depart", "depar", "dep",
    "dps", "public", "safety", "metro", "metropolitan", "bureau",
    "division", "district", "office", "authority",
    "pol", "jur", "juris", "jurisdiction", "rural", "city pd",
}

# Mirrors bareName() in web/src/search.ts. Two small copies rather than a
# shared artifact: the viewer strips these at query time so the user can
# type "Cook County", and the build strips them so a county PD filed as a
# local department does not become a second row for its own county.
GENERIC_TAIL = (
    "county", "parish", "borough", "census area", "municipality",
    "city", "town", "village", "township",
)


def bare_key(s: str) -> str:
    out = norm_key(s)
    for _ in range(2):
        for tail in GENERIC_TAIL:
            if out.endswith(" " + tail):
                out = out[: -len(tail) - 1].strip()
                break
    return out or norm_key(s)

# Places with no agency of their own name, or whose agency is named after
# something else entirely. DC is the one that matters: its force is the
# Metropolitan Police Department, so "Washington" appears nowhere.
ALIASES = [
    ("Washington", "DC", "11001"),
]


def strip_role(name: str) -> str:
    """Turn an agency name back into a place name.

    Never strips down to nothing: an agency called only "Police" leaves
    the original rather than an empty row.
    """
    toks = name.replace(".", " ").replace("/", " ").split()
    while len(toks) > 1 and toks[-1].lower().strip(",") in ROLE_TOKENS:
        toks.pop()
    return " ".join(toks) if toks else name


def titlecase(name: str) -> str:
    """Return A stores agency names lowercase; make them presentable."""
    parts = []
    for i, word in enumerate(name.split()):
        if i and word in SMALL_WORDS:
            parts.append(word)
        elif "-" in word:
            parts.append("-".join(w.capitalize() for w in word.split("-")))
        else:
            parts.append(word.capitalize())
    out = " ".join(parts)
    return CASE_FIXES.get(out, out)


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
    return cands[0]


def open_rows(path: Path):
    if path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path)
        members = [m for m in zf.namelist() if m.lower().endswith(".csv")]
        if not members:
            die("No .csv inside %s" % path.name)
        with zf.open(members[0]) as f:
            yield from csv.DictReader(
                io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""))
        return
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        yield from csv.DictReader(f)


def cities(path: Path) -> list:
    """(city, state) -> canonical county FIPS and peak population.

    The county comes from the agency's most recent appearance and the
    population from its largest, so a town that has shrunk still ranks by
    the size people remember, and one whose county code was corrected
    mid-series lands where the map draws it now.
    """
    best: dict = {}
    for row in open_rows(path):
        if (row.get("agency_type") or "") not in PLACE_TYPES:
            continue
        name = (row.get("agency_name") or "").strip()
        raw = (row.get("fips_state_county_code") or "").strip()
        state = (row.get("state") or "").strip()
        abbr = NAME_TO_ABBR.get(state.title())
        if not name or not raw or raw == "NA" or not abbr:
            continue
        fips = canonical(raw)
        if fips is None:
            continue
        try:
            pop = int(float(row.get("population") or 0))
        except ValueError:
            pop = 0
        try:
            year = int(row.get("year") or 0)
        except ValueError:
            year = 0

        name = strip_role(name)
        key = (name.lower(), abbr)
        cur = best.get(key)
        if cur is None:
            best[key] = [pop, fips, year]
        else:
            if pop > cur[0]:
                cur[0] = pop
            if year > cur[2]:
                cur[1] = fips
                cur[2] = year

    # Collapse rows that differ only in punctuation or a stripped tail and
    # point at the same county: "St. Louis" and "St Louis" in 29510 are one
    # place, and two rows for it is just noise in the result list.
    merged: dict = {}
    for (name, abbr), (pop, fips, _) in best.items():
        key = (norm_key(name), abbr, fips)
        cur = merged.get(key)
        if cur is None or pop > cur[3]:
            merged[key] = [titlecase(name), abbr, fips, pop]
        elif len(name) > len(merged[key][0]):
            # Prefer the fuller spelling when populations tie - the source
            # truncates, so the longer string is usually the better name.
            merged[key][0] = titlecase(name)

    out = list(merged.values())
    out.sort(key=lambda e: (-e[3], e[0], e[1]))
    return out


def norm_key(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch == " ").strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="path to the Return A download")
    ap.add_argument("--out", default=str(DATA / "search.json"))
    args = ap.parse_args()

    geo_counties = json.loads((DATA / "geo-counties.json").read_text())
    geo_states = json.loads((DATA / "geo-states.json").read_text())

    # States, from the geometry the viewer keys its state layer by name.
    state_rows = []
    fips_to_abbr = {}
    for name, entry in sorted(geo_states.items()):
        abbr = NAME_TO_ABBR.get(name)
        if not abbr:
            continue
        state_rows.append([name, abbr, entry["fips"]])
        fips_to_abbr[entry["fips"]] = abbr

    # Counties, already properly cased in the geometry file. Population
    # comes from the county layer so that ambiguous names rank sensibly:
    # without it every county tied at zero and "Cook" sorted Georgia
    # before Illinois.
    county_pop: dict = {}
    cpath = DATA / "counties.json"
    if cpath.exists():
        cdoc = json.loads(cpath.read_text())
        cix = {n: i for i, n in enumerate(cdoc["meta"]["record"])}
        for fips, years in cdoc["counties"].items():
            v = sorted(r[cix["pop"]] for r in years.values() if r[cix["pop"]] > 0)
            if v:
                county_pop[fips] = v[len(v) // 2]
    else:
        warn("No counties.json - counties will not rank by population")

    county_rows = []
    for fips, entry in sorted(geo_counties.items()):
        abbr = fips_to_abbr.get(fips[:2])
        name = entry.get("name") or fips
        county_rows.append([name, abbr or "", fips, county_pop.get(fips, 0)])

    step("Reading agency names")
    city_rows = cities(find_source(args.file))

    # A city row that matches a county row exactly - same name, same state,
    # same target - is the same answer twice. 500 of them, "Los Angeles"
    # and "Philadelphia" among them. The county row wins, being the unit
    # the map actually draws.
    county_keys = {(bare_key(n), a, f) for n, a, f, _ in county_rows}
    before = len(city_rows)
    city_rows = [c for c in city_rows
                 if (bare_key(c[0]), c[1], c[2]) not in county_keys]
    if before != len(city_rows):
        ok("%d city rows dropped as duplicates of a county" % (before - len(city_rows)))

    for name, abbr, fips in ALIASES:
        if not any(c[1] == abbr and c[2] == fips and norm_key(c[0]) == norm_key(name)
                   for c in city_rows):
            city_rows.append([name, abbr, fips, county_pop.get(fips, 0)])
            ok("alias added: %s, %s -> %s" % (name, abbr, fips))
    ok("%d cities, %d counties, %d states"
       % (len(city_rows), len(county_rows), len(state_rows)))

    payload = {
        "meta": {
            "generated": datetime.datetime.now(datetime.timezone.utc)
                          .isoformat(timespec="seconds"),
            "fields": {
                "states": ["name", "abbr", "fips"],
                "counties": ["name", "abbr", "fips", "pop"],
                "cities": ["name", "abbr", "fips", "pop"],
            },
            # One copy of the abbreviation table, so the viewer parses
            # "Chicago, IL" without keeping a second.
            "states_by_abbr": dict(ABBR_TO_NAME,
                                   **{v: k for k, v in EXTRA_ABBR.items()}),
            "source": ("Cities are local police departments in openICPSR "
                       "100707 (Return A); counties and states come from the "
                       "geometry build. A place with no police force of its "
                       "own does not appear."),
        },
        "states": state_rows,
        "counties": county_rows,
        "cities": city_rows,
    }

    p = write_json(Path(args.out), payload)
    step("Wrote %s (%s)" % (p.name, human_size(p)))

    # Spot-checks, expressed as "what would the viewer find", not "is
    # there a city row called this" - a city whose name matches its own
    # county is deduped into the county row, which is the right answer and
    # would look like a failure if probed the other way.
    def lookup(q):
        ql = q.lower()
        hits = [("county", r) for r in county_rows if r[0].lower() == ql]
        hits += [("city", r) for r in city_rows if r[0].lower() == ql]
        return hits

    for probe, want in (("chicago", "17031"), ("new york city", "36998"),
                        ("washington", "11001")):
        hits = lookup(probe)
        got = {r[2] for _, r in hits}
        line = "%s -> %s" % (probe, ", ".join(
            "%s %s %s" % (k, r[1], r[2]) for k, r in hits[:3]) or "nothing")
        (ok if want in got else warn)(line if want in got
                                      else "%s (expected %s)" % (line, want))


if __name__ == "__main__":
    main()
