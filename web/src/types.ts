/* Shapes of the build artefacts in data/ and of the objects derived from
   them. Nothing here has a runtime cost - it exists so that a change to
   the Python side that alters a field name fails at the type level rather
   than as a blank map. */

export type RGB = [number, number, number];

export interface CrimeDef {
  id: string;
  label: string;
  /** CORGIS column for the per-100k rate. */
  rate: string;
  /** CORGIS column for the raw count. */
  total: string;
  /** Offence slug used by the FBI Crime Data Explorer API. */
  cde: string;
}

export interface Palette {
  label: string;
  safe: boolean;
  note?: string;
  stops: RGB[];
}

/** One unit-year. `dens` is present only where the builder precomputed it. */
export interface Rec {
  pop: number;
  /** Percent of the unit's population covered by reporting; null when unknown. */
  cov: number | null;
  /** Share (0..1) of offences estimated rather than filed; null when unknown.
      Independent of `cov`: a unit-year can be fully estimated (imp 1) while
      reporting no coverage at all (cov 0), which is exactly what a county
      nobody filed for looks like. */
  imp: number | null;
  src: string;
  rates: Record<string, number>;
  totals: Record<string, number>;
  dens?: number;
  agencies?: number;
  reporting?: number;
}

export type YearMap = Map<number, Rec>;
/** Keyed by state name for the state layer, by FIPS for counties. */
export type Store = Map<string, YearMap>;

export interface Geometry {
  type: string;
  coordinates: unknown[];
}

/** Static join keys, plus the display values render() writes each frame. */
export interface FeatureProps {
  name: string;
  areaSqMi: number;
  fips?: string;

  rate?: number;
  total?: number;
  pop?: number;
  dens?: number;
  cov?: number | null;
  imp?: number | null;
  src?: string | null;
  lowCov?: boolean;
  estimated?: boolean;

  elev?: number;
  color?: RGB;
  alpha?: number;
  faded?: boolean;
}

export interface Feat {
  type: 'Feature';
  geometry: Geometry;
  props: FeatureProps;
}

/** One row of currentValues() - a feature joined to its selected year. */
export interface Row {
  f: Feat;
  rate: number;
  total: number;
  pop: number;
  dens: number;
  cov: number | null;
  imp: number | null;
  src: string | null;
  lowCov: boolean;
  estimated: boolean;
}

/** data/states.json */
export interface StatesDoc {
  meta?: { nibrsYears?: number[]; [k: string]: unknown };
  states: Record<string, Record<string, RawRec>>;
}

export interface RawRec {
  pop: number;
  cov?: number;
  imp?: number;
  src?: string;
  rates?: Record<string, number>;
  totals?: Record<string, number>;
}

export interface GeoEntry {
  geometry: Geometry;
  area_sq_mi: number;
  fips?: string;
  name?: string;
}

export type GeoDoc = Record<string, GeoEntry>;

/** data/counties.json. Compact records are a flat array whose slot order is
    declared by `meta.record` — `compact-1` was [pop, cov, ...totals] and
    `compact-2` is [pop, cov, imp, ...totals]. Read the offsets from
    `meta.record`; never assume where the totals start. */
export type CompactRec = number[];
export type CountyRec = CompactRec | RawRec;

export interface CountiesDoc {
  meta?: {
    format?: string;
    /** Slot names of a compact record, in order. */
    record?: string[];
    crimeOrder?: string[];
    /** Merged unit key -> display name, e.g. 36998 -> "New York City". */
    merged?: Record<string, string>;
  };
  counties: Record<string, Record<string, CountyRec>>;
}

/** Bounding box as [minX, minY, maxX, maxY]. */
export type BBox = [number, number, number, number];
