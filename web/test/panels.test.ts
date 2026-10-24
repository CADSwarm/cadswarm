import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadBoards, parseBoards } from '../src/ui/brief';
import { parseMm } from '../src/ui/params';
import { currentBrief, findingLines, latestReview } from '../src/ui/panels';
import { mergeStl, startMockSwarm } from '../src/mock';
import { Store } from '../src/store';
import { esc } from '../src/ui/esc';

describe('board library', () => {
  it('reads the contract shape and sorts by label', () => {
    expect(parseBoards({ rpi4: { label: 'Raspberry Pi 4 Model B', pcb: [85, 56, 1.4] }, esp: { label: 'ESP32-S3 DevKitC' }, bare: {} }))
      .toEqual([{ id: 'bare', label: 'bare' }, { id: 'esp', label: 'ESP32-S3 DevKitC' }, { id: 'rpi4', label: 'Raspberry Pi 4 Model B' }]);
  });
  it('treats a missing, empty or broken file as "no list" (free-text fallback)', async () => {
    expect(parseBoards(null)).toEqual([]);
    expect(parseBoards([1, 2])).toEqual([]);
    expect(parseBoards({})).toEqual([]);
    expect(await loadBoards((async () => new Response('not found', { status: 404 })) as typeof fetch)).toEqual([]);
    expect(await loadBoards((async () => new Response('{oops')) as typeof fetch)).toEqual([]);
    expect(await loadBoards((async () => { throw new TypeError('offline'); }) as typeof fetch)).toEqual([]);
  });
});

describe('panel helpers', () => {
  it('parses sizes in millimetres', () => {
    expect(parseMm('2.5')).toBe(2.5);
    expect(parseMm('3,2 mm')).toBe(3.2);
    expect(parseMm('0')).toBeNull();
    expect(parseMm('abc')).toBeNull();
  });
  it('picks the newest brief and its latest review', () => {
    const briefs = [{ id: 'b_1', updatedMs: 1 }, { id: 'b_2', updatedMs: 5 }] as never[];
    expect(currentBrief({ briefs })?.id).toBe('b_2');
    const reviews = [{ brief: 'b_2', round: 1 }, { brief: 'b_2', round: 2 }, { brief: 'b_1', round: 9 }] as never[];
    expect(latestReview({ reviews }, 'b_2')?.round).toBe(2);
  });
  it('reads findings whatever shape the reviewer sends', () => {
    expect(findingLines('[{"message":"hole too small"},"lid loose"]')).toEqual(['hole too small', 'lid loose']);
    expect(findingLines('{"findings":[{"text":"thin wall"}]}')).toEqual(['thin wall']);
    expect(findingLines('plain words')).toEqual(['plain words']);
    expect(findingLines('')).toEqual([]);
  });
  it('escapes markup from members and tickets', () => {
    expect(esc('<img src=x onerror="a">\'')).toBe('&lt;img src=x onerror=&quot;a&quot;&gt;&#39;');
  });
});

describe('mock swarm', () => {
  afterEach(() => vi.useRealTimers());
  it('merges STLs into one valid binary STL', () => {
    const a = new Uint8Array(84 + 50 * 2); new DataView(a.buffer).setUint32(80, 2, true);
    const b = new Uint8Array(84 + 50 * 3); new DataView(b.buffer).setUint32(80, 3, true);
    const m = mergeStl([a, b]);
    expect(m.length).toBe(84 + 50 * 5);
    expect(new DataView(m.buffer).getUint32(80, true)).toBe(5);
  });
  it('plans, builds, reviews and readies a brief, and answers the panels', async () => {
    vi.useFakeTimers();
    const store = new Store();
    const { stop, api } = startMockSwarm(store);
    expect(store.state.briefs[0].status).toBe('building');
    expect(store.state.tasks.length).toBe(5);
    vi.advanceTimersByTime(1800 * 6);
    const b = store.state.briefs[0];
    expect(b.status).toBe('ready');
    expect(b.stlHash).toMatch(/^[0-9a-f]{64}$/);
    expect(store.state.reviews.map(r => r.passed)).toEqual([false, true]);
    expect(store.state.artifacts[0].hash).toBe(b.stlHash);

    await api.setParam('wall', '3 mm');
    expect(store.state.params.find(p => p.alias === 'wall')?.num).toBe(3);
    const id = api.submitBrief('a lid', 'rpi4');
    expect(store.state.briefs).toMatchObject([{ id, text: 'a lid', board: 'rpi4', status: 'new' }]);
    vi.advanceTimersByTime(1800 * 2);
    const held = store.state.locks.find(l => l.holder !== 1)!;
    await api.lock([held.object]);
    expect(store.state.notice?.text).toMatch(/Already locked/);
    stop();
  });
});
