import { MapboxOverlay } from '@deck.gl/mapbox';
import maplibregl from 'maplibre-gl';
import { BASEMAP_STYLE } from './config';
import { $ } from './dom';
import { render } from './render';
import { S } from './state';

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

  // If the basemap style fails, keep going with a plain dark backdrop.
  S.map.on('error', (e) => console.warn('MapLibre:', e && e.error && e.error.message));

  S.map.on('load', () => {
    S.deck = new MapboxOverlay({ interleaved: false, layers: [] });
    S.map!.addControl(S.deck as never);
    render();
    setTimeout(() => $('splash').classList.add('hide'), 250);
  });
}
