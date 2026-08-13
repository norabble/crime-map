/* The registry of drawable geographies.

   `S.geo` used to be `'state' | 'county'` and eight places branched on it:
   which features to draw, which store to read, how to key a feature, the
   scale cache, the deck layer id and two tooltip nouns. Adding a third
   geography meant finding all eight and getting all eight right.

   A layer is now a record. Registering one makes it drawable everywhere,
   and the eight branches collapse into `active()`. */

import type { Feat, Store } from './types';

export interface Layer {
  /** Stable id: also the deck layer suffix and the scale cache key. */
  id: string;
  /** Button text. */
  label: string;
  /** Singular noun for prose — "this county's population was covered". */
  noun: string;
  /* Accessors, not values. The state layer's arrays live on `S` and are
     REASSIGNED by buildData(), so a captured reference would quietly go
     stale and the map would draw the previous build's features. A thunk
     keeps one source of truth wherever it happens to live. */
  features: () => Feat[];
  store: () => Store;
  /** How a feature finds its row in `store`. */
  keyOf: (f: Feat) => string;
}

const registry = new Map<string, Layer>();

export function registerLayer(layer: Layer): void {
  registry.set(layer.id, layer);
}

export function getLayer(id: string): Layer | undefined {
  return registry.get(id);
}

export function hasLayer(id: string): boolean {
  const l = registry.get(id);
  return !!l && l.features().length > 0;
}

/** Every registered layer, in registration order. */
export function layerIds(): string[] {
  return [...registry.keys()];
}

/* The active layer, or the state layer when the requested one has not
   loaded. Falling back rather than throwing matters: the region and county
   layers are fetched on demand, so `S.geo` can name a layer that does not
   exist yet during a click, and a blank map is a worse answer than the
   previous one. */
export function active(id: string): Layer {
  const l = registry.get(id);
  if (l && l.features().length) return l;
  const fallback = registry.get('state');
  if (!fallback) {
    throw new Error('No state layer registered — buildData() never ran.');
  }
  return fallback;
}

/** Test seam. */
export function clearLayers(): void {
  registry.clear();
}
