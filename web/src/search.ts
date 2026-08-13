/* Finding a place, and translating it into whatever the map is drawing.

   What people know is rarely what is on screen. Someone looking for
   Chicago wants Cook County, and may be looking at states at the time. So
   a query is matched against every geography and then resolved into the
   active one, with the finer units offered rather than switched to.

   Everything here is pure: it takes the index and a layer id and returns
   rows. The DOM lives in legend.ts and the camera in geo.ts. */

import type { SearchDoc, SearchHit, SearchIndex, SearchKind } from './types';

/* Fold a query and an index name onto common ground: case, accents,
   punctuation and the words that decorate a county's name without
   identifying it. "St. Louis" must match "st louis", and typing "Cook
   County" must not fail because the index says "Cook". */
export function normalize(s: string): string {
  return (s || '')
    .toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[.'’]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

const SUFFIXES = /\s+(county|parish|borough|census area|city and borough|municipality|city|town|village|township)$/;

/** The identifying part of a place name, with the generic tail removed. */
export function bareName(s: string): string {
  let out = normalize(s);
  // Twice: "Anchorage Municipality city" and similar doubled tails exist.
  for (let i = 0; i < 2; i++) out = out.replace(SUFFIXES, '');
  return out.trim() || normalize(s);
}

export interface ParsedQuery {
  name: string;
  /** Two-letter state code when the query named one, else null. */
  abbr: string | null;
}

/* Split a trailing state off a query. "Chicago, IL", "chicago il" and
   "chicago, illinois" all mean the same thing, and a bare "Illinois" must
   stay a state rather than becoming an empty name with a state attached. */
export function parseQuery(raw: string, statesByAbbr: Record<string, string>): ParsedQuery {
  const byName = new Map<string, string>();
  for (const abbr in statesByAbbr) byName.set(normalize(statesByAbbr[abbr]), abbr);

  const q = normalize(raw);
  if (!q) return { name: '', abbr: null };

  // Longest state name first, so "west virginia" is not read as "virginia".
  const names = [...byName.keys()].sort((a, b) => b.length - a.length);
  for (const n of names) {
    if (q.endsWith(' ' + n)) {
      return { name: q.slice(0, q.length - n.length - 1).trim(), abbr: byName.get(n)! };
    }
  }
  const parts = q.split(' ');
  const last = parts[parts.length - 1];
  if (parts.length > 1 && last.length === 2 && statesByAbbr[last.toUpperCase()]) {
    return { name: parts.slice(0, -1).join(' '), abbr: last.toUpperCase() };
  }
  return { name: q, abbr: null };
}

interface Entry {
  kind: SearchKind;
  name: string;
  abbr: string;
  /** State name for a state, county FIPS otherwise. */
  target: string;
  /** The state layer is keyed by name, so every hit carries one. */
  stateName: string;
  pop: number;
}

export function buildIndex(doc: SearchDoc): SearchIndex {
  const statesByAbbr = (doc.meta && doc.meta.states_by_abbr) || {};
  const entries: Entry[] = [];
  for (const [name, abbr] of doc.states || []) {
    entries.push({
      kind: 'state', name, abbr, target: name, stateName: name,
      pop: Number.MAX_SAFE_INTEGER,
    });
  }
  for (const [name, abbr, fips, pop] of doc.counties || []) {
    entries.push({
      kind: 'county', name, abbr, target: fips,
      stateName: statesByAbbr[abbr] || '', pop: pop || 0,
    });
  }
  for (const [name, abbr, fips, pop] of doc.cities || []) {
    entries.push({
      kind: 'city', name, abbr, target: fips,
      stateName: statesByAbbr[abbr] || '', pop: pop || 0,
    });
  }
  return { entries, statesByAbbr };
}

/* Only a tiebreak, and deliberately so.

   Ranking kind ahead of population put three small Miami counties above
   Miami, Florida. States still win their own name because they carry an
   effectively infinite population, so "Vermont" is the state without
   needing a rule that also breaks Miami. */
const KIND_RANK: Record<SearchKind, number> = { state: 0, county: 1, city: 2 };

export function search(index: SearchIndex, raw: string, limit = 12): SearchHit[] {
  const parsed = parseQuery(raw, index.statesByAbbr);
  const abbr = parsed.abbr;
  // Strip the generic tail from BOTH sides. The index holds "Cook" and a
  // reasonable person types "Cook County"; comparing a stripped index
  // name against an unstripped query matched nothing at all.
  const name = parsed.name ? bareName(parsed.name) : '';
  if (!name) return [];

  const scored: { hit: SearchHit; tier: number; kind: number; pop: number }[] = [];
  for (const e of index.entries) {
    if (abbr && e.abbr !== abbr) continue;
    const bare = bareName(e.name);
    // Exact beats prefix beats substring. Anything else is not a match:
    // fuzzy scoring here would put "Portland" behind six places that
    // merely contain the letters.
    const tier = bare === name ? 0 : bare.startsWith(name) ? 1
      : bare.includes(name) ? 2 : -1;
    if (tier < 0) continue;
    scored.push({
      hit: {
        kind: e.kind, name: e.name, abbr: e.abbr, target: e.target,
        stateName: e.stateName, pop: e.pop,
      },
      tier,
      kind: KIND_RANK[e.kind],
      pop: e.pop,
    });
  }

  scored.sort((a, b) =>
    a.tier - b.tier || b.pop - a.pop || a.kind - b.kind ||
    a.hit.name.localeCompare(b.hit.name) || a.hit.abbr.localeCompare(b.hit.abbr));
  return scored.slice(0, limit).map((s) => s.hit);
}

/* Where a hit lands in a given layer.

   `fipsToRegion` is the reverse of the `members` list each region's
   geometry already carries, built once when that layer loads. Returns
   null when the layer cannot represent the hit at all — a county whose
   region variant is not loaded, say — so the caller can offer to load it
   rather than silently highlighting nothing. */
export function resolve(
  hit: SearchHit,
  layerId: string,
  fipsToRegion: Map<string, string> | null,
): string | null {
  if (layerId === 'state') {
    return hit.kind === 'state' ? hit.target : (hit.stateName || null);
  }
  if (hit.kind === 'state') return null;   // a state has no single county
  if (layerId === 'county') return hit.target;
  if (layerId.startsWith('region:')) {
    return (fipsToRegion && fipsToRegion.get(hit.target)) || null;
  }
  return null;
}

/** fips -> region key, from the members list in a region geo doc. */
export function reverseMembers(geo: Record<string, { members?: string[] }>): Map<string, string> {
  const out = new Map<string, string>();
  for (const key in geo) {
    for (const f of geo[key].members || []) out.set(f, key);
  }
  return out;
}
