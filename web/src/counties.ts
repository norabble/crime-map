import { buildCounties } from './data';
import { DATA } from './dom';
import { loadOptional } from './load';
import { S } from './state';
import { setStatus } from './status';
import type { CountiesDoc, GeoDoc } from './types';

/* The county layer is fetched when it is asked for, not at boot.

   Two reasons. It is 11.6 MB raw / 4.3 MB gzipped against 460 KB for
   everything else, so loading it eagerly put the entire first paint behind
   the largest and slowest request on the site. And when that request
   failed it failed invisibly, because boot could only shrug and carry on.

   Tying it to a click makes a failure attributable to something the user
   did, which is what lets it be reported and retried instead of swallowed. */

let inFlight: Promise<boolean> | null = null;

/** Fetch and join the county layer. Safe to call repeatedly: concurrent
    callers share one request, and a failed attempt can just be retried. */
export async function ensureCounties(): Promise<boolean> {
  if (S.hasCounties) return true;
  if (inFlight) return inFlight;

  inFlight = (async (): Promise<boolean> => {
    setStatus('counties', { kind: 'loading', detail: 'loading 4.3 MB…' });

    const [geo, data] = await Promise.all([
      loadOptional<GeoDoc>(DATA('geo-counties.json')),
      loadOptional<CountiesDoc>(DATA('counties.json')),
    ]);

    if (!geo.ok || !data.ok) {
      // Absent means the build legitimately has no county layer; anything
      // else means it should have worked and did not. Reporting the second
      // as the first is exactly the bug this replaces.
      const absent = (!geo.ok && geo.reason === 'absent') ||
                     (!data.ok && data.reason === 'absent');
      const detail = [!geo.ok ? geo.message : null, !data.ok ? data.message : null]
        .filter(Boolean).join('; ');
      console.warn('county layer unavailable:', detail);
      setStatus('counties', absent
        ? { kind: 'absent', detail: 'not built — run make import' }
        : { kind: 'failed', detail: 'load failed — click County to retry' });
      return false;
    }

    buildCounties(geo.data, data.data);

    if (!S.hasCounties) {
      setStatus('counties', { kind: 'absent', detail: 'no counties matched geometry' });
      return false;
    }
    setStatus('counties', {
      kind: 'ready',
      detail: S.countyFeatures.length.toLocaleString('en-US') + ' counties',
    });
    return true;
  })();

  try {
    return await inFlight;
  } finally {
    // Cleared either way. On success S.hasCounties short-circuits any
    // further call; on failure the next click is a genuine retry.
    inFlight = null;
  }
}
