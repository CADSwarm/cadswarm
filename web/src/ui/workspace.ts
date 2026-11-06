// The desktop CAD workspace around the 3D view: title bar (mark, file, quick access, command search,
// teammates), the tabbed command ribbon, the left pane (feature tree, properties, parameters, library),
// the heads-up view toolbar, the right task pane (AI Build, Team, Review) and the status bar.
// On phones (<760px) all of this hides except a Commands menu; the bottom sheet (panels.ts) takes over.
import type { Api } from '../api';
import type { State, Store } from '../store';
import { memberColor } from '../color';
import { isDark, setTheme, theme } from '../theme';
import { esc } from './esc';
import { icon, MARK } from './icons';
import { commands, modeOf, MODE_WORDS, RIBBON_TABS, searchCommands, type Command, type Ctx, type RibbonTab } from './commands';
import { mountProps, type PropsPanel } from './props';
import { mountTree, buildProgress } from './tree';
import { mountBuildPane, type BuildPane } from './build-pane';
import { mountChat } from './chat-pane';
import { mountParams } from './params';
import { mountSizes } from './sizes';
import { mountSettings } from './settings';
import { mountLibrary } from './library';
import { mountPrinter, mountReview } from './review';
import { helperNow, takesInstructions } from './helpers';
import { heldBy, memberPill, clock, modelBrief } from './panels';
import { parseRefusal, isRefusal } from './refusal';
import { view, type ViewState } from '../viewstate';

type Left = 'tree' | 'props' | 'params' | 'library';
type Task = 'build' | 'team' | 'review';
const LEFT: [Left, string, string][] = [['tree', 'Features', 'tree'], ['props', 'Properties', 'props'], ['params', 'Parameters', 'params'], ['library', 'Library', 'library']];
const TASK: [Task, string, string][] = [['build', 'AI Build', 'spark'], ['team', 'Team', 'team'], ['review', 'Review', 'review']];
const QUICK = ['qa-new', 'qa-save', 'qa-undo', 'qa-redo', 'qa-rebuild'];
const KIND_WORD: Record<string, string> = { human: 'Person', agent: 'AI helper', reviewer: 'AI reviewer', host: 'Host' };

export interface Workspace { setProject(name: string): void }

export function mountWorkspace(root: HTMLElement, store: Store, api: Api | null, project: string): Workspace {
  const cmds = commands();
  const byId = new Map(cmds.map(c => [c.id, c]));
  let ribbonTab: RibbonTab = load('ribbon', 'features') as RibbonTab;
  let left: Left = 'tree';
  let task: Task = load('task', 'build') as Task;
  let props: PropsPanel;
  let build: BuildPane;

  const $ = <T extends Element = HTMLElement>(sel: string) => root.querySelector<T>(sel)!;
  const notify = (text: string) => store.set({ notice: { text, at: Date.now() } });
  const ctx = (): Ctx => ({
    store, api, selection: view.get().selection, notify,
    openLeft: t => selectLeft(t), openTask: t => selectTask(t),
    focusPrompt: () => build.focusPrompt(),
    libraryQuery: q => {
      selectLeft('library');
      const inp = $<HTMLInputElement>('[data-left="library"] .lib-q');
      if (inp) { inp.value = q; inp.dispatchEvent(new Event('input', { bubbles: true })); inp.focus(); }
    },
  });

  // ---- markup ----
  $('#titlebar').innerHTML = `
    <div class="brand">${MARK}<span class="brand-name">CADSwarm</span></div>
    <div class="quick" role="toolbar" aria-label="Quick access">${QUICK.map(id => {
      const c = byId.get(id)!;
      return `<button type="button" class="qa" data-cmd="${id}" title="${esc(c.label)} · ${esc(c.tip)}">${icon(c.icon, 'ico sm')}</button>`;
    }).join('')}</div>
    <div class="doc-name"><span class="doc-file" data-project></span><span class="doc-sep">·</span><span class="doc-state" data-docstate></span></div>
    <div class="cmd-search">
      <span class="field-ico">${icon('search', 'ico xs')}<input type="search" data-search placeholder="Search commands" aria-label="Search commands" autocomplete="off"><kbd>Ctrl K</kbd></span>
      <div class="search-pop" data-search-pop hidden role="listbox"></div>
    </div>
    <div class="people" data-people></div>
    <button type="button" class="qa" data-theme-toggle title="Light / dark theme">${icon('moon', 'ico sm')}</button>`;

  $('#ribbon').innerHTML = `
    <div class="rb-tabs" role="tablist">${RIBBON_TABS.map(([id, label]) =>
      `<button type="button" role="tab" data-rtab="${id}" class="${id === 'ai' ? 'ai-tab' : ''}">${id === 'ai' ? icon('spark', 'ico xs') : ''}${label}</button>`).join('')}</div>
    <div class="rb-body" data-rbody></div>`;

  $('#leftpane').innerHTML = `
    <nav class="pane-tabs" role="tablist">${LEFT.map(([id, label, ic]) =>
      `<button type="button" role="tab" data-ltab="${id}" title="${label}">${icon(ic, 'ico sm')}<span>${label}</span></button>`).join('')}</nav>
    <div class="pane-body" data-left="tree"></div>
    <div class="pane-body props-body" data-left="props"></div>
    <div class="pane-body" data-left="params">
      <h3 class="pane-h">Shared parameters</h3><p class="muted small pad">Every part reads its key sizes from here. A change moves every part that uses it, for the whole team.</p>
      <div data-sizes class="pad"></div>
      <h3 class="pane-h">Dimensions</h3><div class="params pad" data-params></div>
      <h3 class="pane-h">Precision settings</h3><div class="pad" data-settings></div>
    </div>
    <div class="pane-body pad" data-left="library"></div>`;

  $('#taskpane').innerHTML = `
    <nav class="task-rail" role="tablist">${TASK.map(([id, label, ic]) =>
      `<button type="button" role="tab" data-ttab="${id}" title="${label}">${icon(ic, 'ico sm')}<span>${label}</span><i class="rail-badge" data-tbadge="${id}"></i></button>`).join('')}
      <button type="button" class="rail-collapse" data-collapse title="Collapse the task pane">${icon('chevron', 'ico xs')}</button></nav>
    <div class="task-body">
      <section class="task-sec" data-task="build"><header class="task-h">${icon('spark', 'ico sm')}<b>AI Build</b></header><div class="task-scroll" data-build></div></section>
      <section class="task-sec" data-task="team"><header class="task-h">${icon('team', 'ico sm')}<b>Team</b><span class="muted small" data-team-count></span></header>
        <ul class="members" data-members></ul>
        <div class="seg-switch" role="tablist"><button type="button" data-tsub="chat" aria-selected="true">Chat</button><button type="button" data-tsub="activity">Activity</button></div>
        <div class="team-chat chat-tab" data-tsub-body="chat"></div>
        <div class="team-log log" data-tsub-body="activity" hidden></div></section>
      <section class="task-sec" data-task="review"><header class="task-h">${icon('review', 'ico sm')}<b>Review</b></header>
        <div class="task-scroll pad">
          <h3 class="pane-h flush">Reviewer</h3><div data-review></div>
          <div class="btn-row"><button type="button" class="btn sm" data-cmd="review">${icon('review', 'ico xs')}Ask for a review</button>
            <button type="button" class="btn sm" data-cmd="interference">${icon('interference', 'ico xs')}Interference</button></div>
          <h3 class="pane-h flush">Printer bed</h3><div data-printer></div>
          <h3 class="pane-h flush">Export</h3><div data-export></div>
        </div></section>
    </div>`;

  $('#hud').innerHTML = `
    <button type="button" class="hud-b" data-cmd="view-fit" title="Zoom to fit (F)">${icon('fit', 'ico sm')}</button>
    <span class="hud-sep"></span>
    <span class="flyout"><button type="button" class="hud-b" data-fly="orient" title="View orientation">${icon('iso', 'ico sm')}${icon('down', 'ico caret')}</button>
      <span class="fly-menu" data-fly-menu="orient" hidden>${[['iso', 'Isometric', '0'], ['front', 'Front', '1'], ['top', 'Top', '2'], ['right', 'Right', '3'], ['back', 'Back', ''], ['left', 'Left', ''], ['bottom', 'Bottom', '']]
        .map(([id, l, k]) => `<button type="button" data-look="${id}">${icon(['back', 'left', 'bottom'].includes(id) ? 'iso' : id, 'ico sm')}<span>${l}</span>${k ? `<kbd>${k}</kbd>` : ''}</button>`).join('')}</span></span>
    <span class="flyout"><button type="button" class="hud-b" data-fly="display" title="Display style">${icon('edges', 'ico sm')}${icon('down', 'ico caret')}</button>
      <span class="fly-menu" data-fly-menu="display" hidden>${[['edges', 'Shaded with edges'], ['shaded', 'Shaded'], ['wireframe', 'Wireframe']]
        .map(([id, l]) => `<button type="button" data-display="${id}">${icon(id, 'ico sm')}<span>${l}</span></button>`).join('')}</span></span>
    <button type="button" class="hud-b" data-hud="section" title="Section view">${icon('section', 'ico sm')}</button>
    <button type="button" class="hud-b" data-hud="explode" title="Exploded view">${icon('explode', 'ico sm')}</button>
    <span class="hud-sep"></span>
    <button type="button" class="hud-b" data-cmd="hide" title="Hide / show the selection">${icon('eyeoff', 'ico sm')}</button>
    <button type="button" class="hud-b" data-cmd="showall" title="Show all">${icon('eye', 'ico sm')}</button>
    <button type="button" class="hud-b" data-cmd="measure" title="Measure (M)">${icon('ruler', 'ico sm')}</button>`;

  $('#statusbar').innerHTML = `
    <span class="sb conn" data-sb-conn></span>
    <span class="sb" data-sb-rebuild></span>
    <span class="sb grow" data-sb-sel></span>
    <span class="sb" data-sb-edit></span>
    <span class="sb" data-sb-display></span>
    <span class="sb units" title="Document units">mm</span>`;

  $('#phonecmds').innerHTML = `<button type="button" class="phone-cmd-btn" data-phone-menu aria-label="Commands">${icon('more', 'ico sm')}<span>Commands</span></button>
    <div class="phone-menu" data-phone-pop hidden>${RIBBON_TABS.map(([id, label]) => `<section><h4>${label}</h4><div>${cmds.filter(c => c.tab === id && c.group !== 'Quick access')
      .map(c => `<button type="button" data-cmd="${c.id}">${icon(c.icon, 'ico sm')}<span>${esc(c.label)}</span></button>`).join('')}</div></section>`).join('')}</div>`;

  // ---- mount the panes ----
  mountTree($('[data-left="tree"]'), store, project);
  props = mountProps($('[data-left="props"]'), ctx, open => {
    root.classList.toggle('cmd-open', open);
    if (open) selectLeft('props'); else if (left === 'props') selectLeft('tree');
    renderRibbon();
  });
  mountSizes($('[data-sizes]'), store, api);
  mountParams($('[data-params]'), store, api);
  mountSettings($('[data-settings]'), store, api);
  mountLibrary($('[data-left="library"]'), store, api);
  build = mountBuildPane($('[data-build]'), store, api);
  mountChat($('[data-tsub-body="chat"]'), store, api, () => view.get().selection);
  mountReview($('[data-review]'), store);
  mountPrinter($('[data-printer]'), store, api);

  // ---- navigation ----
  function selectLeft(t: Left) {
    left = t;
    root.querySelectorAll<HTMLElement>('[data-left]').forEach(p => { p.hidden = p.dataset.left !== t; });
    root.querySelectorAll<HTMLElement>('[data-ltab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.ltab === t)));
  }
  function selectTask(t: Task) {
    task = t; save('task', t);
    root.classList.remove('task-collapsed');
    root.querySelectorAll<HTMLElement>('[data-task]').forEach(p => { p.hidden = p.dataset.task !== t; });
    root.querySelectorAll<HTMLElement>('[data-ttab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.ttab === t)));
  }
  selectLeft('tree');
  selectTask(task);

  function renderRibbon() {
    root.querySelectorAll<HTMLElement>('[data-rtab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.rtab === ribbonTab)));
    const c = ctx();
    const groups: [string, Command[]][] = [];
    for (const cmd of cmds.filter(x => x.tab === ribbonTab && x.group !== 'Quick access')) {
      const g = groups.find(x => x[0] === cmd.group);
      if (g) g[1].push(cmd); else groups.push([cmd.group, [cmd]]);
    }
    const active = props?.active()?.id;
    const btn = (cmd: Command) => {
      const why = cmd.disabled?.(c) ?? null;
      const mode = modeOf(cmd, {});
      const on = cmd.active?.() || active === cmd.id;
      return `<button type="button" class="rb-btn${cmd.small ? ' small' : ''}${on ? ' on' : ''}" data-cmd="${cmd.id}"
        title="${esc(cmd.label)}${cmd.keys ? ` (${esc(cmd.keys)})` : ''}\n${esc(cmd.tip)}\n${esc(why ?? MODE_WORDS[mode])}"${why ? ' aria-disabled="true"' : ''}>
        ${icon(cmd.icon, cmd.small ? 'ico sm' : 'ico lg')}<span>${esc(cmd.label)}</span>${mode === 'ai' && !cmd.small ? '<i class="ai-dot" aria-hidden="true"></i>' : ''}</button>`;
    };
    $('[data-rbody]').innerHTML = groups.map(([g, list]) => {
      const large = list.filter(x => !x.small), small = list.filter(x => x.small);
      return `<div class="rb-group"><div class="rb-btns">${large.map(btn).join('')}${small.length ? `<div class="rb-stack">${small.map(btn).join('')}</div>` : ''}</div>
        <div class="rb-label">${esc(g)}</div></div>`;
    }).join('') + '<div class="rb-legend"><i class="ai-dot"></i>sent to an AI helper</div>';
  }
  renderRibbon();
  view.on(() => { renderRibbon(); renderHud(view.get()); });
  addEventListener('cadswarm:theme', renderRibbon);

  function run(id: string) {
    const cmd = byId.get(id);
    if (!cmd) return;
    const why = cmd.disabled?.(ctx()) ?? null;
    if (why) { notify(why); return; }
    root.querySelector<HTMLElement>('[data-phone-pop]')!.hidden = true;
    if (cmd.fields) { props.open(cmd); return; }
    void Promise.resolve(cmd.run?.(ctx(), {})).catch(e => notify((e as Error).message));
    renderRibbon();
  }
  addEventListener('cadswarm:command', e => run(String((e as CustomEvent<string>).detail)));

  // ---- events ----
  root.addEventListener('click', e => {
    const t = e.target as HTMLElement;
    const c = t.closest<HTMLElement>('[data-cmd]');
    if (c && !c.closest('[data-search-pop]')) { run(c.dataset.cmd!); return; }
    const rt = t.closest<HTMLElement>('[data-rtab]');
    if (rt) { ribbonTab = rt.dataset.rtab as RibbonTab; save('ribbon', ribbonTab); renderRibbon(); return; }
    const lt = t.closest<HTMLElement>('[data-ltab]');
    if (lt) { selectLeft(lt.dataset.ltab as Left); return; }
    const tt = t.closest<HTMLElement>('[data-ttab]');
    if (tt) {
      if (tt.dataset.ttab === task && !root.classList.contains('task-collapsed')) root.classList.add('task-collapsed');
      else selectTask(tt.dataset.ttab as Task);
      dispatchEvent(new Event('resize')); return;
    }
    if (t.closest('[data-collapse]')) { root.classList.toggle('task-collapsed'); dispatchEvent(new Event('resize')); return; }
    const sub = t.closest<HTMLElement>('[data-tsub]');
    if (sub) {
      root.querySelectorAll<HTMLElement>('[data-tsub]').forEach(b => b.setAttribute('aria-selected', String(b === sub)));
      root.querySelectorAll<HTMLElement>('[data-tsub-body]').forEach(b => { b.hidden = b.dataset.tsubBody !== sub.dataset.tsub; });
      return;
    }
    const fly = t.closest<HTMLElement>('[data-fly]');
    root.querySelectorAll<HTMLElement>('[data-fly-menu]').forEach(m => { m.hidden = !fly || m.dataset.flyMenu !== fly.dataset.fly || !m.hidden; });
    const look = t.closest<HTMLElement>('[data-look]');
    if (look) { view.look(look.dataset.look as never); return; }
    const disp = t.closest<HTMLElement>('[data-display]');
    if (disp) { view.set({ display: disp.dataset.display as ViewState['display'] }); return; }
    const hud = t.closest<HTMLElement>('[data-hud]');
    if (hud) {
      const which = hud.dataset.hud as 'section' | 'explode';
      const on = which === 'section' ? view.get().section.on : view.get().explode > 0;
      if (on && props.active()?.id !== which) {
        if (which === 'section') view.set({ section: { ...view.get().section, on: false } }); else view.set({ explode: 0 });
      } else run(which);
      return;
    }
    if (t.closest('[data-theme-toggle]')) { setTheme(theme() === 'dark' ? 'light' : 'dark'); return; }
    const person = t.closest<HTMLElement>('[data-follow]');
    if (person) {
      const id = Number(person.dataset.follow);
      view.set({ follow: view.get().follow === id ? 0 : id });
      if (view.get().follow) notify(`Following ${store.member(id)?.name}. Click their avatar again to stop.`);
      return;
    }
    if (t.closest('[data-unfollow]')) { view.set({ follow: 0 }); return; }
    if (t.closest('[data-phone-menu]')) { const p = $('[data-phone-pop]'); p.hidden = !p.hidden; return; }
    const std = t.closest<HTMLElement>('[data-place-std]');
    if (std && api) {
      const name = `${std.dataset.placeStd}_${(std.dataset.size ?? '').replace(/\W/g, '')}_${Date.now() % 1000}`;
      void api.placeComponent({ family: std.dataset.placeStd, size: std.dataset.size, name, ...(std.dataset.length ? { length: Number(std.dataset.length) } : {}) });
      return;
    }
    const comp = t.closest<HTMLElement>('[data-place-comp]');
    if (comp && api) { void api.placeComponent({ component: comp.dataset.placeComp, name: `Component_${Date.now() % 1000}` }); return; }
    const stl = t.closest<HTMLElement>('[data-stl]');
    if (stl && api) void api.downloadArtifact(stl.dataset.stl!, stl.dataset.file!).catch(err => notify((err as Error).message));
  });

  // ---- command search ----
  const search = $<HTMLInputElement>('[data-search]');
  const pop = $('[data-search-pop]');
  let hits: Command[] = [];
  let hi = 0;
  const searchable = cmds;
  function paintSearch() {
    hits = searchCommands(searchable, search.value);
    hi = Math.min(hi, Math.max(0, hits.length - 1));
    pop.hidden = !search.value.trim();
    pop.innerHTML = hits.length ? hits.map((c, i) => `<button type="button" role="option" class="hit${i === hi ? ' on' : ''}" data-hit="${c.id}">
        ${icon(c.icon, 'ico sm')}<span class="hit-label">${esc(c.label)}</span><span class="hit-where">${esc(RIBBON_TABS.find(t => t[0] === c.tab)?.[1] ?? '')} › ${esc(c.group)}</span></button>`).join('')
      : '<p class="hit-none">No command matches.</p>';
  }
  search.addEventListener('input', () => { hi = 0; paintSearch(); });
  search.addEventListener('focus', paintSearch);
  search.addEventListener('blur', () => setTimeout(() => { pop.hidden = true; }, 150));
  search.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); hi = Math.min(hits.length - 1, hi + 1); paintSearch(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); hi = Math.max(0, hi - 1); paintSearch(); }
    else if (e.key === 'Enter' && hits[hi]) { e.preventDefault(); const id = hits[hi].id; search.value = ''; pop.hidden = true; search.blur(); ribbonTab = hits[hi]?.tab ?? ribbonTab; run(id); }
    else if (e.key === 'Escape') { search.value = ''; pop.hidden = true; search.blur(); }
  });
  pop.addEventListener('mousedown', e => {
    const h = (e.target as HTMLElement).closest<HTMLElement>('[data-hit]');
    if (!h) return;
    e.preventDefault();
    search.value = ''; pop.hidden = true; search.blur();
    run(h.dataset.hit!);
  });

  addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); search.focus(); search.select(); return; }
    const typing = (e.target as HTMLElement).closest('input, textarea, select, [contenteditable]');
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'Escape') { if (props.active()) props.close(); else view.select([]); return; }
    const map: Record<string, () => void> = {
      f: () => view.look('fit'), '0': () => view.look('iso'), '1': () => view.look('front'), '2': () => view.look('top'), '3': () => view.look('right'),
      e: () => run('extrude'), m: () => run('measure'), s: () => run('sketch'), d: () => run('dimension'),
    };
    map[e.key.toLowerCase()]?.();
  });

  // ---- live chrome ----
  function renderHud(vs: ViewState) {
    root.querySelector('[data-fly="display"]')!.innerHTML = icon(vs.display, 'ico sm') + icon('down', 'ico caret');
    root.querySelectorAll<HTMLElement>('[data-display]').forEach(b => b.classList.toggle('on', b.dataset.display === vs.display));
    root.querySelector('[data-hud="section"]')!.classList.toggle('on', vs.section.on);
    root.querySelector('[data-hud="explode"]')!.classList.toggle('on', vs.explode > 0);
    root.querySelector('[data-cmd="measure"].hud-b')?.classList.toggle('on', vs.measuring);
    const f = vs.follow ? store.member(vs.follow) : undefined;
    const vp = $('#viewport');
    vp.classList.toggle('following', !!f);
    vp.style.setProperty('--follow', f ? memberColor(f.id, isDark()) : 'transparent');
    $('#follow-pill').innerHTML = f ? `<span class="follow-pill" style="--c:${memberColor(f.id, isDark())}">${icon('follow', 'ico xs')}Following ${esc(f.name)}<button type="button" data-unfollow>Stop</button></span>` : '';
    renderStatus(store.state);
  }
  renderHud(view.get());

  let lastNotice = 0, toastTimer: ReturnType<typeof setTimeout> | undefined;
  function renderStatus(s: State) {
    const running = s.ops.filter(o => o.status === 'running' || o.status === 'queued');
    const broken = s.objects.filter(o => o.broken);
    $('[data-sb-conn]').className = `sb conn${s.connected ? ' live' : ''}`;
    $('[data-sb-conn]').textContent = s.connected ? 'Connected' : 'Reconnecting…';
    $('[data-sb-rebuild]').innerHTML = broken.length ? `<span class="err">${icon('error', 'ico xs')}${broken.length} error${broken.length > 1 ? 's' : ''}</span>`
      : running.length ? `<span class="spin"></span>Rebuilding · ${running.length} op${running.length > 1 ? 's' : ''}` : `${icon('check', 'ico xs')}Rebuilt`;
    const sel = view.get().selection;
    const o = sel.length === 1 ? s.objects.find(x => x.name === sel[0]) : undefined;
    const size = o ? [o.bbox[3] - o.bbox[0], o.bbox[4] - o.bbox[1], o.bbox[5] - o.bbox[2]].map(n => Math.round(n * 10) / 10).join(' × ') : '';
    const holder = o ? s.locks.find(l => l.object === o.name) : undefined;
    $('[data-sb-sel]').textContent = o ? `Selected: ${o.label} · ${size} mm · ${(o.volume / 1000).toFixed(1)} cm³${holder ? ` · held by ${store.member(holder.holder)?.name}` : ''}`
      : sel.length ? `${sel.length} bodies selected` : `${s.objects.filter(x => x.name !== 'Params').length} bodies`;
    const editing = s.pointers.filter(p => p.mode === 'editing' && p.object && store.member(p.member)?.status === 'active');
    $('[data-sb-edit]').innerHTML = editing.length ? 'Editing: ' + editing.slice(0, 3).map(p =>
      `<span class="who" style="--c:${memberColor(p.member, isDark())}">${esc(store.member(p.member)?.name ?? '')}</span> ${esc(p.object)}`).join(', ') : '';
    $('[data-sb-display]').textContent = { edges: 'Shaded with edges', shaded: 'Shaded', wireframe: 'Wireframe' }[view.get().display]
      + (view.get().section.on ? ' · Section' : '') + (view.get().explode ? ' · Exploded' : '');
  }

  function render(s: State) {
    root.querySelectorAll('[data-project]').forEach(n => { n.textContent = project || 'Untitled'; });
    const { brief, p } = buildProgress(s);
    $('[data-docstate]').textContent = brief ? (p.total ? p.line : brief.status === 'planning' ? 'planning…' : brief.status) : 'no build';
    // Teammates: avatars with presence; click to follow.
    const here = s.members.filter(m => (m.status === 'active' || m.status === 'stale') && m.kind !== 'host');
    $('[data-people]').innerHTML = here.slice(0, 7).map(m => {
      const doing = m.kind === 'human' ? heldBy(s, m.id) : helperNow(s, m);
      return `<button type="button" class="person${view.get().follow === m.id ? ' followed' : ''}${m.kind !== 'human' ? ' bot' : ''}" data-follow="${m.id}"
        style="--c:${memberColor(m.id, isDark())}" title="${esc(m.name)}${m.id === s.meId ? ' (you)' : ''} · ${KIND_WORD[m.kind] ?? m.kind}${doing ? ` · ${esc(doing)}` : ''}\nClick to follow">
        ${esc(m.name.slice(0, 1).toUpperCase())}<i class="presence ${m.status === 'active' ? 'on' : 'stale'}"></i></button>`;
    }).join('') + (here.length > 7 ? `<span class="person more">+${here.length - 7}</span>` : '');
    // Team pane members.
    $('[data-team-count]').textContent = `${here.length} here`;
    $('[data-members]').innerHTML = here.map(m => {
      const pill = memberPill(s, m);
      const holds = heldBy(s, m.id) || (m.kind !== 'human' ? helperNow(s, m) : '');
      return `<li><span class="av" style="--c:${memberColor(m.id, isDark())}">${esc(m.name.slice(0, 1).toUpperCase())}<i class="presence ${m.status === 'active' ? 'on' : 'stale'}"></i></span>
        <span class="mem-main"><b>${esc(m.name)}${m.id === s.meId ? ' <span class="muted">(you)</span>' : ''}</b><span class="muted">${esc(KIND_WORD[m.kind] ?? m.kind)}${holds ? ` · ${esc(holds)}` : ''}</span></span>
        <span class="pill${pill.tone ? ` ${pill.tone}` : ''}">${esc(pill.word)}</span>
        ${m.id !== s.meId && takesInstructions(m) ? '' : ''}</li>`;
    }).join('');
    // Activity, with refusals as cards.
    $('[data-tsub-body="activity"]').innerHTML = s.activity.slice(-40).reverse().map(a => {
      const name = store.member(a.member)?.name ?? '';
      const who = `<b class="who" style="--c:${memberColor(a.member, isDark())}">${esc(name)}</b>`;
      const refusal = a.kind === 'refused' || isRefusal(a.summary) ? parseRefusal(a.summary) : null;
      if (refusal) return `<div class="log-row refused"><time>${clock(a.atMs)}</time><div class="refusal"><span class="refusal-meta">${who} · ${esc(refusal.title)}</span><span class="refusal-detail">${esc(refusal.detail)}</span></div></div>`;
      return `<div class="log-row"><time>${clock(a.atMs)}</time><span class="line">${who} ${esc(a.summary)}</span></div>`;
    }).join('') || '<p class="log-empty">Nothing has happened yet.</p>';
    // Export.
    const mb = modelBrief(s);
    $('[data-export]').innerHTML = mb?.stlHash ? `<button type="button" class="btn sm primary" data-stl="${esc(mb.stlHash)}" data-file="cadswarm-${esc(mb.id)}.stl">${icon('download', 'ico xs')}Download STL</button>`
      : '<p class="card-empty">The STL is ready once the reviewer passes the build.</p>';
    // Badges: steps in progress, unread-ish chat.
    $('[data-tbadge="build"]').textContent = p.current.length ? String(p.current.length) : '';
    renderStatus(s);
    if (s.notice && s.notice.at !== lastNotice) {
      lastNotice = s.notice.at;
      const t = $('#ws-toast');
      t.textContent = s.notice.text; t.hidden = false;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
    }
  }
  store.subscribe(render);

  return { setProject: name => { project = name; render(store.state); } };
}

function load(k: string, d: string) { try { return localStorage.getItem(`cadswarm:ws:${k}`) ?? d; } catch { return d; } }
function save(k: string, v: string) { try { localStorage.setItem(`cadswarm:ws:${k}`, v); } catch { /* no storage */ } }
