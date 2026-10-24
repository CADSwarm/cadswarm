// Pure helpers for telling AI helpers what to do from the phone (no DOM, no network: unit-tested).
import type { Member, State, Task } from '../store';

/** Picker value that hands the request to the whole team: a brief the host plans and builds. */
export const TEAM = '__team__';
/** One-tap instructions; {parts} becomes the parts you picked in the 3D view (or "the parts you're working on"). */
export const QUICK = [
  ['Status?', "Tell me in two lines what you're working on and what's next."],
  ['Clashes', 'Check the model for clashes and fix any in parts you own; ask the owner about the rest.'],
  ['Strength', 'Run a strength check (FEA) on {parts}. Ask me for the material and loads first.'],
  ['Pause', 'Stop after your current step and wait for my next instruction.'],
  ['Let go', "Release {parts} so I can edit them."],
] as const;

export type CommandState = 'sent' | 'seen' | 'done';
export const STATE_WORDS: Record<CommandState, string> = { sent: 'not read yet', seen: 'working on it', done: 'done' };
const ORDER: CommandState[] = ['sent', 'seen', 'done'];

/** Members that take instructions: AI helpers and the reviewer, never people or the team server itself. */
export const takesInstructions = (m: Pick<Member, 'kind'>) => m.kind !== 'human' && m.kind !== 'host';

/** AI helpers, live first. */
export function helpers(s: Pick<State, 'members'>): Member[] {
  return s.members.filter(m => takesInstructions(m) && (m.status === 'active' || m.status === 'stale'))
    .sort((a, b) => Number(b.status === 'active') - Number(a.status === 'active') || a.id - b.id);
}

/** What a helper is doing right now, in a few words. */
export function helperNow(s: Pick<State, 'tasks'>, m: Member): string {
  if (m.status !== 'active') return 'away';
  const task = s.tasks.find(t => t.owner === m.id && t.status === 'claimed');
  if (task) return `working on “${task.title}”`;
  return m.statusText && m.statusText !== 'idle' ? m.statusText : 'ready for instructions';
}

export interface CommandRow { id: string; body: string; to: string; state: CommandState; byHelper: Record<string, CommandState>; createdMs: number }

/** The instructions I sent, newest first, with how far each got for every helper it went to. */
export function myCommands(s: Pick<State, 'messages' | 'receipts' | 'members' | 'meId'>, limit = 20): CommandRow[] {
  const name = (id: number) => s.members.find(m => m.id === id)?.name ?? String(id);
  const agents = s.members.filter(m => takesInstructions(m) && m.status !== 'left' && m.status !== 'taken_over').map(m => m.id);
  return s.messages.filter(m => m.from === s.meId && m.kind === 'steer')
    .sort((a, b) => b.createdMs - a.createdMs).slice(0, limit)
    .map(m => {
      const targets = m.to === 'all' ? agents : m.to.startsWith('member:') ? [Number(m.to.slice(7))] : [];
      const byHelper: Record<string, CommandState> = {};
      for (const t of targets) {
        const r = s.receipts.find(x => x.messageId === m.id && x.member === t);
        byHelper[name(t)] = r?.state === 'consumed' ? 'done' : r?.state === 'delivered' ? 'seen' : 'sent';
      }
      const states = Object.values(byHelper);
      const state = states.length ? states.reduce((a, b) => (ORDER.indexOf(a) <= ORDER.indexOf(b) ? a : b)) : 'sent';
      const to = m.to === 'all' ? 'all helpers' : m.to.startsWith('member:') ? name(Number(m.to.slice(7))) : m.to.replace(/^@/, '');
      return { id: m.id, body: m.body, to, state, byHelper, createdMs: m.createdMs };
    });
}

/** What helpers said to me or to everyone, newest first: their replies to instructions. */
export function helperReplies(s: Pick<State, 'messages' | 'members' | 'meId'>, limit = 15) {
  const agent = (id: number) => { const m = s.members.find(x => x.id === id); return m && m.kind !== 'human' ? m : undefined; };
  return s.messages.filter(m => agent(m.from) && (m.to === 'all' || m.to === `member:${s.meId}`))
    .sort((a, b) => b.createdMs - a.createdMs).slice(0, limit)
    .map(m => ({ id: m.id, from: m.from, name: agent(m.from)!.name, body: m.body, createdMs: m.createdMs }));
}

/** The instruction that asks a helper to start an existing task now. */
export function startInstruction(t: Pick<Task, 'id' | 'title' | 'objects'>): string {
  return `Please start task ${t.id} now: “${t.title}”.` + (t.objects.length ? ` Parts: ${t.objects.join(', ')}.` : '') +
    ' Claim it with claim_task, tell me in one line how you\'ll do it, and report_result when done.';
}

/** The helper to ask for a task: a live one with no task of its own, else any live one. */
export function freeHelper(s: Pick<State, 'members' | 'tasks'>): Member | undefined {
  const live = helpers(s).filter(m => m.status === 'active' && m.kind === 'agent');
  return live.find(m => !s.tasks.some(t => t.owner === m.id && t.status === 'claimed')) ?? live[0];
}

/** Fill {parts} in a quick instruction. */
export function fillParts(text: string, parts: string[]): string {
  return text.replace('{parts}', parts.length ? parts.join(', ') : "the parts you're working on");
}

// ---- The Ask conversation: my requests, builds and helpers' replies in one thread ----

export type ThreadItem =
  | { type: 'request'; id: string; at: number; text: string; to: string; state: CommandState; byHelper: Record<string, CommandState> }
  | { type: 'reply'; id: string; at: number; from: number; name: string; text: string }
  | { type: 'build'; id: string; at: number; brief: State['briefs'][number] };

/** Everything said in Ask, oldest first (newest at the bottom, like a message thread). */
export function conversation(s: Pick<State, 'messages' | 'receipts' | 'members' | 'meId' | 'briefs'>, limit = 60): ThreadItem[] {
  const items: ThreadItem[] = [
    ...myCommands(s, limit).map(c => ({ type: 'request' as const, id: c.id, at: c.createdMs, text: c.body, to: c.to,
      state: c.state, byHelper: c.byHelper })),
    ...helperReplies(s, limit).map(r => ({ type: 'reply' as const, id: r.id, at: r.createdMs, from: r.from, name: r.name,
      text: r.body })),
    ...s.briefs.map(b => ({ type: 'build' as const, id: b.id, at: b.updatedMs, brief: b })),
  ];
  return items.sort((a, b) => a.at - b.at).slice(-limit);
}

/** Where a request can go. The dropdown is only shown when there's more than one. */
export function destinations(s: Pick<State, 'members' | 'tasks'>): { value: string; label: string; detail: string }[] {
  const hs = helpers(s);
  return [
    ...hs.map(m => ({ value: m.name, label: m.name, detail: helperNow(s, m) })),
    ...(hs.length > 1 ? [{ value: 'all', label: 'All helpers', detail: 'everyone does it' }] : []),
    { value: TEAM, label: 'The team', detail: 'plan it and build it' },
  ];
}
