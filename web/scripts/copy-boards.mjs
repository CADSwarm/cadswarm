// Copies ../boards/boards.json into public/ so the web app can fetch it at runtime.
// The board library is owned by another workstream and may not exist yet; then the brief form
// falls back to a free-text board field, so a missing file is a warning, not a build failure.
import { copyFileSync, existsSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '../../boards/boards.json');
const dst = resolve(here, '../public/boards.json');
if (existsSync(src)) {
  mkdirSync(dirname(dst), { recursive: true });
  copyFileSync(src, dst);
  console.log('boards.json copied into public/');
} else {
  rmSync(dst, { force: true });
  console.warn('boards/boards.json not found: the board picker falls back to free text');
}
