import { describe, expect, it, vi } from 'vitest';
import { once } from './util';

/* Boot has three racing triggers - the map's load event, the watchdog
   timeout, and a style error. Any of them must be able to start the
   viewer; none may start it twice, or the deck overlay is constructed
   twice and added to the map twice. */
describe('once', () => {
  it('runs the function on the first call', () => {
    const fn = vi.fn();
    once(fn)();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('ignores every later call', () => {
    const fn = vi.fn();
    const gated = once(fn);
    gated(); gated(); gated();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('gates by wrapper, so racing callers share one guard', () => {
    // The map's 'load' handler and the watchdog hold the same wrapper.
    const fn = vi.fn();
    const gated = once(fn);
    const asLoadHandler = () => gated();
    const asWatchdog = () => gated();
    asWatchdog();
    asLoadHandler();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('stays closed even if the function throws', () => {
    const fn = vi.fn(() => { throw new Error('boom'); });
    const gated = once(fn);
    expect(() => gated()).toThrow('boom');
    gated();
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
