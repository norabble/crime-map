import { COVERAGE_MIN, CRIMES, SRS_LAST } from './config';
import { S } from './state';
import type {
  CountiesDoc, CountyRec, Feat, GeoDoc, RawRec, Rec, Row, StatesDoc, Store, YearMap,
} from './types';

/* Active layer helpers — everything downstream reads through these so
   the state and county layers share one render path. */
export function activeFeatures(): Feat[] {
  return S.geo === 'county' && S.hasCounties ? S.countyFeatures : S.features;
}
export function activeStore(): Store {
  return S.geo === 'county' && S.hasCounties ? S.countyData! : S.data;
}
export function featureKey(f: Feat): string {
  return S.geo === 'county' && S.hasCounties ? f.props.fips! : f.props.name;
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
): Rec {
  if (!compact) {
    const r = rec as RawRec & { agencies?: number; reporting?: number };
    return {
      pop: r.pop,
      cov: (r.cov === undefined ? null : r.cov),
      src: r.src || 'NIBRS',
      rates: r.rates || {},
      totals: r.totals || {},
      agencies: r.agencies,
      reporting: r.reporting,
    };
  }
  const arr = rec as number[];
  const pop = arr[0], cov = arr[1];
  const totals: Record<string, number> = {};
  const rates: Record<string, number> = {};
  for (let i = 0; i < order.length; i++) {
    const v = arr[2 + i];
    if (v === undefined || v === null) continue;
    totals[order[i]] = v;
    // Rates are derived here rather than stored: they are the larger
    // of the two numbers and reconstructing them costs nothing.
    if (pop > 0) rates[order[i]] = v / pop * 100000;
  }
  return {
    pop,
    cov: (cov === undefined ? null : cov),
    // Return A spans both collection systems; the seam is the same one
    // the state layer marks at 2019/2020.
    src: year > SRS_LAST ? 'NIBRS' : 'SRS',
    rates,
    totals,
  };
}

/* Optional county layer. Stored parallel to the state layer so the
   geography selector can swap between them without a reload. */
export function buildCounties(geoDoc: GeoDoc, dataDoc: CountiesDoc): void {
  const counties = (dataDoc && dataDoc.counties) || {};
  if (!Object.keys(counties).length) return;

  S.countyData = new Map();
  S.countyFeatures = [];
  let matched = 0;

  const meta = (dataDoc && dataDoc.meta) || {};
  const compact = meta.format === 'compact-1';
  const order = meta.crimeOrder || [];

  for (const fips in counties) {
    const g = geoDoc[fips];
    if (!g) continue;
    const byYear: YearMap = new Map();
    for (const ys in counties[fips]) {
      byYear.set(+ys, decodeCountyRec(counties[fips][ys], +ys, compact, order));
    }
    S.countyData.set(fips, byYear);
    S.countyFeatures.push({
      type: 'Feature',
      geometry: g.geometry,
      props: {
        name: g.name + (g.name!.match(/(County|Parish|Borough|city|Area)$/) ? '' : ' County'),
        areaSqMi: g.area_sq_mi,
        fips,
      },
    });
    matched++;
  }

  S.hasCounties = matched > 0;
  if (S.hasCounties) {
    const btn = document.querySelector<HTMLButtonElement>('#geoSeg button[data-geo="county"]');
    if (btn) {
      btn.disabled = false;
      btn.title = matched + ' counties loaded from data/counties.json';
    }
    console.log('County layer: ' + matched + ' counties');
  }
}

/* --- fixed global scales -----------------------------------------
   Both channels are normalised across every unit AND every year, so
   moving the year slider shows genuine change rather than re-scaling
   underneath you. Re-run whenever the active layer changes.        */
export function recomputeScales(): void {
  for (const c of CRIMES) S.crimeMax[c.id] = 0;
  S.densMin = Infinity;
  S.densMax = 0;
  S.absMax = 0;

  const feats = activeFeatures(), store = activeStore();
  for (const f of feats) {
    const byYear = store.get(featureKey(f));
    if (!byYear) continue;
    for (const rec of byYear.values()) {
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
        if (v > S.crimeMax[c.id]) S.crimeMax[c.id] = v;
        if (v > S.absMax) S.absMax = v;
      }
    }
  }
  if (!isFinite(S.densMin)) S.densMin = 0;
  if (S.densMax <= 0) S.densMax = 1;
  for (const c of CRIMES) if (!S.crimeMax[c.id]) S.crimeMax[c.id] = 1;
  if (S.absMax <= 0) S.absMax = 1;
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
    const rec = byYear && byYear.get(S.year);
    const rate = rec ? rec.rates[def.id] : NaN;
    const total = rec ? rec.totals[def.id] : NaN;
    const pop = rec ? rec.pop : NaN;
    const dens = (rec && isFinite(rec.dens!) && rec.dens! > 0)
      ? rec.dens!
      : ((isFinite(pop) && f.props.areaSqMi > 0) ? pop / f.props.areaSqMi : NaN);
    const cov = rec ? rec.cov : null;
    const src = rec ? rec.src : null;
    const lowCov = (cov != null && cov < COVERAGE_MIN);

    if (isFinite(rate) && rate > maxRate) maxRate = rate;
    out.push({ f, rate, total, pop, dens, cov, src, lowCov });
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
