import { activeFeatures } from './data';
import { $ } from './dom';
import { esc, fmt } from './format';
import { bbox } from './geo';
import { cssGradient } from './scales';
import { S } from './state';
import type { CrimeDef, RGB, Row } from './types';

export function updateLegend(
  def: CrimeDef, rows: Row[], heightRef: number, maxRate: number, stops: RGB[],
): void {
  const rateLbl = def.label.toLowerCase() + ' per 100k';
  const densLbl = 'population per sq mile';
  const rateEnds = ['0', fmt(heightRef, 0) +
    (S.normalize ? '  (peak, all yrs)' : '  (absolute, all crimes)')];
  const densEnds = [fmt(S.densMin, 1), fmt(S.densMax, 0)];

  // The two channels are interchangeable, so the legend follows whichever
  // measure is currently driving each one. Leaving the headings fixed
  // would silently mislabel the whole map.
  $('lgHtitle').textContent = 'Height — ' + (S.swap ? densLbl : rateLbl);
  $('lgCtitle').textContent = 'Colour — ' + (S.swap ? rateLbl : densLbl);
  const hEnds = S.swap ? densEnds : rateEnds;
  const cEnds = S.swap ? rateEnds : densEnds;
  $('lgHmin').textContent = hEnds[0];
  $('lgHmax').textContent = hEnds[1];
  $('lgCmin').textContent = cEnds[0];
  $('lgCmax').textContent = cEnds[1];
  $('lgSwatch').style.background = cssGradient(stops);
  $('lgFixed').textContent = S.swap
    ? 'Density is log-scaled; both domains fixed across all years'
    : 'Both domains fixed across all years';

  // Where this year's highest unit sits inside the fixed rate domain —
  // makes the year slider legible, since a low year reads as a low bar.
  $('lgHnow').textContent = S.year + ' peak ' + def.label.toLowerCase() +
    ': ' + fmt(maxRate, 0) +
    ' (' + Math.round(maxRate / heightRef * 100) + '% of rate scale)';

  // Distribution of this year's rates, binned against the FIXED domain.
  // Always the rate, whichever channel is carrying it.
  const vals = rows.map((r) => r.rate).filter(isFinite);
  const N = 22, bars = new Array<number>(N).fill(0);
  for (const v of vals) {
    let k = Math.floor(v / heightRef * N);
    if (k >= N) k = N - 1;
    if (k < 0) k = 0;
    bars[k]++;
  }
  const bMax = Math.max(1, ...bars);
  $('lgBars').innerHTML = bars
    .map((b) => '<i style="height:' + Math.max(2, (b / bMax) * 100) + '%"></i>')
    .join('');
}

export function updateRanking(def: CrimeDef, rows: Row[]): void {
  const sorted = rows.filter((r) => isFinite(r.rate)).sort((a, b) => b.rate - a.rate).slice(0, 12);
  $('rkTitle').textContent = 'Highest — ' + def.label.toLowerCase();
  // esc() in the attribute as well as the text: an unescaped quote in a
  // place name would close data-name and open an attribute of its own.
  // The browser decodes entities back out of dataset.name, so the click
  // handler below still compares against the original string.
  $('rkList').innerHTML = sorted.map((r, i) =>
    '<div class="rk" data-name="' + esc(r.f.props.name) + '">' +
      '<span class="n">' + (i + 1) + '</span>' +
      '<span class="nm">' + esc(r.f.props.name) + '</span>' +
      '<span class="v">' + fmt(r.rate, 0) + '</span>' +
    '</div>',
  ).join('');

  // Click a row to fly to that unit.
  $('rkList').querySelectorAll<HTMLElement>('.rk').forEach((el) => {
    el.onclick = () => {
      // Search the active layer, not the state list — in county mode the
      // clicked row is not a state and would never be found.
      const f = activeFeatures().find((x) => x.props.name === el.dataset.name);
      if (!f || !S.map) return;
      const b = bbox(f);
      // A point has no extent, so fitBounds on it would zoom to maximum.
      const pad = (b[0] === b[2] && b[1] === b[3]) ? 0.25 : 0;
      S.map.fitBounds([[b[0] - pad, b[1] - pad], [b[2] + pad, b[3] + pad]],
        { padding: 160, pitch: 55, duration: 900 });
    };
  });
}
