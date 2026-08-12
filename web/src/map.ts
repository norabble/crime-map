import { MapboxOverlay } from '@deck.gl/mapbox';
import maplibregl from 'maplibre-gl';
import { BASEMAP_STYLE, BASEMAP_WATCHDOG_MS } from './config';
import { $ } from './dom';
import { render } from './render';
import { S } from './state';
import { setStatus } from './status';
import { once } from './util';

/* The only module that touches WebGL or the network at render time. Kept
   separate so every other module stays testable in plain Node - no jsdom,
   no canvas stub. */
export function initMap(): void {
  S.map = new maplibregl.Map({
    container: 'map',
    style: BASEMAP_STYLE,
    center: [-97.5, 36.5],
    zoom: 3.4,
    pitch: 52,
    bearing: -12,
    maxPitch: 80,
    attributionControl: { compact: true },
  });
  S.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
  setStatus('basemap', { kind: 'loading' });

  /* Draw the data. Gated by once() because three things race to trigger
     it and only one may win: the map's load event, the watchdog below,
     and (via the watchdog) a style that never arrives. Constructing the
     overlay twice would add two deck canvases to the map.

     interleaved:false is what makes this safe to run before - or without -
     a basemap: deck draws into its own canvas over the map rather than
     into the basemap's layer stack, so it does not need a loaded style. */
  const start = once(() => {
    S.deck = new MapboxOverlay({ interleaved: false, layers: [] });
    S.map!.addControl(S.deck as never);
    render();
    setTimeout(() => $('splash').classList.add('hide'), 250);
  });

  let basemapReady = false;
  S.map.on('load', () => {
    basemapReady = true;
    setStatus('basemap', { kind: 'ready' });
    start();
  });

  // Errors are logged, not acted on. A single failed tile fires this too,
  // and treating that as a dead basemap would cry wolf constantly. The
  // watchdog is what decides, because it measures the thing that matters:
  // whether the map ever became usable.
  S.map.on('error', (e) => {
    console.warn('MapLibre:', (e && e.error && e.error.message) || 'unknown error');
  });

  window.setTimeout(() => {
    if (basemapReady) return;
    // Self-correcting: if the style does arrive later, the load handler
    // above flips this back to ready and nothing was lost but a few
    // seconds of a pessimistic label.
    setStatus('basemap', { kind: 'failed', detail: 'unavailable — data still shown' });
    start();
  }, BASEMAP_WATCHDOG_MS);
}
