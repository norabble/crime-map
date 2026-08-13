import { buildRegions } from './data';
import { DATA } from './dom';
import { hasLayer } from './layers';
import { loadOptional } from './load';
import { setStatus } from './status';
import type { GeoDoc, RegionsDoc } from './types';

/* Region layers are fetched on demand, for the same reason the county
   layer is: they are megabytes, and a failure that happens during a click
   is attributable, reportable and retryable in a way a boot-time failure
   is not.

   More than one variant can be built while we decide which to keep, so
   this is keyed by variant name. Only the one being looked at is ever
   fetched. */

const inFlight = new Map<string, Promise<boolean>>();

export function regionLayerId(variant: string): string {
  return 'region:' + variant;
}

export async function ensureRegions(variant: string): Promise<boolean> {
  const id = regionLayerId(variant);
  if (hasLayer(id)) return true;
  const running = inFlight.get(variant);
  if (running) return running;

  const p = (async (): Promise<boolean> => {
    setStatus('regions', { kind: 'loading', detail: 'loading ' + variant + '…' });

    const base = 'regions/' + variant + '/';
    const [geo, data] = await Promise.all([
      loadOptional<GeoDoc>(DATA(base + 'geo.json')),
      loadOptional<RegionsDoc>(DATA(base + 'regions.json')),
    ]);

    if (!geo.ok || !data.ok) {
      // Absent means this variant was never built, which is a legitimate
      // state while we are choosing between them. Anything else means it
      // should have worked. Collapsing the two is the bug that made a
      // failed 8.5 MB fetch look like an unbuilt layer.
      const absent = (!geo.ok && geo.reason === 'absent') ||
                     (!data.ok && data.reason === 'absent');
      const detail = [!geo.ok ? geo.message : null, !data.ok ? data.message : null]
        .filter(Boolean).join('; ');
      console.warn('region layer ' + variant + ' unavailable:', detail);
      setStatus('regions', absent
        ? { kind: 'absent', detail: 'not built — run make regions' }
        : { kind: 'failed', detail: 'load failed — click Region to retry' });
      return false;
    }

    const n = buildRegions(variant, geo.data, data.data);
    if (!n) {
      setStatus('regions', { kind: 'absent', detail: 'no regions matched geometry' });
      return false;
    }
    setStatus('regions', {
      kind: 'ready',
      detail: n.toLocaleString('en-US') + ' regions (' + variant + ')',
    });
    return true;
  })();

  inFlight.set(variant, p);
  try {
    return await p;
  } finally {
    inFlight.delete(variant);
  }
}
