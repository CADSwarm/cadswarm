import type { Brief, Member, Review, State, Store } from '../store';
import type { Api } from '../api';
import { memberColor } from '../color';
import { mountChat } from './chat-pane';
import { mountHandoff } from './handoff';
import { mountParams } from './params';
import { freeHelper, helperNow, helpers, startInstruction } from './helpers';
import { chatThread } from './chat';
import { mountLibrary } from './library';
import { mountSettings } from './settings';
import { mountSizes } from './sizes';
import { mountFiles } from './files';
import { mountSponsors } from './sponsors';
import { mountPrinter, mountReview } from './review';
import { esc } from './esc';
import { parseRefusal, isRefusal } from './refusal';

const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;
// Seven tabs, one job each. Icons are inline SVG (no icon font), drawn at 1.8px like SF Symbols' regular weight.
const ICON = {
  chat: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4h0A2.5 2.5 0 0 1 3 13.5"/>',
  model: '<path d="M12 3 4 7.5v9L12 21l8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>',
  sizes: '<path d="M3 8h18M3 16h18"/><path d="M7 5v6M12 13v6M17 5v6M22 13v6"/>',
  library: '<path d="M4 4h4v16H4zM10 4h4v16h-4z"/><path d="m15.5 5.2 3.9-1 3.1 14.9-3.9 1z"/>',
  tasks: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3.5 6 1.5 1.5L7.5 5M3.5 12l1.5 1.5L7.5 11M3.5 18l1.5 1.5L7.5 17"/>',
  files: '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h5l2 2h8A1.5 1.5 0 0 1 21 8.5v10A1.5 1.5 0 0 1 19.5 20h-15A1.5 1.5 0 0 1 3 18.5z"/>',
  team: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18.5 20a6.5 6.5 0 0 0-3-5.5"/>',
};
const TABS = [['chat', 'Chat'], ['model', 'Model'], ['sizes', 'Sizes'], ['library', 'Library'],
  ['tasks', 'Tasks'], ['files', 'Files'], ['team', 'Team']] as const;
type Tab = typeof TABS[number][0];
export const STEPS = ['new', 'planning', 'building', 'reviewing', 'ready'] as const;

/** The brief to show: the newest one. */
export function currentBrief(s: Pick<State, 'briefs'>): Brief | undefined {
  return [...s.briefs].sort((a, b) => b.updatedMs - a.updatedMs)[0];
}

/** The newest request that has a finished model, so a newer unfinished one cannot hide it. */
export function modelBrief(s: Pick<State, 'briefs'>): Brief | undefined {
  return [...s.briefs].filter(b => b.stlHash).sort((a, b) => b.updatedMs - a.updatedMs)[0];
}

/** The latest review round for a brief. */
export function latestReview(s: Pick<State, 'reviews'>, brief: string): Review | undefined {
  return s.reviews.filter(r => r.brief === brief).sort((a, b) => b.round - a.round)[0];
}

/** Findings are free-form JSON from the reviewer; pull readable lines out of whatever shape arrives. */
export function findingLines(json: string, max = 3): string[] {
  let v: unknown;
  try { v = JSON.parse(json); } catch { return json.trim() ? [json.trim()] : []; }
  const list = Array.isArray(v) ? v : v && typeof v === 'object' && Array.isArray((v as { findings?: unknown }).findings)
    ? (v as { findings: unknown[] }).findings : [];
  return list.map(f => {
    if (typeof f === 'string') return f;
    if (!f || typeof f !== 'object') return String(f);
    const r = f as Record<string, unknown>;
    // The reviewer's prompt asks for {"object","problem","task_id"}; older verdicts use other keys.
    const text = String(r.problem ?? r.message ?? r.text ?? r.detail ?? r.summary ?? JSON.stringify(f));
    return r.object ? `${r.object}: ${text}` : text;
  }).filter(Boolean).slice(0, max);
}

/** Ask the 3D view to fly to a part (the viewer listens; panels never touch three.js). */
export function focusPart(name: string) { dispatchEvent(new CustomEvent('cadswarm:focus', { detail: name })); }

function loadTab(): Tab {
  const q = new URLSearchParams(location.search).get('tab');
  if (TABS.some(([id]) => id === q)) return q as Tab;
  try {
    const t = localStorage.getItem('cadswarm:tab');
    const old = t === 'ask' ? 'chat' : t; // the Ask tab became Chat
    if (TABS.some(([id]) => id === old)) return old as Tab;
  } catch { /* no storage */ }
  return 'chat';
}

const TASK_GROUPS: [string, string[]][] = [['In progress', ['claimed', 'awaiting_takeover']], ['To do', ['open', 'blocked']], ['Done', ['done']], ['Cancelled', ['cancelled']]];
const BRIEF_WORDS: Record<string, string> = { new: 'waiting for the team', planning: 'planning the parts', building: 'building',
  reviewing: 'checking the result', ready: 'ready', failed: 'stopped', cancelled: 'cancelled' };
const ACTIVE_BRIEFS = ['new', 'planning', 'building', 'reviewing'];

/** What a member holds, in the words the panel shows: "Base plate, 4 posts". */
export function heldBy(s: Pick<State, 'locks' | 'objects'>, id: number, max = 2): string {
  const names = s.locks.filter(l => l.holder === id && l.object !== 'Params')
    .map(l => s.objects.find(o => o.name === l.object)?.label ?? l.object);
  if (!names.length) return '';
  return names.length <= max ? names.join(', ') : `${names.slice(0, max).join(', ')} +${names.length - max}`;
}

/** The little state pill next to a name: watching, building, away. */
export function memberPill(s: Pick<State, 'tasks'>, m: Member): { word: string; tone: '' | 'live' | 'warn' } {
  if (m.status !== 'active') return { word: 'away', tone: 'warn' };
  if (m.kind === 'human') return { word: 'watching', tone: 'live' };
  if (m.kind === 'host') return { word: 'hosting', tone: 'live' };
  if (m.kind === 'reviewer') return { word: 'reviewing', tone: 'live' };
  if (s.tasks.some(t => t.owner === m.id && t.status === 'claimed')) return { word: 'building', tone: 'live' };
  return { word: m.statusText && m.statusText !== 'idle' ? 'working' : 'ready', tone: 'live' };
}

/** 14:02, the way the panel writes times. */
export function clock(ms: number): string {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function mountPanels(el: HTMLElement, store: Store, api: Api | null, picked: () => string[] = () => [], project = '',
                             topbar: HTMLElement | null = null) {
  el.classList.remove('has-notice');
  el.innerHTML = `
    <div class="grabber" data-grabber aria-hidden="true"><span></span></div>
    <header class="sheet-head">
      <button type="button" class="sheet-title" data-sheet-toggle aria-expanded="false" aria-label="Expand chat">
        <span class="sheet-kicker">CADSwarm</span>
        <b data-project>this build</b>
        <span class="sheet-last" data-sheet-last></span>
      </button>
      <button type="button" class="debug-open" data-sponsors aria-haspopup="dialog" title="How CADSwarm uses its sponsors (Shift+D)">Sponsors</button>
      <span class="conn" data-conn></span>
      <button type="button" class="sheet-toggle" data-sheet-toggle aria-expanded="false" aria-label="Expand chat">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 14 6-6 6 6"/></svg>
      </button>
    </header>
    <div class="toast" role="status" aria-live="polite" hidden></div>
    <nav class="tabbar" role="tablist" aria-label="Sections">
      ${TABS.map(([id, label]) => `<button type="button" role="tab" data-tab="${id}" aria-controls="tab-${id}">
        <svg viewBox="0 0 24 24" aria-hidden="true">${ICON[id]}</svg><span>${label}</span><span class="badge" data-count="${id}"></span></button>`).join('')}
    </nav>
    <section class="tab chat-tab" id="tab-chat" data-tab="chat" role="tabpanel"></section>
    <section class="tab" id="tab-model" data-tab="model" role="tabpanel">
      <h1 class="large-title">Model</h1>
      <h2 class="group-title">Parts</h2><ul class="group" data-parts></ul>
      <h2 class="group-title">Dimensions</h2><div class="group params" data-params></div>
      <h2 class="group-title">Printer bed</h2><div data-printer></div>
      <h2 class="group-title">Review</h2><div data-review></div>
      <div data-export></div>
    </section>
    <section class="tab sizes-tab" id="tab-sizes" data-tab="sizes" role="tabpanel">
      <h1 class="large-title">Shared sizes</h1>
      <p class="muted sizes-intro">Key dimensions live in one sheet every part reads from.
        Change one here once; every part that uses it moves for the whole team.</p>
      <div data-sizes></div>
    </section>
    <section class="tab" id="tab-library" data-tab="library" role="tabpanel">
      <h1 class="large-title">Library</h1><div data-library></div>
    </section>
    <section class="tab" id="tab-tasks" data-tab="tasks" role="tabpanel">
      <h1 class="large-title">Tasks</h1><div data-briefs></div><div data-tasks></div>
    </section>
    <section class="tab files-tab" id="tab-files" data-tab="files" role="tabpanel">
      <h1 class="large-title">Project files</h1>
      <p class="muted sizes-intro">Agents save into one project folder you choose. They can't write outside it,
        and every replaced version stays in its history.</p>
      <div data-files></div>
    </section>
    <section class="tab team-tab" id="tab-team" data-tab="team" role="tabpanel">
      <div class="panel-bar">
        <b>Team<span class="proj"> · <span data-project></span></span></b>
        <span class="state" data-team-state></span>
      </div>
      <div class="panel-sec">
        <span class="section-label">Who's here</span>
        <ul class="who-list" data-team></ul>
      </div>
      <div class="panel-sec">
        <span class="section-label">Activity</span>
        <div class="log" data-log></div>
      </div>
      <div class="panel-sec"><div class="actions">
        <button type="button" data-act="task">Add a task</button>
        <button type="button" data-act="size">Change a shared size</button>
        <button type="button" data-act="clash">Check for clashes</button>
        <button type="button" data-act="save">Save as 3D file</button>
      </div></div>
      <div class="panel-sec"><div data-handoff></div>
        <span class="section-label">Precision settings</span><div data-settings></div></div>
    </section>`;

  if (topbar) topbar.innerHTML = `
    <div class="topbar-title"><b data-project>this build</b></div>
    <span class="conn" data-conn></span>
    <div class="topbar-avatars" data-avatars></div>`;


  const $ = <T extends Element = HTMLElement>(sel: string) => el.querySelector<T>(sel)!;
  mountChat($('#tab-chat'), store, api, picked);
  mountParams($('[data-params]'), store, api);
  mountSizes($('[data-sizes]'), store, api);
  const files = mountFiles($('[data-files]'), store, api);
  mountPrinter($('[data-printer]'), store, api);
  mountReview($('[data-review]'), store);
  mountHandoff($('[data-handoff]'), store, api);
  mountLibrary($('[data-library]'), store, api);
  mountSettings($('[data-settings]'), store, api);
  // A debug view of the outside services, for showing a judge how each one is wired in.
  const sponsors = mountSponsors(document.body, store, project);
  $('[data-sponsors]').addEventListener('click', sponsors.open);

  function select(tab: Tab) {
    el.querySelectorAll<HTMLElement>('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
    el.querySelectorAll<HTMLButtonElement>('.tabbar [role=tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
    try { localStorage.setItem('cadswarm:tab', tab); } catch { /* no storage */ }
    if (tab === 'files') void files.load();
  }
  $('.tabbar').addEventListener('click', e => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[role=tab]');
    if (!b) return;
    select(b.dataset.tab as Tab);
    dispatchEvent(new CustomEvent('cadswarm:tab', { detail: b.dataset.tab })); // the sheet expands to half on phones
  });
  select(loadTab());

  let selectedPart = '';
  const notice = (text: string) => store.set({ notice: { text, at: Date.now() } });

  // Actions, delegated so re-rendering never loses a listener.
  el.addEventListener('click', e => {
    const t = e.target as HTMLElement;
    const stl = t.closest<HTMLButtonElement>('[data-stl]');
    if (stl && api) {
      const label = stl.textContent || 'Download STL';
      stl.disabled = true;
      stl.textContent = 'Preparing…';
      api.downloadArtifact(stl.dataset.stl!, stl.dataset.file!)
        .catch(err => notice((err as Error).message ?? String(err)))
        .finally(() => { stl.disabled = false; stl.textContent = label; });
      return;
    }
    const stop = t.closest<HTMLButtonElement>('[data-cancel-task]');
    if (stop && api) {
      const task = store.state.tasks.find(x => x.id === stop.dataset.cancelTask);
      if (!task || !confirm(`Cancel “${task.title}”? The team stops working on it.`)) return;
      stop.disabled = true;
      void api.cancelTask(task.id, '').catch(err => notice((err as Error).message ?? String(err)))
        .finally(() => { stop.disabled = false; });
      return;
    }
    const del = t.closest<HTMLButtonElement>('[data-delete-task]');
    if (del && api) {
      const task = store.state.tasks.find(x => x.id === del.dataset.deleteTask);
      if (!task || !confirm(`Delete “${task.title}”? It was never started.`)) return;
      del.disabled = true;
      void api.deleteTask(task.id).catch(err => notice((err as Error).message ?? String(err)))
        .finally(() => { del.disabled = false; });
      return;
    }
    const stopBrief = t.closest<HTMLButtonElement>('[data-cancel-brief]');
    if (stopBrief && api) {
      const brief = store.state.briefs.find(x => x.id === stopBrief.dataset.cancelBrief);
      if (!brief || !confirm(`Cancel “${brief.text}”? Its unstarted tasks are cancelled too.`)) return;
      stopBrief.disabled = true;
      void api.cancelBrief(brief.id).catch(err => notice((err as Error).message ?? String(err)))
        .finally(() => { stopBrief.disabled = false; });
      return;
    }
    const lock = t.closest<HTMLButtonElement>('[data-lock]');
    if (lock && api) { void (lock.dataset.mine ? api.release([lock.dataset.lock!]) : api.lock([lock.dataset.lock!])); return; }
    const start = t.closest<HTMLButtonElement>('[data-start]');
    if (start && api) {
      const task = store.state.tasks.find(x => x.id === start.dataset.start);
      const helper = freeHelper(store.state);
      if (!task) return;
      if (!helper) { notice('No AI helper is online. Send it to “The team” in Chat instead.'); return; }
      api.sendCommand(helper.name, startInstruction(task), task.id);
      select('chat'); // the request shows up in the conversation
      return;
    }
    const part = t.closest<HTMLElement>('[data-focus]');
    if (part) { selectedPart = part.dataset.focus!; focusPart(selectedPart); render(store.state); return; }
    // The four things the site promises you can do from the panel.
    const act = t.closest<HTMLButtonElement>('[data-act]');
    if (act) {
      const which = act.dataset.act;
      if (which === 'save') {
        const brief = modelBrief(store.state);
        if (!api || !brief?.stlHash) { notice('No 3D file yet. Finish a build first.'); return; }
        act.disabled = true;
        api.downloadArtifact(brief.stlHash, `cadswarm-${brief.id}.stl`)
          .catch(err => notice((err as Error).message ?? String(err)))
          .finally(() => { act.disabled = false; });
        return;
      }
      if (which === 'clash') {
        if (!api) { notice('Not connected to a team yet.'); return; }
        api.sendCommand('all', 'Check the model for clashes and fix any in parts you own; ask the owner about the rest.');
        select('chat');
        return;
      }
      if (which === 'task') { select('chat'); el.querySelector<HTMLTextAreaElement>('[data-text]')?.focus(); return; }
      if (which === 'size') { select('sizes'); el.querySelector<HTMLInputElement>('[data-sizes] .param input')?.focus(); return; }
    }
  });

  let toastTimer: ReturnType<typeof setTimeout> | undefined;
  let lastNotice = 0;
  const who = (id: number) => {
    const m = store.member(id);
    return m ? `<span class="who" style="--c:${memberColor(id, dark())}">${esc(m.name)}</span>` : '';
  };

  function render(s: State) {
    el.ownerDocument.querySelectorAll('[data-conn]').forEach(c => {
      c.className = `conn${s.connected ? ' live' : ''}`;
      c.textContent = s.connected ? 'Live' : 'Reconnecting…';
    });

    // Model: parts (tap to fly there; lock controls only on the selected one), then dimensions, then the export.
    const parts = s.objects.filter(o => o.name !== 'Params' && o.visible);
    $('[data-parts]').innerHTML = parts.map(o => {
      const h = s.locks.find(l => l.object === o.name);
      const editing = s.pointers.find(p => p.object === o.name && p.mode === 'editing');
      const mine = h && h.holder === s.meId;
      const sel = o.name === selectedPart;
      return `<li class="row-item${sel ? ' selected' : ''}" data-focus="${esc(o.name)}">
        <span class="dot" style="--c:${h ? memberColor(h.holder, dark()) : 'var(--fill)'}"></span>
        <span class="row-main"><span class="row-title${o.broken ? ' err' : ''}">${esc(o.label)}</span>
          <span class="row-detail">${editing ? `${esc(store.member(editing.member)?.name ?? '')} is editing` : h ? `held by ${esc(store.member(h.holder)?.name ?? '')}` : 'free'}${o.broken ? ' · broken' : ''}</span></span>
        ${sel && api && (!h || mine) ? `<button type="button" class="text-button" data-lock="${esc(o.name)}"${mine ? ' data-mine="1"' : ''}>${mine ? 'Release' : 'Hold'}</button>` : '<span class="chevron" aria-hidden="true">›</span>'}
      </li>`;
    }).join('') || '<li class="row-item empty">Parts appear here as they are built.</li>';
    const brief = modelBrief(s);
    const art = brief?.stlHash ? s.artifacts.find(a => a.hash === brief.stlHash) : undefined;
    $('[data-export]').innerHTML = brief?.stlHash && !(art && art.size === 0)
      ? `<button type="button" class="wide-button" data-stl="${esc(brief.stlHash)}" data-file="cadswarm-${esc(brief.id)}.stl"${api ? '' : ' disabled'}>Download STL</button>` : '';

    // Builds: every request, newest first, so an older finished model stays reachable.
    $('[data-briefs]').innerHTML = s.briefs.length
      ? `<h2 class="group-title">Builds</h2><ul class="group">${[...s.briefs].sort((a, b) => b.updatedMs - a.updatedMs)
        .map(b => {
          const n = s.tasks.filter(t => t.brief === b.id).length;
          return `<li class="row-item">
          <span class="row-main"><span class="row-title">${esc(b.text)}</span>
            <span class="row-detail">${esc(BRIEF_WORDS[b.status] ?? b.status)}${n ? ` · ${n} parts` : ''}</span></span>
          ${b.stlHash && !(s.artifacts.find(a => a.hash === b.stlHash)?.size === 0)
            ? `<button type="button" class="text-button" data-stl="${esc(b.stlHash)}" data-file="cadswarm-${esc(b.id)}.stl">Download</button>` : ''}
          ${api && ACTIVE_BRIEFS.includes(b.status)
            ? `<button type="button" class="text-button" data-cancel-brief="${esc(b.id)}">Cancel</button>` : ''}
        </li>`;
        }).join('')}</ul>`
      : '';

    // Tasks, grouped like Reminders.
    $('[data-tasks]').innerHTML = (s.tasks.length ? '<h2 class="group-title">Parts</h2>' : '')
      + TASK_GROUPS.map(([title, statuses]) => {
      const rows = s.tasks.filter(t => statuses.includes(t.status));
      if (!rows.length) return '';
      return `<h2 class="group-title">${title}</h2><ul class="group">${rows.map(t => `<li class="row-item">
          <span class="row-main"><span class="row-title">${esc(t.title)}</span>
            <span class="row-detail">${t.owner ? `${who(t.owner)}` : t.assignee ? `for ${esc(t.assignee)}` : t.status === 'blocked' ? 'stuck' : 'nobody yet'}</span></span>
          ${api && t.status === 'open' ? `<button type="button" class="text-button" data-start="${esc(t.id)}">Start</button>` : ''}
          ${api && t.status === 'open' ? `<button type="button" class="text-button" data-delete-task="${esc(t.id)}">Delete</button>` : ''}
          ${api && ['claimed', 'awaiting_takeover', 'blocked'].includes(t.status)
            ? `<button type="button" class="text-button" data-cancel-task="${esc(t.id)}">Cancel</button>` : ''}
        </li>`).join('')}</ul>`;
    }).join('') || '<p class="empty-note">No tasks yet. When the team plans a build, its parts show up here.</p>';

    // Team: the panel the site sells. Who's here, what happened, what you can do about it.
    el.ownerDocument.querySelectorAll('[data-project]').forEach(n => { n.textContent = project || 'this build'; });
    const conn = $('[data-team-state]');
    conn.className = `state${s.connected ? ' live' : ''}`;
    conn.textContent = s.connected ? 'Connected' : 'Reconnecting…';
    const here = s.members.filter(m => m.status === 'active' || m.status === 'stale' || m.id === s.meId);
    $('[data-team]').innerHTML = here.map(m => {
      const holds = heldBy(s, m.id) || (m.kind === 'agent' ? helperNow(s, m) : '');
      const pill = memberPill(s, m);
      return `<li>
        <span class="dot" style="--c:${memberColor(m.id, dark())}"></span>
        <span class="who-main"><span class="name">${esc(m.name)}${m.id === s.meId ? ' <span class="holds">(you)</span>' : ''}</span>${holds ? `<span class="holds"> · ${esc(holds)}</span>` : ''}</span>
        <span class="pill${pill.tone ? ` ${pill.tone}` : ''}">${esc(pill.word)}</span>
      </li>`;
    }).join('') || '<li class="log-empty">Nobody has joined this build yet.</li>';

    // Activity only: the chat is its own tab, so this reads as what the team did.
    $('[data-log]').innerHTML = s.activity.slice(-20).map(a => {
      const refused = a.kind === 'refused' || isRefusal(a.summary);
      const name = store.member(a.member)?.name ?? '';
      const refusal = refused ? parseRefusal(a.summary) : null;
      if (refusal) return `<div class="log-row refused">
          <time>${clock(a.atMs)}</time>
          <div class="refusal">
            <span class="refusal-meta">${name ? `<b class="who" style="--c:${memberColor(a.member, dark())}">${esc(name)}</b> · ` : ''}${esc(refusal.title)}</span>
            <span class="refusal-detail">${esc(refusal.detail)}</span>
          </div>
        </div>`;
      const tone = refused ? ' refused' : '';
      return `<div class="log-row${tone}">
          <time>${clock(a.atMs)}</time>
          <span class="line"><b class="who" style="--c:${memberColor(a.member, dark())}">${esc(store.member(a.member)?.name ?? '')}</b> ${esc(a.summary)}</span>
        </div>`;
    }).join('') || '<p class="log-empty">Nothing has happened yet. Start something in Chat.</p>';

    // The sheet's one-line preview while it peeks, and the glass top bar's helper avatars.
    const last = chatThread(s, 1)[0];
    const sheetLast = $('[data-sheet-last]');
    if (sheetLast) {
      sheetLast.textContent = !last ? 'Type @ to give a helper a task'
        : last.kind === 'build' ? `Building: ${last.brief.text}`
        : `${last.mine ? 'You' : last.name}: ${last.body}`;
    }
    const avatars = el.ownerDocument.querySelector('[data-avatars]');
    if (avatars) {
      avatars.innerHTML = helpers(s).filter(m => m.status === 'active').slice(0, 4).map(m =>
        `<span class="avatar" style="--c:${memberColor(m.id, dark())}" title="${esc(m.name)}">${esc(m.name.slice(0, 1).toUpperCase())}</span>`).join('');
    }

    const counts: Record<Tab, number> = { chat: 0, model: 0, sizes: 0, library: 0, files: 0,
      team: s.suggestions.filter(g => g.status === 'pending').length,
      tasks: s.tasks.filter(t => t.status !== 'done' && t.status !== 'cancelled').length };
    el.querySelectorAll<HTMLElement>('[data-count]').forEach(c => { const n = counts[c.dataset.count as Tab]; c.textContent = n ? String(n) : ''; });

    if (s.notice && s.notice.at !== lastNotice) {
      lastNotice = s.notice.at;
      const toast = $('.toast');
      toast.textContent = s.notice.text;
      toast.hidden = false;
      el.classList.add('has-notice'); // at the peek, the hint gives its line to the notice
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { toast.hidden = true; el.classList.remove('has-notice'); }, 5000);
    }
  }

  store.subscribe(render);
}
