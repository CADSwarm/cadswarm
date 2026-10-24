// Copies shared data files into public/ so the web app can fetch them at runtime:
// ../boards/boards.json (the board library), and from ../cadswarm the team settings spec and the
// built-in standard parts catalog (the same files the module and the Python side read).
// The board library is owned by another workstream and may not exist yet; then the brief form
// falls back to a free-text board field, so a missing file is a warning, not a build failure.
import { copyFileSync, existsSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const files = [
  ['../../boards/boards.json', 'boards.json', 'the board picker falls back to free text'],
  ['../../cadswarm/settings_spec.json', 'settings_spec.json', 'the Settings tab shows nothing'],
  ['../../cadswarm/library/standard_parts.json', 'standard_parts.json', 'the Library tab shows only team parts'],
];
for (const [from, to, without] of files) {
  const src = resolve(here, from);
  const dst = resolve(here, '../public', to);
  if (existsSync(src)) {
    mkdirSync(dirname(dst), { recursive: true });
    copyFileSync(src, dst);
    console.log(`${to} copied into public/`);
  } else {
    rmSync(dst, { force: true });
    console.warn(`${from} not found: ${without}`);
  }
}
