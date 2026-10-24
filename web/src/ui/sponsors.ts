/**
 * The sponsors panel: what each outside service does for CADSwarm, the exact calls we make, and the
 * live trace of it working in this workspace. It opens from the sidebar's "Sponsors" button, Shift+D,
 * or `?debug`.
 */
import type { Review, State, Store } from '../store';
import { esc } from './esc';
import { SPONSOR_EVIDENCE } from './sponsor-evidence';

export interface Card {
  id: 'spacetime' | 'factory' | 'elastic';
  name: string;
  role: string;
  facts: Array<[string, string]>;
  list?: { title: string; rows: string[] };
  trace?: string[];
  calls: string[];
}

export interface Finding { object: string; problem: string }

/** The newest verdict for the highest round: the review the brief's state reflects. */
export function latestReviewOf(reviews: Pick<Review, 'round' | 'passed' | 'findingsJson'>[]): Review | undefined {
  return [...reviews].sort((a, b) => a.round - b.round).slice(-1)[0] as Review | undefined;
}

/** The findings of a review, tolerant of the empty or truncated JSON a crashed reviewer leaves. */
export function parseFindings(review?: Pick<Review, 'findingsJson'>): Finding[] {
  if (!review) return [];
  try {
    const raw = JSON.parse(review.findingsJson || '[]');
    if (!Array.isArray(raw)) return [];
    return raw.filter(f => f && typeof f === 'object' && typeof f.problem === 'string')
      .map(f => ({ object: String(f.object ?? ''), problem: String(f.problem) }));
  } catch { return []; }
}

/** Droid names a tool `<server>___<tool>`; the trace reads better as the name a person would say. */
export function toolLine(summary: string): string {
  const name = String(summary).trim().split(/\s+/)[0] || '';
  return name.startsWith('cadswarm___') ? `cadswarm.${name.slice('cadswarm___'.length)}` : name;
}

const list = (rows: string[], max: number) => rows.slice(0, max).map(r => (r.length > 170 ? `${r.slice(0, 167)}…` : r));

/** The three cards, built only from state the browser already has, plus the stamped snapshot. */
export function sponsorCards(s: Pick<State, 'members' | 'objects' | 'params' | 'locks' | 'tasks' | 'ops' | 'activity' | 'briefs' | 'reviews'>, project: string): Card[] {
  const online = s.members.filter(m => m.status === 'active').length;
  const done = s.ops.filter(o => o.status === 'done').length;
  const lastSeq = s.activity.length ? Math.max(...s.activity.map(a => a.id)) : 0;
  const brief = [...s.briefs].sort((a, b) => b.updatedMs - a.updatedMs)[0];
  const review = latestReviewOf(s.reviews);
  const findings = parseFindings(review);
  const reviewer = s.members.find(m => m.kind === 'reviewer');
  const trace = reviewer ? s.activity.filter(a => a.member === reviewer.id) : [];
  const filed = trace.filter(a => a.summary.includes('create_task')).length;

  return [
    {
      id: 'spacetime',
      name: 'SpacetimeDB',
      role: 'the team\'s shared state',
      facts: [
        ['database', project || 'this workspace'],
        ['members', `${online} active · ${s.members.length} joined`],
        ['model', `${s.objects.length} parts · ${s.params.length} params · ${s.locks.length} held`],
        ['work', `${s.tasks.filter(t => t.status === 'open').length} open tasks · ${done}/${s.ops.length} ops done`],
        ['events', `${s.activity.length} activity rows · seq ${lastSeq}`],
      ],
      calls: ['submit_op(kind, args) → host runs it alone → complete_op(result)', 'views: v_member · v_object · v_task · v_op · v_review'],
    },
    {
      id: 'factory',
      name: 'Factory.ai',
      role: 'the design reviewer (Droid)',
      facts: [
        ['agent', reviewer ? `${reviewer.name} · ${reviewer.client} · ${reviewer.status}` : 'not set up'],
        ['mode', 'autonomy HIGH · 7 read-only MCP tools'],
        ['last review', review ? `round ${review.round} · ${review.passed ? 'approved' : `${findings.length} findings`}` : 'no review yet'],
        ['tasks filed', String(filed)],
      ],
      list: findings.length ? {
        title: 'what it found',
        rows: findings.slice(0, 3).map(f => `${f.object || 'model'}: ${f.problem.length > 150 ? `${f.problem.slice(0, 147)}…` : f.problem}`),
      } : undefined,
      trace: list(trace.map(a => toolLine(a.summary)), 7),
      calls: ['Session(mcp_servers=[cadswarm], restrict_tools=REVIEW_TOOLS, runtime=Runtime(executable=droid.exe))', 'Droid → MCP over stdio → model_status, check_interference, check_printability, report_review'],
    },
    {
      id: 'elastic',
      name: 'Elastic',
      role: 'board knowledge, design memory, lessons',
      facts: [
        ['board', brief?.board || 'none'],
        ['design memory', brief ? `${brief.id} · ${findings.length} problems` : 'no designs yet'],
        ['lessons', 'distil-lessons · every 10 min'],
        ['agent tools', 'lookup_board · recall_designs · board_spec'],
      ],
      list: findings.length ? {
        title: 'what the Workflow distils',
        rows: list(findings.map(f => `${brief?.board || 'board'}::${f.problem}`), 4),
      } : undefined,
      calls: ['POST /cadswarm-boards/_search   hybrid + jina rerank', 'PUT  /cadswarm-designs/_doc/{brief}', 'ES|QL over cadswarm-designs → cadswarm-lessons'],
    },
  ];
}

function checked(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/** The stamped facts, so a checked number is never read as a live one. */
export function evidenceLine(): string {
  const e = SPONSOR_EVIDENCE;
  return `checked ${checked(e.checkedAt)} · Elastic ${e.elastic.boards} board docs, ${e.elastic.designs} designs, ${e.elastic.lessons} lessons · Droid ${e.factory.droid} / droid-sdk ${e.factory.sdk}`;
}

function cardHTML(c: Card): string {
  return `<section class="sponsor" data-sponsor="${c.id}">
    <header><h3>${esc(c.name)}</h3><p>${esc(c.role)}</p></header>
    <dl>${c.facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
    ${c.list ? `<div class="sponsor-list"><span class="section-label">${esc(c.list.title)}</span>
      <ul>${c.list.rows.map(r => `<li>${esc(r)}</li>`).join('')}</ul></div>` : ''}
    ${c.trace?.length ? `<div class="sponsor-trace"><span class="section-label">trace</span>
      <ol>${c.trace.map(r => `<li>${esc(r)}</li>`).join('')}</ol></div>` : ''}
    <pre class="sponsor-code">${c.calls.map(esc).join('\n')}</pre>
  </section>`;
}

/** Mount the dialog and wire its openers: `?debug`, Shift+D, and a button the sidebar adds. */
export function mountSponsors(host: HTMLElement, store: Store, project: string) {
  const root = document.createElement('div');
  root.className = 'sponsors';
  root.hidden = true;
  root.innerHTML = `<div class="sponsors-sheet" role="dialog" aria-modal="true" aria-labelledby="sponsors-title">
    <header class="sponsors-head">
      <div><h2 id="sponsors-title">Sponsors</h2>
        <p class="muted small">The outside services this build runs on, and the traffic in this workspace.</p></div>
      <button type="button" class="text-button" data-sponsors-close>Close</button>
    </header>
    <div class="sponsors-grid" data-sponsors-cards></div>
    <footer class="sponsors-foot">${esc(evidenceLine())}</footer>
  </div>`;
  host.append(root);

  const cards = root.querySelector<HTMLElement>('[data-sponsors-cards]')!;
  let open = false;
  const paint = () => { cards.innerHTML = sponsorCards(store.state, project).map(cardHTML).join(''); };
  const show = (on: boolean) => {
    open = on;
    root.hidden = !on;
    if (on) { paint(); root.querySelector<HTMLElement>('[data-sponsors-close]')?.focus(); }
  };

  root.addEventListener('click', e => {
    if (e.target === root || (e.target as HTMLElement).closest('[data-sponsors-close]')) show(false);
  });
  document.addEventListener('keydown', e => {
    const typing = /^(input|textarea|select)$/i.test((e.target as HTMLElement)?.tagName ?? '');
    if (e.key === 'Escape' && open) { show(false); return; }
    if (!typing && e.shiftKey && !e.ctrlKey && !e.metaKey && (e.key === 'D' || e.key === 'd')) { e.preventDefault(); show(!open); }
  });
  store.subscribe(() => { if (open) paint(); });
  if (new URLSearchParams(location.search).has('debug')) show(true);

  return { open: () => show(true), close: () => show(false), isOpen: () => open };
}
