import { GeoJsonLayer } from '@deck.gl/layers';
import {
  ESTIMATED_MIX, ESTIMATED_RGB, FADE_ALPHA, FADE_RGB, MAX_HEIGHT_M, PALETTES,
} from './config';
import { activeFeatures, currentValues } from './data';
import { $ } from './dom';
import { fmt } from './format';
import { updateLegend, updateRanking } from './legend';
import {
  densityFromNormalized, densityLogDomain, isFaded, mixToward,
  normalizedDensity, normalizedRate, rampColor,
} from './scales';
import { S } from './state';
import { showTip } from './tooltip';
import type { LogDomain } from './scales';
import type { RGB } from './types';

export function render(): void {
  if (!S.deck) return;
  const { def, rows, maxRate } = currentValues();
  const stops = PALETTES[S.palette].stops;

  // Fixed colour domain: lowest-density unit-year -> highest, all years.
  const dom = densityLogDomain(S.densMin, S.densMax);

  // Fixed height domain. "Relative" = tallest for this crime type across
  // all years; "Absolute" = tallest of any crime type across all years.
  const heightRef = S.normalize
    ? Math.max(S.crimeMax[S.crime], 0.0001)
    : S.absMax;

  // Attach computed display values onto each feature for the layer accessors.
  for (const r of rows) {
    r.f.props.rate = r.rate;
    r.f.props.total = r.total;
    r.f.props.pop = r.pop;
    r.f.props.dens = r.dens;
    r.f.props.cov = r.cov;
    r.f.props.imp = r.imp;
    r.f.props.span = r.span;
    r.f.props.src = r.src;
    r.f.props.lowCov = r.lowCov;
    r.f.props.estimated = r.estimated;
    // Each measure is reduced to a 0..1 position inside its own fixed
    // domain, so either can drive either channel.
    const tRate = normalizedRate(r.rate, heightRef);
    const tDens = normalizedDensity(r.dens, dom);

    const tHeight = S.swap ? tDens : tRate;
    const tColour = S.swap ? tRate : tDens;

    r.f.props.elev = isFinite(tHeight) ? tHeight * MAX_HEIGHT_M * S.exag : 0;

    const faded = isFaded(tHeight, S.fade);

    let col: RGB = isFinite(tColour) ? rampColor(tColour, stops) : [55, 62, 80];

    // Under-reported unit-years are pulled toward grey so an artificially
    // low pillar is visibly untrustworthy rather than reading as real.
    if (r.lowCov) col = mixToward(col, [96, 96, 96], 0.66);

    // Mostly-estimated unit-years get a distinct hue on top. Applied after
    // the grey so a pillar that is both under-reported and estimated still
    // reads as estimated - which is the more important of the two, because
    // grey says "this number is too low" and violet says "nobody filed this
    // number at all".
    if (r.estimated) col = mixToward(col, ESTIMATED_RGB, ESTIMATED_MIX);

    let alpha = isFinite(tHeight) ? (r.lowCov ? 130 : 255) : 70;

    // Fade composes on top of the low-coverage grey rather than replacing
    // it, so a faded under-reported unit stays distinguishable from a faded
    // well-reported one and neither signal is lost.
    if (faded) {
      col = mixToward(col, FADE_RGB, 0.72);
      alpha = Math.min(alpha, FADE_ALPHA);
    }

    r.f.props.color = col;
    r.f.props.alpha = alpha;
    r.f.props.faded = faded;
  }

  /* The accessor parameter is `any` on purpose. deck.gl types these against
     GeoJSON's Feature<Geometry, Properties>, but this layer's features carry
     their joined values on a non-standard `props` key rather than
     `properties` - deck.gl passes the object through untouched, so the
     runtime is correct and only the declared type disagrees. Narrowing it
     properly would mean restating deck.gl's generics for no behavioural
     gain during a migration that is meant to change nothing. */
  const layer = new GeoJsonLayer({
    id: 'units-3d-' + S.geo,
    data: { type: 'FeatureCollection', features: activeFeatures() } as any,
    extruded: true,
    wireframe: true,
    filled: true,
    stroked: false,
    getElevation: (f: any) => f.props.elev as number,
    getFillColor: (f: any) => {
      const p = f.props as { color: RGB; alpha: number };
      return [p.color[0], p.color[1], p.color[2], p.alpha] as [number, number, number, number];
    },
    // An accessor rather than a constant: a constant wireframe would draw a
    // bright cage around every faded pillar and undo the effect entirely.
    getLineColor: (f: any) =>
      (f.props.faded
        ? [255, 255, 255, 7]
        : [255, 255, 255, 22]) as [number, number, number, number],
    lineWidthMinPixels: 1,
    pickable: true,
    autoHighlight: true,
    highlightColor: [246, 196, 83, 90],
    material: { ambient: .55, diffuse: .65, shininess: 24, specularColor: [50, 50, 55] },
    updateTriggers: {
      getElevation: [S.crime, S.year, S.exag, S.normalize, S.geo, S.swap, S.window],
      getFillColor: [S.year, S.palette, S.geo, S.crime, S.normalize, S.swap, S.fade,
        S.window],
      getLineColor: [S.year, S.geo, S.crime, S.normalize, S.swap, S.fade, S.window],
    },
    transitions: { getElevation: 260, getFillColor: 260 },
    onHover: (info: any) => showTip(info),
  });

  S.deck.setProps({ layers: [layer] });
  updateLegend(def, rows, heightRef, maxRate, stops);
  updateRanking(def, rows);
  updateFadeReadout(heightRef, dom);
}

/* S.fade is a fraction of the fixed height domain — the honest internal
   value, since that is what the threshold is actually compared against.
   Nobody thinks in fractions of a domain, so the readout converts it back
   into whichever units are currently driving height. */
export function updateFadeReadout(heightRef: number, dom: LogDomain): void {
  const el = $('fadeVal');
  if (S.fade <= 0) { el.textContent = 'off'; return; }
  if (S.swap) {
    // Density drives height on a log scale, so invert the log mapping.
    // Interpolating linearly between the domain ends would report a value
    // the slider is not actually cutting at.
    const d = densityFromNormalized(S.fade, dom);
    el.textContent = fmt(d, d < 100 ? 1 : 0) + ' /sq mi';
  } else {
    el.textContent = fmt(S.fade * heightRef, 0) + ' /100k';
  }
}
