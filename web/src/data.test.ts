import { beforeEach, describe, expect, it } from 'vitest';
import { SRS_LAST } from './config';
import {
  decodeCountyRec, invalidateScales, percentile, recomputeScales,
} from './data';
import { clearLayers, registerLayer } from './layers';
import { S } from './state';
import type { Feat, Rec, YearMap } from './types';

/* ─────────────────────────────────────────────────────────────────
   compact-1 county encoding

   counties.json stores each county-year as [pop, cov, ...totals] ordered
   by meta.crimeOrder, because the verbose object form is 74 MB against
   8.4 MB. A mismatch between crimeOrder and the array positions would
   mislabel every offence in the county layer without throwing anything.
   ───────────────────────────────────────────────────────────────── */
const ORDER = ['violent', 'murder', 'burglary'];

describe('decodeCountyRec: compact-1', () => {
  it('reads pop and cov from the first two slots', () => {
    const r = decodeCountyRec([50000, 91.5, 100, 2, 400], 2023, true, ORDER);
    expect(r.pop).toBe(50000);
    expect(r.cov).toBe(91.5);
  });

  it('maps totals positionally against crimeOrder', () => {
    const r = decodeCountyRec([50000, 91.5, 100, 2, 400], 2023, true, ORDER);
    expect(r.totals).toEqual({ violent: 100, murder: 2, burglary: 400 });
  });

  it('derives rates per 100k from the totals', () => {
    const r = decodeCountyRec([50000, 91.5, 100, 2, 400], 2023, true, ORDER);
    expect(r.rates.violent).toBeCloseTo(200, 9);
    expect(r.rates.murder).toBeCloseTo(4, 9);
    expect(r.rates.burglary).toBeCloseTo(800, 9);
  });

  it('keeps a genuine zero as a reported zero', () => {
    // A county that filed twelve real zeros is data, not a gap. Skipping
    // on falsiness rather than null would silently delete quiet years.
    const r = decodeCountyRec([50000, 100, 0, 0, 5], 2023, true, ORDER);
    expect(r.totals.violent).toBe(0);
    expect(r.rates.violent).toBe(0);
    expect('murder' in r.totals).toBe(true);
  });

  it('omits offences whose slot is null or absent', () => {
    const r = decodeCountyRec([50000, 100, 10, null as unknown as number], 2023, true, ORDER);
    expect(r.totals.violent).toBe(10);
    expect('murder' in r.totals).toBe(false);
    expect('burglary' in r.totals).toBe(false);
  });

  it('emits no rates when population is zero, but keeps the counts', () => {
    const r = decodeCountyRec([0, 100, 7], 2023, true, ORDER);
    expect(r.totals.violent).toBe(7);
    expect(r.rates.violent).toBeUndefined();
  });

  it('labels the collection system at the 2019/2020 seam', () => {
    expect(decodeCountyRec([1, 1, 1], SRS_LAST, true, ORDER).src).toBe('SRS');
    expect(decodeCountyRec([1, 1, 1], SRS_LAST + 1, true, ORDER).src).toBe('NIBRS');
  });

  it('turns a missing coverage figure into null, not undefined', () => {
    // null means "unknown"; the viewer tests `cov != null` before
    // de-saturating, so undefined would work by luck rather than design.
    const r = decodeCountyRec([50000], 2023, true, ORDER);
    expect(r.cov).toBeNull();
  });
});

/* ─────────────────────────────────────────────────────────────────
   compact-2, and why offsets come from meta.record

   compact-2 inserts `imp` between `cov` and the totals. The old decoder
   hardcoded totals at index 2, so reading a compact-2 file with it would
   have labelled `imp` as the first offence and shifted every other one —
   silently, with no error and plausible-looking numbers.
   ───────────────────────────────────────────────────────────────── */
const REC2 = ['pop', 'cov', 'imp'].concat(ORDER);

describe('decodeCountyRec: compact-2', () => {
  it('reads imp and offsets the totals past it', () => {
    const r = decodeCountyRec([50000, 91.5, 0.25, 100, 2, 400], 2023, true, ORDER, REC2);
    expect(r.pop).toBe(50000);
    expect(r.cov).toBe(91.5);
    expect(r.imp).toBe(0.25);
    expect(r.totals).toEqual({ violent: 100, murder: 2, burglary: 400 });
  });

  it('still decodes compact-1 when meta.record says so', () => {
    const r = decodeCountyRec([50000, 91.5, 100, 2, 400], 2023, true, ORDER,
      ['pop', 'cov'].concat(ORDER));
    expect(r.totals).toEqual({ violent: 100, murder: 2, burglary: 400 });
    expect(r.imp).toBeNull();
  });

  it('assumes compact-1 when meta.record is absent, as old files are', () => {
    const r = decodeCountyRec([50000, 91.5, 100, 2, 400], 2023, true, ORDER);
    expect(r.totals).toEqual({ violent: 100, murder: 2, burglary: 400 });
    expect(r.imp).toBeNull();
  });

  it('follows meta.record even when the slots are reordered', () => {
    // The point of reading offsets rather than assuming them: this layout
    // is not one the builder emits, and it still decodes correctly.
    const r = decodeCountyRec([0.5, 50000, 91.5, 100, 2, 400], 2023, true, ORDER,
      ['imp', 'pop', 'cov'].concat(ORDER));
    expect(r.imp).toBe(0.5);
    expect(r.pop).toBe(50000);
    expect(r.cov).toBe(91.5);
    expect(r.totals.violent).toBe(100);
  });

  it('distinguishes a fully-estimated county-year from an absent one', () => {
    // cov 0 with imp 1 is a real county nobody filed for. It must decode
    // as a drawable figure, not as missing data.
    const r = decodeCountyRec([50000, 0, 1, 100, 2, 400], 2023, true, ORDER, REC2);
    expect(r.cov).toBe(0);
    expect(r.imp).toBe(1);
    expect(r.rates.violent).toBeCloseTo(200, 9);
  });
});

describe('decodeCountyRec: verbose object form', () => {
  it('passes through the per-state API build unchanged', () => {
    const r = decodeCountyRec(
      { pop: 100, cov: 80, src: 'NIBRS', rates: { violent: 5 }, totals: { violent: 1 } },
      2023, false, ORDER,
    );
    expect(r.pop).toBe(100);
    expect(r.rates).toEqual({ violent: 5 });
    expect(r.totals).toEqual({ violent: 1 });
  });

  it('defaults missing collections rather than yielding undefined', () => {
    const r = decodeCountyRec({ pop: 100 }, 2023, false, ORDER);
    expect(r.rates).toEqual({});
    expect(r.totals).toEqual({});
    expect(r.cov).toBeNull();
    expect(r.src).toBe('NIBRS');
  });
});

/* ─────────────────────────────────────────────────────────────────
   Fixed scale domains

   Height and colour are normalised across ALL years, never per-year.
   Per-year normalisation makes the tallest unit full height in every
   year and hides all temporal change. That shipped once.
   ───────────────────────────────────────────────────────────────── */
function feat(name: string, areaSqMi: number): Feat {
  return {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [] },
    props: { name, areaSqMi },
  };
}

function rec(pop: number, rates: Record<string, number>): Rec {
  return { pop, cov: null, imp: null, src: 'SRS', rates, totals: {} };
}

function years(entries: [number, Rec][]): YearMap {
  return new Map(entries);
}

beforeEach(() => {
  S.geo = 'state';
  S.hasCounties = false;
  S.features = [feat('Alpha', 100), feat('Beta', 1000)];
  S.data = new Map([
    ['Alpha', years([
      [1990, rec(1000, { violent: 900 })],   // the all-time peak
      [2020, rec(2000, { violent: 100 })],
    ])],
    ['Beta', years([
      [1990, rec(50000, { violent: 300, murder: 12 })],
      [2020, rec(60000, { violent: 200, murder: 4 })],
    ])],
  ]);
  S.countyData = null;
  S.countyFeatures = [];
  S.geo = 'state';
  // The real app registers this in buildData(). Registering accessors
  // rather than the arrays themselves is what lets the fixtures above
  // reassign S.features and S.data and still be seen.
  clearLayers();
  registerLayer({
    id: 'state', label: 'State', noun: 'state',
    features: () => S.features, store: () => S.data, keyOf: (f) => f.props.name,
  });
  // Scales are cached per layer, so every test must start from a cold
  // cache or it inherits the previous test's fixture.
  invalidateScales();
});

describe('percentile', () => {
  const hundred = Array.from({ length: 100 }, (_, i) => i + 1); // 1..100

  it('is insensitive to how extreme the extreme value is', () => {
    // The whole point. A max-based scale moves with the worst datum; a
    // p99 barely notices it.
    const a = percentile(hundred.concat([1000]), 0.99);
    const b = percentile(hundred.concat([1000000]), 0.99);
    expect(a).toBeCloseTo(b, 6);
  });

  it('interpolates between neighbours rather than snapping to one', () => {
    expect(percentile([0, 10], 0.5)).toBe(5);
  });

  it('returns the endpoints at 0 and 1', () => {
    expect(percentile(hundred, 0)).toBe(1);
    expect(percentile(hundred, 1)).toBe(100);
  });

  it('does not reorder the caller\'s array', () => {
    // The samples array is reused across crime types; sorting in place
    // would corrupt every subsequent read of it.
    const v = [5, 1, 3];
    percentile(v, 0.5);
    expect(v).toEqual([5, 1, 3]);
  });

  it('survives empty and single-element input', () => {
    expect(percentile([], 0.99)).toBe(0);
    expect(percentile([42], 0.99)).toBe(42);
  });
});

describe('recomputeScales', () => {
  it('takes the crime reference across every year, not the current one', () => {
    recomputeScales();
    // Alpha 1990 is 900; 2020 alone would top out at 300. The reference is
    // a percentile now rather than the maximum, so this pins the invariant
    // that matters — all years, not this one — without pinning the
    // interpolation arithmetic.
    expect(S.crimeMax.violent).toBeGreaterThan(300);
    expect(S.crimeMax.violent).toBeLessThanOrEqual(900);
  });

  it('takes the density range across every unit and every year', () => {
    recomputeScales();
    // Alpha 1990: 1000/100 = 10 (min). Beta 2020: 60000/1000 = 60 (max).
    // Density keeps its true min/max — log10 already absorbs its tail.
    expect(S.densMin).toBe(10);
    expect(S.densMax).toBe(60);
  });

  it('takes absMax as the largest per-crime reference, not a pooled one', () => {
    recomputeScales();
    // Pooling every crime's rates together would be dominated by whichever
    // offence is most common and would drag the absolute scale below the
    // violent reference, breaking cross-crime comparison.
    expect(S.absMax).toBe(S.crimeMax.violent);
    expect(S.absMax).toBeGreaterThan(S.crimeMax.murder);
  });

  it('lets one absurd outlier no longer set the whole scale', () => {
    // The reason this changed. A single county-year of 500,000 per 100k
    // used to be the ceiling, which put every real unit under 1% of full
    // height and made the fade slider hide the entire map by its first
    // third. Against p99 the outlier is clamped instead of governing.
    //
    // Needs a realistic number of units to demonstrate: with a handful of
    // samples a 99th percentile interpolates to nearly the maximum, which
    // is correct — a percentile only buys headroom when there is data to
    // spare. The real layers have 3,380 state-years and 200,000
    // county-years.
    S.features = [];
    S.data = new Map();
    for (let i = 0; i < 200; i++) {
      S.features.push(feat('U' + i, 100));
      S.data.set('U' + i, years([[1990, rec(1000, { violent: 100 + i })]]));
    }
    S.features.push(feat('Freak', 100));
    S.data.set('Freak', years([[1990, rec(1000, { violent: 500000 })]]));

    recomputeScales();
    expect(S.crimeMax.violent).toBeLessThan(1000);
    // And the freak is still drawn — clamped to full height, not dropped.
    expect(500000 / S.crimeMax.violent).toBeGreaterThan(1);
  });

  it('caches per layer, and invalidating recomputes', () => {
    recomputeScales();
    const first = S.crimeMax.violent;

    // Same layer, changed data, no invalidation: the cache is meant to
    // hold, because in the real app a layer's data cannot change without
    // being rebuilt.
    S.data.get('Alpha')!.set(1975, rec(1000, { violent: 900000 }));
    recomputeScales();
    expect(S.crimeMax.violent).toBe(first);

    invalidateScales();
    recomputeScales();
    expect(S.crimeMax.violent).toBeGreaterThan(first);
  });

  it('gives an unseen crime type a domain of 1 rather than 0', () => {
    // A zero domain would divide every rate by zero and blank the map.
    recomputeScales();
    expect(S.crimeMax.robbery).toBe(1);
  });

  it('is idempotent - running it twice does not accumulate', () => {
    recomputeScales();
    const first = { ...S.crimeMax, densMin: S.densMin, densMax: S.densMax, absMax: S.absMax };
    recomputeScales();
    expect({ ...S.crimeMax, densMin: S.densMin, densMax: S.densMax, absMax: S.absMax })
      .toEqual(first);
  });

  it('falls back to a usable domain when there is no data at all', () => {
    S.features = [];
    S.data = new Map();
    recomputeScales();
    expect(S.densMin).toBe(0);
    expect(S.densMax).toBe(1);
    expect(S.absMax).toBe(1);
    expect(Number.isFinite(S.densMin)).toBe(true);
  });

  it('ignores units with no area rather than producing Infinity', () => {
    S.features = [feat('Zero', 0)];
    S.data = new Map([['Zero', years([[2020, rec(100, { violent: 5 })]])]]);
    recomputeScales();
    expect(Number.isFinite(S.densMax)).toBe(true);
    expect(S.crimeMax.violent).toBe(5);
  });
});
