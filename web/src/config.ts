import type { CrimeDef, Palette, RGB } from './types';

/* ---------- crime type definitions -------------------------------- */
/* Keys map onto CORGIS column names. `rate` is already per 100,000. */
export const CRIMES: CrimeDef[] = [
  {id:'violent',   label:'Violent crime — all',                rate:'Data.Rates.Violent.All',       total:'Data.Totals.Violent.All',       cde:'violent-crime'},
  {id:'property',  label:'Property crime — all (non-violent)', rate:'Data.Rates.Property.All',      total:'Data.Totals.Property.All',      cde:'property-crime'},
  {id:'murder',    label:'Homicide',                           rate:'Data.Rates.Violent.Murder',    total:'Data.Totals.Violent.Murder',    cde:'homicide'},
  {id:'rape',      label:'Rape',                               rate:'Data.Rates.Violent.Rape',      total:'Data.Totals.Violent.Rape',      cde:'rape'},
  {id:'robbery',   label:'Robbery',                            rate:'Data.Rates.Violent.Robbery',   total:'Data.Totals.Violent.Robbery',   cde:'robbery'},
  {id:'assault',   label:'Aggravated assault',                 rate:'Data.Rates.Violent.Assault',   total:'Data.Totals.Violent.Assault',   cde:'aggravated-assault'},
  {id:'burglary',  label:'Burglary',                           rate:'Data.Rates.Property.Burglary', total:'Data.Totals.Property.Burglary', cde:'burglary'},
  {id:'larceny',   label:'Theft (larceny)',                    rate:'Data.Rates.Property.Larceny',  total:'Data.Totals.Property.Larceny',  cde:'larceny'},
  {id:'motor',     label:'Motor vehicle theft',                rate:'Data.Rates.Property.Motor',    total:'Data.Totals.Property.Motor',    cde:'motor-vehicle-theft'}
];

export const SRS_LAST = 2019;      // last year of Summary Reporting System data
export const NIBRS_FIRST = 2020;   // first year pulled from the CDE API
export const COVERAGE_MIN = 80;    // below this %, a unit-year is flagged unreliable

/* Above this share of estimated offences, a unit-year is marked as an
   estimate rather than a measurement.

   This is a SEPARATE reading from coverage and the two do not imply each
   other. Coverage says how much of the population filed; `imp` says how
   much of the number on screen nobody filed at all. A county with no
   coverage and no estimate is absent; one with no coverage and a full set
   of estimates is drawn, at full height, and must not be mistaken for a
   measured one. Half is the threshold because below it the filed majority
   still carries the figure. */
export const ESTIMATED_MIN = 0.5;

/* Estimated unit-years are hatched toward this violet rather than being
   greyed: grey is already spoken for by low coverage, and the two land on
   the same unit constantly — a county nobody filed for has cov 0 AND imp 1.
   Distinct hue, so the two readings stay separable. */
export const ESTIMATED_RGB: RGB = [138, 120, 214];
export const ESTIMATED_MIX = 0.5;

export const MAX_HEIGHT_M = 420000; // metres at exaggeration 1.0

/* Pillar height is scaled to this percentile of the rate distribution,
   not to the maximum.

   Rates are heavy-tailed and the tail is mostly damage: a county of 38
   people recording 34 offences is a real figure and a useless ceiling.
   Against the true max, EVERY county-year of every crime type sat below
   30% of full height — so the fade slider hid the entire map by its first
   third, and the pillars carried almost no visible variation.

   At p99 the median violent county-year rises from 1.1% of full height to
   13.7%. The top 1% clamp at full height (normalizedRate already does
   Math.min(1, …)), which is the intended trade: the very tallest pillars
   stop being distinguishable from each other so that the other 99% become
   distinguishable at all.

   Height stays LINEAR in the rate below the cap, so a pillar twice as
   tall still means twice the rate. Density keeps its log scale — it spans
   four orders of magnitude and log10 already absorbs its tail. */
export const PILLAR_PERCENTILE = 0.99;

/* Faded pillars sink toward this dark blue-grey and drop to this alpha.
   Deliberately one notch fainter than the low-coverage treatment (which
   mixes toward mid grey at alpha 130), so the two readings stay distinct
   when they land on the same unit. */
export const FADE_RGB: RGB = [46, 54, 72];
export const FADE_ALPHA = 48;

/* ---------- density colour ramps ---------------------------------- */
export const PALETTES: Record<string, Palette> = {
  greenred: {
    label: 'Green → red',
    safe: false,
    // Luminance falls monotonically (234→189→177→137→63) so the ramp reads
    // as an ordered sequence. A pure yellow midpoint would be brighter than
    // the green before it and break that ordering.
    stops: [[220,243,186],[158,206,110],[214,176,74],[226,120,48],[198,26,26]]
  },
  magma: {
    label: 'Magma — colour-blind safe',
    safe: true,
    stops: [[13,8,66],[86,25,120],[174,48,108],[237,104,60],[252,204,102]]
  },
  viridis: {
    label: 'Viridis — colour-blind safe',
    safe: true,
    stops: [[68,1,84],[59,82,139],[33,145,140],[94,201,98],[253,231,37]]
  },
  // Single-hue ramps. Only lightness and saturation vary, so the ordering
  // survives every form of colour vision deficiency — there is no hue
  // pair left to confuse. Luminance falls 82 -> 7 and saturation reaches
  // 100% at the top end.
  reds: {
    label: 'Red — single hue',
    safe: true,
    note: 'One hue, so the order survives any colour vision deficiency. ' +
          'Saturation is full at the high end.',
    stops: [[254,229,217],[252,174,145],[251,106,74],[222,45,38],[153,0,13]]
  },
  blues: {
    label: 'Blue — single hue',
    safe: true,
    note: 'One hue, so the order survives any colour vision deficiency.',
    stops: [[222,235,247],[158,202,225],[107,174,214],[49,130,189],[8,69,148]]
  },
  cividis: {
    label: 'Cividis — colour-blind safe',
    safe: true,
    note: 'Designed so that people with and without colour vision ' +
          'deficiency read the same ordering.',
    stops: [[0,32,76],[62,74,110],[123,120,116],[190,172,105],[255,233,69]]
  }
};

/** Basemap style. The only remote dependency left after the CDN scripts went. */
export const BASEMAP_STYLE =
  'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';

/* How long to wait for the basemap before drawing the data without it.

   If the style request stalls or fails, MapLibre never fires 'load' — and
   the viewer used to hang the splash screen on that event forever, which
   is what "the first load fails, a refresh fixes it" actually was. Long
   enough that a slow-but-working CDN is not written off; short enough that
   a dead one does not look like a broken app. If the style does turn up
   afterwards the status flips back to ready, so guessing low is cheap. */
export const BASEMAP_WATCHDOG_MS = 8000;
