import type { Api } from '../api';
import type { Param, State, Store } from '../store';
import { esc } from './esc';

/** The shared sizes a person may change from the phone (seeded per brief by the host). */
export const EDITABLE = ['wall', 'gap', 'standoff_h', 'clear_top', 'clear_bottom'];

const SOURCE_KEYS = ['pcb', 'holes', 'ports'] as const;
export type DatasheetLinks = Partial<Record<(typeof SOURCE_KEYS)[number], string>>;

/** The host seeds a looked-up board's datasheet URLs into the brief's plan JSON (plan 05). */
export function datasheetLinks(planJson: string): DatasheetLinks {
  let plan: { sources?: Record<string, unknown> } | null = null;
  try { plan = JSON.parse(planJson) as { sources?: Record<string, unknown> } | null; } catch { return {}; }
  const sources = plan?.sources;
  if (!sources || typeof sources !== 'object') return {};
  const out: DatasheetLinks = {};
  for (const key of SOURCE_KEYS) {
    const url = sources[key];
    // Only web links: a plan written by an agent must not become a javascript: link.
    if (typeof url === 'string' && /^https?:\/\//i.test(url)) out[key] = url;
  }
  return out;
}

const LABEL: Record<(typeof SOURCE_KEYS)[number], string> = { pcb: 'PCB size', holes: 'holes', ports: 'ports' };

function sheetLink(url: string, text: string) {
  return `<a class="src" href="${esc(url)}" target="_blank" rel="noreferrer">${text}</a>`;
}

/** Parse what a person typed into millimetres; null when it is not a sensible size. */
export function parseMm(s: string): number | null {
  const v = Number(s.replace(',', '.').replace(/\s*mm\s*$/i, '').trim());
  return Number.isFinite(v) && v > 0 && v < 1000 ? Math.round(v * 100) / 100 : null;
}

/** Re-renders only when the values change and never while someone is typing in a field. */
export function mountParams(el: HTMLElement, store: Store, api: Api | null) {
  let key = '';
  el.addEventListener('change', e => {
    const inp = e.target as HTMLInputElement;
    const alias = inp.dataset.alias;
    if (!alias) return;
    const v = parseMm(inp.value);
    const row = inp.closest('.param');
    if (v == null) { row?.classList.add('bad'); return; }
    row?.classList.remove('bad');
    row?.classList.add('saving');
    void api?.setParam(alias, `${v} mm`);
    api?.logActivity('status', `changed ${alias} to ${v} mm`, ['Params']);
  });
  store.subscribe(s => {
    const shown: Param[] = s.params.filter(p => EDITABLE.includes(p.alias))
      .sort((a, b) => EDITABLE.indexOf(a.alias) - EDITABLE.indexOf(b.alias));
    const links = datasheetLinks(newestPlan(s));
    const k = shown.map(p => `${p.alias}=${p.num}`).join('|') + (api ? '' : '|ro') + JSON.stringify(links);
    if (k === key || el.contains(document.activeElement)) return;
    key = k;
    const sourceLine = SOURCE_KEYS.filter(key => links[key])
      .map(key => sheetLink(links[key]!, LABEL[key])).join(' · ');
    el.innerHTML = (shown.map(p => {
      const seeded = links.pcb && (p.alias === 'clear_top' || p.alias === 'clear_bottom');
      return `
      <label class="param"><span class="alias">${esc(p.alias.replace(/_/g, ' '))}${seeded ? ` ${sheetLink(links.pcb!, 'from datasheet ↗')}` : ''}</span>
        <input data-alias="${esc(p.alias)}" inputmode="decimal" enterkeyhint="done" value="${p.num}"${api ? '' : ' disabled'}>
        <span class="unit">mm</span></label>`;
    }).join('')
      || '<p class="muted">Dimensions appear once a build starts.</p>')
      + (sourceLine ? `<p class="muted small sources">Board from its datasheet: ${sourceLine}</p>` : '');
  });
}

/** The brief whose Params are on screen: the newest one (same rule as the panels). */
function newestPlan(s: Pick<State, 'briefs'>): string {
  return [...s.briefs].sort((a, b) => b.updatedMs - a.updatedMs)[0]?.planJson ?? '';
}
