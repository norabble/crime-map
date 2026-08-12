import { describe, expect, it } from 'vitest';
import { esc, fmt } from './format';

/* esc() is the XSS boundary. Place names arrive from data/geo-*.json, which
   fetch_geometry builds out of TopoJSON downloaded from a CDN, and they are
   interpolated into innerHTML in the tooltip, the ranking list and the
   ranking list's data-name attribute. CSP does not cover this - escaping
   here is the whole defence. */
describe('esc', () => {
  it('escapes every character that can break out of markup', () => {
    expect(esc('<script>')).toBe('&lt;script&gt;');
    expect(esc('a & b')).toBe('a &amp; b');
    expect(esc('"quoted"')).toBe('&quot;quoted&quot;');
    expect(esc("O'Brien County")).toBe('O&#39;Brien County');
  });

  it('neutralises an attribute break-out, not just a tag one', () => {
    // updateRanking puts the name inside data-name="...". An unescaped
    // quote would close that attribute and open one of its own.
    const hostile = '" onmouseover="alert(1)';
    expect(esc(hostile)).not.toContain('"');
    expect(esc(hostile)).toBe('&quot; onmouseover=&quot;alert(1)');
  });

  it('escapes the ampersand first, so entities are not double-decoded', () => {
    // If & were escaped last, "&lt;" would come back out as "<".
    expect(esc('&lt;')).toBe('&amp;lt;');
  });

  it('passes ordinary place names through unchanged', () => {
    expect(esc('Miami-Dade County')).toBe('Miami-Dade County');
    expect(esc('Doña Ana County')).toBe('Doña Ana County');
  });

  it('is safe on null and undefined', () => {
    expect(esc(null)).toBe('');
    expect(esc(undefined)).toBe('');
  });
});

describe('fmt', () => {
  it('formats to a fixed number of decimals', () => {
    expect(fmt(1234.567, 1)).toBe('1,234.6');
    expect(fmt(1234.567, 0)).toBe('1,235');
  });

  it('renders a genuine zero as zero, not as missing', () => {
    // A county with twelve reported zero-crime months is data, not a gap.
    expect(fmt(0, 0)).toBe('0');
  });

  it('renders non-finite values as an em dash', () => {
    expect(fmt(NaN, 1)).toBe('—');
    expect(fmt(Infinity, 1)).toBe('—');
    expect(fmt(null, 1)).toBe('—');
  });
});
