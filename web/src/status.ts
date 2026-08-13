/* A standing answer to "what is actually working right now".

   The viewer used to degrade invisibly: a failed county fetch left the
   County button greyed out with a tooltip claiming no such dataset
   exists, and a stalled basemap left the splash spinning forever. Neither
   wrote anything to the console. This panel means every layer is either
   visibly working or visibly not.

   The model and its labels are pure so they can be tested; only render()
   touches the DOM. */

export type LayerKey = 'states' | 'regions' | 'counties' | 'basemap';

export type LayerState =
  | { kind: 'pending'; detail?: string }
  | { kind: 'loading'; detail?: string }
  | { kind: 'ready'; detail?: string }
  // Loaded and usable, but with a defect worth naming — county data that
  // joined to no geometry, for instance. Distinct from 'failed', which
  // means there is nothing to draw at all.
  | { kind: 'degraded'; detail?: string }
  | { kind: 'absent'; detail?: string }
  | { kind: 'failed'; detail?: string };

export type Tone = 'ok' | 'warn' | 'bad' | 'idle';

export type StatusModel = Record<LayerKey, LayerState>;

export const LAYER_LABELS: Record<LayerKey, string> = {
  states: 'States',
  regions: 'Regions',
  counties: 'Counties',
  basemap: 'Basemap',
};

export function initialStatus(): StatusModel {
  return {
    states: { kind: 'pending' },
    regions: { kind: 'pending' },
    counties: { kind: 'pending' },
    basemap: { kind: 'pending' },
  };
}

/** Display text and severity for one layer. */
export function describeState(s: LayerState): { text: string; tone: Tone } {
  switch (s.kind) {
    case 'pending': return { text: s.detail ?? 'waiting', tone: 'idle' };
    case 'loading': return { text: s.detail ?? 'loading…', tone: 'idle' };
    case 'ready': return { text: s.detail ?? 'ready', tone: 'ok' };
    // Warn, not bad: the layer draws. Something in it does not.
    case 'degraded': return { text: s.detail ?? 'degraded', tone: 'warn' };
    // Absent is not a fault. A build without counties.json is a legitimate
    // build, so it reads as information rather than as an error.
    case 'absent': return { text: s.detail ?? 'not built', tone: 'warn' };
    case 'failed': return { text: s.detail ?? 'failed', tone: 'bad' };
  }
}

/* One line summarising anything genuinely wrong, or null when nothing is.
   Only 'failed' counts: 'absent' means the data was never built, which is
   a choice rather than a malfunction, and must not cry wolf. */
export function problemSummary(m: StatusModel): string | null {
  const broken = (Object.keys(m) as LayerKey[]).filter((k) => m[k].kind === 'failed');
  if (!broken.length) return null;
  return broken.map((k) => LAYER_LABELS[k]).join(' and ') +
    (broken.length > 1 ? ' failed to load.' : ' failed to load.');
}

/* ---------------------------------------------------------------- */

const model: StatusModel = initialStatus();

export function getStatus(): Readonly<StatusModel> {
  return model;
}

export function setStatus(key: LayerKey, state: LayerState): void {
  model[key] = state;
  renderStatus();
}

/** Builds the rows with createElement rather than innerHTML - none of this
    text is remote input today, and it should stay impossible for it to
    matter if that changes. */
export function renderStatus(): void {
  // Guarded so the model stays usable outside a browser: the tests drive
  // real load paths through setStatus() and assert on getStatus().
  if (typeof document === 'undefined') return;
  const host = document.getElementById('statusList');
  if (!host) return;
  host.textContent = '';
  for (const key of Object.keys(model) as LayerKey[]) {
    const { text, tone } = describeState(model[key]);
    const row = document.createElement('div');
    row.className = 'st';

    const name = document.createElement('span');
    name.className = 'st-n';
    name.textContent = LAYER_LABELS[key];

    const val = document.createElement('span');
    val.className = 'st-v st-' + tone;
    val.textContent = text;

    row.append(name, val);
    host.append(row);
  }
}
