import type { Api } from '../api';
import type { Param, Store } from '../store';
import { esc } from './esc';

/** The shared sizes a person may change from the phone (seeded per brief by the host). */
export const EDITABLE = ['wall', 'gap', 'standoff_h', 'clear_top', 'clear_bottom'];

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
    const k = shown.map(p => `${p.alias}=${p.num}`).join('|') + (api ? '' : '|ro');
    if (k === key || el.contains(document.activeElement)) return;
    key = k;
    el.innerHTML = shown.map(p => `
      <label class="param"><span class="alias">${esc(p.alias.replace(/_/g, ' '))}</span>
        <input data-alias="${esc(p.alias)}" inputmode="decimal" enterkeyhint="done" value="${p.num}"${api ? '' : ' disabled'}>
        <span class="unit">mm</span></label>`).join('')
      || '<p class="muted">Dimensions appear once a build starts.</p>';
  });
}
