/* ══════════════════════════════════════════════════════════════════
   Crime Rates per Capita — 3D extruded choropleth
   Height : offences per 100,000 residents (selected crime type/year)
   Colour : population density (people per square mile, log scale)
   ══════════════════════════════════════════════════════════════════ */

import 'maplibre-gl/dist/maplibre-gl.css';

import { buildData } from './data';
import { $, DATA } from './dom';
import { esc } from './format';
import { loadLocal } from './load';
import { initMap } from './map';
import { S } from './state';
import { renderStatus, setStatus } from './status';
import { initUI } from './ui';
import type { GeoDoc, StatesDoc } from './types';

/* `bootStage` tracks how far boot got. Every step below runs inside one
   try, so without it a missing data file and a WebGL failure surface as
   the same message — which sends you looking at the data when the problem
   is elsewhere.

   There is no longer a 'libraries' stage: MapLibre and deck.gl are bundled
   imports rather than CDN script tags, so they cannot fail at runtime. */
let bootStage = 'crime data';

async function boot(): Promise<void> {
  const splashMsg = $('splashMsg');
  renderStatus();
  try {
    // Everything below is served from <base>/data/, produced by the build
    // scripts. No API key, no rate limits, works offline once built.
    splashMsg.textContent = 'Loading crime data…';
    setStatus('states', { kind: 'loading' });
    const states = await loadLocal<StatesDoc>(DATA('states.json'));

    bootStage = 'geometry';
    splashMsg.textContent = 'Loading geometry…';
    const geo = await loadLocal<GeoDoc>(DATA('geo-states.json'));

    bootStage = 'joining datasets';
    splashMsg.textContent = 'Joining datasets…';
    buildData(states, geo);
    setStatus('states', {
      kind: 'ready',
      detail: S.features.length + ' states, ' +
        S.years[0] + '–' + S.years[S.years.length - 1],
    });

    /* The county layer is NOT fetched here. It is 4.3 MB gzipped against
       460 KB for everything above, so loading it eagerly put first paint
       behind the slowest request on the site - and when it failed, it
       failed silently. counties.ts fetches it when the County button is
       clicked, where a failure can be reported and retried. */
    setStatus('counties', { kind: 'pending', detail: 'click County to load' });

    bootStage = 'rendering';
    splashMsg.textContent = 'Rendering…';
    initUI();
    initMap();
  } catch (err) {
    console.error('boot failed during stage:', bootStage, err);
    setStatus('states', { kind: 'failed', detail: 'see message' });
    $('spinner').style.display = 'none';
    splashMsg.style.display = 'none';
    const e = $('splashErr');
    e.style.display = 'block';
    const isFileProto = location.protocol === 'file:';

    // Advice is per-stage: pointing at the data files is actively
    // misleading when the failure was a WebGL problem.
    let hint: string;
    if (isFileProto) {
      hint = '<br><br>You opened this with <code>file://</code>. Browsers block ' +
        'local file reads from that origin. Serve the folder instead:' +
        '<br><br><code style="color:#f6c453">npm run dev</code>';
    } else if (bootStage === 'crime data' || bootStage === 'geometry') {
      hint = '<br><br>Check that <code>data/states.json</code> and ' +
        '<code>data/geo-states.json</code> exist. If not, run:' +
        '<br><br><code style="color:#f6c453">make data</code>';
    } else if (bootStage === 'rendering') {
      hint = '<br><br>The data loaded fine — this is a rendering failure. ' +
        'It usually means WebGL is unavailable or disabled in this ' +
        'browser. Check <code>about:gpu</code>, or try another browser.';
    } else {
      hint = '<br><br>The files loaded but could not be combined. Rebuild with:' +
        '<br><br><code style="color:#f6c453">make data</code>';
    }

    // hint is our own markup and stays raw; the error text is not, so it
    // is escaped. Today every URL that reaches it is a hardcoded relative
    // path, but an error message is exactly the kind of string that grows
    // a remote component later without anyone re-checking this line.
    e.innerHTML = '<b>Could not load.</b><br><br>' +
      '<span style="opacity:.7">Failed while loading: ' + esc(bootStage) + '</span><br><br>' +
      esc(String((err as Error).message || err)) + hint;
  }
}

void boot();
