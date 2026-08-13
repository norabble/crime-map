#!/usr/bin/env python3
"""
County identity: renames, merged units, and the codes we do not serve.

Return A spans 1960-2024, so it carries county codes from six decades of
FIPS vintages. `data/geo-counties.json` carries exactly one vintage -
whatever us-atlas ships today. Where those disagree the join silently
loses data: `buildCounties` in the viewer drops any key with no geometry,
which is how 17 years of Oglala Lakota County and 64 years of Bedford
City went missing without a single warning.

This module is the one place those disagreements are recorded. It is
imported by the data builds AND by fetch_geometry.py, so geometry and
data cannot drift apart: if a merge is added here, both sides act on it.

Nothing here is a guess. Every entry is a documented boundary change with
its effective year, and `canonical()` is deliberately vintage-blind - it
maps a historical code to today's geography rather than trying to
reconstruct what the map looked like in 1974. A pillar has to stand
somewhere that exists.
"""

from __future__ import annotations

# Retired code -> the code carrying that territory in current geometry.
# Applied to every county key on the way in, regardless of year, because
# the viewer only ever has one set of polygons to draw on.
RENAMES = {
    "02270": "02158",  # Wade Hampton Census Area -> Kusilvak (2015)
    "12025": "12086",  # Dade -> Miami-Dade (1997)
    "46113": "46102",  # Shannon -> Oglala Lakota (2015)
    "51515": "51019",  # Bedford City -> absorbed by Bedford County (2013)
    "51560": "51005",  # Clifton Forge -> absorbed by Alleghany County (2001)
    "51780": "51143",  # South Boston -> absorbed by Halifax County (1995)
}

# Codes that are not counties at all. `57999` appears once, in 1961.
DROP = {"57999"}

# States whose whole territory folds into a single unit, and the explicit
# multi-county merges. Both exist for the same reason: one agency reports
# for the whole area, so there is no honest way to draw it as parts.
#
#   NYPD files one Return A row for 8.2 million people. Attributed to
#   36061 it made Manhattan's pillar carry all five boroughs while the
#   other four rendered as permanent holes.
#
#   Puerto Rico's 29 agencies carry no county FIPS at all, and the
#   territory has 78 municipio polygons. One unit is the only shape the
#   data supports.
MERGED = {
    "36998": {
        "name": "New York City",
        "parts": ["36005", "36047", "36061", "36081", "36085"],
    },
    "72999": {
        "name": "Puerto Rico",
        "state": "72",
    },
}

# Territories too thin in Return A to draw. Across the whole 1960-2024
# span: American Samoa 8 agency-years, Guam 88, Virgin Islands 48. Their
# polygons are dropped rather than left as a dozen permanent no-data
# holes. Puerto Rico is the line: 305 agency-years and 3.2 million people
# is enough for one unit, which is why it is in MERGED and not here.
NOT_SERVED = {"60", "66", "69", "78"}

# Reverse lookups, built once at import.
_PART_TO_MERGED = {p: k for k, m in MERGED.items() for p in m.get("parts", ())}
_STATE_TO_MERGED = {m["state"]: k for k, m in MERGED.items() if "state" in m}


def canonical(fips: str | None) -> str | None:
    """Map any county code onto the unit the viewer actually draws.

    Returns None for codes we do not serve, so callers have exactly one
    thing to test. Order matters: rename first, because a retired code
    may be a part of a merged unit under its modern name.
    """
    if not fips:
        return None
    fips = str(fips).strip().zfill(5)
    if len(fips) != 5 or not fips.isdigit():
        return None
    if fips in DROP:
        return None
    fips = RENAMES.get(fips, fips)
    state = fips[:2]
    if state in NOT_SERVED:
        return None
    if fips in _PART_TO_MERGED:
        return _PART_TO_MERGED[fips]
    if state in _STATE_TO_MERGED:
        return _STATE_TO_MERGED[state]
    return fips


def merged_parts(all_fips) -> dict[str, list[str]]:
    """Merged key -> the geometry keys that dissolve into it.

    Takes the available geometry rather than assuming it: a state-wide
    merge names its members by prefix, and only fetch_geometry.py knows
    which polygons actually shipped.
    """
    out: dict[str, list[str]] = {}
    for key, spec in MERGED.items():
        if "parts" in spec:
            parts = [f for f in spec["parts"] if f in all_fips]
        else:
            # The merged key shares the state prefix it selects on, so it
            # would otherwise be listed as a part of itself and get
            # dissolved a second time on any re-run.
            parts = sorted(f for f in all_fips
                           if f[:2] == spec["state"] and f != key)
        if parts:
            out[key] = parts
    return out


def merged_name(key: str) -> str | None:
    spec = MERGED.get(key)
    return spec["name"] if spec else None


def is_served(fips: str) -> bool:
    """Whether a geometry key should carry data at all."""
    return canonical(fips) is not None
