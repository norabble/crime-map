import { describe, expect, it } from 'vitest';
import {
  densityFromNormalized, densityLogDomain, isFaded, mixToward,
  normalizedDensity, normalizedRate, rampColor,
} from './scales';
import type { RGB } from './types';

const STOPS: RGB[] = [[0, 0, 0], [100, 100, 100], [200, 200, 200]];

describe('rampColor', () => {
  it('hits the endpoints exactly', () => {
    expect(rampColor(0, STOPS)).toEqual([0, 0, 0]);
    expect(rampColor(1, STOPS)).toEqual([200, 200, 200]);
  });

  it('interpolates between stops', () => {
    expect(rampColor(0.25, STOPS)).toEqual([50, 50, 50]);
    expect(rampColor(0.5, STOPS)).toEqual([100, 100, 100]);
  });

  it('clamps out-of-range input rather than extrapolating', () => {
    expect(rampColor(-5, STOPS)).toEqual([0, 0, 0]);
    expect(rampColor(99, STOPS)).toEqual([200, 200, 200]);
  });
});

/* The fade threshold. These four properties are what the control promises;
   each was checked against real data/ before the feature shipped, and this
   is the port of those checks. */
describe('isFaded', () => {
  it('fades nothing at all when the threshold is zero', () => {
    // The map at fade=0 must be pixel-identical to the map without the
    // feature, including for units that have no data (tHeight NaN).
    for (const t of [0, 0.5, 1, NaN]) {
      expect(isFaded(t, 0)).toBe(false);
    }
  });

  it('fades units below the threshold and keeps those at or above it', () => {
    expect(isFaded(0.29, 0.3)).toBe(true);
    expect(isFaded(0.3, 0.3)).toBe(false);
    expect(isFaded(0.31, 0.3)).toBe(false);
  });

  it('treats a unit with no data as faded once the feature is on', () => {
    expect(isFaded(NaN, 0.1)).toBe(true);
  });

  it('is monotonic: raising the threshold never un-fades anything', () => {
    const heights = [0, 0.05, 0.1, 0.22, 0.35, 0.5, 0.78, 1, NaN];
    let prev = -1;
    for (let f = 0.01; f <= 0.9; f += 0.01) {
      const n = heights.filter((t) => isFaded(t, f)).length;
      expect(n).toBeGreaterThanOrEqual(prev);
      prev = n;
    }
  });

  it('cannot depend on exaggeration, by construction', () => {
    // Not a behavioural proof - it is a tripwire on the signature. The
    // guarantee that exaggeration never changes WHICH pillars fade comes
    // from the predicate having no access to it, so if someone widens
    // this to take an exaggeration argument, this fails and forces the
    // question to be asked deliberately rather than slipped in.
    expect(isFaded.length).toBe(2);
  });
});

describe('density log domain', () => {
  const dom = densityLogDomain(0.39, 12182); // real state-layer extremes

  it('round-trips a normalised position back to a real density', () => {
    for (const t of [0.1, 0.25, 0.5, 0.75, 0.9]) {
      expect(normalizedDensity(densityFromNormalized(t, dom), dom)).toBeCloseTo(t, 12);
    }
  });

  it('maps the domain ends to 0 and 1', () => {
    expect(normalizedDensity(0.39, dom)).toBeCloseTo(0, 12);
    expect(normalizedDensity(12182, dom)).toBeCloseTo(1, 12);
  });

  it('clamps rather than extrapolating outside the fixed domain', () => {
    expect(normalizedDensity(0.0001, dom)).toBe(0);
    expect(normalizedDensity(1e9, dom)).toBe(1);
  });

  it('survives a degenerate domain without dividing by zero', () => {
    const flat = densityLogDomain(5, 5);
    expect(Number.isFinite(normalizedDensity(5, flat))).toBe(true);
  });

  it('yields NaN for a unit with no density', () => {
    expect(normalizedDensity(NaN, dom)).toBeNaN();
  });
});

describe('normalizedRate', () => {
  it('is the fraction of the fixed domain', () => {
    expect(normalizedRate(292, 2921.8)).toBeCloseTo(0.09994, 4);
  });

  it('clamps above the domain instead of overflowing the scale', () => {
    expect(normalizedRate(5000, 2921.8)).toBe(1);
  });

  it('yields NaN for a unit with no rate', () => {
    expect(normalizedRate(NaN, 2921.8)).toBeNaN();
  });
});

describe('mixToward', () => {
  it('reproduces the low-coverage grey mix', () => {
    // Was col*0.34 + 96*0.66 inline; must stay numerically identical.
    expect(mixToward([200, 100, 50], [96, 96, 96], 0.66))
      .toEqual([Math.round(200 * 0.34 + 96 * 0.66),
        Math.round(100 * 0.34 + 96 * 0.66),
        Math.round(50 * 0.34 + 96 * 0.66)]);
  });

  it('returns the original colour at amount 0', () => {
    expect(mixToward([1, 2, 3], [255, 255, 255], 0)).toEqual([1, 2, 3]);
  });
});
