import { CRIMES } from './config';
import { $ } from './dom';
import { esc, fmt } from './format';
import { S } from './state';
import type { FeatureProps } from './types';

interface HoverInfo {
  object?: { props: FeatureProps };
  x: number;
  y: number;
}

export function showTip(info: HoverInfo | null): void {
  const tip = $('tip');
  if (!info || !info.object) { tip.style.display = 'none'; return; }
  const p = info.object.props;
  const def = CRIMES.find((c) => c.id === S.crime)!;
  tip.style.display = 'block';
  tip.style.left = (info.x + 16) + 'px';
  tip.style.top = (info.y + 16) + 'px';
  const covLine = p.cov == null ? '' :
    '<div class="l"><span>Reporting coverage</span><b' +
    (p.lowCov ? ' style="color:#e8734a"' : '') + '>' + fmt(p.cov, 1) + '%</b></div>';
  const unit = S.geo === 'county' ? "this county's" : "this state's";
  const warn = p.lowCov
    ? '<div class="hr"></div><div style="color:#e8734a;font-size:10.5px;line-height:1.45">' +
      'Only ' + fmt(p.cov, 0) + '% of ' + unit + ' population was covered by ' +
      'reporting in ' + S.year +
      '. The true rate is almost certainly higher.</div>'
    : '';

  const placeRows =
    '<div class="l"><span>Population (' + S.year + ')</span><b>' + fmt(p.pop, 0) + '</b></div>' +
    '<div class="l"><span>Density /sq mi</span><b>' + fmt(p.dens, 1) + '</b></div>' +
    '<div class="l"><span>Area sq mi</span><b>' + fmt(p.areaSqMi, 0) + '</b></div>';

  tip.innerHTML =
    '<div class="t">' + esc(p.name) + '</div>' +
    '<div class="l"><span>' + def.label + '</span><b>' + fmt(p.rate, 1) + '</b></div>' +
    '<div class="l"><span>per 100,000 residents</span><b></b></div>' +
    '<div class="hr"></div>' +
    '<div class="l"><span>Reported offences</span><b>' + fmt(p.total, 0) + '</b></div>' +
    placeRows +
    '<div class="l"><span>Source</span><b>' + esc(p.src || '—') + '</b></div>' +
    covLine + warn;
}
