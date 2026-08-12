/* Colour ramps and the normalisation maths behind both visual channels.

   Everything here is pure. That is deliberate: the fixed-domain rule and
   the fade threshold are the two places where a silent regression would
   change every figure on screen without throwing, so they need to be
   testable without a browser. */

import type { RGB } from './types';

export function rampColor(t: number, stops: RGB[]): RGB {
  t = Math.max(0, Math.min(1, t));
  const x = t * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i, a = stops[i], b = stops[i + 1];
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f)
  ];
}

export function cssGradient(stops: RGB[]): string {
  return 'linear-gradient(90deg,' +
    stops.map((c, i) => 'rgb(' + c.join(',') + ') ' +
      Math.round(i / (stops.length - 1) * 100) + '%').join(',') + ')';
}

export interface LogDomain {
  logMin: number;
  logMax: number;
  logSpan: number;
}

/* Density spans four orders of magnitude, so both of its roles - colour and
   height - run on a log scale. Linear would leave every rural unit flat
   against Manhattan. The 0.5 and 1 floors keep log10 away from zero and
   negative densities. */
export function densityLogDomain(densMin: number, densMax: number): LogDomain {
  const logMin = Math.log10(Math.max(0.5, densMin));
  const logMax = Math.log10(Math.max(1, densMax));
  return { logMin, logMax, logSpan: Math.max(0.0001, logMax - logMin) };
}

/** Position of a rate inside the fixed rate domain, 0..1. NaN if no data. */
export function normalizedRate(rate: number, heightRef: number): number {
  return isFinite(rate) ? Math.min(1, rate / heightRef) : NaN;
}

/** Position of a density inside the fixed log domain, 0..1. NaN if no data. */
export function normalizedDensity(dens: number, d: LogDomain): number {
  return isFinite(dens)
    ? Math.min(1, Math.max(0, (Math.log10(Math.max(0.5, dens)) - d.logMin) / d.logSpan))
    : NaN;
}

/** Inverse of normalizedDensity, for reporting a threshold in real units. */
export function densityFromNormalized(t: number, d: LogDomain): number {
  return Math.pow(10, d.logMin + t * d.logSpan);
}

/* Whether a unit falls below the fade threshold.

   `tHeight` is the PRE-exaggeration position in the fixed domain. That is
   the whole point: dragging the exaggeration slider must change how tall
   the pillars are and never which ones are faded.

   The `fade > 0` guard leaves units with no data (tHeight is NaN) on their
   own alpha-70 treatment rather than fading them while the feature is off. */
export function isFaded(tHeight: number, fade: number): boolean {
  return fade > 0 && !(isFinite(tHeight) && tHeight >= fade);
}

/** Mix `col` toward `toward` by `amount` (0 = unchanged, 1 = fully toward). */
export function mixToward(col: RGB, toward: RGB, amount: number): RGB {
  const keep = 1 - amount;
  return [
    Math.round(col[0] * keep + toward[0] * amount),
    Math.round(col[1] * keep + toward[1] * amount),
    Math.round(col[2] * keep + toward[2] * amount)
  ];
}
