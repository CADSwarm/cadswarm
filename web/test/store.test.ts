import { describe, expect, it } from 'vitest';
import { Store } from '../src/store';

const m = (id: number, status = 'active') => ({ id, name: `m${id}`, kind: 'agent', client: '', role: 'member', status, statusText: '', fence: 1, online: false });

describe('Store', () => {
  it('batches updates and notifies subscribers once per tick', async () => {
    const s = new Store();
    let calls = 0;
    s.subscribe(() => calls++);
    s.set({ connected: true });
    s.set({ meId: 1 });
    await Promise.resolve();
    expect(calls).toBe(2); // the immediate call on subscribe + one batched notification
  });
  it('resolves lock holders and hides idle or stale pointers', () => {
    const s = new Store();
    s.set({ members: [m(1), m(2, 'stale')], locks: [{ object: 'Lid', holder: 1 }],
      pointers: [
        { member: 1, object: 'Lid', point: [], mode: 'editing', label: '', op: '', seq: 1, atMs: 0 },
        { member: 2, object: 'Base', point: [], mode: 'pointing', label: '', op: '', seq: 1, atMs: 0 },
      ] });
    expect(s.holderOf('Lid')?.name).toBe('m1');
    expect(s.activePointers().map(p => p.member)).toEqual([1]);
  });
  it('hides a pointer that has nowhere to point (an agent whose op just finished)', () => {
    const s = new Store();
    s.set({ members: [m(1), m(2)],
      pointers: [
        { member: 1, object: '', point: [], mode: 'pointing', label: '', op: '', seq: 2, atMs: 0 },
        { member: 2, object: 'Lid', point: [], mode: 'editing', label: 'building Lid', op: 'o_1', seq: 1, atMs: 0 },
      ] });
    expect(s.activePointers().map(p => p.member)).toEqual([2]);
  });
});
