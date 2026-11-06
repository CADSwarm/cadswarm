import { describe, expect, it } from 'vitest';
import {
  attributeOps, formatDuration, humanise, opLabel, opLine, opTargets, parsePlan, progress, rollbackIndex, summaryLine,
  titleNumber,
} from '../src/steps';
import type { Op, Task } from '../src/store';

const task = (id: string, status: string, owner = 0, extra: Partial<Task> = {}): Task =>
  ({ id, title: id, status, owner, assignee: '', brief: 'b_1', objects: [], createdBy: 2, createdMs: 0, updatedMs: 0, ...extra });
const op = (id: string, by: number, kind: string, args: Record<string, unknown>, started: number, finished: number,
  status = 'done'): Op =>
  ({ id, by, kind, status, errorCode: '', error: '', argsJson: JSON.stringify(args), resultJson: '{}',
    createdMs: started, startedMs: started, finishedMs: finished });

const V2 = JSON.stringify({
  version: 2, title: 'Four-blade rotor head',
  steps: [
    { n: 2, id: 's02', name: 'Hub', kind: 'part', task: 't2', objects: ['Hub'], depends_on: ['s01'], summary: 'The hub.' },
    { n: 1, id: 's01', name: 'Mast', kind: 'part', task: 't1', objects: ['Mast'], depends_on: [],
      summary: 'The mast: a steel shaft.', ops: ['Create sketch SK_Mast', 'Revolve Mast'], params: { d: 10, bad: {} } },
    { n: 3, id: 's03', name: 'Screws', kind: 'library', task: 't3', objects: ['Screw1'], depends_on: [1],
      component: { family: 'iso4762', size: 'M3', length: 10 } },
    { n: 4, id: 's04', name: 'Assembly', kind: 'assembly', task: 't4', depends_on: ['s01', 's02', 's03'],
      mates: [{ type: 'concentric', a: 'Hub.bore', b: 'Mast.axis' }], ports: [{ name: 'axis', type: 'axis' }] },
    { n: 5, id: 's05', name: 'Check fit', kind: 'weird', task: 't5', depends_on: ['s04'] },
  ],
});

describe('parsePlan', () => {
  it('reads a v2 plan sorted by number, with deps by id or number', () => {
    const p = parsePlan(V2);
    expect(p.version).toBe(2);
    expect(p.title).toBe('Four-blade rotor head');
    expect(p.steps.map(s => s.n)).toEqual([1, 2, 3, 4, 5]);
    expect(p.steps[2].dependsOn).toEqual(['s01']);
    expect(p.steps[2].component).toEqual({ family: 'iso4762', size: 'M3', length: 10 });
    expect(p.steps[0].params).toEqual({ d: 10 });
    expect(p.steps[3].mates).toEqual([{ type: 'concentric', a: 'Hub.bore', b: 'Mast.axis' }]);
    expect(p.steps[3].ports).toEqual([{ name: 'axis', type: 'axis' }]);
    expect(p.steps[4].kind).toBe('part'); // unknown kinds read as a modelled part
  });
  it('reads a v1 plan (parts only) as steps in list order, matching tasks', () => {
    const tasks = [task('t_a', 'done', 3, { title: 'Model the Base', objects: ['Base'] })];
    const p = parsePlan(JSON.stringify({ parts: [{ name: 'Base', summary: 'flat plate' }, { name: 'Lid', task: 't_b' }, 'Clip'] }), tasks, 'b_1');
    expect(p.version).toBe(1);
    expect(p.steps.map(s => [s.n, s.id, s.name, s.task, s.dependsOn.length])).toEqual([
      [1, 's01', 'Base', 't_a', 0], [2, 's02', 'Lid', 't_b', 0], [3, 's03', 'Clip', '', 0]]);
  });
  it('falls back to the brief tasks when there is no plan, ordered by their number', () => {
    const tasks = [task('t9', 'open', 0, { title: '2. Lid' }), task('t8', 'open', 0, { title: '1. Base' }),
      task('tx', 'open', 0, { title: 'other', brief: 'b_2' })];
    expect(parsePlan('', tasks, 'b_1').steps.map(s => [s.name, s.task])).toEqual([['Base', 't8'], ['Lid', 't9']]);
    expect(parsePlan('{oops', [], 'b_1').steps).toEqual([]);
    expect(parsePlan('null').steps).toEqual([]);
  });
  it('splits numbered titles', () => {
    expect(titleNumber('3. Pitch link')).toEqual([3, 'Pitch link']);
    expect(titleNumber('Pitch link')).toEqual([null, 'Pitch link']);
  });
});

describe('progress', () => {
  const plan = parsePlan(V2);
  it('says N of M done and names the current steps', () => {
    const tasks = [task('t1', 'done', 3), task('t2', 'claimed', 3), task('t3', 'claimed', 4), task('t4', 'open'), task('t5', 'open')];
    const p = progress(plan, tasks);
    expect(p.line).toBe('1 of 5 done · now: Hub, Screws');
    expect(p.steps.map(s => s.state)).toEqual(['done', 'current', 'current', 'waiting', 'waiting']);
    expect(p.steps[3].waitingFor).toEqual(['Hub', 'Screws']);
    expect(p.fraction).toBeCloseTo(0.2);
    expect(rollbackIndex(p.steps)).toBe(1);
  });
  it('marks open steps ready once dependencies are done, and keeps missing/cancelled', () => {
    const tasks = [task('t1', 'done'), task('t2', 'done'), task('t3', 'done'), task('t4', 'open'), task('t5', 'cancelled')];
    const p = progress(plan, tasks);
    expect(p.steps.map(s => s.state)).toEqual(['done', 'done', 'done', 'ready', 'cancelled']);
    expect(p.line).toBe('3 of 5 done');
    expect(progress(plan, []).steps.every(s => s.state === 'missing')).toBe(true);
  });
  it('summary line without current work', () => {
    expect(summaryLine(0, 0, [])).toBe('0 of 0 done');
  });
});

describe('op feed', () => {
  it('humanises kinds and finds targets', () => {
    expect(humanise('create_sketch')).toBe('Create sketch');
    expect(opLabel('set_property', JSON.stringify({ name: 'Mast', prop: 'ShapeColor' }))).toBe('Set color');
    expect(opLabel('boolean', JSON.stringify({ kind: 'cut' }))).toBe('Cut');
    expect(opLabel('create_extrusion')).toBe('Extrude');
    expect(opTargets(JSON.stringify({ base: 'A', tool: 'B', names: ['C', 'A'] }))).toEqual(['A', 'B', 'C']);
    expect(opTargets('not json')).toEqual([]);
  });
  it('times finished ops and marks running ones', () => {
    expect(opLine(op('o1', 3, 'create_sketch', { name: 'SK_Mast' }, 1000, 2900))).toMatchObject(
      { label: 'Create sketch', target: 'SK_Mast', durationMs: 1900, running: false });
    expect(opLine(op('o2', 3, 'extrude', { name: 'Mast' }, 1000, 0, 'running'))).toMatchObject({ running: true, durationMs: null });
    expect(opLine(op('o3', 3, 'extrude', {}, 1000, 1200, 'failed'))).toMatchObject({ failed: true, running: false });
  });
  it('formats durations', () => {
    expect(formatDuration(1900)).toBe('1.9 s');
    expect(formatDuration(400)).toBe('0.4 s');
    expect(formatDuration(12_300)).toBe('12 s');
    expect(formatDuration(65_000)).toBe('1 min 05 s');
  });
  it('attributes ops to steps by owner window, then by the objects they name', () => {
    const plan = parsePlan(V2);
    const tasks = [task('t1', 'done', 3, { createdMs: 0, updatedMs: 5000 }), task('t2', 'claimed', 3, { createdMs: 0, updatedMs: 5100 }),
      task('t3', 'open')];
    const ops = [
      op('a', 3, 'create_sketch', { name: 'SK_Mast' }, 1000, 2000),
      op('b', 3, 'revolve', { name: 'Mast' }, 2000, 4000),
      op('c', 3, 'create_sketch', { name: 'SK_Hub' }, 6000, 7000),
      op('d', 5, 'set_property', { name: 'Mast', prop: 'Radius' }, 8000, 8500), // a fix by someone else
      op('e', 9, 'interference', {}, 9000, 9100),                                // nobody's step
    ];
    const by = attributeOps(plan.steps, tasks, ops);
    expect(by.get('s01')!.map(o => o.id)).toEqual(['a', 'b', 'd']);
    expect(by.get('s02')!.map(o => o.id)).toEqual(['c']);
    const p = progress(plan, tasks, ops);
    expect(p.steps[0].opLines.map(o => `${o.label} ${o.target}`)).toEqual(['Create sketch SK_Mast', 'Revolve Mast', 'Set Radius Mast']);
  });
});

describe('the ?mock build', () => {
  it('streams a v2 plan with library and assembly steps, in dependency order, with timed ops', async () => {
    const { vi } = await import('vitest');
    const { Store } = await import('../src/store');
    const { startMockSwarm, MOCK_STEPS } = await import('../src/mock');
    vi.useFakeTimers();
    const store = new Store();
    const { stop } = startMockSwarm(store);
    try {
      const read = () => { const b = store.state.briefs[0]; return progress(parsePlan(b.planJson, store.state.tasks, b.id), store.state.tasks, store.state.ops); };
      let p = read();
      expect(p.total).toBe(MOCK_STEPS);
      expect(new Set(p.steps.map(s => s.kind))).toEqual(new Set(['part', 'library', 'assembly', 'check']));
      expect(p.done).toBeGreaterThan(0);
      expect(p.line).toMatch(/^\d+ of 16 done · now: /);
      // No step ever runs before its dependencies are done.
      for (let i = 0; i < 40; i++) {
        vi.advanceTimersByTime(2000);
        p = read();
        for (const s of p.steps.filter(x => x.state === 'current' || x.state === 'done')) {
          for (const d of s.dependsOn) expect(p.steps.find(x => x.id === d)?.state).toBe('done');
        }
      }
      expect(p.done).toBe(16);
      const timed = p.steps.flatMap(s => s.opLines).filter(o => o.durationMs != null);
      expect(timed.length).toBeGreaterThan(40);
      expect(p.steps.every(s => s.opLines.length > 0)).toBe(true);
      expect(p.steps[0].opLines[0]).toMatchObject({ label: 'Create sketch', target: 'SK_BasePlate' });
    } finally { stop(); vi.useRealTimers(); }
  });
});
