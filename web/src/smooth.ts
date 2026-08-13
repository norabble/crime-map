/* Multi-year windows.

   Spatial aggregation cannot fix rare crimes. Rate noise is Poisson, so
   the relative error is 1/sqrt(expected events), and a 100,000-person
   region expects about 6.5 murders a year — roughly 39% error no matter
   how the boundary is drawn. Reaching 10% would take 1.5 million people,
   which is a state. Widening the window is the other axis: three years of
   a region is three times the events.

   THE ONE THING THAT MATTERS HERE
   -------------------------------
   Sum the counts and sum the population, then divide. Averaging the
   yearly *rates* is a different and wrong number: it weights a year of
   40,000 people the same as a year of 60,000, so a growing unit reads as
   though its rate fell. The test fixture for this uses a population that
   changes, precisely because an equal-population fixture cannot tell the
   two apart. */

import type { Rec, YearMap } from './types';

/** Selectable windows, in years. Odd so they can be centred. */
export const WINDOWS = [1, 3, 5] as const;

/** Years actually available in a centred window, in order. */
export function windowYears(byYear: YearMap, year: number, window: number): number[] {
  if (window <= 1) return byYear.has(year) ? [year] : [];
  const half = Math.floor(window / 2);
  const out: number[] = [];
  for (let y = year - half; y <= year + half; y++) {
    if (byYear.has(y)) out.push(y);
  }
  return out;
}

/* One unit-year, widened.

   Returns undefined when the centre year itself has no record: a window
   is a smoothing of a value that exists, not a way to invent one for a
   year the unit was absent. Otherwise every neighbouring year present in
   the store contributes, and the window silently truncates at the ends of
   the series — 1960 and 2024 are averages of fewer years, which the
   tooltip says. */
export function smoothRec(
  byYear: YearMap, year: number, window: number,
): Rec | undefined {
  const centre = byYear.get(year);
  if (!centre || window <= 1) return centre;

  const years = windowYears(byYear, year, window);
  if (years.length <= 1) return centre;

  let pop = 0;
  let repPop = 0;
  let estOff = 0;
  let allOff = 0;
  const totals: Record<string, number> = {};

  for (const y of years) {
    const r = byYear.get(y)!;
    pop += r.pop;
    if (r.cov != null) repPop += r.cov / 100 * r.pop;
    let n = 0;
    for (const k in r.totals) {
      totals[k] = (totals[k] || 0) + r.totals[k];
      n += r.totals[k];
    }
    allOff += n;
    if (r.imp != null) estOff += r.imp * n;
  }

  const rates: Record<string, number> = {};
  if (pop > 0) {
    for (const k in totals) rates[k] = totals[k] / pop * 100000;
  }

  return {
    // Population is the window's total, matching the totals it divides.
    // Showing a single year's population beside a three-year count would
    // invite exactly the arithmetic this function exists to prevent.
    pop,
    cov: centre.cov == null ? null : (pop > 0 ? repPop / pop * 100 : null),
    imp: centre.imp == null ? null : (allOff > 0 ? estOff / allOff : 0),
    src: centre.src,
    rates,
    totals,
    span: [years[0], years[years.length - 1]],
  };
}
