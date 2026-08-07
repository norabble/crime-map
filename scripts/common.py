"""
Shared helpers: throttled HTTP with on-disk caching, retry/backoff, progress.

Stdlib only, deliberately — no pip install step before the first run.

The CDE API sits behind api.data.gov, which rate-limits per key:
    DEMO_KEY      30 requests/hour   (unusable for a full pull)
    free key    1000 requests/hour
Everything here is built around not tripping that: requests are paced,
429s back off and retry, and every response is cached on disk so a
re-run costs nothing and an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / ".cache"
RAW = CACHE / "raw"

for _d in (DATA, CACHE, RAW):
    _d.mkdir(parents=True, exist_ok=True)

USER_AGENT = "crime-map-pipeline/1.0 (+local research build)"

# 50 states + DC. Territories are excluded: the SRS series does not
# carry them and the geometry files treat them inconsistently.
STATE_ABBR = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
]

ABBR_TO_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

# Crime types exposed by the viewer, with the column names used by the
# SRS (CORGIS) CSV and the offence slugs used by the CDE API.
CRIMES = [
    {"id": "violent",  "label": "Violent crime - all",
     "srs_rate": "Data.Rates.Violent.All",       "srs_total": "Data.Totals.Violent.All",
     "cde": "violent-crime"},
    {"id": "property", "label": "Property crime - all (non-violent)",
     "srs_rate": "Data.Rates.Property.All",      "srs_total": "Data.Totals.Property.All",
     "cde": "property-crime"},
    {"id": "murder",   "label": "Homicide",
     "srs_rate": "Data.Rates.Violent.Murder",    "srs_total": "Data.Totals.Violent.Murder",
     "cde": "homicide"},
    {"id": "rape",     "label": "Rape",
     "srs_rate": "Data.Rates.Violent.Rape",      "srs_total": "Data.Totals.Violent.Rape",
     "cde": "rape"},
    {"id": "robbery",  "label": "Robbery",
     "srs_rate": "Data.Rates.Violent.Robbery",   "srs_total": "Data.Totals.Violent.Robbery",
     "cde": "robbery"},
    {"id": "assault",  "label": "Aggravated assault",
     "srs_rate": "Data.Rates.Violent.Assault",   "srs_total": "Data.Totals.Violent.Assault",
     "cde": "aggravated-assault"},
    {"id": "burglary", "label": "Burglary",
     "srs_rate": "Data.Rates.Property.Burglary", "srs_total": "Data.Totals.Property.Burglary",
     "cde": "burglary"},
    {"id": "larceny",  "label": "Theft (larceny)",
     "srs_rate": "Data.Rates.Property.Larceny",  "srs_total": "Data.Totals.Property.Larceny",
     "cde": "larceny"},
    {"id": "motor",    "label": "Motor vehicle theft",
     "srs_rate": "Data.Rates.Property.Motor",    "srs_total": "Data.Totals.Property.Motor",
     "cde": "motor-vehicle-theft"},
]

SRS_LAST = 2019        # last year of Summary Reporting System coverage
NIBRS_FIRST = 2020     # first year pulled from the CDE API
COVERAGE_MIN = 80.0    # below this %, a unit-year is flagged unreliable


# ---------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------
def api_key() -> str:
    """Read the api.data.gov key from the environment or a .env file."""
    key = os.environ.get("DATA_GOV_API_KEY", "").strip()
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == "DATA_GOV_API_KEY":
                return v.strip().strip("'\"")
    return ""


def require_api_key() -> str:
    key = api_key()
    if not key:
        die(
            "No API key found.\n\n"
            "  1. Get a free key (instant): https://api.data.gov/signup/\n"
            "  2. cp .env.example .env  and paste it in,\n"
            "     or:  export DATA_GOV_API_KEY=your_key_here\n\n"
            "DEMO_KEY will not work - it allows only 30 requests/hour and a\n"
            "full pull needs roughly 460."
        )
    if key.upper() == "DEMO_KEY":
        die("DEMO_KEY is capped at 30 requests/hour. "
            "Get a free key: https://api.data.gov/signup/")
    return key


# ---------------------------------------------------------------------
# console
# ---------------------------------------------------------------------
_IS_TTY = sys.stdout.isatty()


def log(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    print(("\n\033[1m> " + msg + "\033[0m") if _IS_TTY else ("\n> " + msg), flush=True)


def warn(msg: str) -> None:
    print(("\033[33m  ! " + msg + "\033[0m") if _IS_TTY else ("  ! " + msg), flush=True)


def ok(msg: str) -> None:
    print(("\033[32m  OK " + msg + "\033[0m") if _IS_TTY else ("  OK " + msg), flush=True)


def die(msg: str, code: int = 1):
    print(("\033[31m\nERROR: " + msg + "\033[0m") if _IS_TTY else ("\nERROR: " + msg),
          flush=True)
    sys.exit(code)


class Progress:
    """Single-line progress counter that degrades to plain lines when piped."""

    def __init__(self, total: int, label: str = ""):
        self.total = max(1, total)
        self.label = label
        self.n = 0
        self.t0 = time.time()

    def tick(self, note: str = "") -> None:
        self.n += 1
        pct = self.n / self.total * 100
        el = time.time() - self.t0
        eta = (el / self.n) * (self.total - self.n) if self.n else 0
        line = "  %s %d/%d (%4.1f%%) eta %5.1fs %s" % (
            self.label, self.n, self.total, pct, eta, note)
        if _IS_TTY:
            sys.stdout.write("\r" + line[:110].ljust(110))
            sys.stdout.flush()
        elif self.n % 10 == 0 or self.n == self.total:
            print(line, flush=True)

    def done(self) -> None:
        if _IS_TTY:
            sys.stdout.write("\r" + " " * 110 + "\r")
            sys.stdout.flush()
        ok("%s %d/%d in %.1fs" % (self.label, self.n, self.total, time.time() - self.t0))


# ---------------------------------------------------------------------
# HTTP with cache + throttle + retry
# ---------------------------------------------------------------------
class Throttle:
    """Spaces requests to stay under a per-hour budget."""

    def __init__(self, per_hour: int = 900):
        self.min_gap = 3600.0 / max(1, per_hour)
        self._last = 0.0

    def wait(self) -> None:
        gap = time.time() - self._last
        if gap < self.min_gap:
            time.sleep(self.min_gap - gap)
        self._last = time.time()


# 900/hr leaves headroom under the 1000/hr free-key ceiling.
THROTTLE = Throttle(per_hour=900)


def _cache_path(url: str, ext: str = ".bin") -> Path:
    """Cache filename keyed on the URL, with any API key stripped first
    so the same cache works across keys and no key lands on disk."""
    scrubbed = re.sub(r"([?&]API_KEY=)[^&]*", r"\1REDACTED", url)
    h = hashlib.sha256(scrubbed.encode()).hexdigest()[:20]
    tail = re.sub(r"[^A-Za-z0-9._-]+", "_", scrubbed.split("/")[-1])[:60]
    return RAW / (tail + "." + h + ext)


def fetch(url, cache=True, tries=5, timeout=60, throttle=True,
          binary=False, expect_json=False):
    """GET a URL. Returns str, bytes, or parsed JSON.

    Caches on disk. Retries 429/5xx with exponential backoff, honouring
    Retry-After when the server sends it. Returns None on 404 or on an
    empty body (the CDE API answers unknown routes with 200 and a
    zero-length body rather than a 404)."""
    cp = _cache_path(url, ".json" if expect_json else (".bin" if binary else ".txt"))
    if cache and cp.exists() and cp.stat().st_size > 0:
        raw = cp.read_bytes()
        if binary:
            return raw
        text = raw.decode("utf-8", "replace")
        return json.loads(text) if expect_json else text

    delay = 2.0
    for attempt in range(1, tries + 1):
        if throttle:
            THROTTLE.wait()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            if not raw.strip():
                return None
            if cache:
                cp.write_bytes(raw)
            if binary:
                return raw
            text = raw.decode("utf-8", "replace")
            return json.loads(text) if expect_json else text

        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and attempt < tries:
                ra = e.headers.get("Retry-After") if e.headers else None
                sleep_s = float(ra) if (ra and str(ra).isdigit()) else delay
                if e.code == 429:
                    warn("rate limited, waiting %.0fs (attempt %d/%d)"
                         % (sleep_s, attempt, tries))
                time.sleep(sleep_s)
                delay = min(delay * 2, 120)
                continue
            if e.code == 403:
                die("403 Forbidden from api.data.gov - the API key is missing or invalid.")
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < tries:
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise RuntimeError("network failure for %s: %s" % (url, e))

    return None


def download(url: str, dest: Path, timeout: int = 300):
    """Stream a (potentially large) file to disk. Skips if already present."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total and _IS_TTY:
                    sys.stdout.write("\r    %s: %6.1f / %6.1f MB"
                                     % (dest.name, got / 1e6, total / 1e6))
                    sys.stdout.flush()
        if _IS_TTY and total:
            sys.stdout.write("\r" + " " * 70 + "\r")
        tmp.replace(dest)
        return dest
    except urllib.error.HTTPError as e:
        tmp.unlink(missing_ok=True)
        if e.code == 404:
            return None
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def unzip(path: Path, dest_dir: Path):
    """Extract a zip, returning the extracted file paths."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            # Guard against path traversal in untrusted archives.
            target = dest_dir / Path(info.filename).name
            with z.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            out.append(target)
    return out


# ---------------------------------------------------------------------
# CDE helpers
# ---------------------------------------------------------------------
def cde_state_url(abbr: str, slug: str, key: str, first: int, last: int) -> str:
    q = urllib.parse.urlencode({
        "from": "01-%d" % first,
        "to": "12-%d" % last,
        "API_KEY": key,
    })
    return ("https://api.usa.gov/crime/fbi/cde/summarized/state/%s/%s?%s"
            % (abbr, slug, q))


def roster_items(data):
    """Flatten whichever shape the agency roster route returns.

    The live CDE route returns an object keyed by county name, each value a
    list of agencies - not a flat list, and not a {"results": [...]}
    envelope. Assuming otherwise yields zero agencies, which reads as "this
    route is dead" rather than "this parser is wrong", so all three shapes
    are handled explicitly.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for envelope in ("results", "data", "agencies"):
        v = data.get(envelope)
        if isinstance(v, list):
            return v
    out = []
    for v in data.values():
        if isinstance(v, list):
            out.extend(x for x in v if isinstance(x, dict))
    return out


def fold_monthly(payload):
    """Collapse the CDE monthly response into annual totals/rates/coverage.

    Mirrors the viewer's logic exactly. Years without all twelve months
    are dropped: a part-year sum would otherwise render as a spurious
    collapse in the rate.
    """
    if not payload:
        return None
    offenses = (payload.get("offenses") or {}).get("actuals") or {}
    off_key = None
    for k in offenses:
        if k.endswith(" Offenses"):
            off_key = k
            break
    if not off_key:
        return None
    name = off_key[: -len(" Offenses")]

    months = offenses.get(off_key) or {}
    pops = ((payload.get("populations") or {}).get("population") or {}).get(name) or {}
    cov = ((payload.get("tooltips") or {})
           .get("Percent of Population Coverage") or {}).get(name) or {}

    buckets = {}
    for mk, val in months.items():
        try:
            year = int(str(mk)[-4:])
        except ValueError:
            continue
        b = buckets.setdefault(year, {"n": 0, "total": 0.0, "cov": [], "pop": None})
        # An unreported month comes back as a present key with a null value,
        # not as a missing key. Counting it would satisfy the twelve-month
        # test below on a part-year sum - which is how 2026 once rendered as
        # a 60% crash. `is None` rather than falsiness: a month with a
        # genuine zero count is reported data and must still count.
        if val is None:
            continue
        b["n"] += 1
        b["total"] += float(val)
        c = cov.get(mk)
        if isinstance(c, (int, float)):
            b["cov"].append(float(c))
        if b["pop"] is None and isinstance(pops.get(mk), (int, float)):
            b["pop"] = float(pops[mk])

    years = []
    for year in sorted(buckets):
        b = buckets[year]
        if b["n"] < 12:
            continue
        if not b["pop"] or b["pop"] <= 0:
            continue
        years.append({
            "year": year,
            "total": int(round(b["total"])),
            "rate": round(b["total"] / b["pop"] * 100000, 3),
            "pop": int(b["pop"]),
            "cov": round(sum(b["cov"]) / len(b["cov"]), 2) if b["cov"] else None,
        })
    return {"name": name, "years": years}


# ---------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------
R_MI = 3958.7613  # mean Earth radius, miles
_DEG = math.pi / 180.0


def _ring_steradians(ring) -> float:
    t = 0.0
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i][0], ring[i][1]
        b = ring[(i + 1) % n]
        bx, by = b[0], b[1]
        dx = bx - ax
        if dx > 180:
            dx -= 360
        elif dx < -180:
            dx += 360
        t += dx * _DEG * (2 + math.sin(ay * _DEG) + math.sin(by * _DEG))
    return t / 2


def geo_area_sq_mi(geometry) -> float:
    """Spherical polygon area in square miles, antimeridian-safe."""
    if not geometry:
        return 0.0
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []

    def poly(rings):
        if not rings:
            return 0.0
        a = abs(_ring_steradians(rings[0]))
        for hole in rings[1:]:
            a -= abs(_ring_steradians(hole))
        return max(0.0, a)

    if gtype == "Polygon":
        sr = poly(coords)
    elif gtype == "MultiPolygon":
        sr = sum(poly(p) for p in coords)
    else:
        return 0.0
    return sr * R_MI * R_MI


def point_in_ring(x: float, y: float, ring) -> bool:
    """Ray-casting test for a point inside a single ring."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and \
           (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi):
            inside = not inside
        j = i
    return inside


def point_in_geometry(x: float, y: float, geometry) -> bool:
    """Point-in-polygon honouring holes, for Polygon and MultiPolygon."""
    if not geometry:
        return False
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []

    def in_poly(rings):
        if not rings or not point_in_ring(x, y, rings[0]):
            return False
        for h in rings[1:]:
            if point_in_ring(x, y, h):
                return False
        return True

    if gtype == "Polygon":
        return in_poly(coords)
    if gtype == "MultiPolygon":
        for p in coords:
            if in_poly(p):
                return True
        return False
    return False


def bbox_of(geometry):
    """(minx, miny, maxx, maxy) for cheap pre-filtering."""
    box = [1e18, 1e18, -1e18, -1e18]

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            box[0] = min(box[0], c[0]); box[2] = max(box[2], c[0])
            box[1] = min(box[1], c[1]); box[3] = max(box[3], c[1])
        else:
            for k in c:
                walk(k)

    walk(geometry.get("coordinates") or [])
    return tuple(box)


# ---------------------------------------------------------------------
# TopoJSON -> GeoJSON (stdlib, no topojson package needed)
# ---------------------------------------------------------------------
def topo_to_features(topo, object_name):
    """Decode one TopoJSON object into a list of GeoJSON features."""
    obj = (topo.get("objects") or {}).get(object_name)
    if obj is None:
        raise KeyError("TopoJSON has no object %r; available: %s"
                       % (object_name, list((topo.get("objects") or {}).keys())))

    transform = topo.get("transform")
    raw_arcs = topo.get("arcs") or []

    def decode_arc(arc):
        if not transform:
            return [[p[0], p[1]] for p in arc]
        sx, sy = transform["scale"]
        tx, ty = transform["translate"]
        out = []
        x = y = 0
        for d in arc:
            x += d[0]
            y += d[1]
            out.append([x * sx + tx, y * sy + ty])
        return out

    arcs = [decode_arc(a) for a in raw_arcs]

    def stitch(indices):
        line = []
        for idx in indices:
            seg = arcs[~idx][::-1] if idx < 0 else arcs[idx]
            if line:
                line.extend(seg[1:])
            else:
                line.extend(seg)
        return line

    def geom(g):
        t = g.get("type")
        if t == "Polygon":
            return {"type": "Polygon", "coordinates": [stitch(r) for r in g["arcs"]]}
        if t == "MultiPolygon":
            return {"type": "MultiPolygon",
                    "coordinates": [[stitch(r) for r in poly] for poly in g["arcs"]]}
        return None

    feats = []
    for g in (obj.get("geometries") or []):
        gm = geom(g)
        if gm is None:
            continue
        feats.append({
            "type": "Feature",
            "id": g.get("id"),
            "properties": dict(g.get("properties") or {}),
            "geometry": gm,
        })
    return feats


def write_json(path: Path, obj, compact: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, separators=(",", ":"))
        else:
            json.dump(obj, f, indent=1)
    return path


def human_size(path: Path) -> str:
    n = float(path.stat().st_size)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024
    return "%.1f TB" % n


def parse_csv(text):
    """Minimal RFC4180 reader returning a list of dicts."""
    import csv
    import io
    return list(csv.DictReader(io.StringIO(text)))
