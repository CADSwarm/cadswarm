import type { Api, Printability } from '../api';
import type { Review, State, Store, Workspace } from '../store';
import { esc } from './esc';

/** Findings are free-form JSON from the reviewer; pull readable lines out of whatever shape arrives. */
function allFindings(json: string): string[] {
  let v: unknown;
  try { v = JSON.parse(json || '[]'); } catch { return json.trim() ? [json.trim()] : []; }
  const list = Array.isArray(v) ? v : v && typeof v === 'object' && Array.isArray((v as { findings?: unknown }).findings)
    ? (v as { findings: unknown[] }).findings : [];
  return list.map(f => {
    if (typeof f === 'string') return f;
    if (!f || typeof f !== 'object') return String(f);
    const r = f as Record<string, unknown>;
    // The reviewer's prompt asks for {"object","problem","task_id"}; older verdicts use other keys.
    const text = String(r.problem ?? r.message ?? r.text ?? r.detail ?? r.summary ?? JSON.stringify(f));
    return r.object ? `${r.object}: ${text}` : text;
  }).map(f => f.trim()).filter(Boolean);
}

/** The first few findings, for a compact panel or chat bubble. */
export function reviewFindings(json: string, max = 3): string[] {
  return allFindings(json).slice(0, max);
}

/** One line for the round: 2 passed, or 3 asks for changes. */
export function reviewHeadline(r: Pick<Review, 'round' | 'passed'>): string {
  return `Round ${r.round} ${r.passed ? 'passed' : 'asks for changes'}`;
}

/**
 * The review the downloadable model reflects: the latest round of the newest brief that has a finished
 * model (the same brief the Model tab exports, modelBrief in panels.ts). A newer unfinished request must
 * not make an older model's verdict read as missing or different.
 */
export function shownReview(s: Pick<State, 'briefs' | 'reviews'>): Review | undefined {
  const brief = [...s.briefs].filter(b => b.stlHash).sort((a, b) => b.updatedMs - a.updatedMs)[0];
  if (!brief) return undefined;
  return [...s.reviews].filter(r => r.brief === brief.id)
    .sort((a, b) => b.round - a.round || b.atMs - a.atMs)[0];
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

function mm(v: number): string {
  return String(Math.round(v * 10) / 10);
}

/** One line per part: its measured size, then what (if anything) would stop it printing. */
export function printLines(p: Printability): string[] {
  return p.objects.map(o => {
    const size = o.size.length ? `${o.size.slice(0, 3).map(mm).join(' x ')} mm` : 'unknown size';
    const issues: string[] = [];
    if (!o.fitsBed) issues.push('too big for the bed');
    if (!o.closedSolid) issues.push('not a closed solid');
    if (o.thinWalls.length) {
      const closest = Math.min(...o.thinWalls);
      issues.push(`${o.thinWalls.length} thin wall${o.thinWalls.length === 1 ? '' : 's'}`
        + (closest > 0 ? ` (closest ${mm(closest)} mm)` : ''));
    }
    return `${o.name}: ${size} - ${issues.join('; ') || 'fits'}`;
  });
}

export function printHeadline(p: Printability): string {
  return p.ok ? 'Fits the printer' : 'Not printable yet';
}

function printCard(p: Printability): string {
  if (!p.objects.length) return '<p class="card-empty">There are no solid parts to check yet. Build something first.</p>';
  return `<article class="review-card ${p.ok ? 'ok' : 'warn'}">
      <div class="review-head"><span class="review-verdict">${printHeadline(p)}</span></div>
      <ul class="findings">${printLines(p).map(l => `<li>${esc(l)}</li>`).join('')}</ul>
    </article>`;
}

/** The printer block in the Model tab: the team's bed, presets, and one Save button. */
export function mountPrinter(el: HTMLElement, store: Store, api: Api | null) {
  let key = '';
  const notice = (text: string) => store.set({ notice: { text, at: Date.now() } });

  el.addEventListener('click', async e => {
    const preset = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-preset]');
    if (preset) {
      const [x, y, z] = preset.dataset.preset!.split(',').map(Number);
      el.querySelectorAll<HTMLInputElement>('[data-bed]').forEach((input, i) => { input.value = String([x, y, z][i]); });
      return;
    }
    const check = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-print-check]');
    if (!check || !api) return;
    const inputs = [...el.querySelectorAll<HTMLInputElement>('[data-bed]')];
    const [x, y, z] = inputs.map(bedValue);
    const problem = bedError(x, y, z);
    if (problem) { notice(problem); return; }
    const label = check.textContent;
    check.disabled = true;
    check.textContent = 'Checking…';
    const result = await api.checkPrintability([x, y, z]);
    const box = el.querySelector<HTMLElement>('[data-print-result]');
    if (box) box.innerHTML = result ? printCard(result) : '';
    check.disabled = false;
    check.textContent = label;
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
        <button type="button" class="text-button print-check" data-print-check${api ? '' : ' disabled'}
          title="Check the parts fit the bed, are closed solids and have printable walls">Check it fits</button>
      </form>
      <div data-print-result></div>`;
  });
}
