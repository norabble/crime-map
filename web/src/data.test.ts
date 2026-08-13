import { beforeEach, describe, expect, it } from 'vitest';
import { SRS_LAST } from './config';
import { decodeCountyRec, recomputeScales } from './data';
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
});

describe('recomputeScales', () => {
  it('takes the crime maximum across every year, not the current one', () => {
    recomputeScales();
    // 900 is Alpha in 1990. A per-year domain would give 300 for 2020.
    expect(S.crimeMax.violent).toBe(900);
  });

  it('takes the density range across every unit and every year', () => {
    recomputeScales();
    // Alpha 1990: 1000/100 = 10 (min). Beta 2020: 60000/1000 = 60 (max).
    expect(S.densMin).toBe(10);
    expect(S.densMax).toBe(60);
  });

  it('takes absMax across all crime types at once', () => {
    recomputeScales();
    expect(S.absMax).toBe(900);
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
