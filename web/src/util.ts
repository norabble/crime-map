/** Wraps fn so it runs at most once, whoever calls it and however often.

    Boot has three racing triggers - the map's load event, a watchdog
    timeout, and a style error - and any of them should be able to start
    the viewer while none may start it twice. */
export function once(fn: () => void): () => void {
  let called = false;
  return () => {
    if (called) return;
    called = true;
    fn();
  };
}
