#!/usr/bin/env python3
"""
Download state and county geometry and precompute land areas.

Writes:
    data/geo-states.json    lon/lat GeoJSON + area_sq_mi per state
    data/geo-counties.json  lon/lat GeoJSON + area_sq_mi per county (FIPS keyed)

us-atlas publishes both WGS84 and pre-projected ("albers") builds, and
mirrors are inconsistent, so every candidate is validated as real
longitude/latitude before it is accepted.

    python scripts/fetch_geometry.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    DATA, die, fetch, geo_area_sq_mi, human_size, log, ok, step,
    topo_to_features, warn, write_json,
)

STATE_SOURCES = [
    "https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/states-10m.json",
    "https://unpkg.com/us-atlas@3.0.1/states-10m.json",
    "https://cdn.jsdelivr.net/npm/us-atlas@2.1.0/states-10m.json",
]
COUNTY_SOURCES = [
    "https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/counties-10m.json",
    "https://unpkg.com/us-atlas@3.0.1/counties-10m.json",
    "https://cdn.jsdelivr.net/npm/us-atlas@2.1.0/counties-10m.json",
]


def first_coord(feature):
    c = (feature.get("geometry") or {}).get("coordinates")
    while isinstance(c, list) and c and isinstance(c[0], list):
        c = c[0]
    return c if isinstance(c, list) and len(c) >= 2 else None


def load_topo(urls, object_name):
    """Return features from the first source that decodes to lon/lat."""
    last = None
    for url in urls:
        try:
            topo = fetch(url, expect_json=True, throttle=False, timeout=180)
            if not topo or "objects" not in topo:
                last = "not a TopoJSON"
                continue

            # A pre-projected build translates into a pixel viewport,
            # not degrees; reject before doing any real work.
            tr = (topo.get("transform") or {}).get("translate")
            if tr and (abs(tr[0]) > 180 or abs(tr[1]) > 90):
                last = "pre-projected build"
                warn("%s -> %s, skipping" % (url.split("/")[-1], last))
                continue

            name = object_name if object_name in topo["objects"] \
                else list(topo["objects"].keys())[0]
            feats = topo_to_features(topo, name)
            if not feats:
                last = "zero features"
                continue

            probe = first_coord(feats[0])
            if not probe or abs(probe[0]) > 180 or abs(probe[1]) > 90:
                last = "coordinates are not lon/lat"
                warn("%s -> %s, skipping" % (url.split("/")[-1], last))
                continue

            ok("%s -> %d features" % (url.split("/")[-1], len(feats)))
            return feats
        except Exception as e:  # noqa: BLE001
            last = str(e)
            warn("%s failed: %s" % (url.split("/")[-1], e))
    die("No usable lon/lat geometry for %r. Last problem: %s" % (object_name, last))


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__ or "",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--states-only", action="store_true",
                    help="skip county geometry (much faster)")
    args = ap.parse_args()

    step("State geometry")
    states = load_topo(STATE_SOURCES, "states")
    out_states = {}
    for f in states:
        name = (f.get("properties") or {}).get("name")
        if not name:
            continue
        out_states[name] = {
            "fips": str(f.get("id") or "").zfill(2),
            "name": name,
            "area_sq_mi": round(geo_area_sq_mi(f["geometry"]), 2),
            "geometry": f["geometry"],
        }
    p = write_json(DATA / "geo-states.json", out_states)
    ok("%d states -> %s (%s)" % (len(out_states), p.name, human_size(p)))

    if args.states_only:
        ok("--states-only: skipping county geometry")
        return

    step("County geometry")
    counties = load_topo(COUNTY_SOURCES, "counties")
    out_counties = {}
    for f in counties:
        fips = str(f.get("id") or "").zfill(5)
        if len(fips) != 5 or not fips.isdigit():
            continue
        name = (f.get("properties") or {}).get("name") or ""
        out_counties[fips] = {
            "fips": fips,
            "name": name,
            "state_fips": fips[:2],
            "area_sq_mi": round(geo_area_sq_mi(f["geometry"]), 2),
            "geometry": f["geometry"],
        }
    p = write_json(DATA / "geo-counties.json", out_counties)
    ok("%d counties -> %s (%s)" % (len(out_counties), p.name, human_size(p)))

    # Sanity check against a known figure. Colorado is close to a
    # lat/lon rectangle, so a large error here means the projection or
    # the area maths is wrong.
    co = out_states.get("Colorado")
    if co:
        err = abs(co["area_sq_mi"] - 104094) / 104094 * 100
        msg = "Colorado area %.0f sq mi vs published 104,094 (%.2f%% error)" % (
            co["area_sq_mi"], err)
        (ok if err < 3 else warn)(msg)

    n_small = sum(1 for c in out_counties.values() if c["area_sq_mi"] <= 0)
    if n_small:
        warn("%d counties computed zero area - inspect before trusting density"
             % n_small)


if __name__ == "__main__":
    main()
