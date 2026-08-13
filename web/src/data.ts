import {
  COVERAGE_MIN, CRIMES, ESTIMATED_MIN, PILLAR_PERCENTILE, SRS_LAST,
} from './config';
import { active, registerLayer } from './layers';
import { smoothRec } from './smooth';
import { S } from './state';
import type {
  CountiesDoc, CountyRec, Feat, GeoDoc, RawRec, Rec, RegionsDoc, Row, StatesDoc,
  Store, YearMap,
} from './types';

/* Active layer helpers — everything downstream reads through these so
   the state and county layers share one render path. */
export function activeFeatures(): Feat[] {
  return active(S.geo).features();
}
export function activeStore(): Store {
  return active(S.geo).store();
}
export function featureKey(f: Feat): string {
  return active(S.geo).keyOf(f);
}
/** Singular noun for the active layer, for prose in the tooltip. */
export function activeNoun(): string {
  return active(S.geo).noun;
}

/* Consume data/states.json + data/geo-states.json, both produced by the
   build scripts. Areas are precomputed there, so nothing is recalculated
   at load time. */
export function buildData(statesDoc: StatesDoc | null, geoDoc: GeoDoc | null): void {
  if (!statesDoc || !statesDoc.states) {
    throw new Error('data/states.json is malformed — rebuild with: make data');
  }
  S.meta = statesDoc.meta || {};

  const yearSet = new Set<number>();
  for (const name in statesDoc.states) {
    const byYear: YearMap = new Map();
    const src = statesDoc.states[name];
    for (const ys in src) {
      const yr = +ys;
      if (!isFinite(yr)) continue;
      const rec = src[ys];
      byYear.set(yr, {
        pop: rec.pop,
        cov: (rec.cov === undefined ? null : rec.cov),
        // The state layer comes from the CDE API and carries no estimates
        // of its own; null means "not applicable here", not "zero".
        imp: (rec.imp === undefined ? null : rec.imp),
        src: rec.src || 'SRS',
        rates: rec.rates || {},
        totals: rec.totals || {},
      });
      yearSet.add(yr);
    }
    S.data.set(name, byYear);
  }
  S.years = [...yearSet].sort((a, b) => a - b);
  if (!S.years.length) throw new Error('states.json contains no usable years.');
  S.nibrsYears = ((S.meta.nibrsYears as number[]) || []).slice();

  /* --- geometry --------------------------------------------------- */
  let matched = 0;
  const missingGeo: string[] = [];
  S.features = [];
  for (const name in geoDoc) {
    const g = geoDoc[name];
    if (!S.data.has(name)) continue;
    matched++;
    S.features.push({
      type: 'Feature',
      geometry: g.geometry,
      props: { name, areaSqMi: g.area_sq_mi, fips: g.fips },
    });
  }
  for (const name of S.data.keys()) {
    if (!geoDoc || !(name in geoDoc)) missingGeo.push(name);
  }
  if (missingGeo.length) {
    console.warn('no geometry for: ' + missingGeo.join(', '));
  }
  if (matched < 40) {
    throw new Error('Only ' + matched +
      ' states joined between geometry and crime data — expected ~51.');
  }

  registerLayer({
    id: 'state', label: 'State', noun: 'state',
    features: () => S.features, store: () => S.data, keyOf: (f) => f.props.name,
  });

  recomputeScales();
  console.log('Joined ' + matched + ' states; years ' +
    S.years[0] + '–' + S.years[S.years.length - 1] +
    '; density ' + S.densMin.toFixed(2) + '–' + S.densMax.toFixed(0) + ' /sq mi');
}

/* One county-year, in either of the two encodings the build can emit.

   The national import writes "compact-1" — a flat array per county-year,
   [pop, cov, ...totals in meta.crimeOrder] — because the verbose object
   form costs ~360 bytes a record, and 3,135 counties x 65 years of that is
   74 MB of JSON for a browser to parse. The per-state API build still
   writes objects, so both must decode.

   Exported because this encoding is load-bearing: a silent mismatch
   between crimeOrder and the array positions would mislabel every offence
   in the county layer without throwing. */
export function decodeCountyRec(
  rec: CountyRec,
  year: number,
  compact: boolean,
  order: string[],
  record?: string[],
): Rec {
  if (!compact) {
    const r = rec as RawRec & { agencies?: number; reporting?: number };
    return {
      pop: r.pop,
      cov: (r.cov === undefined ? null : r.cov),
      imp: (r.imp === undefined ? null : r.imp),
      src: r.src || 'NIBRS',
      rates: r.rates || {},
      totals: r.totals || {},
      agencies: r.agencies,
      reporting: r.reporting,
    };
  }
  const arr = rec as number[];
  // Slot positions come from meta.record, never from a constant. compact-1
  // was [pop, cov, ...totals] and compact-2 inserts `imp` in front of the
  // totals; reading a fixed offset would silently relabel every offence in
  // the file the next time a field is added.
  const slots = record && record.length ? record : ['pop', 'cov'].concat(order);
  const first = slots.length - order.length;
  const at = (name: string): number | undefined => {
    const i = slots.indexOf(name);
    return i < 0 ? undefined : arr[i];
  };
  const pop = at('pop') ?? 0;
  const cov = at('cov');
  const imp = at('imp');
  const totals: Record<string, number> = {};
  const rates: Record<string, number> = {};
  for (let i = 0; i < order.length; i++) {
    const v = arr[first + i];
    if (v === undefined || v === null) continue;
    totals[order[i]] = v;
    // Rates are derived here rather than stored: they are the larger
    // of the two numbers and reconstructing them costs nothing.
    if (pop > 0) rates[order[i]] = v / pop * 100000;
  }
  return {
    pop,
    cov: (cov === undefined ? null : cov),
    imp: (imp === undefined ? null : imp),
    // Return A spans both collection systems; the seam is the same one
    // the state layer marks at 2019/2020.
    src: year > SRS_LAST ? 'NIBRS' : 'SRS',
    rates,
    totals,
  };
}

/* Optional region layer: contiguous groups of low-population counties.

   Same artifact shape as the county layer, so it goes through the same
   decoder. Its geometry file carries a `members` list and an already-
   composed name ("Rutland +2"), so nothing is appended to it the way
   " County" is below. */
export function buildRegions(
  variant: string, geoDoc: GeoDoc, dataDoc: RegionsDoc,
): number {
  const regions = (dataDoc && dataDoc.regions) || {};
  if (!Object.keys(regions).length) return 0;

  invalidateScales();
  const meta = (dataDoc && dataDoc.meta) || {};
  const compact = !!meta.format && meta.format.indexOf('compact-') === 0;
  const order = meta.crimeOrder || [];
  const record = meta.record;

  const store: Store = new Map();
  const features: Feat[] = [];
  for (const key in regions) {
    const g = geoDoc[key];
    if (!g) continue;
    const byYear: YearMap = new Map();
    for (const ys in regions[key]) {
      byYear.set(+ys, decodeCountyRec(regions[key][ys], +ys, compact, order, record));
    }
    store.set(key, byYear);
    features.push({
      type: 'Feature',
      geometry: g.geometry,
      props: { name: g.name || key, areaSqMi: g.area_sq_mi, fips: key },
    });
  }
  if (!features.length) return 0;

  const id = 'region:' + variant;
  registerLayer({
    id, label: 'Region', noun: 'region',
    features: () => features, store: () => store, keyOf: (f) => f.props.fips!,
  });
  console.log('Region layer ' + variant + ': ' + features.length + ' regions');
  return features.length;
}

/* Optional county layer. Stored parallel to the state layer so the
   geography selector can swap between them without a reload. */
export function buildCounties(geoDoc: GeoDoc, dataDoc: CountiesDoc): void {
  const counties = (dataDoc && dataDoc.counties) || {};
  if (!Object.keys(counties).length) return;

  // This layer's contents are about to change, so anything cached about
  // its scales is now a lie.
  invalidateScales();

  S.countyData = new Map();
  S.countyFeatures = [];
  let matched = 0;

  const meta = (dataDoc && dataDoc.meta) || {};
  const compact = !!meta.format && meta.format.indexOf('compact-') === 0;
  const order = meta.crimeOrder || [];
  const record = meta.record;
  const merged = meta.merged || {};
  const unmatched: string[] = [];

  for (const fips in counties) {
    const g = geoDoc[fips];
    if (!g) { unmatched.push(fips); continue; }
    const byYear: YearMap = new Map();
    for (const ys in counties[fips]) {
      byYear.set(+ys, decodeCountyRec(counties[fips][ys], +ys, compact, order, record));
    }
    S.countyData.set(fips, byYear);
    // Merged units are already whole places — "New York City County" would
    // be nonsense, and so would "Puerto Rico County".
    const label = merged[fips] || g.name || fips;
    S.countyFeatures.push({
      type: 'Feature',
      geometry: g.geometry,
      props: {
        name: merged[fips] ? label
          : label + (label.match(/(County|Parish|Borough|city|Area)$/) ? '' : ' County'),
        areaSqMi: g.area_sq_mi,
        fips,
      },
    });
    matched++;
  }

  S.countyUnmatched = unmatched.length;
  S.hasCounties = matched > 0;
  if (S.hasCounties) {
    registerLayer({
      id: 'county', label: 'County', noun: 'county',
      features: () => S.countyFeatures, store: () => S.countyData!,
      keyOf: (f) => f.props.fips!,
    });
  }
  // A county key with no geometry is data that will never be drawn, and it
  // used to vanish here without a word: 17 years of Oglala Lakota and 64 of
  // Bedford City went missing behind a bare `continue`. fips.py should keep
  // this at zero, but a silent detector is how the next boundary change
  // hides too, so it is counted and surfaced through the Status panel.
  if (unmatched.length) {
    console.warn('County data with no geometry (' + unmatched.length + '): ' +
      unmatched.slice(0, 12).join(', ') + (unmatched.length > 12 ? '…' : ''));
  }
  // No DOM here. Whether the button is usable and what it says is the UI
  // layer's business (see counties.ts and status.ts) - this function's job
  // is the join, and keeping it DOM-free keeps it testable.
  if (S.hasCounties) console.log('County layer: ' + matched + ' counties');
}

/* --- fixed global scales -----------------------------------------
   Both channels are normalised across every unit AND every year, so
   moving the year slider shows genuine change rather than re-scaling
   underneath you. Re-run whenever the active layer changes.        */
interface ScaleSet {
  crimeMax: Record<string, number>;
  densMin: number;
  densMax: number;
  absMax: number;
}

/* Scales are a pure function of a layer's contents, and a layer's contents
   never change after it loads — so the answer is cached per layer rather
   than recomputed on every geography toggle. A percentile has to sort, and
   sorting nine arrays of 200,000 county-years on each switch is real work
   for a result that cannot have changed. */
const scaleCache = new Map<string, ScaleSet>();

/** Drop cached scales. Call when a layer's data is replaced. */
export function invalidateScales(): void {
  scaleCache.clear();
}

export function recomputeScales(): void {
  const key = active(S.geo).id + '@' + S.window;
  const hit = scaleCache.get(key);
  if (hit) {
    for (const c of CRIMES) S.crimeMax[c.id] = hit.crimeMax[c.id];
    S.densMin = hit.densMin;
    S.densMax = hit.densMax;
    S.absMax = hit.absMax;
    return;
  }

  for (const c of CRIMES) S.crimeMax[c.id] = 0;
  S.densMin = Infinity;
  S.densMax = 0;
  S.absMax = 0;

  // Collected rather than reduced, because a percentile needs the whole
  // distribution. One array per crime across every unit and every year —
  // the fixed-domain rule is what makes the year slider show change
  // rather than re-scale underneath you, and it applies to the percentile
  // exactly as it applied to the maximum.
  const samples: Record<string, number[]> = {};
  for (const c of CRIMES) samples[c.id] = [];

  const feats = activeFeatures(), store = activeStore();
  for (const f of feats) {
    const byYear = store.get(featureKey(f));
    if (!byYear) continue;
    for (const year of byYear.keys()) {
      // Sample the smoothed value, because that is what gets drawn. A
      // scale built from single years would put the pillars against a
      // domain computed for different numbers.
      const rec = smoothRec(byYear, year, S.window);
      if (!rec) continue;
      // Agencies carry a precomputed county density; areas belong to
      // polygons, and an agency is a point.
      const d = isFinite(rec.dens!) && rec.dens! > 0
        ? rec.dens!
        : ((isFinite(rec.pop) && rec.pop > 0 && f.props.areaSqMi > 0)
          ? rec.pop / f.props.areaSqMi : NaN);
      if (isFinite(d)) {
        if (d < S.densMin) S.densMin = d;
        if (d > S.densMax) S.densMax = d;
      }
      for (const c of CRIMES) {
        const v = rec.rates[c.id];
        if (!isFinite(v)) continue;
        samples[c.id].push(v);
      }
    }
  }

  for (const c of CRIMES) {
    S.crimeMax[c.id] = percentile(samples[c.id], PILLAR_PERCENTILE) || 1;
    // The absolute reference is the largest of the per-crime references,
    // NOT the percentile of every crime pooled together. Pooling is
    // dominated by whichever crime is most common — property — and would
    // put a far larger share of property county-years at full height,
    // destroying the cross-crime comparison absolute mode exists for.
    if (S.crimeMax[c.id] > S.absMax) S.absMax = S.crimeMax[c.id];
  }
  if (!isFinite(S.densMin)) S.densMin = 0;
  if (S.densMax <= 0) S.densMax = 1;
  if (S.absMax <= 0) S.absMax = 1;

  scaleCache.set(key, {
    crimeMax: { ...S.crimeMax },
    densMin: S.densMin,
    densMax: S.densMax,
    absMax: S.absMax,
  });
}

/* Linear-interpolated percentile. Sorts a copy: the caller's array is
   reused across crime types and reordering it would be a silent bug. */
export function percentile(values: number[], p: number): number {
  if (!values.length) return 0;
  const v = values.slice().sort((a, b) => a - b);
  if (v.length === 1) return v[0];
  const k = (v.length - 1) * Math.max(0, Math.min(1, p));
  const lo = Math.floor(k);
  if (lo >= v.length - 1) return v[v.length - 1];
  return v[lo] + (v[lo + 1] - v[lo]) * (k - lo);
}

/* Values for the current crime type + year. Scaling maxima are NOT
   derived here — both channels use the fixed global ranges computed
   in recomputeScales(). maxRate is returned for reporting only.      */
export function currentValues() {
  const def = CRIMES.find((c) => c.id === S.crime)!;
  const out: Row[] = [];
  let maxRate = 0;

  const store = activeStore();
  for (const f of activeFeatures()) {
    const byYear = store.get(featureKey(f));
    const rec = byYear && smoothRec(byYear, S.year, S.window);
    const rate = rec ? rec.rates[def.id] : NaN;
    const total = rec ? rec.totals[def.id] : NaN;
    const pop = rec ? rec.pop : NaN;
    const dens = (rec && isFinite(rec.dens!) && rec.dens! > 0)
      ? rec.dens!
      : ((isFinite(pop) && f.props.areaSqMi > 0) ? pop / f.props.areaSqMi : NaN);
    const cov = rec ? rec.cov : null;
    const imp = rec ? rec.imp : null;
    const src = rec ? rec.src : null;
    const lowCov = (cov != null && cov < COVERAGE_MIN);
    const estimated = (imp != null && imp >= ESTIMATED_MIN);

    if (isFinite(rate) && rate > maxRate) maxRate = rate;
    out.push({ f, rate, total, pop, dens, cov, imp, span: rec ? rec.span : undefined,
      src, lowCov, estimated });
  }
  return { def, rows: out, maxRate };
}

/* Which crime types the active layer actually holds. The dropdown lists
   all nine, but a build scoped with --crimes carries only some: selecting
   a missing one yields no rate, and a zero-height pillar reads as "no
   crime here" when it means "not built". */
export function availableCrimes(): Set<string> {
  const have = new Set<string>();
  const store = activeStore();
  if (!store) return have;
  for (const byYear of store.values()) {
    for (const rec of byYear.values()) {
      for (const c in rec.rates) if (isFinite(rec.rates[c])) have.add(c);
    }
  }
  return have;
}
