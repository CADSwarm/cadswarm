/**
 * The sponsors panel: what each outside service does for CADSwarm, the exact calls we make, and the
 * live trace of it working in this workspace. It opens from the sidebar's "Sponsors" button, Shift+D,
 * or `?debug`.
 */
import type { Member, Review, State, Store } from '../store';
import { esc } from './esc';
import { SPONSOR_EVIDENCE } from './sponsor-evidence';

export interface Card {
  id: 'spacetime' | 'anthropic' | 'creao' | 'factory' | 'elastic';
  name: string;
  role: string;
  facts: Array<[string, string]>;
  list?: { title: string; rows: string[] };
  trace?: string[];
  traceTitle?: string;
  calls: string[];
}

/** The window's order is part of the claim: the team's state, then who builds, then who checks, then memory. */
export function cardOrder(cards: Card[]): Card['id'][] {
  return cards.map(c => c.id);
}

export interface Finding { object: string; problem: string }

/** The newest verdict for this brief: the review the brief's state reflects. */
export function latestReviewOf(
  reviews: Array<Pick<Review, 'round' | 'passed' | 'findingsJson'> & { brief?: string }>,
  briefId?: string,
): Review | undefined {
  const wanted = briefId ? reviews.filter(r => r.brief === briefId) : [...reviews];
  return wanted.sort((a, b) => a.round - b.round).slice(-1)[0] as Review | undefined;
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

const LOOPBACK = /^(127\.\d+\.\d+\.\d+|localhost|\[::1\])$/i;

/** A short phrase for where the team's database lives, from the address the browser connected to.
 *  Kept in step with the FreeCAD panel's `server_label` (cadswarm/hosting.py). */
export function serverLabel(uri: string): string {
  let host = '';
  try { host = new URL(uri).hostname; } catch { return 'this computer'; }
  if (/spacetimedb\.com$/i.test(host)) return 'SpacetimeDB Maincloud';
  if (!host || LOOPBACK.test(host)) return 'this computer';
  return `this computer · ${host}`;
}

/** The five cards, built only from state the browser already has, plus the stamped snapshot. */
export function sponsorCards(s: Pick<State, 'members' | 'objects' | 'params' | 'locks' | 'tasks' | 'ops' | 'activity' | 'briefs' | 'reviews' | 'server'>, project: string): Card[] {
  const online = s.members.filter(m => m.status === 'active').length;
  const done = s.ops.filter(o => o.status === 'done').length;
  const lastSeq = s.activity.length ? Math.max(...s.activity.map(a => a.id)) : 0;
  const brief = [...s.briefs].sort((a, b) => b.updatedMs - a.updatedMs)[0];
  const review = latestReviewOf(s.reviews, brief?.id);
  const findings = parseFindings(review);
  const reviewer = s.members.find(m => m.kind === 'reviewer');
  const trace = reviewer ? s.activity.filter(a => a.member === reviewer.id) : [];
  // Same rule as the FreeCAD tab: tasks the reviewer filed, not the tool calls it made.
  const filed = reviewer ? s.tasks.filter(t => t.createdBy === reviewer.id).length : 0;
  const parts = s.objects.filter(o => o.name !== 'Params').length;
  // Params is the shared spreadsheet, not a part, and its lock is not a held part (same as the FreeCAD panel).
  const held = s.locks.filter(l => l.object !== 'Params').length;
  const server = serverLabel(s.server ?? '');
  const maincloud = server === 'SpacetimeDB Maincloud';

  const byClient = (client: string) => s.members.filter(m => m.client === client);
  const owned = (m: Member) => s.tasks.filter(t => t.owner === m.id);
  const opsBy = (m: Member) => s.ops.filter(o => o.by === m.id);
  const doneOf = (rows: Array<{ status: string }>) => rows.filter(r => r.status === 'done').length;
  const traceOf = (members: Member[]) =>
    s.activity.filter(a => members.some(m => m.id === a.member)).map(a => a.summary);
  const people = (members: Member[]) =>
    members.length ? members.map(m => `${m.name} · ${m.status}`).join(', ') : 'not joined';
  const claude = byClient('claude');
  const creao = byClient('creao');
  const claudeActive = claude.filter(m => m.status === 'active').length;
  const claudeTasks = claude.flatMap(owned);
  const claudeOps = claude.flatMap(opsBy);
  const creaoTasks = creao.flatMap(owned);
  const creaoOps = creao.flatMap(opsBy);

  return [
    {
      id: 'spacetime',
      name: 'SpacetimeDB',
      role: 'the team\'s shared state',
      facts: [
        ['server', server],
        ['sign-in', maincloud ? 'joined by invite' : 'no account needed'],
        ['database', project || 'this workspace'],
        ['members', `${online} active · ${s.members.length} joined`],
        ['model', `${parts} parts · ${s.params.length} params · ${held} held`],
        ['work', `${s.tasks.filter(t => t.status === 'open').length} open tasks · ${done}/${s.ops.length} ops done`],
        ['events', `${s.activity.length} activity rows · seq ${lastSeq}`],
      ],
      calls: ['submit_op(kind, args) → host runs it alone → complete_op(result)', 'views: v_member · v_object · v_task · v_op · v_review'],
    },
    {
      id: 'anthropic',
      name: 'Anthropic',
      role: 'the planners and builders (Claude Code)',
      facts: [
        ['agents', claude.length ? `${people(claude)} · ${claudeActive}/${claude.length} active` : 'not joined'],
        ['tasks done', `${doneOf(claudeTasks)}/${claudeTasks.length}`],
        ['mcp ops', `${claudeOps.length} submitted · ${doneOf(claudeOps)} done`],
        ['surface', 'Claude Code → MCP over stdio → the host runs the op'],
      ],
      trace: list(traceOf(claude).slice(-7), 7),
      traceTitle: 'recent work',
      calls: ['Session(mcp_servers=[cadswarm]) → model_status, ops, tasks, recall_designs',
        'claude → MCP over stdio → submit_op → host → complete_op'],
    },
    {
      id: 'creao',
      name: 'Creao AI',
      role: 'the helper that drives its own conversation',
      facts: [
        ['helper', people(creao)],
        ['mode', 'local bridge · one JSON tool call per turn'],
        ['tasks done', `${doneOf(creaoTasks)}/${creaoTasks.length}`],
        ['mcp ops', `${creaoOps.length} submitted · ${doneOf(creaoOps)} done`],
        ['thread', 'one Creao conversation per helper folder'],
      ],
      trace: list(traceOf(creao).slice(-7), 7),
      traceTitle: 'recent work',
      calls: ['cadswarm.creao_agent → CREAO_API_KEY → one tool call per turn',
        'a stale card is the takeover proof: the fence moved and the old thread stops'],
    },
    {
      id: 'factory',
      name: 'Factory.ai',
      role: 'the design reviewer (Droid)',
      facts: [
        ['agent', reviewer ? `${reviewer.name} · ${reviewer.client} · ${reviewer.status}` : 'not set up'],
        ['mode', 'autonomy HIGH · 8 MCP tools, geometry read-only'],
        ['last review', review ? `round ${review.round} · ${review.passed ? 'approved' : `${findings.length} findings`}` : 'no review yet'],
        ['tasks filed', String(filed)],
        ['pointer', reviewer ? 'own arrow, in its own colour' : 'none until the reviewer joins'],
      ],
      list: findings.length ? {
        title: 'what it found',
        rows: findings.slice(0, 3).map(f => `${f.object || 'model'}: ${f.problem.length > 150 ? `${f.problem.slice(0, 147)}…` : f.problem}`),
      } : undefined,
      trace: list(trace.map(a => toolLine(a.summary)), 7),
      calls: ['Session(mcp_servers=[cadswarm], restrict_tools=REVIEW_TOOLS, runtime=Runtime(executable=droid.exe))', 'Droid → MCP over stdio → model_status, point_at, check_interference, check_printability, report_review'],
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
    ${c.trace?.length ? `<div class="sponsor-trace"><span class="section-label">${esc(c.traceTitle ?? 'trace')}</span>
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
