// Pure helpers for telling AI helpers what to do from the phone (no DOM, no network: unit-tested).
import type { Member, State, Task } from '../store';

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
