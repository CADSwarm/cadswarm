// Chat routing and the team thread, mirroring cadswarm/mentions.py and cadswarm/gui/panel.py:
// the same words send the same thing from a phone as from FreeCAD, and the same messages show in the thread.
import type { Brief, Message, Receipt, State } from '../store';
import { parseMentions, stripMentions } from './mentions';

export type CommandState = 'sent' | 'seen' | 'done';

/** What sending this text means. The composer turns each into API calls. */
export type ChatAction =
  | { type: 'brief'; text: string }
  | { type: 'task'; helpers: string[]; text: string; what: string }
  | { type: 'command'; to: 'all'; text: string }
  | { type: 'chat'; text: string }
  | { type: 'unknown'; name: string };

const PEOPLE = (names: string[]) => new Set(names.map(n => n.toLowerCase()));

/**
 * The panel's send rules, in the same order: a team brief first, then one task per named helper,
 * then @all, then plain chat. An unknown @name that is not a person is refused before sending.
 */
export function routeChat(text: string, helperNames: string[], personNames: string[]): ChatAction {
  const body = text.trim();
  const { helpers, special, unknown } = parseMentions(body, helperNames);
  if (unknown.length && !helpers.length && !special.size && !PEOPLE(personNames).has(unknown[0].toLowerCase())) {
    return { type: 'unknown', name: unknown[0] };
  }
  const what = stripMentions(body) || body;
  if (special.has('team')) return { type: 'brief', text: what };
  if (helpers.length) return { type: 'task', helpers, text: body, what };
  if (special.has('all')) return { type: 'command', to: 'all', text: body };
  return { type: 'chat', text: body };
}

export interface ChatMessage {
  kind: 'message';
  id: string;
  at: number;
  from: number;
  name: string;
  mine: boolean;
  /** Any member that is not a person (an AI helper or the team server). */
  helper: boolean;
  /** The addressee's name, or "all". */
  to: string;
  body: string;
  state: CommandState;
  /** Whether to show the sent/seen/done word: only my own instruction to helpers. */
  receipt: boolean;
  /** Whether to show "to <name>": not for everyone, not for me, and not when the body says @name. */
  showTo: boolean;
}

export interface ChatBuild {
  kind: 'build';
  id: string;
  at: number;
  brief: Brief;
}

export type ChatItem = ChatMessage | ChatBuild;

/** Kinds that belong in Activity or the task board, never in the conversation. */
const HIDDEN = new Set(['notice', 'help', 'result']);
const ORDER: CommandState[] = ['sent', 'seen', 'done'];

type ChatState = Pick<State, 'messages' | 'receipts' | 'members' | 'meId' | 'briefs'>;

/** Members an @all instruction goes to: everyone who takes instructions, minus people, the host and anyone gone. */
function targetsOf(m: Message, members: State['members']): number[] {
  if (m.to === 'all') {
    return members.filter(x => x.kind !== 'human' && x.kind !== 'host' && x.status !== 'left' && x.status !== 'taken_over')
      .map(x => x.id);
  }
  if (m.to.startsWith('member:')) return [Number(m.to.slice(7))];
  return [];
}

function receiptState(m: Message, members: State['members'], receipts: Receipt[]): CommandState {
  const states = targetsOf(m, members).map(id => {
    const r = receipts.find(x => x.messageId === m.id && x.member === id);
    return r?.state === 'consumed' ? 'done' : r?.state === 'delivered' ? 'seen' : 'sent';
  });
  return states.length ? states.reduce((a, b) => (ORDER.indexOf(a) <= ORDER.indexOf(b) ? a : b)) : 'sent';
}

function targetName(to: string, nameOf: (id: number) => string): string {
  if (to === 'all') return 'all';
  if (to.startsWith('member:')) {
    const id = Number(to.slice(7));
    return Number.isNaN(id) ? to : nameOf(id);
  }
  return to.startsWith('@') ? to.slice(1) : to;
}

/** Messages and builds oldest first, newest at the bottom, like the panel's chat. */
export function chatThread(s: ChatState, limit = 60): ChatItem[] {
  const byId = new Map(s.members.map(m => [m.id, m]));
  const me = s.meId == null ? undefined : byId.get(s.meId);
  const messages = s.messages.filter(m => !HIDDEN.has(m.kind)).map((m): ChatMessage => {
    const sender = byId.get(m.from);
    const mine = s.meId != null && m.from === s.meId;
    const to = targetName(m.to, id => byId.get(id)?.name ?? String(id));
    return {
      kind: 'message', id: m.id, at: m.createdMs, from: m.from,
      name: sender?.name ?? '?', mine, helper: !!sender && sender.kind !== 'human',
      to, body: m.body, state: receiptState(m, s.members, s.receipts),
      receipt: mine && m.kind === 'steer',
      showTo: to !== 'all' && to !== (me?.name ?? '') &&
        !m.body.toLowerCase().includes(`@${to.toLowerCase()}`),
    };
  });
  const builds = s.briefs.map((b): ChatBuild => ({ kind: 'build', id: b.id, at: b.updatedMs, brief: b }));
  return [...messages, ...builds].sort((a, b) => a.at - b.at).slice(-limit);
}
