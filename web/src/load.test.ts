import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadLocal, loadOptional } from './load';

function mockFetch(impl: () => Promise<unknown>) {
  vi.stubGlobal('fetch', vi.fn(impl));
}
const ok = (json: () => Promise<unknown>) => async () => ({ ok: true, status: 200, json });
const status = (code: number) => async () =>
  ({ ok: code >= 200 && code < 300, status: code, json: async () => ({}) });

afterEach(() => vi.unstubAllGlobals());

/* The distinction these tests pin down cost real debugging time once. A
   stalled transfer was reported as "not valid JSON — rebuild with: make
   data", which sent the investigation at the data files while the actual
   fault was in the server. The two causes need opposite advice. */
describe('loadLocal (required files)', () => {
  it('returns parsed JSON on success', async () => {
    mockFetch(ok(async () => ({ a: 1 })));
    await expect(loadLocal('/data/x.json')).resolves.toEqual({ a: 1 });
  });

  it('blames the build when the bytes arrived but were not JSON', async () => {
    mockFetch(ok(async () => { throw new SyntaxError('Unexpected token <'); }));
    await expect(loadLocal('/data/x.json')).rejects.toThrow(/is not valid JSON/);
    await expect(loadLocal('/data/x.json')).rejects.toThrow(/make data/);
  });

  it('blames the transfer when the body never finished arriving', async () => {
    mockFetch(ok(async () => { throw new TypeError('network error'); }));
    await expect(loadLocal('/data/x.json')).rejects.toThrow(/did not complete/);
    // The critical part: it must NOT tell you to rebuild a file that is fine.
    await expect(loadLocal('/data/x.json')).rejects.not.toThrow(/make data/);
  });

  it('reports an unreachable server distinctly from a bad response', async () => {
    mockFetch(async () => { throw new TypeError('Failed to fetch'); });
    await expect(loadLocal('/data/x.json')).rejects.toThrow(/Could not reach/);
  });

  it('reports the status code on an HTTP error', async () => {
    mockFetch(status(500));
    await expect(loadLocal('/data/x.json')).rejects.toThrow(/HTTP 500/);
  });
});

/* THE COUNTY-TAB BUG.

   Every one of these outcomes used to return null, indistinguishably, and
   boot then skipped the county layer with no signal at all. Only the first
   is a legitimate reason to stay quiet. */
describe('loadOptional distinguishes "never built" from "broke"', () => {
  it('reports success with the payload', async () => {
    mockFetch(ok(async () => ({ counties: {} })));
    const r = await loadOptional<{ counties: unknown }>('/data/counties.json');
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.data).toEqual({ counties: {} });
  });

  it('calls a 404 absent - the build legitimately lacks this layer', async () => {
    mockFetch(status(404));
    const r = await loadOptional('/data/counties.json');
    expect(r).toMatchObject({ ok: false, reason: 'absent' });
  });

  it('calls a truncated transfer FAILED, not absent', async () => {
    mockFetch(ok(async () => { throw new TypeError('network error'); }));
    const r = await loadOptional('/data/counties.json');
    expect(r).toMatchObject({ ok: false, reason: 'failed' });
    if (!r.ok) expect(r.message).toMatch(/did not complete/);
  });

  it('calls an unreachable server FAILED, not absent', async () => {
    mockFetch(async () => { throw new TypeError('Failed to fetch'); });
    expect(await loadOptional('/data/counties.json'))
      .toMatchObject({ ok: false, reason: 'failed' });
  });

  it('calls malformed JSON FAILED, and says to rebuild', async () => {
    mockFetch(ok(async () => { throw new SyntaxError('Unexpected token <'); }));
    const r = await loadOptional('/data/counties.json');
    expect(r).toMatchObject({ ok: false, reason: 'failed' });
    if (!r.ok) expect(r.message).toMatch(/make data/);
  });

  it('calls a 500 FAILED, not absent', async () => {
    mockFetch(status(500));
    expect(await loadOptional('/data/counties.json'))
      .toMatchObject({ ok: false, reason: 'failed' });
  });

  it('never reports a failure without a message to show the user', async () => {
    for (const impl of [
      status(500),
      ok(async () => { throw new TypeError('network error'); }),
      async () => { throw new TypeError('Failed to fetch'); },
    ]) {
      mockFetch(impl as () => Promise<unknown>);
      const r = await loadOptional('/data/counties.json');
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.message.length).toBeGreaterThan(10);
    }
  });
});
