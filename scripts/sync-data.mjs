#!/usr/bin/env node
/**
 * Copy data/*.json into the Vite public dir so the dev server and the build
 * both serve it at /data/.
 *
 * A copy rather than a symlink: `vite build` walks publicDir with fs.cp, and
 * a symlink there behaves differently between dev and build. The whole point
 * of this project's serving layer is that what you test is what you get.
 *
 * data/ stays where it is because scripts/ writes there and the Python
 * pipeline's paths are not worth disturbing for this.
 */
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'data');
const DEST = join(ROOT, 'web', 'public', 'data');

if (!existsSync(SRC)) {
  console.error('\n  No data/ directory. Build it first:\n\n    make data\n');
  process.exit(1);
}

const files = readdirSync(SRC).filter((f) => f.endsWith('.json'));
if (files.length === 0) {
  console.error('\n  data/ has no .json files. Build them first:\n\n    make data\n');
  process.exit(1);
}

// Replace rather than merge, so a file deleted from data/ cannot linger here
// and make a stale build look complete.
rmSync(DEST, { recursive: true, force: true });
mkdirSync(DEST, { recursive: true });

let total = 0;
for (const f of files) {
  cpSync(join(SRC, f), join(DEST, f));
  total += statSync(join(SRC, f)).size;
}

console.log(
  '  data -> web/public/data: %d files, %s MB',
  files.length,
  (total / 1024 / 1024).toFixed(1)
);
