// The feature tree: the document, its origin planes, then one node per plan step (in step order, so it
// reads like a history) with its bodies under it, a rollback bar after the last built step, and any
// bodies the plan does not name. Each row shows the owner's colour, a lock when someone holds it and a
// status mark. Clicking selects in 3D; double-click zooms to it; the eye hides it in your view.
import type { State, Store } from '../store';
import { memberColor } from '../color';
import { isDark } from '../theme';
import { esc } from './esc';
import { icon } from './icons';
import { parsePlan, progress, rollbackIndex, type StepView } from '../steps';
import { view } from '../viewstate';

const KIND_ICON: Record<string, string> = { part: 'part', library: 'screw', assembly: 'assembly', check: 'review' };
export const STATE_WORD: Record<string, string> = {
  done: 'Built', current: 'Building', ready: 'Ready to build', waiting: 'Waiting for earlier steps',
  blocked: 'Blocked', cancelled: 'Cancelled', missing: 'Not planned yet',
};

export function statusMark(state: string, broken = false): string {
  if (broken) return `<span class="mark-st err" title="Fails to rebuild">${icon('error', 'ico xs')}</span>`;
  switch (state) {
    case 'done': return `<span class="mark-st ok" title="${STATE_WORD.done}">${icon('check', 'ico xs')}</span>`;
    case 'current': return `<span class="mark-st run" title="${STATE_WORD.current}"><span class="spin"></span></span>`;
    case 'blocked': return `<span class="mark-st warn" title="${STATE_WORD.blocked}">${icon('warn', 'ico xs')}</span>`;
    case 'cancelled': return `<span class="mark-st off" title="${STATE_WORD.cancelled}">${icon('x', 'ico xs')}</span>`;
    default: return `<span class="mark-st off" title="${STATE_WORD[state] ?? ''}">${icon('clock', 'ico xs')}</span>`;
  }
}

export function avatar(s: Pick<State, 'members'>, id: number, size = ''): string {
  const m = s.members.find(x => x.id === id);
  if (!m) return '';
  return `<span class="av${size ? ` ${size}` : ''}" style="--c:${memberColor(id, isDark())}" title="${esc(m.name)}">${esc(m.name.slice(0, 1).toUpperCase())}</span>`;
}

export function currentBriefOf(s: Pick<State, 'briefs'>) {
  return [...s.briefs].sort((a, b) => b.updatedMs - a.updatedMs)[0];
}

/** The progress of the newest brief (empty when there is none). */
export function buildProgress(s: State) {
  const b = currentBriefOf(s);
  const plan = b ? parsePlan(b.planJson, s.tasks, b.id) : { version: 2, title: '', steps: [] };
  return { brief: b, plan, p: progress(plan, s.tasks, s.ops) };
}

export function mountTree(el: HTMLElement, store: Store, project: string) {
  el.innerHTML = `<div class="tree-tools">
      <span class="field-ico">${icon('search', 'ico xs')}<input type="search" class="tree-q" placeholder="Filter the tree" aria-label="Filter the feature tree"></span>
    </div>
    <div class="tree" role="tree" aria-label="Feature tree" data-tree></div>`;
  const box = el.querySelector<HTMLElement>('[data-tree]')!;
  const q = el.querySelector<HTMLInputElement>('.tree-q')!;
  const open = new Set<string>();
  const shut = new Set<string>(['origin']);
  let last = '';

  function row(depth: number, key: string, opts: { caret?: boolean; open?: boolean; ico: string; n?: number | string; name: string; title?: string;
    end?: string; cls?: string; data?: string }): string {
    return `<div class="tr d${depth}${opts.cls ? ` ${opts.cls}` : ''}" role="treeitem" data-key="${esc(key)}" ${opts.data ?? ''}${opts.title ? ` title="${esc(opts.title)}"` : ''}>
      ${opts.caret ? `<button type="button" class="tr-caret${opts.open ? ' open' : ''}" data-toggle="${esc(key)}" aria-label="Expand">${icon('chevron', 'ico xs')}</button>` : '<span class="tr-caret"></span>'}
      <span class="tr-ico">${icon(opts.ico, 'ico sm')}</span>
      ${opts.n != null ? `<span class="tr-n">${esc(String(opts.n))}</span>` : ''}
      <span class="tr-name">${esc(opts.name)}</span>
      <span class="tr-end">${opts.end ?? ''}</span></div>`;
  }

  function render(s: State) {
    const vs = view.get();
    const { plan, p } = buildProgress(s);
    const filter = q.value.trim().toLowerCase();
    const match = (t: string) => !filter || t.toLowerCase().includes(filter);
    const objs = s.objects.filter(o => o.name !== 'Params');
    const inPlan = new Set(plan.steps.flatMap(st => st.objects));
    const holderOf = (name: string) => s.locks.find(l => l.object === name)?.holder ?? 0;
    const lockMark = (holder: number) => holder ? `<span class="tr-lock" style="--c:${memberColor(holder, isDark())}" title="Held by ${esc(s.members.find(m => m.id === holder)?.name ?? '')}">${icon('lock', 'ico xs')}</span>` : '';
    const eye = (names: string[]) => names.length ? `<button type="button" class="tr-eye${names.every(n => vs.hidden.has(n)) ? ' off' : ''}" data-eye="${esc(names.join(','))}" title="Hide / show">${icon(names.every(n => vs.hidden.has(n)) ? 'eyeoff' : 'eye', 'ico xs')}</button>` : '';
    const worst = objs.some(o => o.broken) ? 'err' : p.steps.some(st => st.state === 'blocked') ? 'warn' : p.current.length ? 'run' : '';

    const bodyRow = (name: string, depth: number) => {
      const o = objs.find(x => x.name === name);
      if (!o || !match(o.label)) return '';
      const h = holderOf(name);
      return row(depth, `o:${name}`, { ico: 'body', name: o.label, cls: `tr-body${vs.selection.includes(name) ? ' sel' : ''}${vs.hidden.has(name) ? ' hidden' : ''}${o.broken ? ' broken' : ''}`,
        data: `data-obj="${esc(name)}"`, end: `${eye([name])}${lockMark(h)}${o.broken ? statusMark('', true) : ''}` });
    };
    const stepRow = (st: StepView) => {
      const built = st.objects.filter(n => objs.some(o => o.name === n));
      const kids = built.map(n => bodyRow(n, 2)).join('');
      if (!match(st.name) && !kids) return '';
      const isOpen = open.has(st.id) || (st.state === 'current' && !shut.has(st.id)) || !!filter;
      const holder = st.objects.map(holderOf).find(Boolean) ?? 0;
      const sel = built.length > 0 && built.every(n => vs.selection.includes(n));
      return row(1, st.id, { caret: built.length > 0, open: isOpen, ico: KIND_ICON[st.kind] ?? 'part', n: st.n, name: st.name,
        title: `${st.n}. ${st.name} · ${STATE_WORD[st.state]}${st.summary ? `\n${st.summary}` : ''}`,
        cls: `tr-step st-${st.state}${sel ? ' sel' : ''}`, data: `data-objs="${esc(built.join(','))}"`,
        end: `${eye(built)}${lockMark(holder)}${st.owner ? avatar(s, st.owner, 'xs') : ''}${statusMark(st.state, built.some(n => objs.find(o => o.name === n)?.broken))}` })
        + (isOpen ? kids : '');
    };

    const rb = rollbackIndex(p.steps);
    const steps = p.steps.map((st, i) => (i === rb && p.steps.length && rb < p.steps.length
      ? `<div class="rollback" title="Rollback bar: steps below it are not all built yet"><span>${p.done} of ${p.total} built</span></div>` : '') + stepRow(st)).join('');
    const extra = objs.filter(o => !inPlan.has(o.name));
    const html = row(0, 'doc', { ico: 'doc', name: project || 'Model', cls: 'tr-doc', end: worst ? `<span class="mark-st ${worst}">${worst === 'run' ? '<span class="spin"></span>' : icon(worst === 'err' ? 'error' : 'warn', 'ico xs')}</span>` : '' })
      + row(1, 'origin', { caret: true, open: !shut.has('origin'), ico: 'origin', name: 'Origin', cls: 'tr-ref' })
      + (shut.has('origin') ? '' : [['Front plane', 'XZ'], ['Top plane', 'XY'], ['Right plane', 'YZ']].map(([n, k]) => row(2, `pl:${k}`, { ico: 'plane', name: n, cls: 'tr-ref' })).join(''))
      + (p.steps.length ? steps : objs.length ? '' : '<p class="tree-empty">The model is empty. Start a build in <b>AI Build</b>, or add a body from <b>Features</b>.</p>')
      + (extra.length ? row(1, 'extra', { caret: true, open: !shut.has('extra'), ico: 'body', name: p.steps.length ? 'Other bodies' : 'Bodies', n: undefined, cls: 'tr-group' })
        + (shut.has('extra') ? '' : extra.map(o => bodyRow(o.name, 2)).join('')) : '')
      + (rb >= p.steps.length && p.steps.length ? '<div class="rollback end" title="All steps are built"><span>All built</span></div>' : '');
    if (html === last) return;
    last = html;
    box.innerHTML = html;
  }

  box.addEventListener('click', e => {
    const t = e.target as HTMLElement;
    const tog = t.closest<HTMLElement>('[data-toggle]');
    if (tog) {
      const k = tog.dataset.toggle!;
      const wasOpen = tog.classList.contains('open');
      if (wasOpen) { open.delete(k); shut.add(k); } else { open.add(k); shut.delete(k); }
      last = ''; render(store.state); return;
    }
    const eyeB = t.closest<HTMLElement>('[data-eye]');
    if (eyeB) { view.toggleHidden(eyeB.dataset.eye!.split(',').filter(Boolean)); return; }
    const r = t.closest<HTMLElement>('.tr');
    if (!r) return;
    const names = r.dataset.obj ? [r.dataset.obj] : (r.dataset.objs ?? '').split(',').filter(Boolean);
    const add = (e as MouseEvent).shiftKey || (e as MouseEvent).ctrlKey || (e as MouseEvent).metaKey;
    view.select(add ? [...view.get().selection, ...names] : names);
  });
  box.addEventListener('dblclick', e => {
    if ((e.target as HTMLElement).closest('.tr[data-obj], .tr[data-objs]')) view.look('selection');
  });
  q.addEventListener('input', () => { last = ''; render(store.state); });
  store.subscribe(render);
  view.on(() => { last = ''; render(store.state); });
}
