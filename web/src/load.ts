/* ---------- local build artefacts ---------------------------------

   Two entry points, because required and optional files need genuinely
   different contracts.

   loadLocal throws: a missing states.json is fatal and the boot handler
   turns it into an on-screen message.

   loadOptional returns a discriminated result instead of null. The old
   version collapsed every outcome to null, which is how a failed 8.5 MB
   counties.json fetch produced no signal anywhere - not on screen, not in
   the console - while the County button kept a tooltip saying no such
   dataset exists. "Not built" and "failed to load" want opposite advice
   and only one of them is worth interrupting anybody about.            */

export type LoadFailure = 'absent' | 'failed';

export type LoadResult<T> =
  | { ok: true; data: T }
  | { ok: false; reason: LoadFailure; message: string };

/** Reads a required JSON build artefact. Throws with per-cause advice. */
export async function loadLocal<T = unknown>(url: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { cache: 'no-cache' });
  } catch (err) {
    throw new Error('Could not reach ' + url + ' (' + (err as Error).message + ')');
  }
  if (!res.ok) {
    throw new Error('HTTP ' + res.status + ' for ' + url);
  }
  try {
    return (await res.json()) as T;
  } catch (err) {
    // res.json() rejects for two unrelated reasons, and they need opposite
    // advice. A SyntaxError means bytes arrived and were not JSON — a build
    // problem. Anything else (a TypeError from a connection dropped mid-body)
    // means the body never finished arriving, which is a transport problem;
    // reporting that as "not valid JSON" sends you off to rebuild a file that
    // was never broken. That misdiagnosis cost real time once already.
    if (err instanceof SyntaxError) {
      throw new Error(url + ' is not valid JSON — rebuild with: make data');
    }
    throw new Error('Transfer of ' + url + ' did not complete (' +
      (err as Error).message + '). The file on disk is probably fine — reload the page.');
  }
}

/** Reads an optional build artefact, distinguishing "never built" from "broke". */
export async function loadOptional<T = unknown>(url: string): Promise<LoadResult<T>> {
  let res: Response;
  try {
    res = await fetch(url, { cache: 'no-cache' });
  } catch (err) {
    return {
      ok: false,
      reason: 'failed',
      message: 'Could not reach ' + url + ' (' + (err as Error).message + ')',
    };
  }
  // 404 is the one outcome that means the build legitimately does not
  // include this layer. Everything else means something went wrong.
  if (res.status === 404) {
    return { ok: false, reason: 'absent', message: url + ' has not been built' };
  }
  if (!res.ok) {
    return { ok: false, reason: 'failed', message: 'HTTP ' + res.status + ' for ' + url };
  }
  try {
    return { ok: true, data: (await res.json()) as T };
  } catch (err) {
    if (err instanceof SyntaxError) {
      return {
        ok: false,
        reason: 'failed',
        message: url + ' is not valid JSON — rebuild with: make data',
      };
    }
    return {
      ok: false,
      reason: 'failed',
      message: 'Transfer of ' + url + ' did not complete (' + (err as Error).message + ')',
    };
  }
}
