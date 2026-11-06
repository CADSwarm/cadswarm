// The AI Build task pane: a prompt box, the numbered step plan with done / current / pending states,
// "N of M done · now: X" over a segmented progress bar, and each step's live feed of CAD ops with timings.
import type { Api } from '../api';
import type { State, Store } from '../store';
import { esc } from './esc';
import { icon } from './icons';
import { formatDuration, type OpLine, type StepView } from '../steps';
import { avatar, buildProgress, statusMark, STATE_WORD } from './tree';
import { findingLines, latestReview } from './panels';
import { view } from '../viewstate';

const KIND_WORD: Record<string, string> = { part: 'Part', library: 'Library', assembly: 'Assembly', check: 'Check' };
const BRIEF_WORD: Record<string, string> = { new: 'Queued', planning: 'Planning', building: 'Building', reviewing: 'Reviewing',
  ready: 'Ready', failed: 'Stopped', cancelled: 'Cancelled' };
const ACTIVE = ['new', 'planning', 'building', 'reviewing'];

function opRow(o: OpLine): string {
  const st = o.failed ? `<span class="op-st err">${icon('x', 'ico xs')}</span>`
    : o.running ? '<span class="op-st run"><span class="spin"></span></span>' : `<span class="op-st ok">${icon('check', 'ico xs')}</span>`;
  const time = o.failed ? `<span class="op-t err" title="${esc(o.error)}">Failed</span>`
    : o.running ? `<span class="op-t run" data-run-start="${o.startedMs}">Running</span>`
    : `<span class="op-t">${o.durationMs != null ? formatDuration(o.durationMs) : ''}</span>`;
  return `<li class="op${o.running ? ' running' : ''}">${st}<span class="op-label">${esc(o.label)}${o.target ? ` <span class="op-target">${esc(o.target)}</span>` : ''}</span>${time}</li>`;
}

function chips(rec: Record<string, unknown>): string {
  return Object.entries(rec).map(([k, v]) => `<span class="kv"><span>${esc(k.replace(/_/g, ' '))}</span><b>${esc(typeof v === 'object' ? JSON.stringify(v) : String(v))}</b></span>`).join('');
}

function stepDetail(s: State, st: StepView): string {
  const meta: string[] = [];
  if (st.owner) meta.push(`Built by ${esc(s.members.find(m => m.id === st.owner)?.name ?? '')}`);
  if (st.assignee) meta.push(`Pinned to ${esc(st.assignee)}`);
  if (st.waitingFor.length) meta.push(`Waits for ${st.waitingFor.map(esc).join(', ')}`);
  const comp = st.component ? Object.entries(st.component).map(([k, v]) => `${k} ${v}`).join(' · ') : '';
  return `<div class="step-detail">
    ${st.summary ? `<p class="step-sum">${esc(st.summary)}</p>` : ''}
    ${meta.length ? `<p class="step-meta">${meta.join(' · ')}</p>` : ''}
    ${comp ? `<p class="step-kv"><span class="kv"><span>library part</span><b>${esc(comp)}</b></span></p>` : ''}
    ${Object.keys(st.params).length ? `<p class="step-kv">${chips(st.params)}</p>` : ''}
    ${Object.keys(st.targets).length ? `<p class="step-kv">${chips(st.targets)}</p>` : ''}
    ${st.ports.length ? `<p class="step-kv">${st.ports.map(p => `<span class="kv"><span>port</span><b>${esc(p.name)}${p.type ? ` (${esc(p.type)})` : ''}</b></span>`).join('')}</p>` : ''}
    ${st.mates.length ? `<ul class="mates">${st.mates.map(m => `<li><b>${esc(m.type)}</b> ${esc(m.a)}${m.b ? ` ↔ ${esc(m.b)}` : ''}</li>`).join('')}</ul>` : ''}
    ${st.opLines.length ? `<ol class="op-feed">${st.opLines.map(opRow).join('')}</ol>`
      : st.state === 'current' ? '<p class="step-meta">Starting…</p>' : ''}
  </div>`;
}

export interface BuildPane { focusPrompt(): void }

export function mountBuildPane(el: HTMLElement, store: Store, api: Api | null): BuildPane {
  el.innerHTML = `
    <form class="prompt" data-prompt>
      <textarea data-brief rows="2" placeholder="Describe a part or mechanism to build…" aria-label="Describe what to build"${api ? '' : ' disabled'}></textarea>
      <div class="prompt-foot">
        <span class="muted small">The team plans it step by step, then builds one component at a time.</span>
        <button type="submit" class="btn primary sm"${api ? '' : ' disabled'}>${icon('spark', 'ico xs')}Build</button>
      </div>
    </form>
    <div data-build></div>`;
  const text = el.querySelector<HTMLTextAreaElement>('[data-brief]')!;
  const out = el.querySelector<HTMLElement>('[data-build]')!;
  const expanded = new Set<string>();
  const collapsed = new Set<string>();
  let last = '';

  el.querySelector('[data-prompt]')!.addEventListener('submit', e => {
    e.preventDefault();
    const t = text.value.trim();
    if (!t || !api) return;
    api.submitBrief(t, '');
    text.value = '';
  });
  text.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); el.querySelector<HTMLFormElement>('[data-prompt]')!.requestSubmit(); }
  });

  function render(s: State) {
    const { brief, plan, p } = buildProgress(s);
    if (!brief) {
      out.innerHTML = `<div class="build-empty">${icon('spark', 'ico big')}<p><b>Nothing is being built</b></p>
        <p class="muted">Describe what you need above, for example “a four-blade rotor head with a servo-driven swashplate”.
        The planner writes a numbered plan; helpers and teammates build one step each, in order.</p></div>`;
      last = '';
      return;
    }
    const review = latestReview(s, brief.id);
    const findings = review && !review.passed ? findingLines(review.findingsJson, 4) : [];
    const segs = p.steps.map(st => `<span class="seg st-${st.state}" title="${st.n}. ${esc(st.name)} · ${STATE_WORD[st.state]}"></span>`).join('');
    const live = ACTIVE.includes(brief.status);
    const html = `
      <section class="build-card">
        <div class="build-title"><b>${esc(plan.title || brief.text)}</b><span class="chip-st b-${esc(brief.status)}">${esc(BRIEF_WORD[brief.status] ?? brief.status)}</span></div>
        ${plan.title ? `<p class="build-brief">${esc(brief.text)}</p>` : ''}
        ${p.total ? `<div class="segbar" aria-hidden="true">${segs}</div>
          <p class="build-line"><b>${p.done} of ${p.total} done</b>${p.current.length ? ` · now: ${p.current.map(c => esc(c.name)).join(', ')}` : ''}</p>`
          : `<p class="build-line"><span class="spin"></span> The planner is writing the step plan…</p>`}
        <div class="build-actions">
          ${live ? `<button type="button" class="btn sm" data-b="pause">${icon('pause', 'ico xs')}Pause</button>
            <button type="button" class="btn sm" data-b="stop">${icon('stop', 'ico xs')}Stop</button>` : ''}
          <button type="button" class="btn sm" data-b="review">${icon('review', 'ico xs')}Review</button>
          ${brief.stlHash ? `<button type="button" class="btn sm primary" data-b="stl">${icon('download', 'ico xs')}Download STL</button>` : ''}
        </div>
        ${review ? `<div class="review-line ${review.passed ? 'ok' : 'warn'}">${icon(review.passed ? 'check' : 'warn', 'ico xs')}
          Review round ${review.round} ${review.passed ? 'passed' : 'asks for changes'}</div>
          ${findings.length ? `<ul class="findings">${findings.map(f => `<li>${esc(f)}</li>`).join('')}</ul>` : ''}` : ''}
      </section>
      ${p.total ? `<div class="pane-sub">Plan · ${p.total} steps</div>` : ''}
      <ol class="step-list">${p.steps.map(st => {
        const isOpen = expanded.has(st.id) || (st.state === 'current' && !collapsed.has(st.id));
        return `<li class="step st-${st.state}${isOpen ? ' open' : ''}">
          <button type="button" class="step-head" data-step="${esc(st.id)}" aria-expanded="${isOpen}">
            ${statusMark(st.state)}<span class="step-n">${st.n}</span>
            <span class="step-name">${esc(st.name)}</span>
            ${st.kind !== 'part' ? `<span class="kind k-${st.kind}">${KIND_WORD[st.kind]}</span>` : ''}
            ${st.owner ? avatar(s, st.owner, 'xs') : st.assignee ? `<span class="pinned" title="Pinned to ${esc(st.assignee)}">${esc(st.assignee)}</span>` : ''}
            ${st.opLines.length && !isOpen ? `<span class="step-ops">${st.opLines.length} ops</span>` : ''}
          </button>
          ${isOpen ? stepDetail(s, st) : ''}</li>`;
      }).join('')}</ol>`;
    if (html === last) return;
    last = html;
    const keepScroll = el.scrollTop;
    out.innerHTML = html;
    el.scrollTop = keepScroll;
    tickRunning();
  }

  out.addEventListener('click', e => {
    const t = e.target as HTMLElement;
    const head = t.closest<HTMLElement>('[data-step]');
    if (head) {
      const id = head.dataset.step!;
      const open = head.getAttribute('aria-expanded') === 'true';
      if (open) { expanded.delete(id); collapsed.add(id); } else { expanded.add(id); collapsed.delete(id); }
      const st = buildProgress(store.state).p.steps.find(x => x.id === id);
      if (st && !open) view.select(st.objects.filter(n => store.state.objects.some(o => o.name === n)));
      last = ''; render(store.state); return;
    }
    const b = t.closest<HTMLElement>('[data-b]');
    if (!b || !api) return;
    const brief = buildProgress(store.state).brief;
    if (!brief) return;
    if (b.dataset.b === 'pause') { api.sendCommand('all', 'Pause the build: finish the op you are running, report_result for your step, and take no new step until I say continue.'); store.set({ notice: { text: 'Asked every helper to pause.', at: Date.now() } }); }
    if (b.dataset.b === 'stop' && confirm(`Stop “${brief.text}”? Steps nobody started are cancelled.`)) void api.cancelBrief(brief.id);
    if (b.dataset.b === 'review') dispatchEvent(new CustomEvent('cadswarm:command', { detail: 'review' }));
    if (b.dataset.b === 'stl' && brief.stlHash) void api.downloadArtifact(brief.stlHash, `cadswarm-${brief.id}.stl`)
      .catch(err => store.set({ notice: { text: (err as Error).message, at: Date.now() } }));
  });

  // Running ops count up in place, without re-rendering the list.
  function tickRunning() {
    out.querySelectorAll<HTMLElement>('[data-run-start]').forEach(n => {
      const t0 = Number(n.dataset.runStart);
      n.textContent = t0 ? `Running ${formatDuration(Math.max(0, Date.now() - t0))}` : 'Running';
    });
  }
  setInterval(tickRunning, 250);

  store.subscribe(render);
  return { focusPrompt: () => { text.focus(); } };
}
