import { activeFeatures } from './data';
import { S } from './state';
import type { BBox, Feat } from './types';

export function bbox(f: Feat): BBox {
  let minX = 180, minY = 90, maxX = -180, maxY = -90;
  const walk = (c: unknown): void => {
    const arr = c as unknown[];
    if (typeof arr[0] === 'number') {
      const x = arr[0] as number, y = arr[1] as number;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    } else arr.forEach(walk);
  };
  walk(f.geometry.coordinates);
  return [minX, minY, maxX, maxY];
}

/* Frame whatever the active layer actually covers. The state layer spans
   the country, but a build scoped to one state occupies a corner of it —
   at the national camera those columns are barely a pixel wide, which
   reads as "the layer is broken" rather than "you are 2,000 miles away
   from it". */
export function fitActiveLayer(): void {
  const feats = activeFeatures();
  if (!S.map || !feats.length) return;
  let minX = 180, minY = 90, maxX = -180, maxY = -90;
  for (const f of feats) {
    const b = bbox(f);
    if (b[0] < minX) minX = b[0];
    if (b[1] < minY) minY = b[1];
    if (b[2] > maxX) maxX = b[2];
    if (b[3] > maxY) maxY = b[3];
  }
  if (minX > maxX || minY > maxY) return;
  S.map.fitBounds([[minX, minY], [maxX, maxY]],
    { padding: 120, pitch: 52, duration: 900 });
}
