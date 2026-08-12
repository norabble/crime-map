/* Pure formatting and escaping. No DOM, no state - so this is directly
   unit-testable, which matters most for esc(). */

/* HTML-escape untrusted text before it goes anywhere near innerHTML.
   Place names reach this file from data/geo-*.json, which fetch_geometry
   builds out of TopoJSON downloaded from a CDN — so a name is remote
   input, not a constant. The numbers interpolated alongside it all come
   from fmt() and are safe by construction; the strings are not. */
export function esc(s: unknown): string {
  const map: Record<string, string> = {
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  };
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => map[c]!);
}

/** Fixed-decimal locale number, or an em dash for anything non-finite. */
export function fmt(v: number | null | undefined, d: number): string {
  if (v == null || !isFinite(v)) return '—';
  return v.toLocaleString('en-US', {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}
