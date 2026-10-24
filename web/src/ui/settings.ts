// The Settings tab: the team's fits, bolt-hole class, thread mode, printing and precision settings, and the
// suggestions AI helpers made. People change settings (the module refuses agents); helpers only suggest.
import type { Api } from '../api';
import type { State, Store } from '../store';
import { esc } from './esc';
import { checkValue, effective, loadJson, parseSpec, type SettingSpec } from './library-data';

export function mountSettings(el: HTMLElement, store: Store, api: Api | null) {
  let spec: SettingSpec[] = [];
  let key = '';
  void loadJson('./settings_spec.json', parseSpec).then(s => { spec = s; key = ''; render(store.state); });

  el.addEventListener('change', e => {
    const inp = e.target as HTMLInputElement | HTMLSelectElement;
    const s = spec.find(x => x.key === inp.dataset.setting);
    if (!s || !api) return;
    const row = inp.closest('.setting');
    const v = checkValue(s, inp.value);
    if ('error' in v) { row?.classList.add('bad'); row?.setAttribute('title', v.error); return; }
    row?.classList.remove('bad');
    row?.classList.add('saving');
    void api.setSetting(s.key, v.num, v.text);
  });
  el.addEventListener('click', e => {
    const t = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-accept],[data-dismiss],[data-reset]');
    if (!t || !api) return;
    t.disabled = true;
    if (t.dataset.reset) void api.resetSetting(t.dataset.reset);
    else void api.resolveSuggestion((t.dataset.accept ?? t.dataset.dismiss)!, t.dataset.accept != null);
  });

  function fmt(s: SettingSpec | undefined, v: number | string) {
    if (!s) return String(v);
    return s.kind === 'choice' ? String(v).replace(/_/g, ' ') : `${v} ${s.unit}`.trim();
  }

  function render(st: State) {
    const pending = st.suggestions.filter(g => g.status === 'pending').sort((a, b) => b.createdMs - a.createdMs);
    const k = JSON.stringify([st.settings, pending.map(g => g.id), spec.length, !!api]);
    if (k === key || el.contains(document.activeElement)) return;
    key = k;
    const values = effective(spec, st.settings);
    const changed = new Map(st.settings.map(r => [r.setting, r]));
    const groups = [...new Set(spec.map(s => s.group))];
    const name = (id: number) => store.member(id)?.name ?? 'someone';
    el.innerHTML = `
      ${pending.length ? `<div class="card suggestions"><h3>Suggested by the team's AI helpers</h3>
        ${pending.map(g => {
          const s = spec.find(x => x.key === g.setting);
          const value = s?.kind === 'choice' ? g.text : g.num;
          return `<div class="suggestion"><p><b>${esc(s?.label ?? g.setting)}</b>: ${esc(fmt(s, values[g.setting] ?? ''))} → <b>${esc(fmt(s, value))}</b></p>
            <p class="small muted">${esc(g.reason)} — ${esc(name(g.by))}</p>
            ${api ? `<button type="button" class="mini" data-accept="${esc(g.id)}">Accept</button>
                     <button type="button" class="mini ghost" data-dismiss="${esc(g.id)}">Dismiss</button>` : ''}</div>`;
        }).join('')}</div>` : ''}
      <p class="muted small">These apply to the whole team. Fits are also shared sizes (Params.tol_clearance…), so parts built with them update when you change them.</p>
      ${groups.map(group => `<h3>${esc(group)}</h3>${spec.filter(s => s.group === group).map(s => {
        const v = values[s.key];
        const row = changed.get(s.key);
        const input = s.kind === 'choice'
          ? `<select data-setting="${esc(s.key)}"${api ? '' : ' disabled'}>${(s.choices ?? []).map(c => `<option value="${esc(c)}"${c === v ? ' selected' : ''}>${esc(c.replace(/_/g, ' '))}</option>`).join('')}</select>`
          : `<input data-setting="${esc(s.key)}" inputmode="decimal" value="${esc(String(v))}"${api ? '' : ' disabled'}><span class="unit">${esc(s.unit)}</span>`;
        return `<label class="setting param" title="${esc(s.help ?? '')}"><span class="alias">${esc(s.label)}${row ? ` <span class="muted small">(set by ${esc(name(row.setBy))})</span>` : ''}</span>
          ${input}${row && api ? `<button type="button" class="mini ghost" data-reset="${esc(s.key)}" title="Back to ${esc(fmt(s, s.default))}">Default</button>` : ''}</label>`;
      }).join('')}`).join('') || '<p class="muted">Loading settings…</p>'}`;
  }

  store.subscribe(render);
}
