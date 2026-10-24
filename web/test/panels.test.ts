import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadBoards, parseBoards } from '../src/ui/brief';
import { datasheetLinks, parseMm } from '../src/ui/params';
import { currentBrief, findingLines, latestReview, modelBrief } from '../src/ui/panels';
import { bedError, printHeadline, printLines, reviewFindings, reviewHeadline, shownReview } from '../src/ui/review';
import { toPrintability } from '../src/api';
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
  it('shows only web datasheet links from the brief plan', () => {
    const plan = JSON.stringify({ parts: [], sources: {
      pcb: 'https://files.seeedstudio.com/xiao.pdf', holes: 'https://files.seeedstudio.com/xiao.pdf',
      ports: 'javascript:alert(1)', extra: 'https://x/y' } });
    expect(datasheetLinks(plan)).toEqual({
      pcb: 'https://files.seeedstudio.com/xiao.pdf', holes: 'https://files.seeedstudio.com/xiao.pdf' });
    expect(datasheetLinks('{}')).toEqual({});
    expect(datasheetLinks('{"sources":{"pcb":"ftp://x"}}')).toEqual({});
    expect(datasheetLinks('{"sources":null}')).toEqual({});
    expect(datasheetLinks('not json')).toEqual({});
    expect(datasheetLinks('')).toEqual({});
  });
  it('picks the newest brief and its latest review', () => {
    const briefs = [{ id: 'b_1', updatedMs: 1 }, { id: 'b_2', updatedMs: 5 }] as never[];
    expect(currentBrief({ briefs })?.id).toBe('b_2');
    const reviews = [{ brief: 'b_2', round: 1 }, { brief: 'b_2', round: 2 }, { brief: 'b_1', round: 9 }] as never[];
    expect(latestReview({ reviews }, 'b_2')?.round).toBe(2);
  });
  it('keeps an older finished model reachable when a newer request is unfinished', () => {
    const briefs = [
      { id: 'b_1', updatedMs: 1, stlHash: 'aa'.repeat(32) },
      { id: 'b_2', updatedMs: 2, stlHash: 'bb'.repeat(32) },
      { id: 'b_3', updatedMs: 9, stlHash: '' },
    ] as never[];
    expect(currentBrief({ briefs })?.id).toBe('b_3');
    expect(modelBrief({ briefs })?.id).toBe('b_2');
    expect(modelBrief({ briefs: [{ id: 'b_4', updatedMs: 1, stlHash: '' }] as never })).toBeUndefined();
  });
  it('reads findings whatever shape the reviewer sends', () => {
    expect(findingLines('[{"message":"hole too small"},"lid loose"]')).toEqual(['hole too small', 'lid loose']);
    expect(findingLines('{"findings":[{"text":"thin wall"}]}')).toEqual(['thin wall']);
    expect(findingLines('[{"object":"Base","problem":"the wall is 1.1 mm","task_id":"t_1"}]'))
      .toEqual(['Base: the wall is 1.1 mm']);
    expect(findingLines('plain words')).toEqual(['plain words']);
    expect(findingLines('')).toEqual([]);
  });
  it('names the newest review round and caps its findings', () => {
    expect(reviewHeadline({ round: 2, passed: true })).toBe('Round 2 passed');
    expect(reviewHeadline({ round: 3, passed: false })).toBe('Round 3 asks for changes');
    expect(reviewFindings('[{"detail":"thin wall"},{"summary":"hole small"},"lid loose","extra"]', 3))
      .toEqual(['thin wall', 'hole small', 'lid loose']);
    expect(reviewFindings('[{"object":"Base","problem":"the wall is 1.1 mm"}]')).toEqual(['Base: the wall is 1.1 mm']);
    expect(reviewFindings('not json')).toEqual(['not json']);
    const said = shownReview({
      briefs: [{ id: 'b_1', updatedMs: 1, stlHash: 'aa'.repeat(32) }, { id: 'b_2', updatedMs: 5, stlHash: 'bb'.repeat(32) }],
      reviews: [{ brief: 'b_2', round: 1, atMs: 1 }, { brief: 'b_2', round: 2, atMs: 3 }, { brief: 'b_1', round: 9, atMs: 9 }],
    } as never);
    expect(said?.round).toBe(2);
    // A newer unfinished request must not hide the verdict that belongs to the downloadable model.
    const older = shownReview({
      briefs: [{ id: 'b_1', updatedMs: 1, stlHash: 'aa'.repeat(32) }, { id: 'b_2', updatedMs: 9, stlHash: '' }],
      reviews: [{ brief: 'b_1', round: 3, atMs: 2, passed: true }, { brief: 'b_2', round: 1, atMs: 9, passed: false }],
    } as never);
    expect(older?.round).toBe(3);
    expect(shownReview({ briefs: [{ id: 'b_3', updatedMs: 1, stlHash: '' }], reviews: [] } as never)).toBeUndefined();
  });
  it('keeps the printer bed between 20 and 2000 mm on each side', () => {
    expect(bedError(256, 256, 256)).toBe('');
    expect(bedError(20, 2000, 180)).toBe('');
    expect(bedError(19, 180, 180)).toMatch(/between 20 and 2000/);
    expect(bedError(180, 2001, 180)).toMatch(/between 20 and 2000/);
    expect(bedError(Number.NaN, 180, 180)).toMatch(/millimetres/);
  });
  it('turns the worker printability answer into plain lines', () => {
    const p = toPrintability({ ok: false, objects: [
      { name: 'Base', size: [70, 32, 2], fits_bed: true, closed_solid: true, thin_wall_suspects: [] },
      { name: 'Lid', size: [300, 32.25, 3], fits_bed: false, closed_solid: false,
        thin_wall_suspects: [{ distance: 0.8 }, { distance: 0.6 }] },
    ] });
    expect(printHeadline(p)).toBe('Not printable yet');
    expect(printLines(p)).toEqual(['Base: 70 x 32 x 2 mm - fits',
      'Lid: 300 x 32.3 x 3 mm - too big for the bed; not a closed solid; 2 thin walls (closest 0.6 mm)']);
    expect(toPrintability({ ok: true })).toEqual({ ok: true, objects: [] });
    expect(toPrintability(null)).toEqual({ ok: false, objects: [] });
    expect(printLines({ ok: true, objects: [] })).toEqual([]);
    expect(printHeadline({ ok: true, objects: [] })).toBe('Fits the printer');
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
    await api.setBed(256, 256, 300);
    expect(store.state.workspace).toEqual({ bedXMm: 256, bedYMm: 256, bedZMm: 300 });
    const printed = await api.checkPrintability();
    expect(printed?.objects.map(o => o.name)).toEqual(store.state.objects.map(o => o.name));
    expect(printed?.ok).toBe(true);
    expect(printed?.objects.every(o => o.fitsBed && o.closedSolid)).toBe(true);
    const id = api.submitBrief('a lid', 'rpi4');
    expect(store.state.briefs).toMatchObject([{ id, text: 'a lid', board: 'rpi4', status: 'new' }]);
    vi.advanceTimersByTime(1800 * 2);
    const held = store.state.locks.find(l => l.holder !== 1)!;
    await api.lock([held.object]);
    expect(store.state.notice?.text).toMatch(/Already locked/);
    stop();
  });
  it('stops a task or a whole request and leaves finished work alone', async () => {
    vi.useFakeTimers();
    const store = new Store();
    const { stop, api } = startMockSwarm(store);
    const briefId = store.state.briefs[0].id;
    const claimed = store.state.tasks.find(t => t.status === 'claimed')!;
    const open = store.state.tasks.find(t => t.status === 'open')!;
    await api.cancelTask(claimed.id, 'not needed');
    expect(store.state.tasks.find(t => t.id === claimed.id)?.status).toBe('cancelled');
    await api.deleteTask(open.id);
    expect(store.state.tasks.some(t => t.id === open.id)).toBe(false);

    await api.cancelBrief(briefId);
    expect(store.state.briefs[0].status).toBe('cancelled');
    // Unstarted parts go with the request; the task already cancelled stays cancelled.
    expect(store.state.tasks.filter(t => t.brief === briefId)
      .every(t => t.status === 'done' || t.status === 'cancelled')).toBe(true);
    // The scripted build does not resurrect a stopped request.
    vi.advanceTimersByTime(1800 * 10);
    expect(store.state.briefs[0].status).toBe('cancelled');
    expect(store.state.tasks.find(t => t.id === claimed.id)?.status).toBe('cancelled');
    stop();
  });
});
