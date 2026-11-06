// The Properties panel: the active command's inputs in collapsible groups, with OK ✓ / Cancel ✕ / Help / Pin.
// Body fields are selection boxes: the active one is tinted and takes the next pick in the 3D view or tree.
import { icon } from './icons';
import { esc } from './esc';
import { MODE_WORDS, helperFor, modeOf, type Command, type Ctx, type Field, type Values } from './commands';
import { view } from '../viewstate';

export interface PropsPanel {
  open(cmd: Command): void;
  close(): void;
  active(): Command | null;
}

export function mountProps(host: HTMLElement, ctx: () => Ctx, onChange: (open: boolean) => void): PropsPanel {
  let cmd: Command | null = null;
  let fields: Field[] = [];
  let values: Values = {};
  let activeSel = '';
  let pinned = false;
  let help = false;
  let busy = false;
  const collapsed = new Set<string>();

  function empty() {
    host.innerHTML = `<div class="pm-empty">${icon('props', 'ico big')}
      <p><b>No command is active</b></p>
      <p class="muted">Pick a command in the ribbon, or press <kbd>Ctrl</kbd> <kbd>K</kbd> and type its name.
      Its inputs appear here with OK and Cancel.</p></div>`;
  }
  empty();

  function read(): Values {
    const v: Values = { ...values };
    host.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>('[data-f]').forEach(inp => {
      const k = inp.dataset.f!;
      if (inp instanceof HTMLInputElement && inp.type === 'checkbox') v[k] = inp.checked;
      else if (inp instanceof HTMLInputElement && (inp.type === 'number' || inp.type === 'range')) v[k] = inp.value === '' ? '' : Number(inp.value);
      else v[k] = inp.value;
    });
    return v;
  }

  function fieldHtml(f: Field): string {
    const v = values[f.key] ?? f.value ?? '';
    const unit = f.unit ? `<span class="unit">${esc(f.unit)}</span>` : '';
    let input = '';
    switch (f.kind) {
      case 'body':
        input = `<button type="button" class="selbox${activeSel === f.key ? ' on' : ''}" data-sel="${esc(f.key)}" title="${esc(f.hint ?? '')}">
          ${v ? `<span class="chip">${icon('body', 'ico xs')}${esc(String(v))}<span class="chip-x" data-clear="${esc(f.key)}" aria-label="Clear">×</span></span>`
            : `<span class="selbox-hint">${activeSel === f.key ? 'Pick a body…' : 'Click, then pick a body'}</span>`}</button>`;
        break;
      case 'select':
        input = `<select data-f="${esc(f.key)}">${(f.options ?? []).map(([k, l]) => `<option value="${esc(k)}"${k === String(v) ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
        break;
      case 'check':
        return `<label class="pm-row pm-check" data-row="${esc(f.key)}"><input type="checkbox" data-f="${esc(f.key)}"${v ? ' checked' : ''}><span>${esc(f.label)}</span></label>`;
      case 'range':
        input = `<span class="range"><input type="range" data-f="${esc(f.key)}" min="${f.min ?? 0}" max="${f.max ?? 100}" step="${f.step ?? 1}" value="${esc(String(v))}">
          <output data-out="${esc(f.key)}">${esc(String(v))}${esc(f.unit ?? '')}</output></span>`;
        break;
      case 'textarea':
        return `<label class="pm-row pm-stack" data-row="${esc(f.key)}"><span class="pm-label">${esc(f.label)}</span>
          <textarea data-f="${esc(f.key)}" rows="3" placeholder="${esc(f.hint ?? '')}">${esc(String(v))}</textarea></label>`;
      case 'number':
        input = `<span class="num"><input type="number" data-f="${esc(f.key)}" value="${esc(String(v))}" step="${f.step ?? 1}"${f.min != null ? ` min="${f.min}"` : ''}${f.max != null ? ` max="${f.max}"` : ''}>${unit}</span>`;
        break;
      default:
        input = `<span class="num"><input type="text" data-f="${esc(f.key)}" value="${esc(String(v))}" placeholder="${esc(f.kind === 'xyz' ? 'x, y, z' : f.hint ?? '')}">${f.kind === 'xyz' ? unit : ''}</span>`;
    }
    return `<label class="pm-row" data-row="${esc(f.key)}"><span class="pm-label" title="${esc(f.hint ?? '')}">${esc(f.label)}</span>${input}</label>`;
  }

  function render() {
    if (!cmd) { empty(); return; }
    const c = ctx();
    const mode = modeOf(cmd, values);
    const helper = mode === 'ai' ? helperFor(c.store.state, Object.values(values).filter(v => typeof v === 'string') as string[]) : undefined;
    const groups: [string, Field[]][] = [];
    for (const f of fields) {
      const g = f.group ?? 'Parameters';
      const at = groups.find(x => x[0] === g);
      if (at) at[1].push(f); else groups.push([g, [f]]);
    }
    const ok = cmd.okLabel ?? (mode === 'ai' ? 'Send to helpers' : 'OK');
    host.innerHTML = `<div class="pm">
      <header class="pm-head">
        <span class="pm-icon">${icon(cmd.icon)}</span><b class="pm-title">${esc(cmd.label)}</b>
        <span class="pm-tools">
          <button type="button" class="pm-btn ok" data-pm="ok" title="${esc(ok)} (Enter)"${busy ? ' disabled' : ''}>${icon('check')}</button>
          <button type="button" class="pm-btn cancel" data-pm="cancel" title="Cancel (Esc)">${icon('x')}</button>
          <button type="button" class="pm-btn" data-pm="help" title="Help" aria-pressed="${help}">${icon('help')}</button>
          <button type="button" class="pm-btn" data-pm="pin" title="Keep visible after OK" aria-pressed="${pinned}">${icon('pin')}</button>
        </span>
      </header>
      <div class="pm-mode mode-${mode}">${esc(MODE_WORDS[mode])}${helper ? ` · ${esc(helper.name)}` : ''}</div>
      ${help ? `<p class="pm-help">${esc(cmd.tip)}</p>` : ''}
      <form class="pm-form" data-pm-form>
        ${groups.map(([g, fs]) => `<section class="pm-group${collapsed.has(g) ? ' closed' : ''}">
          <button type="button" class="pm-group-head" data-group="${esc(g)}">${icon('down', 'ico xs caret')}<span>${esc(g)}</span></button>
          <div class="pm-group-body">${fs.map(fieldHtml).join('')}</div></section>`).join('')}
        ${!fields.length ? `<p class="pm-help">${esc(cmd.tip)}</p>` : ''}
        <div class="pm-result" data-result></div>
        <div class="pm-foot">
          <button type="submit" class="btn primary"${busy ? ' disabled' : ''}>${busy ? 'Working…' : esc(ok)}</button>
          <button type="button" class="btn" data-pm="cancel">Cancel</button>
        </div>
      </form></div>`;
    applyWhen();
  }

  function applyWhen() {
    const v = read();
    for (const f of fields) {
      const row = host.querySelector<HTMLElement>(`[data-row="${CSS.escape(f.key)}"]`);
      if (row) row.hidden = !!f.when && !f.when(v);
    }
  }

  function setResult(html: string) {
    const r = host.querySelector<HTMLElement>('[data-result]');
    if (r) r.innerHTML = html;
  }

  host.addEventListener('input', e => {
    if (!cmd) return;
    values = read();
    const t = e.target as HTMLInputElement;
    const out = host.querySelector(`[data-out="${CSS.escape(t.dataset.f ?? '')}"]`);
    if (out) out.textContent = `${t.value}${fields.find(f => f.key === t.dataset.f)?.unit ?? ''}`;
    applyWhen();
    cmd.live?.(ctx(), values);
  });
  host.addEventListener('change', () => {
    if (!cmd) return;
    const before = modeOf(cmd, values);
    values = read();
    applyWhen();
    if (modeOf(cmd, values) !== before) render(); // the badge and OK words follow the mode
  });
  host.addEventListener('click', e => {
    const t = e.target as HTMLElement;
    const clear = t.closest<HTMLElement>('[data-clear]');
    if (clear) { e.preventDefault(); values = { ...read(), [clear.dataset.clear!]: '' }; activeSel = clear.dataset.clear!; render(); return; }
    const box = t.closest<HTMLElement>('[data-sel]');
    if (box) { e.preventDefault(); values = read(); activeSel = activeSel === box.dataset.sel ? '' : box.dataset.sel!; render(); return; }
    const g = t.closest<HTMLElement>('[data-group]');
    if (g) { const k = g.dataset.group!; if (collapsed.has(k)) collapsed.delete(k); else collapsed.add(k); g.parentElement?.classList.toggle('closed'); return; }
    const b = t.closest<HTMLElement>('[data-pm]');
    if (!b) return;
    const what = b.dataset.pm;
    if (what === 'ok') void ok();
    else if (what === 'cancel') cancel();
    else if (what === 'help') { values = read(); help = !help; render(); }
    else if (what === 'pin') { pinned = !pinned; b.setAttribute('aria-pressed', String(pinned)); }
  });
  host.addEventListener('submit', e => { e.preventDefault(); void ok(); });
  host.addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.preventDefault(); cancel(); }
  });

  // Picks fill the active selection box, then move on to the next empty one.
  view.on(s => {
    if (!cmd || !activeSel) return;
    const name = s.selection.at(-1);
    if (!name) return;
    values = { ...read(), [activeSel]: name };
    const bodyKeys = fields.filter(f => f.kind === 'body' && (!f.when || f.when(values))).map(f => f.key);
    const next = bodyKeys.find(k => k !== activeSel && !values[k]);
    activeSel = next ?? activeSel;
    render();
  });
  addEventListener('cadswarm:measure', e => { if (cmd?.id === 'measure') setResult(String((e as CustomEvent<string>).detail)); });

  async function ok() {
    if (!cmd || busy) return;
    values = read();
    const c = cmd;
    busy = true; render();
    let result: { html: string } | void = undefined;
    try { result = await c.run?.(ctx(), values); }
    catch (err) { ctx().notify((err as Error).message ?? String(err)); }
    finally { busy = false; }
    if (cmd !== c) return;
    if (result?.html) { render(); setResult(result.html); return; }
    if (pinned) { render(); return; }
    finish(false);
  }
  function cancel() {
    if (!cmd) return;
    cmd.cancel?.(ctx());
    finish(true);
  }
  function finish(_cancelled: boolean) {
    if (cmd?.id === 'measure') cmd.cancel?.(ctx());
    cmd = null; fields = []; values = {}; activeSel = '';
    empty();
    onChange(false);
  }

  return {
    open(next) {
      if (cmd && cmd !== next) cmd.cancel?.(ctx());
      cmd = next;
      const c = ctx();
      fields = next.fields?.(c) ?? [];
      values = Object.fromEntries(fields.map(f => [f.key, f.value ?? '']));
      activeSel = fields.find(f => f.kind === 'body' && !values[f.key])?.key ?? '';
      help = !fields.length;
      render();
      onChange(true);
      next.live?.(c, values);
      host.querySelector<HTMLElement>('input:not([type=checkbox]):not([type=range]), textarea, select')?.focus();
    },
    close: cancel,
    active: () => cmd,
  };
}
