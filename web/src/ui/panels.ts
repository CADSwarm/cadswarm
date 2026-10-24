import type { Brief, Review, State, Store } from '../store';
import type { Api } from '../api';
import { memberColor } from '../color';
import { mountAsk } from './ask';
import { mountHandoff } from './handoff';
import { mountParams } from './params';
import { freeHelper, helperNow, startInstruction } from './helpers';
import { esc } from './esc';

const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;
// Four tabs, one job each. Icons are inline SVG (no icon font), drawn at 1.8px like SF Symbols' regular weight.
const ICON = {
  ask: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4h0A2.5 2.5 0 0 1 3 13.5"/>',
  model: '<path d="M12 3 4 7.5v9L12 21l8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>',
  tasks: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3.5 6 1.5 1.5L7.5 5M3.5 12l1.5 1.5L7.5 11M3.5 18l1.5 1.5L7.5 17"/>',
  team: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18.5 20a6.5 6.5 0 0 0-3-5.5"/>',
};
const TABS = [['ask', 'Ask'], ['model', 'Model'], ['tasks', 'Tasks'], ['team', 'Team']] as const;
type Tab = typeof TABS[number][0];
export const STEPS = ['new', 'planning', 'building', 'reviewing', 'ready'] as const;

/** The brief to show: the newest one. */
export function currentBrief(s: Pick<State, 'briefs'>): Brief | undefined {
  return [...s.briefs].sort((a, b) => b.updatedMs - a.updatedMs)[0];
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
  return list.slice(0, max).map(f => typeof f === 'string' ? f
    : f && typeof f === 'object' ? String((f as Record<string, unknown>).message ?? (f as Record<string, unknown>).text
      ?? (f as Record<string, unknown>).detail ?? (f as Record<string, unknown>).summary ?? JSON.stringify(f)) : String(f));
}

/** Ask the 3D view to fly to a part (the viewer listens; panels never touch three.js). */
export function focusPart(name: string) { dispatchEvent(new CustomEvent('cadswarm:focus', { detail: name })); }

function ago(ms: number) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  return s < 45 ? 'now' : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`;
}

function loadTab(): Tab {
  const q = new URLSearchParams(location.search).get('tab');
  if (TABS.some(([id]) => id === q)) return q as Tab;
  try { const t = localStorage.getItem('cadswarm:tab'); if (TABS.some(([id]) => id === t)) return t as Tab; } catch { /* no storage */ }
  return 'ask';
}

const TASK_GROUPS: [string, string[]][] = [['In progress', ['claimed', 'awaiting_takeover']], ['To do', ['open', 'blocked']], ['Done', ['done']]];

export function mountPanels(el: HTMLElement, store: Store, api: Api | null, picked: () => string[] = () => []) {
  el.innerHTML = `
    <header class="sidebar-head">
      <span class="brand">CadSwarm</span>
      <span class="conn" data-conn></span>
    </header>
    <nav class="tabbar" role="tablist" aria-label="Sections">
      ${TABS.map(([id, label]) => `<button type="button" role="tab" data-tab="${id}" aria-controls="tab-${id}">
        <svg viewBox="0 0 24 24" aria-hidden="true">${ICON[id]}</svg><span>${label}</span><span class="badge" data-count="${id}"></span></button>`).join('')}
    </nav>
    <section class="tab ask-tab" id="tab-ask" data-tab="ask" role="tabpanel"></section>
    <section class="tab" id="tab-model" data-tab="model" role="tabpanel">
      <h1 class="large-title">Model</h1>
      <h2 class="group-title">Parts</h2><ul class="group" data-parts></ul>
      <h2 class="group-title">Dimensions</h2><div class="group params" data-params></div>
      <div data-export></div>
    </section>
    <section class="tab" id="tab-tasks" data-tab="tasks" role="tabpanel">
      <h1 class="large-title">Tasks</h1><div data-tasks></div>
    </section>
    <section class="tab" id="tab-team" data-tab="team" role="tabpanel">
      <h1 class="large-title">Team</h1>
      <ul class="group" data-team></ul>
      <div class="group" data-handoff></div>
      <h2 class="group-title">Activity</h2><ol class="group feed" data-activity></ol>
      <button type="button" class="text-button" data-more hidden>Show all activity</button>
    </section>
    <div class="toast" role="status" aria-live="polite" hidden></div>`;

  const $ = <T extends Element = HTMLElement>(sel: string) => el.querySelector<T>(sel)!;
  mountAsk($('#tab-ask'), store, api, picked);
  mountParams($('[data-params]'), store, api);
  mountHandoff($('[data-handoff]'), store, api);

  function select(tab: Tab) {
    el.querySelectorAll<HTMLElement>('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
    el.querySelectorAll<HTMLButtonElement>('.tabbar [role=tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
    try { localStorage.setItem('cadswarm:tab', tab); } catch { /* no storage */ }
  }
  $('.tabbar').addEventListener('click', e => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[role=tab]');
    if (b) select(b.dataset.tab as Tab);
  });
  select(loadTab());

  let selectedPart = '';
  let allActivity = false;
  const notice = (text: string) => store.set({ notice: { text, at: Date.now() } });

  // Actions, delegated so re-rendering never loses a listener.
  el.addEventListener('click', e => {
    const t = e.target as HTMLElement;
    const stl = t.closest<HTMLButtonElement>('[data-stl]');
    if (stl && api) {
      stl.disabled = true;
      stl.textContent = 'Preparing…';
      api.downloadArtifact(stl.dataset.stl!, stl.dataset.file!)
        .catch(err => notice((err as Error).message ?? String(err)))
        .finally(() => { stl.disabled = false; stl.textContent = 'Download STL'; });
      return;
    }
    const lock = t.closest<HTMLButtonElement>('[data-lock]');
    if (lock && api) { void (lock.dataset.mine ? api.release([lock.dataset.lock!]) : api.lock([lock.dataset.lock!])); return; }
    const start = t.closest<HTMLButtonElement>('[data-start]');
    if (start && api) {
      const task = store.state.tasks.find(x => x.id === start.dataset.start);
      const helper = freeHelper(store.state);
      if (!task) return;
      if (!helper) { notice('No AI helper is online. Send it to “The team” in Ask instead.'); return; }
      api.sendCommand(helper.name, startInstruction(task), task.id);
      select('ask'); // the request shows up in the conversation
      return;
    }
    const part = t.closest<HTMLElement>('[data-focus]');
    if (part) { selectedPart = part.dataset.focus!; focusPart(selectedPart); render(store.state); return; }
    if (t.closest('[data-more]')) { allActivity = true; render(store.state); }
  });

  let toastTimer: ReturnType<typeof setTimeout> | undefined;
  let lastNotice = 0;
  const who = (id: number) => {
    const m = store.member(id);
    return m ? `<span class="who" style="--c:${memberColor(id, dark())}">${esc(m.name)}</span>` : '';
  };

  function render(s: State) {
    el.querySelectorAll('[data-conn]').forEach(c => {
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
    const brief = currentBrief(s);
    const art = brief?.stlHash ? s.artifacts.find(a => a.hash === brief.stlHash) : undefined;
    $('[data-export]').innerHTML = brief?.stlHash && !(art && art.size === 0)
      ? `<button type="button" class="wide-button" data-stl="${esc(brief.stlHash)}" data-file="cadswarm-${esc(brief.id)}.stl"${api ? '' : ' disabled'}>Download STL</button>` : '';

    // Tasks, grouped like Reminders.
    $('[data-tasks]').innerHTML = TASK_GROUPS.map(([title, statuses]) => {
      const rows = s.tasks.filter(t => statuses.includes(t.status));
      if (!rows.length) return '';
      return `<h2 class="group-title">${title}</h2><ul class="group">${rows.map(t => `<li class="row-item">
          <span class="row-main"><span class="row-title">${esc(t.title)}</span>
            <span class="row-detail">${t.owner ? `${who(t.owner)}` : t.assignee ? `for ${esc(t.assignee)}` : t.status === 'blocked' ? 'stuck' : 'nobody yet'}</span></span>
          ${api && t.status === 'open' ? `<button type="button" class="text-button" data-start="${esc(t.id)}">Start</button>` : ''}
        </li>`).join('')}</ul>`;
    }).join('') || '<p class="empty-note">No tasks yet. When the team plans a build, its parts show up here.</p>';

    // Team: who's here and what they're doing; then activity.
    $('[data-team]').innerHTML = s.members.filter(m => m.status === 'active' || m.status === 'stale').map(m => `<li class="row-item">
        <span class="dot" style="--c:${memberColor(m.id, dark())}"></span>
        <span class="row-main"><span class="row-title">${esc(m.name)}${m.id === s.meId ? ' <span class="row-detail">(you)</span>' : ''}</span>
          <span class="row-detail">${m.kind === 'human' ? (m.status === 'active' ? 'here' : 'away') : m.kind === 'host' ? 'team server' : esc(helperNow(s, m))}</span></span>
        <span class="presence${m.status === 'active' ? ' on' : ''}" aria-label="${m.status === 'active' ? 'online' : 'away'}"></span>
      </li>`).join('');
    const acts = s.activity.slice().reverse();
    $('[data-activity]').innerHTML = (allActivity ? acts : acts.slice(0, 15)).map(a =>
      `<li class="row-item"><span class="row-main"><span class="row-title small">${esc(a.summary)}</span>
        <span class="row-detail">${who(a.member)} · ${ago(a.atMs)}</span></span></li>`).join('')
      || '<li class="row-item empty">Quiet so far.</li>';
    $('[data-more]').hidden = allActivity || acts.length <= 15;

    const counts: Record<Tab, number> = { ask: 0, model: 0, team: 0,
      tasks: s.tasks.filter(t => t.status === 'open' || t.status === 'claimed').length };
    el.querySelectorAll<HTMLElement>('[data-count]').forEach(c => { const n = counts[c.dataset.count as Tab]; c.textContent = n ? String(n) : ''; });

    if (s.notice && s.notice.at !== lastNotice) {
      lastNotice = s.notice.at;
      const toast = $('.toast');
      toast.textContent = s.notice.text;
      toast.hidden = false;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { toast.hidden = true; }, 5000);
    }
  }

  store.subscribe(render);
}
