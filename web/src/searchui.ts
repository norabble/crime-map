/* The search box, its results, and what clicking one does.

   Kept apart from search.ts so the matching stays pure and testable. This
   file owns the DOM and the side effects: selecting a unit, flying to it,
   and loading another layer when the user asks for one.

   The result row shows the resolution chain — the same place named in
   every geography, with the active one lit. That is not decoration: it is
   the answer to "what will clicking this do", and it is the only place in
   the UI that makes the relationship between the three layers visible. */

import { ensureCounties } from './counties';
import { activeFeatures, featureKey } from './data';
import { $ } from './dom';
import { esc, fmt } from './format';
import { bbox } from './geo';
import { getLayer, hasLayer, layerIds } from './layers';
import { ensureRegions, regionLayerId } from './regions';
import { render } from './render';
import { resolve, search } from './search';
import { S } from './state';
import type { SearchHit } from './types';

const MAX_RESULTS = 8;

/* Which layers a result offers, in coarse-to-fine order. Region variants
   are discovered rather than listed: only the ones actually built appear,
   and the one named in the geography selector comes first. */
function chainLayers(): string[] {
  const regions = layerIds().filter((id) => id.startsWith('region:'));
  const current = S.geo.startsWith('region:') ? S.geo : null;
  const seen = new Set<string>();
  const out: string[] = ['state'];
  for (const id of [current, ...regions, defaultRegionId()]) {
    if (id && !seen.has(id)) { seen.add(id); out.push(id); }
  }
  out.push('county');
  return out;
}

function defaultRegionId(): string | null {
  const sel = document.getElementById('variantSel') as HTMLSelectElement | null;
  return sel ? regionLayerId(sel.value) : null;
}

function layerLabel(id: string): string {
  if (id === 'state') return 'State';
  if (id === 'county') return 'County';
  return 'Region';
}

/* What a hit is called in a given layer.

   Returns null when the layer is not loaded, which is a different thing
   from "this place is not in that layer" — the caller offers to load it
   rather than claiming the place does not exist there. */
function unitName(hit: SearchHit, layerId: string): string | null {
  const key = resolve(hit, layerId, S.regionMembers.get(layerId) || null);
  if (!key) return null;
  const layer = getLayer(layerId);
  if (!layer) return null;
  const f = layer.features().find((x) => layer.keyOf(x) === key);
  return f ? f.props.name : null;
}

export function selectHit(hit: SearchHit, layerId: string): void {
  const key = resolve(hit, layerId, S.regionMembers.get(layerId) || null);
  if (!key) return;
  S.selected = { layer: layerId, key };
  S.selectedHit = hit;
  flyToSelected();
  render();
  renderResults();
}

/** Frame the selection, matching how a ranking click already flies. */
export function flyToSelected(): void {
  if (!S.selected || !S.map) return;
  const f = activeFeatures().find((x) => featureKey(x) === S.selected!.key);
  if (!f) return;
  const b = bbox(f);
  const pad = (b[0] === b[2] && b[1] === b[3]) ? 0.25 : 0;
  S.map.fitBounds([[b[0] - pad, b[1] - pad], [b[2] + pad, b[3] + pad]],
    { padding: 140, pitch: 55, duration: 900 });
}

/* Re-point the selection after the layer changes.

   The original hit is kept precisely for this: following Chicago from
   state to region to county should keep Chicago framed, not drop the
   selection because Illinois is not a county. */
export function reselectForLayer(): void {
  if (!S.selectedHit) { S.selected = null; return; }
  const key = resolve(S.selectedHit, S.geo, S.regionMembers.get(S.geo) || null);
  S.selected = key ? { layer: S.geo, key } : null;
  renderResults();
}

async function jumpToLayer(hit: SearchHit, layerId: string): Promise<void> {
  // Switching through the geography buttons rather than setting S.geo
  // directly, so the year clamp, the variant row and the scale rebuild all
  // happen exactly as they do for a manual click.
  const btn = document.querySelector<HTMLButtonElement>(
    '#geoSeg button[data-geo="' + layerId + '"]');
  if (btn) {
    if (layerId.startsWith('region:')) {
      const sel = document.getElementById('variantSel') as HTMLSelectElement | null;
      if (sel) sel.value = layerId.slice('region:'.length);
    }
    S.selectedHit = hit;
    btn.click();
    return;
  }
  // No button for this variant (it is not in the selector): load it and
  // select in place.
  const okLoad = layerId.startsWith('region:')
    ? await ensureRegions(layerId.slice('region:'.length))
    : await ensureCounties();
  if (okLoad) selectHit(hit, layerId);
}

let results: SearchHit[] = [];

function renderResults(): void {
  const list = $('srList');
  const note = $('srNote');
  const chain = chainLayers();

  if (!results.length) {
    list.innerHTML = '';
    return;
  }

  list.innerHTML = results.map((hit, i) => {
    const steps = chain.map((id) => {
      const name = unitName(hit, id);
      const resolvable = !!resolve(hit, id, S.regionMembers.get(id) || null);
      const loaded = hasLayer(id);
      // A state has no single county, so those steps are shown disabled
      // rather than hidden: the gap is information.
      const dead = loaded && !resolvable;
      const label = name || (dead ? '—' : layerLabel(id));
      const on = id === S.geo && S.selected && !dead;
      return '<button class="srStep' + (on ? ' on' : '') + '"' +
        (dead ? ' disabled' : '') +
        ' data-i="' + i + '" data-layer="' + esc(id) + '"' +
        ' title="' + esc(loaded ? layerLabel(id) : layerLabel(id) + ' — loads on click') + '">' +
        esc(label) + '</button>';
    }).join('');

    return '<div class="sr">' +
      '<div class="srHead">' +
        '<span class="nm">' + esc(hit.name) + '</span>' +
        '<span class="ab">' + esc(hit.abbr) + '</span>' +
        (hit.kind === 'city' && hit.pop
          ? '<span class="pop">' + fmt(hit.pop, 0) + '</span>' : '') +
      '</div>' +
      '<div class="srChain">' + steps + '</div>' +
    '</div>';
  }).join('');

  list.querySelectorAll<HTMLButtonElement>('.srStep').forEach((b) => {
    b.onclick = () => {
      const hit = results[+(b.dataset.i || 0)];
      const layerId = b.dataset.layer!;
      if (!hit) return;
      if (layerId === S.geo) selectHit(hit, layerId);
      else void jumpToLayer(hit, layerId);
    };
  });
  note.textContent = '';
}

function runQuery(raw: string): void {
  const panel = $('ranking');
  const note = $('srNote');
  const clear = $('srClear');
  clear.classList.toggle('on', !!raw);

  if (!raw.trim()) {
    results = [];
    panel.classList.remove('searching');
    $('srList').innerHTML = '';
    note.textContent = '';
    return;
  }
  panel.classList.add('searching');

  if (!S.searchIndex) {
    results = [];
    $('srList').innerHTML = '';
    note.textContent = 'Search index not built — run make search.';
    return;
  }

  results = search(S.searchIndex, raw, MAX_RESULTS);
  if (!results.length) {
    $('srList').innerHTML = '';
    // Precise about what was searched. The index is police agencies, so a
    // place with no force of its own is genuinely absent — saying "no such
    // place" would be a different and false claim.
    note.textContent = 'No state, county, or city with a police agency by ' +
      'that name. Try adding a state, as in “Springfield, IL”.';
    return;
  }
  renderResults();
}

export function initSearch(): void {
  const input = $('srInput') as HTMLInputElement;
  const clear = $('srClear');

  // Debounced: a full pass is about 7ms over 18,000 entries, fast enough
  // per keystroke, but there is no reason to redo it mid-word.
  let timer: number | undefined;
  input.oninput = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => runQuery(input.value), 90);
  };
  input.onkeydown = (e) => {
    if (e.key === 'Escape') {
      input.value = '';
      runQuery('');
      input.blur();
    } else if (e.key === 'Enter' && results.length) {
      // Enter takes the top hit into the layer already on screen, which is
      // what the highlighted chain step shows.
      e.preventDefault();
      selectHit(results[0], S.geo);
    }
  };
  clear.onclick = () => {
    input.value = '';
    runQuery('');
    input.focus();
  };

  if (!S.searchIndex) {
    input.placeholder = 'Search unavailable';
    input.disabled = true;
  }
}
