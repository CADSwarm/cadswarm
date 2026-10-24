import type { Brief, Review, State, Store } from '../store';
import type { Api } from '../api';
import { memberColor } from '../color';
import { mountBrief } from './brief';
import { mountHandoff } from './handoff';
import { mountParams } from './params';
import { esc } from './esc';

const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;
const TABS = [['build', 'Build'], ['parts', 'Parts'], ['tasks', 'Tasks'], ['activity', 'Activity']] as const;
type Tab = typeof TABS[number][0];
export const STEPS = ['new', 'planning', 'building', 'reviewing', 'ready'] as const;
const STEP_LABEL: Record<string, string> = { new: 'Sent', planning: 'Plan', building: 'Build', reviewing: 'Review', ready: 'Ready' };

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

function ago(ms: number) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  return s < 45 ? 'now' : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`;
}

function kb(n: number) { return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`; }

function loadTab(): Tab {
  const q = new URLSearchParams(location.search).get('tab');
  if (TABS.some(([id]) => id === q)) return q as Tab;
  try { const t = localStorage.getItem('cadswarm:tab'); if (TABS.some(([id]) => id === t)) return t as Tab; } catch { /* no storage */ }
  return 'build';
}

export function mountPanels(el: HTMLElement, store: Store, api: Api | null) {
  el.innerHTML = `
    <header class="panel-head">
      <span class="brand">CadSwarm</span>
      <span class="conn" data-conn></span>
    </header>
    <nav class="tabs" role="tablist" aria-label="Panels">
      ${TABS.map(([id, label]) => `<button type="button" role="tab" data-tab="${id}" aria-controls="tab-${id}">${label}<span class="count" data-count="${id}"></span></button>`).join('')}
      <span class="conn compact" data-conn></span>
    </nav>
    <div class="tab" id="tab-build" data-tab="build" role="tabpanel">
      <section id="status" class="card status-card" aria-live="polite"></section>
      <section id="handoff"></section>
      <section id="brief"></section>
      <h2>Dimensions</h2><section id="params"></section>
      <h2>Team</h2><ul id="team" class="team"></ul>
    </div>
    <div class="tab" id="tab-parts" data-tab="parts" role="tabpanel"><h2>Parts</h2><ul id="tree" class="list"></ul></div>
    <div class="tab" id="tab-tasks" data-tab="tasks" role="tabpanel"><h2>Tasks</h2><ul id="tasks" class="list"></ul></div>
    <div class="tab" id="tab-activity" data-tab="activity" role="tabpanel"><h2>Activity</h2><ol id="activity" class="list feed"></ol></div>
    <div class="toast" role="status" aria-live="polite" hidden></div>`;

  const $ = <T extends Element = HTMLElement>(sel: string) => el.querySelector<T>(sel)!;
  mountBrief($('#brief'), api, store.state.briefs.length === 0);
  mountHandoff($('#handoff'), store, api);
  mountParams($('#params'), store, api);

  // Tabs (phones only; wide screens show every section stacked).
  function select(tab: Tab) {
    el.querySelectorAll<HTMLElement>('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
    el.querySelectorAll<HTMLButtonElement>('.tabs [role=tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
    try { localStorage.setItem('cadswarm:tab', tab); } catch { /* no storage */ }
  }
  $('.tabs').addEventListener('click', e => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[role=tab]');
    if (b) select(b.dataset.tab as Tab);
  });
  select(loadTab());

  // Actions, delegated so re-rendering never loses a listener.
  el.addEventListener('click', e => {
    const t = e.target as HTMLElement;
    const stl = t.closest<HTMLButtonElement>('[data-stl]');
    if (stl && api) {
      stl.disabled = true;
      stl.textContent = 'Preparing…';
      api.downloadArtifact(stl.dataset.stl!, stl.dataset.file!)
        .catch(err => store.set({ notice: { text: (err as Error).message ?? String(err), at: Date.now() } }))
        .finally(() => { stl.disabled = false; stl.textContent = 'Download STL'; });
    }
    const lock = t.closest<HTMLButtonElement>('[data-lock]');
    if (lock && api) void (lock.dataset.mine ? api.release([lock.dataset.lock!]) : api.lock([lock.dataset.lock!]));
  });

  let toastTimer: ReturnType<typeof setTimeout> | undefined;
  let lastNotice = 0;

  function chip(id: number) {
    const m = store.member(id);
    return m ? `<span class="chip" style="--c:${memberColor(id, dark())}">${esc(m.name)}</span>` : '';
  }

  function renderStatus(s: State) {
    const brief = currentBrief(s);
    if (!brief) { $('#status').innerHTML = '<p class="muted">No build yet. Describe what you want below and the swarm will plan it, build it and check it.</p>'; return; }
    const review = latestReview(s, brief.id);
    const tasks = s.tasks.filter(t => t.brief === brief.id);
    const done = tasks.filter(t => t.status === 'done').length;
    const at = STEPS.indexOf(brief.status as typeof STEPS[number]);
    const art = brief.stlHash ? s.artifacts.find(a => a.hash === brief.stlHash) : undefined;
    const findings = review && !review.passed ? findingLines(review.findingsJson) : [];
    $('#status').innerHTML = `
      <p class="brief-text">${esc(brief.text)}</p>
      ${brief.board ? `<p class="muted small">Board: ${esc(brief.board)}</p>` : ''}
      <ol class="steps ${brief.status === 'failed' ? 'failed' : ''}" aria-label="Build progress">
        ${STEPS.map((st, i) => `<li class="${i < at ? 'done' : i === at ? 'now' : ''}"${i === at ? ' aria-current="step"' : ''} title="${st}" aria-label="${st}">${STEP_LABEL[st]}</li>`).join('')}
      </ol>
      ${brief.status === 'failed' ? '<p class="err">The build failed. Check the activity feed for why.</p>' : ''}
      ${tasks.length ? `<p class="small"><b>${done}</b> of ${tasks.length} tasks done</p>` : ''}
      ${review ? `<div class="review ${review.passed ? 'pass' : 'fail'}">
          <p><b>Review round ${review.round}:</b> ${review.passed ? 'passed' : 'changes needed'}</p>
          ${findings.length ? `<ul>${findings.map(f => `<li>${esc(f)}</li>`).join('')}</ul>` : ''}
        </div>` : ''}
      ${brief.stlHash ? (art && art.size === 0
        ? '<p class="muted small">The STL is too large to keep online; get it from the host.</p>'
        : `<button class="button primary" data-stl="${esc(brief.stlHash)}" data-file="cadswarm-${esc(brief.id)}.stl"${api ? '' : ' disabled'}>Download STL</button>
           ${art ? `<span class="muted small">${kb(art.size)}</span>` : ''}`) : ''}`;
  }

  function render(s: State) {
    el.querySelectorAll('[data-conn]').forEach(c => {
      c.className = `conn${c.classList.contains('compact') ? ' compact' : ''}${s.connected ? ' live' : ''}`;
      c.textContent = s.connected ? 'Live' : 'Reconnecting…';
    });
    if (!el.querySelector('[data-stl][disabled]') || !api) renderStatus(s);

    $('#team').innerHTML = s.members.filter(m => m.status === 'active' || m.status === 'stale')
      .map(m => `<li>${chip(m.id)}<span class="muted small">${esc(m.kind)}${m.id === s.meId ? ' · you' : ''}${m.status === 'stale' ? ' · away' : ''}</span></li>`).join('');

    const parts = s.objects.filter(o => o.name !== 'Params');
    $('#tree').innerHTML = parts.map(o => {
      const h = s.locks.find(l => l.object === o.name);
      const editing = s.pointers.find(p => p.object === o.name && p.mode === 'editing');
      const mine = h && h.holder === s.meId;
      return `<li class="part${editing ? ' editing' : ''}${o.broken ? ' broken' : ''}"${editing ? ` style="--c:${memberColor(editing.member, dark())}"` : ''}>
        <span class="name">${esc(o.label)}</span>
        <span class="muted small">rev ${o.rev}${o.broken ? ' · broken' : ''}</span>
        <span class="right">${h ? chip(h.holder) : ''}
          ${api && (!h || mine) ? `<button type="button" class="mini" data-lock="${esc(o.name)}"${mine ? ' data-mine="1"' : ''}>${mine ? 'Release' : 'Lock'}</button>` : ''}</span>
      </li>`;
    }).join('') || '<li class="empty muted">Parts appear here as agents build them.</li>';

    $('#tasks').innerHTML = s.tasks.map(t => `<li class="task">
        <span class="status ${esc(t.status)}">${esc(t.status.replace(/_/g, ' '))}</span>
        <span class="title">${esc(t.title)}</span>
        <span class="right">${t.owner ? chip(t.owner) : t.assignee ? `<span class="muted small">for ${esc(t.assignee)}</span>` : ''}</span>
      </li>`).join('') || '<li class="empty muted">No tasks yet. The planner writes them when a build starts.</li>';

    $('#activity').innerHTML = s.activity.slice(-40).reverse().map(a =>
      `<li>${chip(a.member)} <span class="summary">${esc(a.summary)}</span> <time class="muted small">${ago(a.atMs)}</time></li>`).join('')
      || '<li class="empty muted">Quiet so far.</li>';

    const counts: Record<Tab, number> = { build: 0, parts: parts.length, tasks: s.tasks.filter(t => t.status !== 'done').length, activity: 0 };
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
