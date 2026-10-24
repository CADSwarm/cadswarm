import type { Api } from '../api';
import { esc } from './esc';

export interface BoardOption { id: string; label: string }

/** `boards/boards.json` is `{ "<id>": { "label": ..., ... } }` (plan 00). Anything else gives an empty list. */
export function parseBoards(json: unknown): BoardOption[] {
  if (!json || typeof json !== 'object' || Array.isArray(json)) return [];
  return Object.entries(json as Record<string, unknown>)
    .filter(([id, b]) => id && b && typeof b === 'object')
    .map(([id, b]) => {
      const label = (b as { label?: unknown }).label;
      return { id, label: typeof label === 'string' && label.trim() ? label : id };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

/** The board list is copied into the build when it exists; missing or broken means "type it". */
export async function loadBoards(fetchFn: typeof fetch = fetch): Promise<BoardOption[]> {
  try {
    const r = await fetchFn('./boards.json', { cache: 'no-cache' });
    return r.ok ? parseBoards(await r.json()) : [];
  } catch { return []; }
}

const OTHER = '__other__';

export function mountBrief(el: HTMLElement, api: Api | null, open: boolean) {
  el.innerHTML = `
    <details class="brief-box"${open ? ' open' : ''}>
      <summary>${open ? 'Start a build' : 'Start a new build'}</summary>
      <form class="brief">
        <label>What should we build?
          <textarea name="text" required rows="3" maxlength="2000"
            placeholder="A case for my ESP32-S3 with a USB-C cutout and a snap-on lid"></textarea>
        </label>
        <div class="board-field"></div>
        <button type="submit">Build it</button>
      </form>
    </details>`;
  const form = el.querySelector<HTMLFormElement>('form')!;
  const field = el.querySelector<HTMLDivElement>('.board-field')!;

  const freeText = (note: string) => `
    <label>Board <span class="muted">(optional)</span>
      <input name="board_text" autocomplete="off" maxlength="80" placeholder="e.g. rpi4, esp32-s3-devkitc-1">
    </label>${note ? `<p class="hint muted">${note}</p>` : ''}`;

  field.innerHTML = freeText('');
  void loadBoards().then(boards => {
    if (!boards.length) { field.innerHTML = freeText(''); return; }
    const typed = (form.elements.namedItem('board_text') as HTMLInputElement | null)?.value ?? '';
    field.innerHTML = `
      <label>Board
        <select name="board">
          <option value="">None</option>
          ${boards.map(b => `<option value="${esc(b.id)}">${esc(b.label)}</option>`).join('')}
          <option value="${OTHER}">Other (type it)</option>
        </select>
      </label>
      <label class="board-other" hidden>Board name
        <input name="board_text" autocomplete="off" maxlength="80" value="${esc(typed)}">
      </label>`;
    const select = field.querySelector('select')!;
    const other = field.querySelector<HTMLLabelElement>('.board-other')!;
    if (typed) { select.value = OTHER; other.hidden = false; }
    select.addEventListener('change', () => { other.hidden = select.value !== OTHER; });
  });

  form.addEventListener('submit', e => {
    e.preventDefault();
    const text = (form.elements.namedItem('text') as HTMLTextAreaElement).value.trim();
    if (!text) return;
    const select = form.elements.namedItem('board') as HTMLSelectElement | null;
    const typed = (form.elements.namedItem('board_text') as HTMLInputElement | null)?.value.trim() ?? '';
    const board = select && select.value !== OTHER ? select.value : typed;
    api?.submitBrief(text, board);
    form.reset();
    field.querySelector<HTMLLabelElement>('.board-other')?.setAttribute('hidden', '');
    el.querySelector('details')!.open = false;
  });
}
