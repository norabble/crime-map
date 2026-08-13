import { describe, expect, it } from 'vitest';
import { smoothRec, windowYears } from './smooth';
import type { Rec, YearMap } from './types';

function rec(pop: number, violent: number, cov: number | null = 100,
             imp: number | null = 0): Rec {
  return {
    pop,
    cov,
    imp,
    src: 'SRS',
    rates: pop > 0 ? { violent: violent / pop * 100000 } : {},
    totals: { violent },
  };
}

function years(entries: [number, Rec][]): YearMap {
  return new Map(entries);
}

describe('smoothRec', () => {
  it('is a no-op at window 1, returning the very same record', () => {
    // The guarantee that the window is a layer over the existing build
    // rather than a rewrite of it.
    const r = rec(1000, 10);
    const by = years([[2000, r]]);
    expect(smoothRec(by, 2000, 1)).toBe(r);
  });

  it('sums counts over summed population rather than averaging rates', () => {
    // The fixture has a CHANGING population on purpose. With equal
    // populations both methods agree, so an equal-population fixture
    // cannot catch the mistake this test exists for.
    //   correct:  (10+30+30) / (10000+50000+50000) = 70/110000 = 63.6/100k
    //   averaging rates: (100 + 60 + 60)/3 = 73.3/100k
    const by = years([
      [1999, rec(10000, 10)],
      [2000, rec(50000, 30)],
      [2001, rec(50000, 30)],
    ]);
    const r = smoothRec(by, 2000, 3)!;
    expect(r.rates.violent).toBeCloseTo(70 / 110000 * 100000, 6);
    expect(r.rates.violent).not.toBeCloseTo((100 + 60 + 60) / 3, 1);
  });

  it('reports the population it actually divided by', () => {
    const by = years([
      [1999, rec(10000, 10)],
      [2000, rec(50000, 30)],
      [2001, rec(50000, 30)],
    ]);
    const r = smoothRec(by, 2000, 3)!;
    expect(r.pop).toBe(110000);
    expect(r.totals.violent).toBe(70);
    expect(r.span).toEqual([1999, 2001]);
  });

  it('truncates at the ends of the series instead of inventing years', () => {
    const by = years([
      [2000, rec(1000, 5)],
      [2001, rec(1000, 5)],
    ]);
    const r = smoothRec(by, 2000, 5)!;
    expect(r.span).toEqual([2000, 2001]);
    expect(r.pop).toBe(2000);
  });

  it('returns nothing when the centre year is absent', () => {
    // A window smooths a value that exists; it must not conjure one for a
    // year the unit never reported.
    const by = years([[1999, rec(1000, 5)], [2001, rec(1000, 5)]]);
    expect(smoothRec(by, 2000, 3)).toBeUndefined();
  });

  it('weights coverage by population, not by year', () => {
    // A big year at 100% and a small year at 0% is mostly covered.
    const by = years([
      [2000, rec(90000, 100, 100)],
      [2001, rec(10000, 0, 0)],
    ]);
    const r = smoothRec(by, 2000, 3)!;
    expect(r.cov).toBeCloseTo(90, 6);
  });

  it('weights the estimated share by offences, not by year', () => {
    // 10 filed offences and 90 estimated ones is 90% estimated, whichever
    // year each came from.
    const by = years([
      [2000, rec(1000, 10, 100, 0)],
      [2001, rec(1000, 90, 100, 1)],
    ]);
    const r = smoothRec(by, 2000, 3)!;
    expect(r.imp).toBeCloseTo(0.9, 6);
  });

  it('keeps null coverage null rather than turning it into zero', () => {
    // null means "unknown" and the viewer tests it before de-saturating;
    // zero would mean "nothing reported" and grey the unit out.
    const by = years([
      [2000, rec(1000, 5, null)],
      [2001, rec(1000, 5, null)],
    ]);
    expect(smoothRec(by, 2000, 3)!.cov).toBeNull();
  });
});

describe('windowYears', () => {
  const by = years([
    [1998, rec(1, 0)], [1999, rec(1, 0)], [2000, rec(1, 0)], [2001, rec(1, 0)],
  ]);

  it('centres the window on the requested year', () => {
    expect(windowYears(by, 1999, 3)).toEqual([1998, 1999, 2000]);
  });

  it('skips years the unit has no record for', () => {
    const gappy = years([[1998, rec(1, 0)], [2000, rec(1, 0)]]);
    expect(windowYears(gappy, 1999, 3)).toEqual([1998, 2000]);
  });

  it('narrows rather than extending past the series', () => {
    expect(windowYears(by, 2001, 5)).toEqual([1999, 2000, 2001]);
  });
});
