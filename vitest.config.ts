import { defineConfig } from 'vitest/config';

/* Separate from vite.config.ts on purpose: that file sets root:'web' and a
   /crime-map/ base for serving, neither of which should shape test
   discovery.

   environment:'node' - no jsdom. Everything that touches the DOM or WebGL
   lives in map.ts, render.ts, ui.ts, legend.ts and tooltip.ts; the modules
   worth testing (scales, data decoding, loading, escaping) are pure and
   import nothing from maplibre or deck.gl at runtime. Keeping that true is
   itself a design constraint. */
export default defineConfig({
  test: {
    include: ['web/src/**/*.test.ts'],
    environment: 'node',
  },
});
