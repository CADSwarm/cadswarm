// The shared sizes sheet: every value a part reads, in one place, with the same edit path as
// the Model tab's dimensions (set_param on the Params lock). Re-renders only when a value moves
// and never while someone is typing in a field.
import type { Api } from '../api';
import type { Param, State, Store } from '../store';
import { esc } from './esc';
import { parseMm } from './params';

const mm = (p: Param) => Number.isFinite(p.num) ? String(p.num) : p.value.replace(/\s*mm\s*$/i, '').trim();

function holder(s: Pick<State, 'locks' | 'members'>): string {
  const lock = s.locks.find(l => l.object === 'Params');
  return lock ? s.members.find(m => m.id === lock.holder)?.name ?? '' : '';
}

export function mountSizes(el: HTMLElement, store: Store, api: Api | null) {
  let key = '';
  let note = '';
  let changed = '';

  el.addEventListener('change', e => {
    const inp = e.target as HTMLInputElement;
    const alias = inp.dataset.alias;
    if (!alias) return;
    const v = parseMm(inp.value);
    const row = inp.closest('.param');
    if (v == null) { row?.classList.add('bad'); return; }
    row?.classList.remove('bad');
    row?.classList.add('saving');
    changed = alias;
    note = `Changed ${alias.replace(/_/g, ' ')} to ${v} mm. Every part that reads it updates, and the helper that owns those parts is told.`;
    el.querySelectorAll<HTMLElement>('.sizes-note').forEach(n => { n.textContent = note; });
    void api?.setParam(alias, `${v} mm`);
    api?.logActivity('status', `changed ${alias} to ${v} mm`, ['Params']);
  });

  store.subscribe(s => {
    const params = [...s.params].sort((a, b) => a.alias.localeCompare(b.alias));
    const held = holder(s);
    const k = params.map(p => `${p.alias}=${p.value}`).join('|') + `|${held}|${api ? '' : 'ro'}`;
    if (k === key || el.contains(document.activeElement)) return;
    key = k;
    const rows = params.map(p => {
      const label = p.alias.replace(/_/g, ' ');
      if (!Number.isFinite(p.num)) {
        return `<div class="param static"><span class="alias">${esc(label)}</span>
          <span class="size-value">${esc(p.value)}</span></div>`;
      }
      return `<label class="param${p.alias === changed ? ' changed' : ''}">
        <span class="alias">${esc(label)}</span>
        <input data-alias="${esc(p.alias)}" inputmode="decimal" enterkeyhint="done" value="${mm(p)}"${api ? '' : ' disabled'}>
        <span class="unit">mm</span></label>`;
    }).join('');
    el.innerHTML = `
      <div class="sizes-holder">${held
        ? `Params are held by <b>${esc(held)}</b>. You can still change a size: CadSwarm asks them to let go for a moment.`
        : 'Nobody holds the Params sheet right now, so your change can go straight through.'}</div>
      ${rows ? `<div class="group params sizes-sheet">${rows}</div>`
        : '<p class="empty-note">No shared sizes yet. They appear as soon as a build starts.</p>'}
      <p class="muted small sizes-note">${esc(note || (params.length
        ? 'Change a size once and every part that reads it updates for the whole team.'
        : ''))}</p>`;
  });
}
