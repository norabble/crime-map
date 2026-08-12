import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadLocal } from './load';

function mockFetch(impl: () => Promise<unknown>) {
  vi.stubGlobal('fetch', vi.fn(impl));
}

afterEach(() => vi.unstubAllGlobals());

/* The distinction these tests pin down cost real debugging time once. A
   stalled transfer was reported as "not valid JSON — rebuild with: make
   data", which sent the investigation at the data files while the actual
   fault was in the server. The two causes need opposite advice. */
describe('loadLocal error reporting', () => {
  it('returns parsed JSON on success', async () => {
    mockFetch(async () => ({ ok: true, status: 200, json: async () => ({ a: 1 }) }));
    await expect(loadLocal('/data/x.json')).resolves.toEqual({ a: 1 });
  });

  it('blames the build when the bytes arrived but were not JSON', async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => { throw new SyntaxError('Unexpected token <'); },
    }));
    await expect(loadLocal('/data/x.json')).rejects.toThrow(/is not valid JSON/);
    await expect(loadLocal('/data/x.json')).rejects.toThrow(/make data/);
  });

  it('blames the transfer when the body never finished arriving', async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => { throw new TypeError('network error'); },
    }));
    const p = loadLocal('/data/x.json');
    await expect(p).rejects.toThrow(/did not complete/);
    // The critical part: it must NOT tell you to rebuild a file that is fine.
    await expect(loadLocal('/data/x.json')).rejects.not.toThrow(/make data/);
  });

  it('reports an unreachable server distinctly from a bad response', async () => {
    mockFetch(async () => { throw new TypeError('Failed to fetch'); });
    await expect(loadLocal('/data/x.json')).rejects.toThrow(/Could not reach/);
  });

  it('reports the status code on an HTTP error', async () => {
    mockFetch(async () => ({ ok: false, status: 404, json: async () => ({}) }));
    await expect(loadLocal('/data/x.json')).rejects.toThrow(/HTTP 404/);
  });
});

/* Today `optional` collapses every failure to null. That is the mechanism
   behind the silently-missing County tab, and Phase 3 changes it. These
   tests document the CURRENT behaviour so the change is visible as a
   deliberate edit to them rather than an accident. */
describe('loadLocal optional files (pre-Phase-3 behaviour)', () => {
  it('returns null when the file is genuinely absent', async () => {
    mockFetch(async () => ({ ok: false, status: 404, json: async () => ({}) }));
    await expect(loadLocal('/data/counties.json', true)).resolves.toBeNull();
  });

  it('ALSO returns null on a truncated transfer, indistinguishably', async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => { throw new TypeError('network error'); },
    }));
    await expect(loadLocal('/data/counties.json', true)).resolves.toBeNull();
  });

  it('ALSO returns null when the server is unreachable, indistinguishably', async () => {
    mockFetch(async () => { throw new TypeError('Failed to fetch'); });
    await expect(loadLocal('/data/counties.json', true)).resolves.toBeNull();
  });
});
