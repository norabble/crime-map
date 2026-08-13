#!/usr/bin/env python3
"""
County adjacency, and an exact union of adjacent counties.

Both come out of `data/geo-counties.json` with no extra download. That
file descends from us-atlas TopoJSON, where a shared border is one arc
referenced by both counties, so after decoding the two sides carry
*identical* vertices. Measured on the committed file: 3,132 of 3,139
counties get neighbours this way, mean 5.8 each, against a true US mean
of about 5.7.

The seven with no neighbour are real islands - Nantucket, San Juan WA,
Kauai, Newport RI and three Alaskan boroughs - not a failure of the
method.

WHY THE UNION IS EXACT
----------------------
Because the vertices match, dissolving a group of counties needs no
floating-point geometry: an interior border is an edge traversed once in
each direction (rings wind consistently, so neighbours meet head-to-tail),
and the region outline is whatever is left after those cancel. There is
no intersection test, no tolerance, and no dependency.

Where simplification has left the two sides of a border described
differently, cancellation leaves a dangling edge and stitching fails.
That is reported per region and the caller falls back to concatenating
the parts, which draws the same footprint with internal seams showing.
Silence there would mean a region with a hole through it.
"""

from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import R_MI, point_in_ring  # noqa: E402

# Coordinate rounding used to key a vertex. us-atlas ships ~5 decimal
# places; 6 keeps every distinction it makes without inventing any.
PRECISION = 6


def rings(geometry) -> list:
    """Every linear ring in a Polygon or MultiPolygon, outer and hole."""
    t = (geometry or {}).get("type")
    if t == "Polygon":
        return list(geometry["coordinates"])
    if t == "MultiPolygon":
        return [r for poly in geometry["coordinates"] for r in poly]
    return []


def _key(pt) -> tuple:
    return (round(pt[0], PRECISION), round(pt[1], PRECISION))


def build_adjacency(geo: dict) -> dict:
    """fips -> set of fips sharing at least one vertex."""
    owners: dict = defaultdict(set)
    for fips, entry in geo.items():
        for ring in rings(entry.get("geometry")):
            for pt in ring:
                owners[_key(pt)].add(fips)

    adj: dict = defaultdict(set)
    for who in owners.values():
        if len(who) < 2:
            continue
        who = sorted(who)
        for i, a in enumerate(who):
            for b in who[i + 1:]:
                adj[a].add(b)
                adj[b].add(a)
    return {f: adj.get(f, set()) for f in geo}


def ring_signed_area(ring) -> float:
    """Shoelace in degrees. Sign gives winding; magnitude only ranks."""
    s = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def ring_perimeter_mi(ring) -> float:
    """Great-circle length of a closed ring, in miles."""
    total = 0.0
    for i in range(len(ring) - 1):
        lon1, lat1 = math.radians(ring[i][0]), math.radians(ring[i][1])
        lon2, lat2 = math.radians(ring[i + 1][0]), math.radians(ring[i + 1][1])
        dlon = lon2 - lon1
        # Longitude wraps at the antimeridian; without this the Aleutians
        # measure most of the way around the planet.
        if dlon > math.pi:
            dlon -= 2 * math.pi
        elif dlon < -math.pi:
            dlon += 2 * math.pi
        x = dlon * math.cos((lat1 + lat2) / 2)
        y = lat2 - lat1
        total += math.hypot(x, y) * R_MI
    return total


def assemble(ring_list: list) -> list:
    """Group rings into GeoJSON polygons: [[outer, hole...], ...].

    Nesting decides what is a hole, NOT winding order. The GeoJSON spec
    says exterior rings run counterclockwise, and us-atlas does not follow
    it - testing the sign here returned zero outer rings and a perimeter
    of 0 for the whole of Vermont. Depth is winding-agnostic: a ring
    inside an odd number of others is a hole, whichever way it turns.
    """
    n = len(ring_list)
    if n == 1:
        return [[ring_list[0]]]
    areas = [abs(ring_signed_area(r)) for r in ring_list]
    inside = [[False] * n for _ in range(n)]
    for i in range(n):
        px, py = ring_list[i][0][0], ring_list[i][0][1]
        for j in range(n):
            if i != j and areas[j] > areas[i] and point_in_ring(px, py, ring_list[j]):
                inside[i][j] = True
    depth = [sum(row) for row in inside]

    polys = []
    outer_ix = [i for i in range(n) if depth[i] % 2 == 0]
    outer_ix.sort(key=lambda i: -areas[i])
    slot = {i: len(polys) + k for k, i in enumerate(outer_ix)}
    for i in outer_ix:
        polys.append([ring_list[i]])
    for i in range(n):
        if depth[i] % 2 == 0:
            continue
        # Innermost containing outer ring wins, so a hole inside a hole's
        # island lands on the right polygon.
        owners = [j for j in outer_ix if inside[i][j]]
        if owners:
            polys[slot[min(owners, key=lambda j: areas[j])]].append(ring_list[i])
    return polys


def perimeter_mi(geometry) -> float:
    """Outer perimeter, holes excluded - compactness is about the outline."""
    polys = assemble(rings(geometry))
    return sum(ring_perimeter_mi(p[0]) for p in polys)


def _stitch(edges: dict) -> list | None:
    """Chain surviving directed edges into closed rings.

    `edges` maps a start vertex to the list of end vertices leaving it.
    Returns None if any chain dead-ends, which means the boundary did not
    cancel cleanly and the result would be a lie.
    """
    out = []
    remaining = {a: list(bs) for a, bs in edges.items()}
    starts = [a for a, bs in remaining.items() if bs]
    for start in starts:
        while remaining.get(start):
            ring = [start]
            cur = start
            while True:
                nxt_list = remaining.get(cur)
                if not nxt_list:
                    return None
                nxt = nxt_list.pop()
                ring.append(nxt)
                if nxt == start:
                    break
                cur = nxt
                if len(ring) > 200000:
                    return None
            if len(ring) < 4:
                return None
            out.append(ring)
    return out or None


def union(geo: dict, members: list) -> tuple:
    """Dissolve members into one geometry.

    Returns (geometry, exact). `exact` is False when cancellation left a
    boundary that would not close, in which case the parts are simply
    concatenated - the same footprint, with internal borders visible.
    """
    members = [m for m in members if m in geo]
    if not members:
        return None, True
    if len(members) == 1:
        return geo[members[0]]["geometry"], True

    # Count each directed edge. An interior border appears once each way.
    seen: dict = defaultdict(int)
    for m in members:
        for ring in rings(geo[m]["geometry"]):
            for i in range(len(ring) - 1):
                seen[(_key(ring[i]), _key(ring[i + 1]))] += 1

    edges: dict = defaultdict(list)
    for (a, b), n in seen.items():
        if seen.get((b, a)):
            continue          # interior: traversed from both sides
        for _ in range(n):
            edges[a].append(b)

    stitched = _stitch(edges) if edges else None
    if not stitched:
        parts = []
        for m in members:
            g = geo[m]["geometry"]
            if g.get("type") == "Polygon":
                parts.append(g["coordinates"])
            elif g.get("type") == "MultiPolygon":
                parts.extend(g["coordinates"])
        return {"type": "MultiPolygon", "coordinates": parts}, False

    polys = assemble([[list(p) for p in r] for r in stitched])
    return {"type": "MultiPolygon", "coordinates": polys}, True


def connected(adj: dict, members) -> bool:
    """Whether members form one contiguous block in the adjacency graph."""
    members = set(members)
    if len(members) <= 1:
        return True
    start = next(iter(sorted(members)))
    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for n in adj.get(cur, ()):
            if n in members and n not in seen:
                seen.add(n)
                stack.append(n)
    return seen == members
