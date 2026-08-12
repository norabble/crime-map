import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  // The web root. Note this alone does NOT confine the dev server: a request
  // for /crime-map/../.git/config resolves out of here and is served. Proven,
  // not assumed - see server.fs below, which is what actually confines it.
  root: 'web',

  // Project Pages serve from /<repo>/, and this is what makes
  // import.meta.env.BASE_URL correct. Deliberately the same in dev and in
  // the build: a base that only applies to one of them means the thing you
  // test is not the thing you ship.
  base: '/crime-map/',

  // <root>/public -> served at <base>, copied into dist/. scripts/sync-data.mjs
  // populates web/public/data from data/ before dev and before build.
  publicDir: 'public',

  build: {
    outDir: '../dist',
    emptyOutDir: true,
  },

  server: {
    host: '127.0.0.1', // loopback only; an unbuilt-for-the-internet preview
    port: 8000, // stays off the LAN. Matches the old make serve port.

    fs: {
      strict: true,

      // THIS is the confinement, and it is load-bearing. fs.allow otherwise
      // defaults to the workspace root - which here is the repo, so .env,
      // .git and the whole Python pipeline sit inside it and `..` reaches
      // them. Narrowing it to the two directories a browser legitimately
      // needs is what makes traversal fail. node_modules is required
      // because that is where dependency requests resolve to.
      allow: [here('./web'), here('./node_modules')],

      // Defence in depth behind fs.allow, for the things that would hurt
      // most if allow were ever widened. Vite denies .env by default; this
      // project ships an API key in one, so it is stated rather than
      // inherited, and .git is added because its config can carry
      // credentials.
      deny: ['**/.env', '**/.env.*', '**/.git/**', '**/.cache/**', '**/*.pem', '**/*.key'],
    },
  },
});
