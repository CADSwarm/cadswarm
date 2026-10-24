// The Chat tab: the team's real conversation, and one composer that sends with the panel's @ rules.
// A named helper gets a task and wakes up; @team plans and builds; @all tells everyone; no @ is chat.
import type { Brief, State, Store } from '../store';
import type { Api } from '../api';
import { memberColor } from '../color';
import { esc } from './esc';
import { loadBoards, type BoardOption } from './brief';
import { findingLines } from './panels';
import { helperNow, helpers } from './helpers';
import { chatThread, routeChat, type ChatMessage, type CommandState } from './chat';
import { filterCompletions, insertMention, mentionPrefix, parseMentions } from './mentions';

const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;
const STEPS = ['new', 'planning', 'building', 'reviewing', 'ready'] as const;
const STEP_LABEL: Record<string, string> = { new: 'Sent', planning: 'Plan', building: 'Build', reviewing: 'Review', ready: 'Ready' };
const RECEIPT: Record<CommandState, string> = { sent: '⏳ not read yet', seen: '👀 read, working on it', done: '✓ done' };
const EMPTY = `<div class="empty-thread">
    <p class="empty-title">No messages yet</p>
    <p>Type <b>@</b> and pick a helper to give it something to build. It answers here as it works.</p>
  </div>`;

function time(ms: number) {
  return new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function buildBubble(s: State, b: Brief, api: Api | null) {
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

function messageRow(i: ChatMessage) {
  const who = i.mine ? 'You' : i.name;
  return `<div class="row ${i.mine ? 'mine' : 'theirs'}" data-msg="${esc(i.id)}">
      <div class="msg-who">
        <span class="who" style="--c:${memberColor(i.from, dark())}">${esc(who)}</span>
        ${i.helper && !i.mine ? '<span class="ai-tag">(AI helper)</span>' : ''}
        ${i.showTo ? `<span class="to-tag">to ${esc(i.to)}</span>` : ''}
        <time>${time(i.at)}</time>
      </div>
      <div class="bubble">${esc(i.body)}</div>
      ${i.receipt ? `<span class="receipt ${i.state}">${RECEIPT[i.state]}</span>` : ''}
    </div>`;
}

interface Row { name: string; label: string; detail: string }

/**
 * Chat: the whole team conversation (not a second inbox) and a composer that mirrors the FreeCAD panel.
 * Type @ for helpers, @team for a planned build, @all for every helper, or plain words for the people.
 */
export function mountChat(el: HTMLElement, store: Store, api: Api | null, picked: () => string[]) {
  el.innerHTML = `
    <div class="thread" data-thread aria-live="polite"></div>
    <form class="composer" data-composer>
      <div class="board-row" data-board-row hidden>
        <span class="to-label">Board</span>
        <select data-board aria-label="Board (optional)"><option value="">None</option></select>
      </div>
      <div class="compose-field">
        <div class="mention-menu" data-mentions role="listbox" aria-label="Mention a helper or the team" hidden></div>
        <textarea data-text rows="1" placeholder="@helper what to do, or a message for your team"
                  aria-label="Message the team"${api ? '' : ' disabled'}></textarea>
        <button type="submit" class="send" aria-label="Send"${api ? '' : ' disabled'}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6"/></svg>
        </button>
      </div>
      <p class="compose-hint"><b>@name</b> gives a helper a task · <b>@team</b> plans and builds a request ·
        <b>@all</b> tells every helper · no @ is chat between people.</p>
    </form>`;

  const $ = <T extends Element = HTMLElement>(sel: string) => el.querySelector<T>(sel)!;
  const thread = $('[data-thread]');
  const text = $<HTMLTextAreaElement>('[data-text]');
  const sendBtn = $<HTMLButtonElement>('.send');
  const menu = $('[data-mentions]');
  const boardRow = $('[data-board-row]');
  const board = $<HTMLSelectElement>('[data-board]');
  const notice = (msg: string) => store.set({ notice: { text: msg, at: Date.now() } });

  let boards: BoardOption[] = [];
  void loadBoards().then(b => {
    boards = b;
    board.innerHTML = '<option value="">None</option>' + b.map(x => `<option value="${esc(x.id)}">${esc(x.label)}</option>`).join('');
    syncBoard();
  });

  // ---- mention completion above the field ----
  const picker: { open: boolean; prefix: string; active: number; rows: Row[] } = { open: false, prefix: '', active: 0, rows: [] };
  let pickedAt = -Infinity;

  function rowsFor(s: State): Row[] {
    return [
      ...helpers(s).map(m => ({ name: m.name, label: `@${m.name}`, detail: helperNow(s, m) })),
      { name: 'team', label: '@team', detail: 'plan it and build it' },
      { name: 'all', label: '@all', detail: 'every helper' },
    ];
  }

  function paint() {
    menu.innerHTML = picker.rows.map((r, i) => `<button type="button" role="option" class="mention-row${i === picker.active ? ' active' : ''}"
        data-mention="${esc(r.name)}" aria-selected="${i === picker.active}">
        <span class="mention-name">${esc(r.label)}</span><span class="mention-detail">${esc(r.detail)}</span>
      </button>`).join('');
    menu.hidden = !picker.rows.length;
  }

  function closePicker() {
    picker.open = false;
    picker.prefix = '';
    picker.rows = [];
    menu.hidden = true;
  }

  function refreshPicker() {
    const before = text.value.slice(0, text.selectionStart ?? text.value.length);
    const prefix = mentionPrefix(before);
    if (prefix == null) { closePicker(); return; }
    const all = rowsFor(store.state);
    const wanted = filterCompletions(prefix, all.map(r => r.name));
    const rows = all.filter(r => wanted.includes(r.name));
    if (!rows.length) { closePicker(); return; }
    if (!picker.open || prefix !== picker.prefix) picker.active = 0;
    picker.open = true;
    picker.prefix = prefix;
    picker.rows = rows;
    picker.active = Math.min(picker.active, rows.length - 1);
    paint();
  }

  function move(delta: number) {
    const n = picker.rows.length;
    if (!n) return;
    picker.active = (picker.active + delta + n) % n;
    paint();
  }

  function choose(name: string) {
    const cursor = text.selectionStart ?? text.value.length;
    const next = insertMention(text.value, cursor, name);
    text.value = next.text;
    text.setSelectionRange(next.cursor, next.cursor);
    text.focus();
    closePicker();
    pickedAt = performance.now();
    grow();
    syncBoard();
  }

  menu.addEventListener('pointerdown', e => {
    const row = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-mention]');
    if (!row) return;
    e.preventDefault(); // keep the textarea focused, so the tap is not a blur
    choose(row.dataset.mention!);
  });

  // ---- composer ----
  const grow = () => { text.style.height = 'auto'; text.style.height = `${text.scrollHeight}px`; };
  const hasTeam = () => parseMentions(text.value, store.state.members.filter(m => m.kind !== 'human').map(m => m.name))
    .special.has('team');
  const syncBoard = () => { boardRow.hidden = !(hasTeam() && boards.length > 0); };

  text.addEventListener('input', () => { grow(); syncBoard(); refreshPicker(); });
  text.addEventListener('click', refreshPicker);
  text.addEventListener('keyup', e => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) refreshPicker();
  });
  el.addEventListener('focusout', e => {
    const next = e.relatedTarget as Node | null;
    if (!next || !$('[data-composer]').contains(next)) closePicker();
  });

  text.addEventListener('keydown', e => {
    if (picker.open) {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); return; }
      if (e.key === 'Escape') { e.preventDefault(); closePicker(); return; }
      if ((e.key === 'Enter' || e.key === 'Tab') && !e.shiftKey && picker.rows.length) {
        e.preventDefault();
        choose(picker.rows[picker.active].name);
        return; // that Enter picked a name; it must not also send
      }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); void send(); }
  });

  let sending = false;
  async function send() {
    if (!api || sending) return;
    if (picker.open || performance.now() < pickedAt + 300) return; // the Enter after a completion
    const body = text.value.trim();
    if (!body) return;
    const action = routeChat(body,
      store.state.members.filter(m => m.kind !== 'human').map(m => m.name),
      store.state.members.filter(m => m.kind === 'human').map(m => m.name));
    if (action.type === 'unknown') {
      notice(`There's no AI helper called @${action.name}. Type @ to pick one from the list.`);
      return;
    }
    sending = true;
    sendBtn.disabled = true;
    try {
      if (action.type === 'brief') {
        api.submitBrief(action.text, board.value);
        clear();
      } else if (action.type === 'task') {
        for (const helper of action.helpers) {
          const taskId = await api.createTask({
            title: (action.what.split('\n')[0] ?? '').trim().slice(0, 90),
            detail: action.what, objects: picked(), assignee: helper,
          });
          api.sendCommand(helper, action.text, taskId);
        }
        clear();
      } else if (action.type === 'command') {
        api.sendCommand('all', action.text);
        clear();
      } else {
        api.sendChat(action.text);
        clear();
      }
    } catch (e) {
      notice((e as Error).message ?? String(e)); // keep the words so nothing typed is lost
    } finally {
      sending = false;
      sendBtn.disabled = false;
    }
  }

  function clear() {
    text.value = '';
    grow();
    closePicker();
    syncBoard();
    stick = true;
  }

  $('[data-composer]').addEventListener('submit', e => { e.preventDefault(); void send(); });

  // ---- thread ----
  let stick = true;
  thread.addEventListener('scroll', () => { stick = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 40; });

  let lastKey = '';
  function render(s: State) {
    const items = chatThread(s);
    const key = JSON.stringify(items.map(i => i.kind === 'message'
      ? [i.id, i.at, i.name, i.mine, i.helper, i.to, i.showTo, i.state, i.receipt, i.body]
      : [i.id, i.at, i.brief.status, i.brief.stlHash, i.brief.text,
        s.tasks.filter(t => t.brief === i.brief.id && t.status === 'done').length,
        s.reviews.filter(r => r.brief === i.brief.id).map(r => [r.round, r.passed]).join(';')]));
    if (key !== lastKey) {
      lastKey = key;
      thread.innerHTML = items.length ? items.map(i => i.kind === 'build'
        ? `<div class="row build-row">${buildBubble(s, i.brief, api)}</div>`
        : messageRow(i)).join('') : EMPTY;
    }
    if (picker.open) refreshPicker();
    if (stick) thread.scrollTop = thread.scrollHeight;
  }
  store.subscribe(render);
}
