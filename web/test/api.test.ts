import { afterEach, describe, expect, it, vi } from 'vitest';
import { splitError, toActivity, toBrief, toMember, toPointer, type Api } from '../src/api';
import { PointerGate } from '../src/pointer-gate';
import { loadSession, saveSession, lastDb } from '../src/session';
import { Store } from '../src/store';
import { startMockSwarm } from '../src/mock';

describe('reducer errors', () => {
  it('splits "<code>: <message>" on the first ": "', () => {
    expect(splitError(new Error('locked: Already locked: Lid by bob (active). Message the holder'))).toEqual({
      code: 'locked', message: 'Already locked: Lid by bob (active). Message the holder' });
    expect(splitError(new Error('socket closed'))).toEqual({ code: '', message: 'socket closed' });
  });
});

describe('row mapping', () => {
  it('turns u64 bigints into numbers and keeps camelCase fields', () => {
    const m = toMember({ id: 7n, ws: 1n, identity: {} as never, name: 'ana', kind: 'human', client: 'phone', role: 'member',
      status: 'active', statusText: 'hi', fence: 3, lastSeenMs: 1n, joinedMs: 1n, staleSinceMs: 0n, takenOverBy: 0n,
      endpointId: '', online: true, contextSeq: 0n });
    expect(m).toEqual({ id: 7, name: 'ana', kind: 'human', client: 'phone', role: 'member', status: 'active', statusText: 'hi', fence: 3, online: true });
    expect(toActivity({ id: 9n, ws: 1n, member: 2n, kind: 'say', summary: 's', atMs: 1700000000000n }).atMs).toBe(1700000000000);
    expect(toPointer({ member: 2n, ws: 1n, object: 'Lid', point: [1, 2, 3], mode: 'editing', label: '', op: '', seq: 4, atMs: 5n }))
      .toMatchObject({ member: 2, point: [1, 2, 3], seq: 4, atMs: 5 });
    expect(toBrief({ id: 'b_1', ws: 1n, by: 3n, text: 't', board: '', status: 'ready', planJson: '', stlHash: 'ab',
      createdMs: 1n, updatedMs: 2n })).toMatchObject({ by: 3, stlHash: 'ab', updatedMs: 2 });
  });
});

describe('PointerGate', () => {
  afterEach(() => vi.useRealTimers());
  it('sends at most once per window, only on change, and flushes the latest', () => {
    vi.useFakeTimers();
    let t = 0;
    const sent: string[] = [];
    const g = new PointerGate<string>(v => sent.push(v), 200, () => t);
    g.push('a', 'a');           // leading edge
    g.push('a', 'a');           // unchanged: dropped
    t = 50; g.push('b', 'b');   // inside the window: held
    t = 100; g.push('c', 'c');  // replaces the held value
    expect(sent).toEqual(['a']);
    t = 200; vi.advanceTimersByTime(200);
    expect(sent).toEqual(['a', 'c']);
    t = 1000; g.push('c', 'c'); // unchanged after flush
    expect(sent).toEqual(['a', 'c']);
  });
});

describe('chat and tasks', () => {
  it('exposes the reducers the composer needs', () => {
    const names: (keyof Api)[] = ['submitBrief', 'sendCommand', 'createTask', 'sendMessage', 'sendChat'];
    expect(names).toEqual(['submitBrief', 'sendCommand', 'createTask', 'sendMessage', 'sendChat']);
  });

  it('strips @mentions from a brief, creates a helper task and chats to everyone', async () => {
    const store = new Store();
    const { api, stop } = startMockSwarm(store);
    try {
      api.submitBrief('@team make a lid', '');
      expect(store.state.briefs[0].text).toBe('make a lid');

      const taskId = await api.createTask({ title: 'make a lid', detail: 'make a lid 20 mm', objects: ['Lid'], assignee: 'worker1' });
      expect(taskId).toMatch(/^t_/);
      expect(store.state.tasks.at(-1)).toMatchObject({ id: taskId, title: 'make a lid', assignee: 'worker1', objects: ['Lid'] });

      const messageId = api.sendChat('hello team');
      expect(store.state.messages.at(-1)).toMatchObject({ id: messageId, kind: 'chat', to: 'all', body: 'hello team' });
    } finally { stop(); }
  });
});

describe('session', () => {  afterEach(() => vi.unstubAllGlobals());
  it('survives storage that throws', () => {
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } });
    expect(loadSession('u', 'd')).toBeNull();
    expect(() => saveSession({ uri: 'u', db: 'd', token: 'x' })).not.toThrow();
    expect(lastDb()).toBeNull();
  });
  it('stores the token per database and remembers the last one', () => {
    const m = new Map<string, string>();
    vi.stubGlobal('localStorage', { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) });
    saveSession({ uri: 'http://127.0.0.1:3000', db: 'cadswarm', token: 'tok' });
    expect(loadSession('http://127.0.0.1:3000', 'cadswarm')).toBe('tok');
    expect(lastDb()).toEqual({ uri: 'http://127.0.0.1:3000', db: 'cadswarm' });
  });
});
