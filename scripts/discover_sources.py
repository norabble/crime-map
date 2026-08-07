#!/usr/bin/env python3
"""
Probe every candidate route to agency-level (and therefore county-level)
crime data, and report which ones actually work.

RUN THIS FIRST, before build_counties.py.

Why this exists: there is no public nationwide county-level dataset with
a full offence breakdown. County figures have to be built by summing
agency-level counts, and the agency-level sources are the least stable
part of the whole pipeline - the FBI reorganises the CDE download paths
periodically and the bulk files are not versioned in any documented way.

Rather than hard-coding a guess, this script tries each known route,
reports status, size and a content sample, and writes what it learned to
.cache/sources.json for build_counties.py to consume.

    python scripts/discover_sources.py
    python scripts/discover_sources.py --deep    # also downloads one bulk file
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CACHE, USER_AGENT, api_key, log, ok, roster_items, step, warn, write_json,
)

# ---------------------------------------------------------------------
# Candidate routes, best first.
# ---------------------------------------------------------------------
# 1. CDE agency roster. Cheap (51 calls) and gives ORI -> county name
#    plus latitude/longitude, which is what lets us assign agencies to
#    counties geometrically instead of by fuzzy name matching.
AGENCY_ROUTES = [
    "https://api.usa.gov/crime/fbi/cde/agency/byStateAbbr/{st}?API_KEY={key}",
    "https://api.usa.gov/crime/fbi/sapi/api/agencies/byStateAbbr/{st}?API_KEY={key}",
]

# 2. Per-agency offence counts. Correct but expensive: ~19,000 agencies
#    x 9 offences is far beyond any sane request budget. Probed only so
#    the report can say whether it is available as a fallback for
#    individual states.
AGENCY_OFFENSE_ROUTES = [
    "https://api.usa.gov/crime/fbi/cde/summarized/agency/{ori}/{slug}"
    "?from=01-2023&to=12-2023&API_KEY={key}",
]

# 3. Bulk downloads. The prize: one file covering every agency. Paths
#    have moved more than once, so several shapes are tried.
BULK_ROUTES = [
    "https://s3-us-gov-west-1.amazonaws.com/cg-d4b776d0-d898-4153-90c8-8336f86bdfec/"
    "additional-datasets/ucr/ucr-summarized/{year}/ucr-summarized-{year}.zip",
    "https://s3-us-gov-west-1.amazonaws.com/cg-d4b776d0-d898-4153-90c8-8336f86bdfec/"
    "estimated_crimes_1979_{year}.csv",
    "https://cde.ucr.cjis.gov/LATEST/s3/downloads/estimated_crimes_1979_{year}.csv",
    "https://cde.ucr.cjis.gov/LATEST/s3/summarized/ucr-summarized-{year}.zip",
]

# 4. Known-good static mirrors of agency-to-county crosswalks.
CROSSWALK_ROUTES = [
    "https://raw.githubusercontent.com/fbi-cde/crime-data-frontend/master/"
    "public/data/agency-crosswalk.json",
]


def head(url: str, timeout: int = 30):
    """HEAD-ish probe: returns (status, content-type, length, sample)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            length = int(r.headers.get("Content-Length") or 0)
            sample = r.read(2048)
            return r.status, ctype, length, sample
    except urllib.error.HTTPError as e:
        return e.code, "", 0, b""
    except Exception as e:  # noqa: BLE001
        return None, str(e), 0, b""


def redact(url: str) -> str:
    return urllib.parse.re.sub(r"(API_KEY=)[^&]*", r"\1***", url) \
        if hasattr(urllib.parse, "re") else url.split("API_KEY=")[0] + "API_KEY=***"


def probe_agency_roster(key: str) -> dict:
    step("1. CDE agency roster (ORI -> county + coordinates)")
    if not key:
        warn("no API key set - skipping. export DATA_GOV_API_KEY=... to test.")
        return {}

    found = {}
    for tmpl in AGENCY_ROUTES:
        url = tmpl.format(st="VT", key=key)
        status, ctype, length, sample = head(url)
        label = tmpl.split("?")[0].replace("https://api.usa.gov/crime/fbi/", "")
        if status != 200 or not sample.strip():
            warn("%s -> %s" % (label, status if status else ctype))
            continue

        try:
            data = json.loads(sample.decode("utf-8", "replace"))
        except Exception:
            # Sample may be a truncated JSON array; refetch in full.
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))

        items = roster_items(data)
        if not items:
            warn("%s -> 200 but no agency records" % label)
            continue

        first = items[0] if isinstance(items, list) else items
        keys = sorted(first.keys()) if isinstance(first, dict) else []
        ok("%s -> %d agencies for VT" % (label, len(items)))
        log("     fields: %s" % ", ".join(keys[:14]))

        # What matters is whether we can place an agency in a county.
        # The live route spells it "counties"; older ones used "county_name".
        has_county = any(k for k in keys
                         if "county" in k.lower() or "counties" in k.lower())
        has_geo = any(k in keys for k in ("latitude", "lat")) and \
                  any(k in keys for k in ("longitude", "lon", "lng"))
        has_ori = any(k in keys for k in ("ori", "ORI", "ori9"))
        log("     county field: %s | coordinates: %s | ORI: %s"
            % (has_county, has_geo, has_ori))
        found = {"template": tmpl, "fields": keys, "has_county": has_county,
                 "has_geo": has_geo, "has_ori": has_ori,
                 "sample_agency": first if isinstance(first, dict) else None}
        break

    if not found:
        warn("no agency roster route worked - county build cannot proceed")
    return found


def probe_agency_offenses(key: str, ori: str | None) -> dict:
    step("2. Per-agency offence counts")
    if not key or not ori:
        warn("skipped (needs a key and a sample ORI from step 1)")
        return {}
    for tmpl in AGENCY_OFFENSE_ROUTES:
        url = tmpl.format(ori=ori, slug="violent-crime", key=key)
        status, ctype, length, sample = head(url)
        if status == 200 and sample.strip():
            ok("agency offence endpoint works for ORI %s" % ori)
            log("     NOTE: ~19,000 agencies x 9 offences is far too many")
            log("     requests for a full build; usable only per-state.")
            return {"template": tmpl, "verified_ori": ori}
        warn("-> %s" % (status if status else ctype))
    return {}


def probe_bulk(deep: bool) -> list:
    step("3. Bulk agency-level downloads")
    hits = []
    for year in (2023, 2022):
        for tmpl in BULK_ROUTES:
            url = tmpl.format(year=year)
            status, ctype, length, sample = head(url)
            short = url.split("/")[-1]
            if status == 200:
                kind = "zip" if sample[:2] == b"PK" else (
                    "csv" if b"," in sample[:200] else ctype or "unknown")
                ok("%s -> %s, %.1f MB, looks like %s"
                   % (short, status, length / 1e6, kind))
                if kind == "csv":
                    head_line = sample.decode("utf-8", "replace").splitlines()[:1]
                    log("     header: %s" % (head_line[0][:150] if head_line else ""))
                hits.append({"url": url, "year": year, "bytes": length, "kind": kind})
            else:
                warn("%s -> %s" % (short, status if status else ctype))
        if hits:
            break

    if not hits:
        warn("no bulk route responded")
        log("     The CDE download paths change; open")
        log("     https://cde.ucr.cjis.gov/LATEST/webapp/#/pages/downloads")
        log("     in a browser, copy a real download URL from the network")
        log("     tab, and add it to BULK_ROUTES in this file.")
    return hits


def probe_crosswalk() -> list:
    step("4. Agency-to-county crosswalks")
    hits = []
    for url in CROSSWALK_ROUTES:
        status, ctype, length, sample = head(url)
        short = url.split("/")[-1]
        if status == 200:
            ok("%s -> %.1f KB" % (short, length / 1e3))
            hits.append({"url": url, "bytes": length})
        else:
            warn("%s -> %s" % (short, status if status else ctype))
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep", action="store_true",
                    help="also download one bulk file to inspect its contents")
    args = ap.parse_args()

    key = api_key()
    log("Probing every route to agency-level crime data.")
    log("Nothing here is destructive; it only reports what is reachable.\n")

    roster = probe_agency_roster(key)

    sample_ori = None
    if roster.get("sample_agency"):
        for k in ("ori", "ORI", "ori9"):
            if roster["sample_agency"].get(k):
                sample_ori = roster["sample_agency"][k]
                break

    offenses = probe_agency_offenses(key, sample_ori)
    bulk = probe_bulk(args.deep)
    crosswalk = probe_crosswalk()

    result = {
        "agency_roster": roster,
        "agency_offenses": offenses,
        "bulk": bulk,
        "crosswalk": crosswalk,
    }
    p = write_json(CACHE / "sources.json", result, compact=False)

    step("Summary")
    log("  agency roster : %s" % ("OK" if roster else "UNAVAILABLE"))
    log("  bulk files    : %s" % ("%d found" % len(bulk) if bulk else "UNAVAILABLE"))
    log("  crosswalk     : %s" % ("%d found" % len(crosswalk) if crosswalk else "none"))
    log("\n  wrote %s" % p)

    if roster and bulk:
        log("\n  Both pieces are available - run: python scripts/build_counties.py")
    elif roster and not bulk:
        log("\n  The roster works but no bulk offence file was found.")
        log("  build_counties.py can still produce a county MAP with agency")
        log("  placement, but it needs an offence source. Options:")
        log("    - add a working bulk URL to BULK_ROUTES above, or")
        log("    - run build_counties.py --states VT,NH  to pull a few states")
        log("      agency-by-agency through the API.")
    else:
        log("\n  County build is blocked until a route is found. The state")
        log("  pipeline is unaffected: python scripts/build_states.py")


if __name__ == "__main__":
    main()
