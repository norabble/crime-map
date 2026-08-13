import type { MapboxOverlay } from '@deck.gl/mapbox';
import type { Map as MapLibreMap } from 'maplibre-gl';
import type { Feat, SearchHit, SearchIndex, Store } from './types';

export interface AppState {
  crime: string;
  year: number;
  exag: number;
  /* Threshold below which pillars are dimmed, held as a FRACTION of the
     fixed height domain rather than in rate or density units. That is what
     lets one slider serve both measures: the handle keeps its meaning when
     `swap` moves height from crime rate to density. 0 disables the effect. */
  fade: number;
  normalize: boolean;
  /* false: height = crime rate, colour = density (the default reading)
     true:  the two channels swap */
  swap: boolean;
  palette: string;
  /** Active layer id from the registry in layers.ts — 'state', 'county'
      or 'region:<variant>'. Not a union type: variants are discovered at
      runtime from what the build produced. */
  geo: string;
  /** Years summed into each displayed value. 1 is off. */
  window: number;
  /** Gazetteer for place search; null when search.json was not built. */
  searchIndex: SearchIndex | null;
  /** The unit the user picked, as (layer, key). Outlined on the map and
      re-resolved rather than cleared when the layer changes. */
  selected: { layer: string; key: string } | null;
  /** The hit `selected` came from, kept so a layer switch can re-resolve
      the original place rather than the key it happened to produce. */
  selectedHit: SearchHit | null;
  /** county FIPS -> region key, per region variant, built when it loads. */
  regionMembers: Map<string, Map<string, string>>;
  meta: Record<string, unknown>;
  hasCounties: boolean;
  countyData: Store | null;
  countyFeatures: Feat[];
  /** County keys in the data with no matching geometry — data that will
      never be drawn. Should be zero; surfaced so it cannot hide. */
  countyUnmatched: number;
  /** GeoJSON features, each with .props (joined data). */
  features: Feat[];
  /* Unified store, both eras merged:
       data: stateName -> year -> { pop, cov, src, rates:{crimeId:rate} }
     SRS years arrive complete; NIBRS years fill in per crime type as
     each one is fetched, so `rates` may be sparse for 2020+. */
  data: Store;
  /** Every year currently selectable. */
  years: number[];
  /** Complete NIBRS years discovered from the API. */
  nibrsYears: number[];
  /** Crime id -> max rate across ALL units and ALL years. */
  crimeMax: Record<string, number>;
  /** Min density across ALL units and ALL years. */
  densMin: number;
  /** Max density across ALL units and ALL years. */
  densMax: number;
  /* Absolute-scale reference: highest single rate seen anywhere in the
     dataset, so "Absolute" mode is comparable across crime types. Lives on
     the state object rather than as a module-level `let` because
     recomputeScales() in data.ts writes it and render.ts reads it, and an
     exported `let` cannot be assigned across a module boundary. */
  absMax: number;
  deck: MapboxOverlay | null;
  map: MapLibreMap | null;
}

export const S: AppState = {
  crime: 'violent',
  year: 2019,
  exag: 1,
  fade: 0,
  normalize: true,
  swap: false,
  palette: 'greenred',
  geo: 'state',
  window: 1,
  searchIndex: null,
  selected: null,
  selectedHit: null,
  regionMembers: new Map(),
  meta: {},
  hasCounties: false,
  countyData: null,
  countyFeatures: [],
  countyUnmatched: 0,
  features: [],
  data: new Map(),
  years: [],
  nibrsYears: [],
  crimeMax: {},
  densMin: 0,
  densMax: 1,
  absMax: 1,
  deck: null,
  map: null,
};
