import type { Api } from '../api';
import type { Brief, Review, State, Store, Workspace } from '../store';
import { esc } from './esc';

/** Findings are free-form JSON from the reviewer; pull readable lines out of whatever shape arrives. */
function allFindings(json: string): string[] {
  let v: unknown;
  try { v = JSON.parse(json || '[]'); } catch { return json.trim() ? [json.trim()] : []; }
  const list = Array.isArray(v) ? v : v && typeof v === 'object' && Array.isArray((v as { findings?: unknown }).findings)
    ? (v as { findings: unknown[] }).findings : [];
  return list.map(f => typeof f === 'string' ? f
    : f && typeof f === 'object' ? String((f as Record<string, unknown>).message ?? (f as Record<string, unknown>).text
      ?? (f as Record<string, unknown>).detail ?? (f as Record<string, unknown>).summary ?? JSON.stringify(f)) : String(f))
    .map(f => f.trim()).filter(Boolean);
}

/** The first few findings, for a compact panel or chat bubble. */
export function reviewFindings(json: string, max = 3): string[] {
  return allFindings(json).slice(0, max);
}

/** One line for the round: 2 passed, or 3 asks for changes. */
export function reviewHeadline(r: Pick<Review, 'round' | 'passed'>): string {
  return `Round ${r.round} ${r.passed ? 'passed' : 'asks for changes'}`;
}

function newestBrief(s: Pick<State, 'briefs'>): Brief | undefined {
  return [...s.briefs].sort((a, b) => b.updatedMs - a.updatedMs)[0];
}

/** The review the model's state reflects: the newest round of the newest brief. */
export function shownReview(s: Pick<State, 'briefs' | 'reviews'>): Review | undefined {
  const brief = newestBrief(s);
  const pool = brief ? s.reviews.filter(r => r.brief === brief.id) : s.reviews;
  return [...pool].sort((a, b) => b.round - a.round || b.atMs - a.atMs)[0];
}

/** The review block in the Model tab. Rebuilds only when the round changes. */
export function mountReview(el: HTMLElement, store: Store) {
  let key = '';
  store.subscribe(s => {
    const review = shownReview(s);
    const k = review ? `${review.id}:${review.round}:${review.passed}:${review.findingsJson}` : 'none';
    if (k === key) return;
    key = k;
    el.innerHTML = review ? reviewCard(review)
      : '<p class="card-empty">No review yet. The reviewer checks the model before it is ready.</p>';
  });
}

function reviewCard(r: Review): string {
  const findings = allFindings(r.findingsJson);
  const shown = findings.slice(0, 3);
  const more = findings.length - shown.length;
  return `<article class="review-card ${r.passed ? 'ok' : 'warn'}">
      <div class="review-head"><span class="review-verdict">${r.passed ? 'Passed' : 'Needs changes'}</span>
        <span class="review-round">${esc(reviewHeadline(r))}</span></div>
      ${shown.length ? `<ul class="findings">${shown.map(f => `<li>${esc(f)}</li>`).join('')}</ul>`
        : '<p class="review-none">No findings.</p>'}
      ${more > 0 ? `<p class="review-more">and ${more} more</p>` : ''}
    </article>`;
}

/** The printer bed bounds a team allows (same range as the set_bed reducer). */
export const BED_MIN = 20;
export const BED_MAX = 2000;

export const BED_PRESETS: [number, number, number, string][] = [
  [180, 180, 180, 'A1 mini'],
  [256, 256, 256, 'P1S / X1C'],
  [250, 210, 220, 'Prusa MK4'],
  [220, 220, 250, 'Ender 3'],
];

/** What is wrong with a bed size, or "" when it is usable. */
export function bedError(x: number, y: number, z: number): string {
  if (![x, y, z].every(Number.isFinite)) return 'Enter the bed size in millimetres.';
  if (Math.min(x, y, z) < BED_MIN || Math.max(x, y, z) > BED_MAX) {
    return `A printer bed between ${BED_MIN} and ${BED_MAX} mm on each side, please.`;
  }
  return '';
}

function bedValue(input: HTMLInputElement): number {
  const v = Number(input.value.replace(',', '.').replace(/\s*mm\s*$/i, '').trim());
  return Number.isFinite(v) ? Math.round(v * 10) / 10 : NaN;
}

const DEFAULT_BED: Workspace = { bedXMm: 180, bedYMm: 180, bedZMm: 180 };

/** The printer block in the Model tab: the team's bed, presets, and one Save button. */
export function mountPrinter(el: HTMLElement, store: Store, api: Api | null) {
  let key = '';
  const notice = (text: string) => store.set({ notice: { text, at: Date.now() } });

  el.addEventListener('click', e => {
    const preset = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-preset]');
    if (!preset) return;
    const [x, y, z] = preset.dataset.preset!.split(',').map(Number);
    el.querySelectorAll<HTMLInputElement>('[data-bed]').forEach((input, i) => { input.value = String([x, y, z][i]); });
  });

  el.addEventListener('submit', e => {
    e.preventDefault();
    if (!api) { notice('Not connected to a team yet.'); return; }
    const inputs = [...el.querySelectorAll<HTMLInputElement>('[data-bed]')];
    const [x, y, z] = inputs.map(bedValue);
    const problem = bedError(x, y, z);
    if (problem) { notice(problem); return; }
    const save = el.querySelector<HTMLButtonElement>('[data-bed-save]');
    if (save) { save.disabled = true; save.textContent = 'Saving…'; }
    void api.setBed(x, y, z).finally(() => {
      if (save) { save.disabled = false; save.textContent = 'Save bed'; }
    });
  });

  store.subscribe(s => {
    const bed = s.workspace ?? DEFAULT_BED;
    const k = `${bed.bedXMm}|${bed.bedYMm}|${bed.bedZMm}|${api ? 'on' : 'off'}`;
    if (k === key || el.contains(document.activeElement)) return;
    key = k;
    const values: [string, string, number][] = [['x', 'Width (X)', bed.bedXMm], ['y', 'Depth (Y)', bed.bedYMm],
      ['z', 'Height (Z)', bed.bedZMm]];
    el.innerHTML = `<form class="printer-card" data-bed-form>
        <div class="bed-grid">${values.map(([axis, label, value]) => `
          <label class="bed-field"><span>${label} <span class="unit">mm</span></span>
            <input data-bed="${axis}" type="number" inputmode="decimal" step="1" min="${BED_MIN}" max="${BED_MAX}"
              value="${value}"${api ? '' : ' disabled'}></label>`).join('')}
        </div>
        <div class="bed-actions">
          <div class="bed-presets">${BED_PRESETS.map(([x, y, z, label]) =>
            `<button type="button" class="chip-button" data-preset="${x},${y},${z}" title="${x} x ${y} x ${z} mm">${esc(label)}</button>`).join('')}
          </div>
          <button type="submit" class="text-button" data-bed-save${api ? '' : ' disabled'}>Save bed</button>
        </div>
      </form>`;
  });
}
