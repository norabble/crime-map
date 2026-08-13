import { CRIMES } from './config';
import { activeNoun } from './data';
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
  const impLine = (p.imp == null || p.imp <= 0) ? '' :
    '<div class="l"><span>Estimated</span><b' +
    (p.estimated ? ' style="color:#8a78d6"' : '') + '>' +
    fmt(p.imp * 100, p.imp < 0.1 ? 1 : 0) + '%</b></div>';
  const unit = "this " + activeNoun() + "'s";
  const warn = p.lowCov
    ? '<div class="hr"></div><div style="color:#e8734a;font-size:10.5px;line-height:1.45">' +
      'Only ' + fmt(p.cov, 0) + '% of ' + unit + ' population was covered by ' +
      'reporting in ' + S.year +
      '. The true rate is almost certainly higher.</div>'
    : '';
  // Deliberately separate from the coverage warning above rather than
  // folded into it. They answer different questions - how much of the
  // population filed, versus how much of this number was filed at all -
  // and a county nobody reported for trips both at once.
  const estWarn = p.estimated
    ? '<div class="hr"></div><div style="color:#8a78d6;font-size:10.5px;line-height:1.45">' +
      fmt(p.imp! * 100, 0) + '% of these offences were estimated, not ' +
      'reported: no agency filed them for this ' + activeNoun() +
      ' in ' + S.year + '.</div>'
    : '';

  // With a window open, every figure below is a sum over several years,
  // so the population label has to say so too - "population 1,900,000" for
  // a county of 640,000 is alarming until you know it is three years.
  const span = p.span && p.span[0] !== p.span[1]
    ? p.span[0] + '–' + p.span[1] : String(S.year);
  const placeRows =
    '<div class="l"><span>Population (' + span + ')</span><b>' + fmt(p.pop, 0) + '</b></div>' +
    '<div class="l"><span>Density /sq mi</span><b>' + fmt(p.dens, 1) + '</b></div>' +
    '<div class="l"><span>Area sq mi</span><b>' + fmt(p.areaSqMi, 0) + '</b></div>';

  tip.innerHTML =
    '<div class="t">' + esc(p.name) + '</div>' +
    '<div class="l"><span>' + def.label + '</span><b>' + fmt(p.rate, 1) + '</b></div>' +
    '<div class="l"><span>per 100,000 residents</span><b></b></div>' +
    '<div class="hr"></div>' +
    '<div class="l"><span>' + (p.estimated ? 'Offences (estimated)' : 'Reported offences') +
    '</span><b>' + fmt(p.total, 0) + '</b></div>' +
    placeRows +
    '<div class="l"><span>Source</span><b>' + esc(p.src || '—') + '</b></div>' +
    covLine + impLine + warn + estWarn;
}
