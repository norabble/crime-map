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

export const MAX_HEIGHT_M = 420000; // metres at exaggeration 1.0

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
