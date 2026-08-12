import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ensureCounties } from './counties';
import { S } from './state';
import { getStatus } from './status';

/* The county-tab bug, end to end.

   Previously every one of these paths ended the same way: null, no signal,
   County button greyed out with a tooltip claiming the dataset does not
   exist. These assert that the four outcomes are now distinguishable and
   that only one of them is silent. */

const GEO = { '50001': { geometry: { type: 'Polygon', coordinates: [] }, area_sq_mi: 100, name: 'Addison' } };
const DATA_DOC = {
  meta: { format: 'compact-1', crimeOrder: ['violent'] },
  counties: { '50001': { 2023: [10000, 95, 20] } },
};

function respond(map: Record<string, { status: number; body?: unknown; throws?: Error }>) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const key = Object.keys(map).find((k) => url.includes(k));
    const r = key ? map[key] : { status: 404 };
    if (r.throws) throw r.throws;
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      json: async () => r.body ?? {},
    };
  }));
}

beforeEach(() => {
  S.hasCounties = false;
  S.countyData = null;
  S.countyFeatures = [];
  S.geo = 'state';
});
afterEach(() => vi.unstubAllGlobals());

describe('ensureCounties', () => {
  it('loads the layer and reports how many counties arrived', async () => {
    respond({
      'geo-counties.json': { status: 200, body: GEO },
      'counties.json': { status: 200, body: DATA_DOC },
    });
    await expect(ensureCounties()).resolves.toBe(true);
    expect(S.hasCounties).toBe(true);
    expect(getStatus().counties).toMatchObject({ kind: 'ready' });
    expect(getStatus().counties.detail).toMatch(/1 count/);
  });

  it('reports a 404 as "not built" and points at the build step', async () => {
    respond({});                      // everything 404s
    await expect(ensureCounties()).resolves.toBe(false);
    expect(getStatus().counties).toMatchObject({ kind: 'absent' });
    expect(getStatus().counties.detail).toMatch(/make import/);
  });

  it('reports a stalled transfer as FAILED, and offers a retry', async () => {
    respond({
      'geo-counties.json': { status: 200, body: GEO },
      'counties.json': { status: 200, throws: undefined, body: undefined },
    });
    // Make counties.json fail during body read specifically.
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => {
        if (url.includes('geo-counties.json')) return GEO;
        throw new TypeError('network error');
      },
    })));
    await expect(ensureCounties()).resolves.toBe(false);
    expect(S.hasCounties).toBe(false);
    expect(getStatus().counties).toMatchObject({ kind: 'failed' });
    expect(getStatus().counties.detail).toMatch(/retry/i);
  });

  it('reports a 500 as FAILED rather than "not built"', async () => {
    respond({
      'geo-counties.json': { status: 200, body: GEO },
      'counties.json': { status: 500 },
    });
    await expect(ensureCounties()).resolves.toBe(false);
    expect(getStatus().counties).toMatchObject({ kind: 'failed' });
  });

  it('retries successfully after a failure', async () => {
    respond({ 'geo-counties.json': { status: 200, body: GEO }, 'counties.json': { status: 500 } });
    expect(await ensureCounties()).toBe(false);

    respond({
      'geo-counties.json': { status: 200, body: GEO },
      'counties.json': { status: 200, body: DATA_DOC },
    });
    expect(await ensureCounties()).toBe(true);
    expect(getStatus().counties).toMatchObject({ kind: 'ready' });
  });

  it('shares one request between concurrent callers', async () => {
    respond({
      'geo-counties.json': { status: 200, body: GEO },
      'counties.json': { status: 200, body: DATA_DOC },
    });
    const [a, b] = await Promise.all([ensureCounties(), ensureCounties()]);
    expect(a).toBe(true);
    expect(b).toBe(true);
    // Two files, one attempt - not four fetches.
    expect((globalThis.fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(2);
  });

  it('does not refetch once the layer is loaded', async () => {
    respond({
      'geo-counties.json': { status: 200, body: GEO },
      'counties.json': { status: 200, body: DATA_DOC },
    });
    await ensureCounties();
    const before = (globalThis.fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
    await ensureCounties();
    expect((globalThis.fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length)
      .toBe(before);
  });
});
