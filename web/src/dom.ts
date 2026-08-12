/** getElementById, kept terse because the UI wiring uses it constantly. */
export const $ = (id: string): HTMLElement => document.getElementById(id)!;

/* Data lives at <base>/data/. BASE_URL is whatever vite.config.ts sets, so
   this resolves identically on the dev server and on Project Pages, where
   the site is served from /crime-map/ rather than the domain root. Hardcoding
   either a leading slash or a ../ hop gets one of the two environments wrong. */
export const DATA = (file: string): string =>
  import.meta.env.BASE_URL + 'data/' + file;
