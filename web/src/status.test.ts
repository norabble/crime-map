import { describe, expect, it } from 'vitest';
import { describeState, initialStatus, problemSummary } from './status';

describe('describeState', () => {
  it('marks a loaded layer as ok', () => {
    expect(describeState({ kind: 'ready', detail: '51 states' }))
      .toEqual({ text: '51 states', tone: 'ok' });
  });

  it('marks a failed layer as bad, never as merely missing', () => {
    // This is the whole point of the county fix: a failure must not be
    // presentable as "this layer does not exist".
    expect(describeState({ kind: 'failed' }).tone).toBe('bad');
  });

  it('marks a never-built layer as warn, not bad', () => {
    // A build without counties.json is a legitimate build.
    expect(describeState({ kind: 'absent' }).tone).toBe('warn');
  });

  it('always produces some text, even with no detail', () => {
    for (const kind of ['pending', 'loading', 'ready', 'absent', 'failed'] as const) {
      expect(describeState({ kind }).text.length).toBeGreaterThan(0);
    }
  });
});

describe('problemSummary', () => {
  it('is null when everything is fine', () => {
    const m = initialStatus();
    m.states = { kind: 'ready' };
    m.basemap = { kind: 'ready' };
    m.counties = { kind: 'ready' };
    expect(problemSummary(m)).toBeNull();
  });

  it('does not cry wolf over a layer that was simply never built', () => {
    const m = initialStatus();
    m.states = { kind: 'ready' };
    m.basemap = { kind: 'ready' };
    m.counties = { kind: 'absent' };
    expect(problemSummary(m)).toBeNull();
  });

  it('names the layer that actually failed', () => {
    const m = initialStatus();
    m.states = { kind: 'ready' };
    m.basemap = { kind: 'ready' };
    m.counties = { kind: 'failed' };
    expect(problemSummary(m)).toMatch(/Counties/);
  });

  it('names every failed layer when more than one is broken', () => {
    const m = initialStatus();
    m.states = { kind: 'ready' };
    m.basemap = { kind: 'failed' };
    m.counties = { kind: 'failed' };
    const s = problemSummary(m)!;
    expect(s).toMatch(/Counties/);
    expect(s).toMatch(/Basemap/);
  });
});
