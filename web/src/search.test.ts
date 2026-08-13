import { describe, expect, it } from 'vitest';
import {
  bareName, buildIndex, normalize, parseQuery, resolve, reverseMembers, search,
} from './search';
import type { SearchDoc } from './types';

const DOC: SearchDoc = {
  meta: {
    states_by_abbr: {
      IL: 'Illinois', NY: 'New York', VT: 'Vermont', MO: 'Missouri',
      MA: 'Massachusetts', WV: 'West Virginia', VA: 'Virginia', NC: 'North Carolina',
    },
  },
  states: [
    ['Illinois', 'IL', '17'], ['New York', 'NY', '36'], ['Vermont', 'VT', '50'],
    ['Missouri', 'MO', '29'], ['Massachusetts', 'MA', '25'],
    ['West Virginia', 'WV', '54'], ['Virginia', 'VA', '51'],
  ] as [string, string, string][],
  counties: [
    ['Cook', 'IL', '17031', 5150233], ['New York City', 'NY', '36998', 8184044],
    ['Addison', 'VT', '50001', 37124], ['St. Louis', 'MO', '29189', 1000438],
    ['Chittenden', 'VT', '50007', 168323],
  ],
  cities: [
    ['Chicago', 'IL', '17031', 2628298],
    ['New York', 'NY', '36998', 8184044],
    ['Springfield', 'MO', '29077', 170521],
    ['Springfield', 'MA', '25013', 154218],
    ['Springfield', 'IL', '17167', 112781],
    ['Burlington', 'VT', '50007', 44522],
    ['Burlington', 'NC', '37001', 60156],
    ['Vermont', 'IL', '17057', 700],
  ],
};

const IX = buildIndex(DOC);
const one = (q: string) => search(IX, q)[0];

describe('normalize and bareName', () => {
  it('folds case, punctuation and accents', () => {
    expect(normalize('St. Louis')).toBe('st louis');
    expect(normalize("O'Fallon")).toBe('ofallon');
    expect(normalize('Doña Ana')).toBe('dona ana');
  });

  it('strips the generic tail so "Cook County" finds "Cook"', () => {
    expect(bareName('Cook County')).toBe('cook');
    expect(bareName('Orleans Parish')).toBe('orleans');
    expect(bareName('Prince of Wales-Hyder Census Area')).toBe('prince of wales hyder');
  });

  it('never strips a name down to nothing', () => {
    // "City" and "Borough" are real place names on their own.
    expect(bareName('City')).toBe('city');
  });
});

describe('parseQuery', () => {
  const by = DOC.meta!.states_by_abbr!;

  it('accepts a comma, a bare abbreviation, or a full state name', () => {
    expect(parseQuery('Chicago, IL', by)).toEqual({ name: 'chicago', abbr: 'IL' });
    expect(parseQuery('chicago il', by)).toEqual({ name: 'chicago', abbr: 'IL' });
    expect(parseQuery('chicago, illinois', by)).toEqual({ name: 'chicago', abbr: 'IL' });
  });

  it('leaves a bare state name as the whole query', () => {
    // Otherwise "Illinois" becomes an empty name filtered to Illinois and
    // matches every place in the state.
    expect(parseQuery('Illinois', by)).toEqual({ name: 'illinois', abbr: null });
  });

  it('prefers the longest state name', () => {
    expect(parseQuery('foo west virginia', by)).toEqual({ name: 'foo', abbr: 'WV' });
  });
});

describe('search', () => {
  it('finds Chicago however the state is written, or omitted', () => {
    for (const q of ['chicago', 'Chicago, IL', 'chicago il', 'CHICAGO']) {
      expect(one(q).target, q).toBe('17031');
    }
  });

  it('resolves New York City to the merged unit, not a borough FIPS', () => {
    // fips.canonical() runs at build time; this is the round trip that
    // proves it survived into the index. 36061 would be Manhattan alone,
    // which this map no longer draws.
    const hits = search(IX, 'new york');
    for (const h of hits.filter((x) => x.kind !== 'state')) {
      expect(h.target).toBe('36998');
    }
  });

  it('reads a bare state name as the state, and still offers the city', () => {
    // "New York" is genuinely ambiguous. The state wins for consistency
    // with "Vermont", and the city stays visible in the list rather than
    // the choice being made silently.
    const hits = search(IX, 'new york');
    expect(hits[0].kind).toBe('state');
    expect(hits.some((h) => h.kind === 'city')).toBe(true);
  });

  it('orders ambiguous names by population and shows each state', () => {
    const hits = search(IX, 'springfield');
    expect(hits.map((h) => h.abbr)).toEqual(['MO', 'MA', 'IL']);
  });

  it('narrows to one when the state is given', () => {
    const hits = search(IX, 'springfield, ma');
    expect(hits).toHaveLength(1);
    expect(hits[0].target).toBe('25013');
  });

  it('ranks a state above a city of the same name', () => {
    // There is a Vermont, Illinois. Typing "Vermont" means the state.
    expect(one('vermont').kind).toBe('state');
  });

  it('ranks an exact match above a longer prefix match', () => {
    expect(one('new york').name).toBe('New York');
  });

  it('matches a county by its full name', () => {
    expect(one('cook county').target).toBe('17031');
  });

  it('returns nothing for an empty or unmatched query', () => {
    expect(search(IX, '')).toEqual([]);
    expect(search(IX, '   ')).toEqual([]);
    expect(search(IX, 'zzzzzz')).toEqual([]);
  });
});

describe('resolve', () => {
  const chicago = one('chicago');
  const vermont = one('vermont');
  const members = reverseMembers({
    R17001: { members: ['17031', '17043'] },
    R50001: { members: ['50001', '50007'] },
  });

  it('lands a city on its state when states are drawn', () => {
    expect(resolve(chicago, 'state', null)).toBe('Illinois');
  });

  it('lands a city on its county when counties are drawn', () => {
    expect(resolve(chicago, 'county', null)).toBe('17031');
  });

  it('lands a city on the region containing its county', () => {
    expect(resolve(chicago, 'region:floor', members)).toBe('R17001');
  });

  it('cannot put a state on a county layer', () => {
    // A state is not one county, and guessing one would be a lie.
    expect(resolve(vermont, 'county', null)).toBeNull();
  });

  it('returns null when the region layer has no member map yet', () => {
    // The caller offers to load the layer rather than highlighting nothing.
    expect(resolve(chicago, 'region:floor', null)).toBeNull();
  });

  it('returns null for a county absent from every region', () => {
    expect(resolve(one('addison'), 'region:floor',
      reverseMembers({ R17001: { members: ['17031'] } }))).toBeNull();
  });
});
