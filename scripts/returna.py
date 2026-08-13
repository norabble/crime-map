#!/usr/bin/env python3
"""
Read Return A into a columnar agency-year table.

`import_bulk.py` used to stream the CSV straight into county totals. That
was enough while every correction was row-local, and it is not enough any
more: the UCR county crosswalk needs a pass over the whole file before any
row can be placed, and imputation needs an agency's entire time series
before it can fill a gap in it.

So the file is read once into memory, columnar. 1.14 million agency-years
across 25,767 agencies: a list of tuples costs ~300 MB, `array` columns
cost ~70 MB, and this project has run in containers where the difference
matters. Strings are interned into index tables for the same reason -
there are 25,767 distinct ORIs and about 3,200 distinct counties, so
storing either as a string per row is pure waste.

WHAT THE COLUMNS MEAN
---------------------
Three fields here were unread by the old build, and each one was
silently corrupting the county layer:

  * `covered_by_ori` names the agency that files on this one's behalf.
    Covered agencies report zero months, so counting them as
    non-reporting understated coverage for ~5 million people in 2023
    alone - and produced whole counties at cov 0 whose crime was sitting
    in a parent agency's row all along.
  * `population_1/2/3` with `population_1/2/3_county` splits an agency
    across up to three counties. Birmingham is 193,312 in Jefferson and
    1,912 in Shelby; the old build put all of it in Jefferson.
  * `population_N_county` is NOT a FIPS code. It is UCR's own sequential
    county number, alphabetical within the state - Jefferson AL is 37,
    whose FIPS is 073. The crosswalk between them is derived from the
    file itself in `build_crosswalk`, never guessed.
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import die, step, warn  # noqa: E402
from fips import canonical  # noqa: E402

# The seven offences actually counted. `violent` and `property` are NOT
# read: they are derived as the sum of their components at aggregation.
# `actual_index_violent` disagrees with its own parts in 18% of
# county-years - legacy vs revised rape definitions, plus agency-supplied
# index totals - which left the tooltip breakdown not adding up to the
# headline figure it sat under.
BASE_COLUMNS = {
    "murder":   "actual_murder",
    "rape":     "actual_rape_total",
    "robbery":  "actual_robbery_total",
    "assault":  "actual_assault_aggravated",
    "burglary": "actual_burglary_total",
    "larceny":  "actual_theft_total",
    "motor":    "actual_motor_vehicle_theft_total",
}
BASE_CRIMES = list(BASE_COLUMNS)

VIOLENT_PARTS = ("murder", "rape", "robbery", "assault")
PROPERTY_PARTS = ("burglary", "larceny", "motor")

FULL_YEAR = 12


def _int(v) -> int:
    """Return A writes 'NA', '.', '' and blanks for absent numbers."""
    if v is None:
        return 0
    v = v.strip()
    if not v or v in ("NA", "NULL", "."):
        return 0
    try:
        return int(float(v))
    except ValueError:
        return 0


class Interner:
    """String -> dense index, with the reverse list kept for output."""

    def __init__(self) -> None:
        self.values: list[str] = []
        self._ix: dict[str, int] = {}

    def index(self, s: str) -> int:
        i = self._ix.get(s)
        if i is None:
            i = len(self.values)
            self._ix[s] = i
            self.values.append(s)
        return i

    def get(self, s: str) -> int:
        return self._ix.get(s, -1)

    def __len__(self) -> int:
        return len(self.values)


class AgencyTable:
    """One row per agency-year, stored columnar.

    `cty`/`wgt` hold the primary county and its share of the agency's
    population. Agencies spanning more than one county - about 3% of rows
    - carry the full split in `extra`, keyed by row index, so the common
    case costs two array slots rather than a list object.
    """

    def __init__(self) -> None:
        self.oris = Interner()
        self.counties = Interner()
        self.types = Interner()
        self.groups = Interner()
        self.states = Interner()

        self.ori = array("i")
        self.year = array("h")
        self.state = array("h")
        self.atype = array("h")
        self.group = array("h")
        self.pop = array("i")
        self.months = array("b")
        self.cover = array("i")          # ORI index of the coverer, -1 none
        self.counts = [array("i") for _ in BASE_CRIMES]

        # County placement, filled by resolve().
        self.cty = array("i")            # -1 until resolved / unplaceable
        self.wgt = array("f")
        self.extra: dict[int, list[tuple[int, float]]] = {}

        # Raw UCR county codes, needed only until resolve() runs.
        self._raw_fips = array("i")      # county-index of fips_state_county_code
        self._uc = [array("h"), array("h"), array("h")]
        self._up = [array("i"), array("i"), array("i")]
        self._cover_raw: list[str] = []

    def __len__(self) -> int:
        return len(self.year)

    def crime(self, row: int, cid: str) -> int:
        return self.counts[BASE_CRIMES.index(cid)][row]

    def shares(self, row: int) -> list[tuple[int, float]]:
        """[(county index, weight)] summing to 1, or [] if unplaceable."""
        e = self.extra.get(row)
        if e is not None:
            return e
        c = self.cty[row]
        return [(c, 1.0)] if c >= 0 else []

    def reported(self, row: int) -> bool:
        return self.months[row] >= FULL_YEAR


def open_rows(path: Path):
    """Stream row dicts from a zipped or plain CSV without loading it."""
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


def load(path: Path, first: int, last: int) -> tuple[AgencyTable, dict]:
    """One pass over the CSV into an AgencyTable. No placement yet."""
    t = AgencyTable()
    stats = {"rows": 0, "out_of_range": 0, "negatives": 0}

    for row in open_rows(path):
        stats["rows"] += 1
        if stats["rows"] % 200000 == 0:
            print("  %d rows..." % stats["rows"], end="\r", flush=True)

        year = _int(row.get("year"))
        if not (first <= year <= last):
            stats["out_of_range"] += 1
            continue

        t.ori.append(t.oris.index((row.get("ori") or "").strip().lower()))
        t.year.append(year)
        t.state.append(t.states.index((row.get("state") or "").strip().lower()))
        t.atype.append(t.types.index((row.get("agency_type") or "").strip().lower()))
        t.group.append(t.groups.index((row.get("population_group") or "").strip().lower()))
        t.pop.append(max(0, _int(row.get("population"))))
        t.months.append(max(0, min(FULL_YEAR, _int(row.get("number_of_months_reported")))))

        for i, cid in enumerate(BASE_CRIMES):
            v = _int(row.get(BASE_COLUMNS[cid]))
            if v < 0:
                # Unfounded-offence adjustments arrive as negative counts.
                # They are corrections to a prior year, not a negative
                # number of crimes, and a negative pillar has no meaning.
                stats["negatives"] += 1
                v = 0
            t.counts[i].append(v)

        raw = (row.get("fips_state_county_code") or "").strip()
        t._raw_fips.append(t.counties.index(raw.zfill(5)) if raw and raw != "NA" else -1)
        for n in range(3):
            # UCR county numbers are small (Texas tops out around 254),
            # but the column is free text and an out-of-range value would
            # raise OverflowError on append rather than being ignored.
            uc = _int(row.get("population_%d_county" % (n + 1)))
            t._uc[n].append(uc if 0 <= uc <= 30000 else 0)
            t._up[n].append(max(0, _int(row.get("population_%d" % (n + 1)))))

        cb = (row.get("covered_by_ori") or "").strip().lower()
        t._cover_raw.append("" if cb in ("", "na") else cb)

    print(" " * 40, end="\r")
    # Coverer ORIs can only be resolved once every ORI has been interned.
    for cb in t._cover_raw:
        t.cover.append(t.oris.get(cb) if cb else -1)
    t._cover_raw = []
    return t, stats


def build_crosswalk(t: AgencyTable) -> tuple[dict, dict]:
    """(state, UCR county code) -> 5-digit FIPS, derived from the file.

    Only agencies confined to a single county contribute: for those,
    `fips_state_county_code` and `population_1_county` necessarily name
    the same place, which pins the mapping. Multi-county agencies are
    then placed using it.

    Conflicts are counted and the majority wins, but a *rate* of conflict
    above a trivial level means the assumption is wrong and the caller
    should stop - a silently wrong crosswalk moves crime between counties
    and nothing downstream would catch it.
    """
    votes: dict[tuple[str, int], dict[str, int]] = {}
    for i in range(len(t)):
        if t._uc[1][i] > 0:          # spans a second county; not a witness
            continue
        uc = t._uc[0][i]
        fx = t._raw_fips[i]
        if uc <= 0 or fx < 0:
            continue
        f5 = t.counties.values[fx]
        votes.setdefault((f5[:2], uc), {}).setdefault(f5, 0)
        votes[(f5[:2], uc)][f5] += 1

    xwalk: dict[tuple[str, int], str] = {}
    conflicts = 0
    total = minority = 0
    for key, tally in votes.items():
        if len(tally) > 1:
            conflicts += 1
        best = max(tally.items(), key=lambda kv: kv[1])
        xwalk[key] = best[0]
        total += sum(tally.values())
        minority += sum(tally.values()) - best[1]
    return xwalk, {"entries": len(xwalk), "conflicts": conflicts,
                   "minority_rate": (minority / total) if total else 0.0}


def resolve(t: AgencyTable, xwalk: dict) -> dict:
    """Place every agency-year onto canonical county units.

    Population share is the weight, because that is the only split the
    file offers and it is the one UCR itself uses to assign population.
    Where the shares resolve to the same unit - the five NYC boroughs, or
    a Virginia city merged into its county - they collapse into one entry
    rather than appearing as duplicate parts.
    """
    stats = {"single": 0, "multi": 0, "via_xwalk": 0, "unplaceable": 0,
             "not_served": 0}

    for i in range(len(t)):
        primary = t._raw_fips[i]
        # `population_2_county` is the only thing that distinguishes an
        # agency spanning counties from one that does not.
        multi = t._uc[1][i] > 0 and t._up[1][i] > 0
        parts: list[tuple[str, int]] = []

        if not multi:
            # The overwhelming majority of rows. `fips_state_county_code`
            # is authoritative and needs no crosswalk - and must not go
            # through one, because a handful of rows carry a
            # `population_1_county` that disagrees with their own FIPS
            # (eight San Bernardino agency-years claim Los Angeles's UCR
            # code). Trusting the crosswalk here would move them.
            if primary >= 0:
                parts = [(t.counties.values[primary], max(1, t.pop[i]))]
        else:
            state = t.counties.values[primary][:2] if primary >= 0 else None
            if primary >= 0:
                parts.append((t.counties.values[primary], max(1, t._up[0][i])))
            for n in (1, 2):
                uc, up = t._uc[n][i], t._up[n][i]
                if uc <= 0 or up <= 0 or state is None:
                    continue
                f5 = xwalk.get((state, uc))
                if f5:
                    parts.append((f5, up))
                    stats["via_xwalk"] += 1

        # Collapse onto the units the viewer actually draws.
        merged: dict[str, float] = {}
        for f5, w in parts:
            c = canonical(f5)
            if c is None:
                stats["not_served"] += 1
                continue
            merged[c] = merged.get(c, 0.0) + w

        if not merged:
            t.cty.append(-1)
            t.wgt.append(0.0)
            stats["unplaceable"] += 1
            continue

        total = sum(merged.values())
        if len(merged) == 1:
            f5 = next(iter(merged))
            t.cty.append(t.counties.index(f5))
            t.wgt.append(1.0)
            stats["single"] += 1
        else:
            items = sorted(merged.items(), key=lambda kv: -kv[1])
            t.extra[i] = [(t.counties.index(f5), w / total) for f5, w in items]
            t.cty.append(t.counties.index(items[0][0]))
            t.wgt.append(items[0][1] / total)
            stats["multi"] += 1

    t._uc = t._up = None
    t._raw_fips = None
    return stats


def load_resolved(path: Path, first: int, last: int) -> tuple[AgencyTable, dict]:
    """load + build_crosswalk + resolve, with the stats merged."""
    t, stats = load(path, first, last)
    xwalk, xstats = build_crosswalk(t)
    # Judged by vote share, not by how many keys have any disagreement at
    # all. 131 of 3,443 keys conflict, but the majority holds 97% of votes
    # in the median case - these are agencies whose county attribution was
    # corrected mid-series, not a broken assumption. A high *vote* rate
    # would mean the crosswalk is genuinely ambiguous, and then placing
    # multi-county components with it would move crime silently.
    if xstats["minority_rate"] > 0.05:
        die("UCR county crosswalk is %.1f%% ambiguous by vote share - the "
            "population_N_county assumption does not hold for this file."
            % (xstats["minority_rate"] * 100))
    if xstats["conflicts"]:
        warn("%d of %d crosswalk entries had competing FIPS (%.2f%% of votes); "
             "majority used" % (xstats["conflicts"], xstats["entries"],
                                xstats["minority_rate"] * 100))
    stats.update(resolve(t, xwalk))
    stats["crosswalk"] = xstats["entries"]
    return t, stats
