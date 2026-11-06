import { describe, expect, it } from 'vitest';
import { commands, freeName, helperFor, instruction, modeOf, rectPoints, RIBBON_TABS, searchCommands } from '../src/ui/commands';
import { toInterference, toRecommendations } from '../src/api';
import { hasIcon } from '../src/ui/icons';

const member = (id: number, name: string, kind = 'agent', status = 'active') =>
  ({ id, name, kind, client: 'claude', role: 'member', status, statusText: '', fence: 1, online: true });

describe('ribbon commands', () => {
  const all = commands();
  it('has every ribbon tab populated, unique ids and a drawn icon for each', () => {
    for (const [tab] of RIBBON_TABS) expect(all.some(c => c.tab === tab)).toBe(true);
    expect(new Set(all.map(c => c.id)).size).toBe(all.length);
    expect(all.filter(c => !hasIcon(c.icon)).map(c => c.id)).toEqual([]);
    for (const id of ['extrude', 'revolve', 'cut', 'fillet', 'chamfer', 'shell', 'hole', 'pattern', 'mirror', 'boolean',
      'measure', 'mass', 'interference', 'section', 'fea', 'printability', 'insert', 'mate', 'move', 'explode',
      'libsearch', 'standard', 'recommend', 'newbuild', 'plan', 'pause', 'stop', 'review']) {
      expect(all.find(c => c.id === id), id).toBeTruthy();
    }
  });
  it('runs a typed rectangle extrude on the host and a sketch extrude through a helper', () => {
    const ex = all.find(c => c.id === 'extrude')!;
    expect(modeOf(ex, { profile: 'rect' })).toBe('op');
    expect(modeOf(ex, { profile: 'sketch' })).toBe('ai');
    expect(modeOf(all.find(c => c.id === 'fillet')!, {})).toBe('ai');
    expect(modeOf(all.find(c => c.id === 'boolean')!, {})).toBe('op');
  });
  it('finds commands by name, word, group and tip', () => {
    expect(searchCommands(all, 'fil')[0].id).toBe('fillet');
    expect(searchCommands(all, 'section')[0].id).toBe('section');
    expect(searchCommands(all, 'bearing').length).toBeGreaterThanOrEqual(0);
    expect(searchCommands(all, '')).toEqual([]);
    expect(searchCommands(all, 'zzzz')).toEqual([]);
  });
});

describe('instructions', () => {
  it('names the bodies and every value, and asks for a one-line report', () => {
    expect(instruction('Fillet', ['Mast'], [['edges', 'top outside edges'], ['radius', '2 mm'], ['skip', '']]))
      .toBe('Fillet on Mast (edges: top outside edges; radius: 2 mm). Keep everything else as it is, then tell me in one line what you changed.');
    expect(instruction('Rebuild the model', [], [])).toMatch(/^Rebuild the model\. Keep/);
  });
  it('sends to the helper who built the body, else a free one', () => {
    const plan = JSON.stringify({ version: 2, steps: [{ n: 1, id: 's01', name: 'Mast', task: 't1', objects: ['Mast'] }] });
    const s = {
      members: [member(3, 'worker1'), member(4, 'worker2'), member(1, 'you', 'human')],
      tasks: [{ id: 't1', title: '1. Mast', status: 'done', owner: 4, assignee: '', brief: 'b', objects: ['Mast'], createdBy: 2 },
        { id: 't2', title: 'x', status: 'claimed', owner: 3, assignee: '', brief: 'b', objects: [], createdBy: 2 }],
      objects: [{ name: 'Lid', lastActor: 3 }] as never[],
      briefs: [{ id: 'b', by: 1, text: '', board: '', status: 'building', planJson: plan, stlHash: '', updatedMs: 1 }],
    };
    expect(helperFor(s, ['Mast'])?.name).toBe('worker2');
    expect(helperFor(s, ['Lid'])?.name).toBe('worker1');
    expect(helperFor(s, [])?.name).toBe('worker2'); // the free one
  });
  it('makes rectangle points and free names', () => {
    expect(rectPoints(1, 2, 10, 5)).toEqual([[1, 2], [11, 2], [11, 7], [1, 7]]);
    expect(freeName('Boss', ['Boss1', 'Boss2'])).toBe('Boss3');
  });
});

describe('host answers', () => {
  it('reads interference and recommendations of any shape', () => {
    expect(toInterference({ checked: ['A', 'B'], clashes: [{ a: 'A', b: 'B', volume: '2.5' }, null] }))
      .toEqual({ checked: ['A', 'B'], clashes: [{ a: 'A', b: 'B', volume: 2.5 }] });
    expect(toInterference(null)).toEqual({ checked: [], clashes: [] });
    expect(toRecommendations({ recommendations: [{ id: 'std:x', name: 'Screw', label: 'M3x10', family: 'iso4762', size: 'M3', length: 10, why: 'fits' }] }))
      .toEqual([{ id: 'std:x', name: 'M3x10', category: '', why: 'fits', family: 'iso4762', size: 'M3', length: 10, label: 'M3x10' }]);
    expect(toRecommendations({})).toEqual([]);
  });
});
