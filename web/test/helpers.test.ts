import { describe, expect, it } from 'vitest';
import { emptyState, type Member, type State } from '../src/store';
import { freeHelper, helperNow, helpers, startInstruction } from '../src/ui/helpers';
import { chatThread } from '../src/ui/chat';

const m = (id: number, name: string, kind = 'agent', status = 'active', statusText = ''): Member =>
  ({ id, name, kind, client: 'claude', role: 'member', status, statusText, fence: 1, online: true });

function world(): State {
  const s = emptyState();
  s.meId = 1;
  s.members = [m(1, 'sam', 'human'), m(2, 'alice-claude'), m(3, 'bob-codex'), m(4, 'old-droid', 'agent', 'left')];
  return s;
}

describe('tell a helper', () => {
  it('lists live helpers, never people or helpers that left', () => {
    expect(helpers(world()).map(h => h.name)).toEqual(['alice-claude', 'bob-codex']);
  });

  it('says what a helper is doing', () => {
    const s = world();
    s.tasks = [{ id: 't_1', title: 'Base plate', status: 'claimed', owner: 2, assignee: '', brief: '', objects: [] }];
    expect(helperNow(s, s.members[1])).toBe('working on “Base plate”');
    expect(helperNow(s, s.members[2])).toBe('ready for instructions');
    expect(helperNow(s, m(5, 'gone', 'agent', 'stale'))).toBe('away');
  });

  it('asks a free helper to start a task, naming its parts', () => {
    const s = world();
    s.tasks = [{ id: 't_1', title: 'Base', status: 'claimed', owner: 2, assignee: '', brief: '', objects: [] }];
    expect(freeHelper(s)?.name).toBe('bob-codex');
    const text = startInstruction({ id: 't_2', title: 'Lid', objects: ['Lid', 'Lip'] });
    expect(text).toContain('start task t_2'); expect(text).toContain('Parts: Lid, Lip');
  });
});

describe('the demo swarm answers instructions', () => {
  it('goes not read → working on it → done, with a reply', async () => {
    const { vi } = await import('vitest');
    const { Store } = await import('../src/store');
    const { startMockSwarm } = await import('../src/mock');
    vi.useFakeTimers();
    const store = new Store();
    const { api, stop } = startMockSwarm(store);
    try {
      const id = api.sendCommand('worker1', 'Make the lid 20 mm tall');
      const item = () => chatThread(store.state).find(i => i.id === id) as Extract<ReturnType<typeof chatThread>[number], { kind: 'message' }>;
      expect(item()).toMatchObject({ to: 'worker1', receipt: true, state: 'sent' });
      vi.advanceTimersByTime(1500);
      expect(item().state).toBe('seen');
      vi.advanceTimersByTime(4000);
      expect(item().state).toBe('done');
      const reply = chatThread(store.state).find(i => i.kind === 'message' && i.name === 'worker1' && i.id !== id);
      expect(reply).toMatchObject({ helper: true, body: 'Done: Make the lid 20 mm tall' });
    } finally { stop(); vi.useRealTimers(); }
  });
});

describe('the team server', () => {
  it('is never offered as a helper', () => {
    const s = world();
    s.members.push(m(9, 'host', 'host'));
    expect(helpers(s).map(h => h.name)).not.toContain('host');
  });
});
