import { describe, expect, it } from 'vitest';
import { emptyState, type Member, type State } from '../src/store';
import { TEAM, conversation, destinations, fillParts, freeHelper, helperNow, helperReplies, helpers, myCommands, startInstruction } from '../src/ui/helpers';

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

  it('follows each instruction per helper: not read, working on it, done', () => {
    const s = world();
    s.messages = [
      { id: 'm_1', from: 1, to: 'member:2', kind: 'steer', body: 'make the lid 20 mm', taskId: '', createdMs: 10 },
      { id: 'm_2', from: 1, to: 'all', kind: 'steer', body: 'check for clashes', taskId: '', createdMs: 20 },
      { id: 'm_3', from: 1, to: 'all', kind: 'chat', body: 'hi team', taskId: '', createdMs: 30 },
      { id: 'm_4', from: 2, to: 'member:3', kind: 'message', body: 'not mine', taskId: '', createdMs: 40 },
    ];
    s.receipts = [{ messageId: 'm_1', member: 2, state: 'delivered' }, { messageId: 'm_2', member: 2, state: 'consumed' }];
    const rows = myCommands(s);
    expect(rows.map(r => r.body)).toEqual(['check for clashes', 'make the lid 20 mm']); // newest first, chat isn't an instruction
    expect(rows[1]).toMatchObject({ to: 'alice-claude', state: 'seen' });
    expect(rows[0]).toMatchObject({ to: 'all helpers', state: 'sent', byHelper: { 'alice-claude': 'done', 'bob-codex': 'sent' } }); // not the helper that left
    s.receipts.push({ messageId: 'm_2', member: 3, state: 'consumed' });
    expect(myCommands(s)[0].state).toBe('done');
  });

  it('shows helpers\' replies to me or to everyone, not their talk with each other', () => {
    const s = world();
    s.messages = [
      { id: 'a', from: 2, to: 'member:1', kind: 'message', body: 'Done: lid is 20 mm', taskId: '', createdMs: 50 },
      { id: 'b', from: 3, to: 'all', kind: 'result', body: 'no clashes', taskId: '', createdMs: 60 },
      { id: 'c', from: 2, to: 'member:3', kind: 'message', body: 'can you move?', taskId: '', createdMs: 70 },
      { id: 'd', from: 1, to: 'all', kind: 'chat', body: 'from me', taskId: '', createdMs: 80 },
    ];
    expect(helperReplies(s).map(r => [r.name, r.body])).toEqual([['bob-codex', 'no clashes'], ['alice-claude', 'Done: lid is 20 mm']]);
  });

  it('asks a free helper to start a task, naming its parts', () => {
    const s = world();
    s.tasks = [{ id: 't_1', title: 'Base', status: 'claimed', owner: 2, assignee: '', brief: '', objects: [] }];
    expect(freeHelper(s)?.name).toBe('bob-codex');
    const text = startInstruction({ id: 't_2', title: 'Lid', objects: ['Lid', 'Lip'] });
    expect(text).toContain('start task t_2') ; expect(text).toContain('Parts: Lid, Lip');
  });

  it('fills the parts you picked into a quick instruction', () => {
    expect(fillParts('Release {parts} so I can edit them.', ['Lid'])).toBe('Release Lid so I can edit them.');
    expect(fillParts('Run FEA on {parts}.', [])).toBe("Run FEA on the parts you're working on.");
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
      api.sendCommand('worker1', 'Make the lid 20 mm tall');
      expect(myCommands(store.state)[0]).toMatchObject({ to: 'worker1', state: 'sent' });
      vi.advanceTimersByTime(1500);
      expect(myCommands(store.state)[0].state).toBe('seen');
      vi.advanceTimersByTime(4000);
      expect(myCommands(store.state)[0].state).toBe('done');
      expect(helperReplies(store.state)[0]).toMatchObject({ name: 'worker1', body: 'Done: Make the lid 20 mm tall' });
    } finally { stop(); vi.useRealTimers(); }
  });
});

describe('the team server', () => {
  it('is never offered as a helper or counted in "all helpers"', () => {
    const s = world();
    s.members.push(m(9, 'host', 'host'));
    expect(helpers(s).map(h => h.name)).not.toContain('host');
    s.messages = [{ id: 'x', from: 1, to: 'all', kind: 'steer', body: 'hi', taskId: '', createdMs: 1 }];
    expect(Object.keys(myCommands(s)[0].byHelper)).not.toContain('host');
  });
});

describe('the Ask tab', () => {
  it('reads as one conversation: requests, builds and replies, oldest first', () => {
    const s = world();
    s.messages = [
      { id: 'r', from: 2, to: 'member:1', kind: 'message', body: 'Done: lid is 20 mm', taskId: '', createdMs: 300 },
      { id: 'c', from: 1, to: 'member:2', kind: 'steer', body: 'make the lid 20 mm', taskId: '', createdMs: 100 },
    ];
    s.receipts = [{ messageId: 'c', member: 2, state: 'consumed' }];
    s.briefs = [{ id: 'b', by: 1, text: 'a phone stand', board: '', status: 'building', planJson: '', stlHash: '', updatedMs: 200 }];
    const items = conversation(s);
    expect(items.map(i => i.type)).toEqual(['request', 'build', 'reply']);
    expect(items[0]).toMatchObject({ text: 'make the lid 20 mm', to: 'alice-claude', state: 'done' });
    expect(items[2]).toMatchObject({ name: 'alice-claude', text: 'Done: lid is 20 mm' });
  });

  it('offers a "To" choice only when there is more than one place to send it', () => {
    const s = world();
    expect(destinations(s).map(d => d.value)).toEqual(['alice-claude', 'bob-codex', 'all', TEAM]);
    s.members = [m(1, 'sam', 'human'), m(9, 'host', 'host')];
    expect(destinations(s)).toEqual([{ value: TEAM, label: 'The team', detail: 'plan it and build it' }]); // just the team: no menu
  });
});
