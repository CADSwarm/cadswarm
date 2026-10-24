import { describe, expect, it } from 'vitest';
import { emptyState, type Member, type State } from '../src/store';
import { chatThread, routeChat } from '../src/ui/chat';

const m = (id: number, name: string, kind = 'agent', status = 'active'): Member =>
  ({ id, name, kind, client: 'claude', role: 'member', status, statusText: '', fence: 1, online: true });

const HELPERS = ['alice-claude', 'bob-codex'];
const PEOPLE = ['sam'];

function world(): State {
  const s = emptyState();
  s.meId = 1;
  s.members = [m(1, 'sam', 'human'), m(2, 'alice-claude'), m(3, 'bob-codex'), m(9, 'host', 'host')];
  return s;
}

describe('routeChat', () => {
  it('makes plain words chat for everyone, never an order', () => {
    expect(routeChat('  how is it going?  ', HELPERS, PEOPLE)).toEqual({ type: 'chat', text: 'how is it going?' });
  });

  it('gives each named helper a task with the mentions stripped', () => {
    expect(routeChat('@alice-claude make the lid 20 mm', HELPERS, PEOPLE))
      .toEqual({ type: 'task', helpers: ['alice-claude'], text: '@alice-claude make the lid 20 mm', what: 'make the lid 20 mm' });
    expect(routeChat('@Alice-Claude and @bob-codex check the fit', HELPERS, PEOPLE))
      .toEqual({ type: 'task', helpers: ['alice-claude', 'bob-codex'], text: '@Alice-Claude and @bob-codex check the fit',
        what: 'and check the fit' });
  });

  it('hands @team the stripped request as a brief, before any helper or @all rule', () => {
    expect(routeChat('@team build a phone stand', HELPERS, PEOPLE)).toEqual({ type: 'brief', text: 'build a phone stand' });
    expect(routeChat('@team @alice-claude @all build a stand', HELPERS, PEOPLE)).toEqual({ type: 'brief', text: 'build a stand' });
    expect(routeChat('@TEAM  plan   it', HELPERS, PEOPLE)).toEqual({ type: 'brief', text: 'plan it' });
  });

  it('tells every helper with @all, @helpers and @everyone', () => {
    for (const word of ['all', 'helpers', 'everyone']) {
      expect(routeChat(`@${word} pause now`, HELPERS, PEOPLE)).toEqual({ type: 'command', to: 'all', text: `@${word} pause now` });
    }
  });

  it('refuses an unknown @name, unless it is a person in the team', () => {
    expect(routeChat('@nobody make it', HELPERS, PEOPLE)).toEqual({ type: 'unknown', name: 'nobody' });
    expect(routeChat('@Sam look at this', HELPERS, PEOPLE)).toEqual({ type: 'chat', text: '@Sam look at this' });
    expect(routeChat('@nobody @team make it', HELPERS, PEOPLE)).toEqual({ type: 'brief', text: 'make it' });
    expect(routeChat('@nobody @all pause', HELPERS, PEOPLE)).toEqual({ type: 'command', to: 'all', text: '@nobody @all pause' });
  });

  it('keeps the whole text as the task title when only mentions were typed', () => {
    expect(routeChat('@bob-codex', HELPERS, PEOPLE)).toEqual({ type: 'task', helpers: ['bob-codex'], text: '@bob-codex', what: '@bob-codex' });
  });
});

describe('chatThread', () => {
  it('shows messages and builds in time order, and never notices, help or results', () => {
    const s = world();
    s.messages = [
      { id: 'c', from: 1, to: 'all', kind: 'chat', body: 'hello', taskId: '', createdMs: 300 },
      { id: 'n', from: 9, to: 'all', kind: 'notice', body: 'host restarted', taskId: '', createdMs: 50 },
      { id: 'h', from: 2, to: 'member:1', kind: 'help', body: 'help wanted', taskId: '', createdMs: 60 },
      { id: 'r', from: 3, to: 'all', kind: 'result', body: 'task result', taskId: '', createdMs: 70 },
      { id: 'm', from: 2, to: 'member:1', kind: 'message', body: 'done', taskId: '', createdMs: 400 },
    ];
    s.briefs = [{ id: 'b', by: 1, text: 'a stand', board: '', status: 'building', planJson: '', stlHash: '', updatedMs: 350 }];
    const items = chatThread(s);
    expect(items.map(i => i.id)).toEqual(['c', 'b', 'm']);
    expect(items.map(i => i.kind)).toEqual(['message', 'build', 'message']);
  });

  it('names the sender, marks mine and non-humans, and resolves targets', () => {
    const s = world();
    s.messages = [
      { id: 'a', from: 1, to: 'member:3', kind: 'steer', body: '@bob-codex make the lid', taskId: '', createdMs: 10 },
      { id: 'b', from: 2, to: 'member:1', kind: 'message', body: 'Done: lid', taskId: '', createdMs: 20 },
      { id: 'c', from: 3, to: 'all', kind: 'chat', body: 'morning', taskId: '', createdMs: 30 },
    ];
    const [a, b, c] = chatThread(s);
    expect(a).toMatchObject({ name: 'sam', mine: true, helper: false, to: 'bob-codex', showTo: false, receipt: true });
    expect(b).toMatchObject({ name: 'alice-claude', mine: false, helper: true, to: 'sam', showTo: false, receipt: false });
    expect(c).toMatchObject({ name: 'bob-codex', mine: false, helper: true, to: 'all', showTo: false });
  });

  it('shows "to name" only when it is not me and the body does not already mention them', () => {
    const s = world();
    s.messages = [
      { id: 'a', from: 3, to: 'member:2', kind: 'message', body: 'can you check?', taskId: '', createdMs: 10 },
      { id: 'b', from: 3, to: 'member:2', kind: 'message', body: '@alice-claude can you check?', taskId: '', createdMs: 20 },
      { id: 'c', from: 1, to: 'member:3', kind: 'steer', body: 'please look', taskId: '', createdMs: 30 },
    ];
    expect(chatThread(s).map(i => (i.kind === 'message' ? i.showTo : null))).toEqual([true, false, true]);
  });

  it('maps receipts: not read yet, read, done; the slowest helper decides for @all', () => {
    const s = world();
    s.messages = [
      { id: 'a', from: 1, to: 'member:2', kind: 'steer', body: 'one', taskId: '', createdMs: 10 },
      { id: 'b', from: 1, to: 'member:3', kind: 'steer', body: 'two', taskId: '', createdMs: 20 },
      { id: 'c', from: 1, to: 'all', kind: 'steer', body: 'three', taskId: '', createdMs: 30 },
    ];
    s.receipts = [
      { messageId: 'a', member: 2, state: 'delivered' },
      { messageId: 'b', member: 3, state: 'consumed' },
      { messageId: 'c', member: 2, state: 'consumed' },
      { messageId: 'c', member: 3, state: 'delivered' },
    ];
    expect(chatThread(s).map(i => (i.kind === 'message' ? i.state : null))).toEqual(['seen', 'done', 'seen']);
    s.receipts = s.receipts.map(r => r.messageId === 'c' && r.member === 3 ? { ...r, state: 'consumed' } : r); // one row per helper

    expect(chatThread(s)[2]).toMatchObject({ state: 'done' });
  });

  it('never shows receipts on someone else\'s instruction or my plain chat', () => {
    const s = world();
    s.messages = [
      { id: 'a', from: 2, to: 'all', kind: 'message', body: 'helper chatter', taskId: '', createdMs: 10 },
      { id: 'b', from: 1, to: 'all', kind: 'chat', body: 'human chatter', taskId: '', createdMs: 20 },
    ];
    s.receipts = [{ messageId: 'a', member: 3, state: 'consumed' }, { messageId: 'b', member: 2, state: 'consumed' }];
    expect(chatThread(s).map(i => (i.kind === 'message' ? i.receipt : null))).toEqual([false, false]);
  });

  it('keeps only the newest slice of the whole thread', () => {
    const s = world();
    s.messages = [1, 2, 3].map(n => ({ id: `m${n}`, from: 1, to: 'all' as const, kind: 'chat', body: String(n), taskId: '', createdMs: n }));
    expect(chatThread(s, 2).map(i => i.id)).toEqual(['m2', 'm3']);
  });
});
