import { CRIMES, NIBRS_FIRST, PALETTES, SRS_LAST } from './config';
import { ensureCounties } from './counties';
import {
  activeFeatures, activeStore, availableCrimes, featureKey, recomputeScales,
} from './data';
import { $ } from './dom';
import { fitActiveLayer } from './geo';
import { hasLayer } from './layers';
import { ensureRegions } from './regions';
import { render } from './render';
import { S } from './state';

/* ---------- year slider + era marker ------------------------------ */
export function syncYearSlider(jumpToEnd: boolean): void {
  const y = $('year') as HTMLInputElement;
  const lo = S.years[0], hi = S.years[S.years.length - 1];
  y.min = String(lo); y.max = String(hi);
  if (jumpToEnd || S.year > hi || S.year < lo) S.year = hi;
  y.value = String(S.year);
  $('yearVal').textContent = String(S.year);
  $('eraEnd').textContent = String(hi);

  // Position the SRS/NIBRS boundary marker proportionally.
  const hasNibrs = hi > SRS_LAST;
  $('eraSplit').style.display = hasNibrs ? '' : 'none';
  const mark = $('eraMark');
  if (hasNibrs) {
    const pct = (SRS_LAST + 0.5 - lo) / (hi - lo) * 100;
    mark.style.left = Math.max(0, Math.min(100, pct)) + '%';
    mark.style.display = '';
  } else {
    mark.style.display = 'none';
  }
  updateEraWarning();
}

export function updateEraWarning(): void {
  const w = $('eraWarn');
  if (S.year >= NIBRS_FIRST) {
    w.style.display = '';
    w.innerHTML = '<strong>NIBRS era.</strong> 2020+ figures come from a different FBI ' +
      'collection system than 1960–2019 and are not directly comparable across the ' +
      SRS_LAST + '/' + NIBRS_FIRST + ' boundary.';
  } else {
    w.style.display = 'none';
  }
}

export function syncCrimeOptions(): void {
  const have = availableCrimes();
  const sel = $('crime') as HTMLSelectElement;
  if (!sel) return;
  // Labels are compile-time constants from config.ts, not remote input.
  sel.innerHTML = CRIMES.map((c) => {
    const ok = have.has(c.id);
    return '<option value="' + c.id + '"' + (ok ? '' : ' disabled') + '>' +
      c.label + (ok ? '' : ' — not in this layer') + '</option>';
  }).join('');
  if (!have.has(S.crime)) {
    const first = CRIMES.find((c) => have.has(c.id));
    if (first) S.crime = first.id;
  }
  sel.value = S.crime;
}

export function initUI(): void {
  // crime type dropdown
  const sel = $('crime') as HTMLSelectElement;
  syncCrimeOptions();
  sel.onchange = () => {
    S.crime = sel.value;
    render();
  };

  // density palette
  const pal = $('palette') as HTMLSelectElement;
  pal.innerHTML = Object.entries(PALETTES)
    .map(([k, v]) => '<option value="' + k + '">' + v.label + '</option>').join('');
  pal.value = S.palette;
  const paletteNote = () => {
    const p = PALETTES[S.palette];
    $('paletteNote').innerHTML = p.note ? p.note : (p.safe
      ? 'Perceptually uniform and readable with any form of colour vision deficiency.'
      : '<strong>Note:</strong> green–red is the hardest pairing for red–green colour ' +
        'vision deficiency (~8% of men). Every other ramp here reads correctly for everyone.');
  };
  paletteNote();
  pal.onchange = () => { S.palette = pal.value; paletteNote(); render(); };

  // year slider bounded by what the data actually contains
  syncYearSlider(true);
  ($('year') as HTMLInputElement).oninput = () => {
    S.year = +($('year') as HTMLInputElement).value;
    $('yearVal').textContent = String(S.year);
    updateEraWarning();
    render();
  };

  // exaggeration
  const ex = $('exag') as HTMLInputElement;
  ex.oninput = () => {
    S.exag = +ex.value;
    $('exagVal').textContent = S.exag.toFixed(2) + '×';
    render();
  };

  // fade threshold. The readout needs the scale domains, which only exist
  // inside render(), so it is updated from there rather than here.
  const fd = $('fade') as HTMLInputElement;
  fd.oninput = () => {
    S.fade = +fd.value;
    render();
  };

  // Which measure drives which channel. Colour always takes the other,
  // so the two can never both show the same thing.
  const encNote = () => {
    $('encNote').innerHTML = S.swap
      ? 'Colour shows the crime rate. Height is density on a log scale — ' +
        'linear height would flatten everywhere outside the big cities.'
      : 'Colour shows population density, log-scaled.';
  };
  const setSwap = (v: boolean) => {
    S.swap = v;
    $('encRate').classList.toggle('on', !v);
    $('encDens').classList.toggle('on', v);
    encNote();
    render();
  };
  encNote();
  $('encRate').onclick = () => setSwap(false);
  $('encDens').onclick = () => setSwap(true);

  $('scNorm').onclick = () => {
    S.normalize = true;
    $('scNorm').classList.add('on'); $('scAbs').classList.remove('on');
    render();
  };
  $('scAbs').onclick = () => {
    S.normalize = false;
    $('scAbs').classList.add('on'); $('scNorm').classList.remove('on');
    render();
  };

  // averaging window
  $('winSeg').querySelectorAll<HTMLButtonElement>('button').forEach((b) => {
    b.onclick = () => {
      $('winSeg').querySelectorAll('button').forEach((x) => x.classList.remove('on'));
      b.classList.add('on');
      S.window = +(b.dataset.window || 1);
      // A different window is a different distribution, so the fixed
      // height domain has to be rebuilt against it.
      recomputeScales();
      render();
    };
  });

  // geography segment
  $('geoSeg').querySelectorAll<HTMLButtonElement>('button').forEach((b) => {
    b.onclick = () => {
      if (b.disabled) return;
      void selectGeo(b);
    };
  });

  // Region-method selector. Temporary, for choosing between the variants;
  // it retargets the Region button and re-selects it if it is already on.
  const variantSel = document.getElementById('variantSel') as HTMLSelectElement | null;
  if (variantSel) {
    variantSel.onchange = () => {
      const btn = $('geoSeg')
        .querySelector<HTMLButtonElement>('button[data-geo^="region:"]');
      if (!btn) return;
      btn.dataset.geo = 'region:' + variantSel.value;
      if (btn.classList.contains('on')) void selectGeo(btn);
    };
  }
}

/* Switching to counties may need a 4.3 MB fetch first. If it fails we stay
   on the current layer rather than switching to an empty one - the status
   panel says why, and clicking County again retries. */
async function selectGeo(b: HTMLButtonElement): Promise<void> {
  const target = b.dataset.geo as string;

  // Every layer but states is fetched on demand, so a click may need a
  // several-megabyte request first. Failing keeps the current layer:
  // switching to an empty one loses what the user was looking at, and the
  // status panel already says what went wrong.
  const needsLoad = target !== 'state' && !hasLayer(target);
  if (needsLoad) {
    b.disabled = true;
    const label = b.textContent;
    b.textContent = 'Loading…';
    let loaded = false;
    try {
      loaded = target.startsWith('region:')
        ? await ensureRegions(target.slice('region:'.length))
        : await ensureCounties();
    } finally {
      b.textContent = label;
      b.disabled = false;
    }
    if (!loaded) return;
  }

  $('geoSeg').querySelectorAll('button').forEach((x) => x.classList.remove('on'));
  b.classList.add('on');
  S.geo = target;

  const row = document.getElementById('variantRow');
  if (row) row.style.display = target.startsWith('region:') ? '' : 'none';

  // Each layer has its own year coverage; clamp into range.
  const yrs = new Set<number>();
  const store = activeStore();
  for (const f of activeFeatures()) {
    const by = store.get(featureKey(f));
    if (by) for (const y of by.keys()) yrs.add(y);
  }
  if (yrs.size) {
    S.years = [...yrs].sort((a, b2) => a - b2);
    syncYearSlider(false);
  }
  syncCrimeOptions();
  recomputeScales();
  render();
  fitActiveLayer();
}
