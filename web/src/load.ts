/* ---------- local build artefacts --------------------------------- */
/* Reads one JSON file produced by the build scripts. Never touches the
   API, so no key and no rate limit. `optional` files — the county layer —
   resolve to null when absent rather than failing the whole boot. */
export async function loadLocal<T = unknown>(
  url: string,
  optional = false,
): Promise<T | null> {
  let res: Response;
  try {
    res = await fetch(url, { cache: 'no-cache' });
  } catch (err) {
    if (optional) return null;
    throw new Error('Could not reach ' + url + ' (' + (err as Error).message + ')');
  }
  if (!res.ok) {
    if (optional) return null;
    throw new Error('HTTP ' + res.status + ' for ' + url);
  }
  try {
    return (await res.json()) as T;
  } catch (err) {
    if (optional) return null;
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
