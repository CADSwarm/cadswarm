import type { Store, State } from '../store';
import type { Api } from '../api';
import { memberColor } from '../color';
import { esc } from './esc';
import { loadBoards, type BoardOption } from './brief';
import { QUICK, STATE_WORDS, TEAM, conversation, destinations, fillParts, type ThreadItem } from './helpers';
import { findingLines } from './panels';

const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;
const STEPS = ['new', 'planning', 'building', 'reviewing', 'ready'] as const;
const STEP_LABEL: Record<string, string> = { new: 'Sent', planning: 'Plan', building: 'Build', reviewing: 'Review', ready: 'Ready' };

function time(ms: number) {
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function buildBubble(s: State, b: Extract<ThreadItem, { type: 'build' }>['brief'], api: Api | null) {
  const at = STEPS.indexOf(b.status as typeof STEPS[number]);
  const tasks = s.tasks.filter(t => t.brief === b.id);
  const done = tasks.filter(t => t.status === 'done').length;
  const review = s.reviews.filter(r => r.brief === b.id).sort((x, y) => y.round - x.round)[0];
  const art = b.stlHash ? s.artifacts.find(a => a.hash === b.stlHash) : undefined;
  const findings = review && !review.passed ? findingLines(review.findingsJson) : [];
  return `<div class="bubble build${b.status === 'failed' ? ' failed' : ''}">
      <p class="bubble-text">${esc(b.text)}</p>
      <ol class="steps" aria-label="Build progress">
        ${STEPS.map((st, i) => `<li class="${i < at ? 'done' : i === at ? 'now' : ''}" title="${st}" aria-label="${st}"${i === at ? ' aria-current="step"' : ''}>${STEP_LABEL[st]}</li>`).join('')}
      </ol>
      <p class="meta">${b.status === 'failed' ? '<span class="err">Stopped — see Team ▸ Activity</span>'
        : tasks.length ? `${done} of ${tasks.length} parts done` : 'The team is planning it'}${review ? ` · review ${review.passed ? 'passed' : 'asks for changes'}` : ''}</p>
      ${findings.length ? `<ul class="findings">${findings.map(f => `<li>${esc(f)}</li>`).join('')}</ul>` : ''}
      ${b.stlHash && !(art && art.size === 0)
        ? `<button type="button" class="text-button" data-stl="${esc(b.stlHash)}" data-file="cadswarm-${esc(b.id)}.stl"${api ? '' : ' disabled'}>Download STL</button>` : ''}
    </div>`;
}

/**
 * Ask: the one place to tell an agent what to do. A text box, a Send button, and — only when there is more than
 * one place it could go — a "To" menu: a helper by name, all helpers, or the team (planned and built for you).
 * Everything you asked and everything helpers answered reads as one conversation.
 */
export function mountAsk(el: HTMLElement, store: Store, api: Api | null, picked: () => string[]) {
  el.innerHTML = `
    <div class="thread" data-thread aria-live="polite"></div>
    <form class="composer" data-composer>
      <div class="chips" role="group" aria-label="Suggestions">
        ${QUICK.map(([label, text], i) => `<button type="button" class="chip-button" data-quick="${i}" title="${esc(text)}">${esc(label)}</button>`).join('')}
      </div>
      <div class="to-row" data-to-row>
        <span class="to-label">To</span>
        <select data-to aria-label="Who should do it"${api ? '' : ' disabled'}></select>
        <span class="to-fixed" data-to-fixed></span>
      </div>
      <div class="board-row" data-board-row hidden>
        <span class="to-label">Board</span>
        <select data-board aria-label="Board (optional)"><option value="">None</option></select>
      </div>
      <div class="field">
        <textarea data-text rows="1" placeholder="Tell an agent what to do…" aria-label="What should it do?"${api ? '' : ' disabled'}></textarea>
        <button type="submit" class="send" aria-label="Send"${api ? '' : ' disabled'}>↑</button>
      </div>
    </form>`;
  const $ = <T extends Element = HTMLElement>(sel: string) => el.querySelector<T>(sel)!;
  const thread = $('[data-thread]');
  const to = $<HTMLSelectElement>('[data-to]');
  const text = $<HTMLTextAreaElement>('[data-text]');
  const boardRow = $('[data-board-row]');
  const board = $<HTMLSelectElement>('[data-board]');
  let boards: BoardOption[] = [];
  void loadBoards().then(b => {
    boards = b;
    board.innerHTML = '<option value="">None</option>' + b.map(x => `<option value="${esc(x.id)}">${esc(x.label)}</option>`).join('');
    syncBoard();
  });

  const target = () => to.value || TEAM;
  function syncBoard() { boardRow.hidden = !(target() === TEAM && boards.length); }
  to.addEventListener('change', syncBoard);

  const grow = () => { text.style.height = 'auto'; text.style.height = `${Math.min(text.scrollHeight, 140)}px`; };
  text.addEventListener('input', grow);
  el.addEventListener('click', e => {
    const q = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-quick]');
    if (q) { text.value = fillParts(QUICK[Number(q.dataset.quick)][1], picked()); grow(); text.focus(); }
  });

  function send() {
    const body = text.value.trim();
    if (!api || !body) return;
    if (target() === TEAM) api.submitBrief(body, board.value);
    else api.sendCommand(target(), body);
    text.value = '';
    grow();
    stick = true;
  }
  $('[data-composer]').addEventListener('submit', e => { e.preventDefault(); send(); });
  text.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } // Shift+Enter: new line
  });

  // Stay pinned to the newest message unless the person scrolled up to read.
  let stick = true;
  thread.addEventListener('scroll', () => { stick = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 40; });

  let lastDest = '', lastThread = '';
  function render(s: State) {
    const dest = destinations(s);
    const destKey = JSON.stringify(dest);
    if (destKey !== lastDest) { // rebuild only when it changed, keeping the person's choice
      lastDest = destKey;
      const keep = to.value;
      to.innerHTML = dest.map(d => `<option value="${esc(d.value)}">${esc(d.label)} — ${esc(d.detail)}</option>`).join('');
      if (dest.some(d => d.value === keep)) to.value = keep;
      const single = dest.length === 1;
      to.hidden = single;                                       // the menu only when there's a real choice
      $('[data-to-fixed]').textContent = single ? `${dest[0].label} — ${dest[0].detail}` : '';
      syncBoard();
    }
    const items = conversation(s);
    const key = JSON.stringify(items.map(i => [i.id, i.at, i.type === 'request' ? i.state : i.type === 'build'
      ? [i.brief.status, i.brief.stlHash, s.tasks.filter(t => t.brief === i.brief.id && t.status === 'done').length] : 0]));
    if (key === lastThread) return;
    lastThread = key;
    thread.innerHTML = items.length ? items.map(i => {
      if (i.type === 'build') return `<div class="row mine">${buildBubble(s, i.brief, api)}</div>`;
      if (i.type === 'reply') {
        return `<div class="row theirs">
          <span class="who" style="--c:${memberColor(i.from, dark())}">${esc(i.name)}</span>
          <div class="bubble">${esc(i.text)}</div><time>${time(i.at)}</time></div>`;
      }
      const detail = Object.entries(i.byHelper).map(([n, v]) => `${n}: ${STATE_WORDS[v]}`).join('\n');
      return `<div class="row mine">
          <div class="bubble">${esc(i.text)}</div>
          <span class="status-line ${i.state}" title="${esc(detail)}">To ${esc(i.to)} · ${STATE_WORDS[i.state]}</span></div>`;
    }).join('') : `<div class="empty-thread">
        <p class="empty-title">What should we make?</p>
        <p>Describe a part or a change. Send it to <b>the team</b> and it's planned and built for you, or to one helper.</p>
      </div>`;
    if (stick) thread.scrollTop = thread.scrollHeight;
  }
  store.subscribe(render);
}
