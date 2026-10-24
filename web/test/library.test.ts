import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  checkValue, effective, holeFor, libraryEntries, loadJson, parseCatalog, parseSpec, partName, partSuggestions, sizeLabel,
  toBase64,
} from '../src/ui/library-data';
import { startMockSwarm } from '../src/mock';
import { Store, type Component } from '../src/store';

// The same files the module and the Python side read (scripts/copy-boards.mjs copies them into public/).
const repo = resolve(__dirname, '../..');
const catalog = parseCatalog(JSON.parse(readFileSync(resolve(repo, 'cadswarm/library/standard_parts.json'), 'utf8')));
const spec = parseSpec(JSON.parse(readFileSync(resolve(repo, 'cadswarm/settings_spec.json'), 'utf8')));

const part = (over: Partial<Component> = {}): Component => ({
  id: 'c_mount_1', name: 'NEMA17 mount', category: 'motion', tags: ['motor'], description: '', source: 'upload',
  sourceUrl: '', license: '', author: 'carol', format: 'step', size: 1, bbox: [0, 0, 0, 42, 42, 5], volume: 1,
  featuresJson: '{}', suggestJson: '[]', uses: 0, updatedMs: 1, ...over,
});

describe('settings spec', () => {
  it('reads every setting with its default and overlays the team values', () => {
    expect(spec.map(s => s.key)).toContain('tol_press_fit');
    const v = effective(spec, [{ setting: 'tol_clearance', num: 0.3, text: '', setBy: 1, updatedMs: 1 },
      { setting: 'hole_clearance_class', num: 0, text: 'close', setBy: 1, updatedMs: 1 },
      { setting: 'gone', num: 1, text: '', setBy: 1, updatedMs: 1 }]);
    expect(v.tol_clearance).toBe(0.3);
    expect(v.hole_clearance_class).toBe('close');
    expect(v.min_wall).toBe(1.2);
    expect('gone' in v).toBe(false);
  });
  it('checks values the way the module will', () => {
    const tol = spec.find(s => s.key === 'tol_clearance')!;
    expect(checkValue(tol, '0,25')).toEqual({ num: 0.25, text: '' });
    expect(checkValue(tol, '9')).toHaveProperty('error');
    expect(checkValue(tol, '')).toHaveProperty('error');
    const cls = spec.find(s => s.key === 'hole_clearance_class')!;
    expect(checkValue(cls, 'loose')).toEqual({ num: 0, text: 'loose' });
    expect(checkValue(cls, 'snug')).toHaveProperty('error');
  });
  it('gives an empty list for a missing or broken file', async () => {
    expect(parseSpec(null)).toEqual([]);
    expect(await loadJson('./x.json', parseSpec, (async () => new Response('nope', { status: 404 })) as typeof fetch)).toEqual([]);
  });
});

describe('standard parts and holes', () => {
  it('reads the catalog the worker builds from', () => {
    const screw = catalog.families.find(f => f.id === 'iso4762')!;
    expect(screw.sizes.M3).toEqual({ d: 3, dk: 5.5, k: 3, s: 2.5 });
    expect(screw.lengths).toContain(12);
    expect(catalog.clearance.M3).toEqual([3.2, 3.4, 3.6]);
  });
  it('sizes holes from the team fits like standard.hole_for', () => {
    const values = effective(spec, []);
    const fam = (id: string) => catalog.families.find(f => f.id === id)!;
    expect(holeFor(catalog, fam('iso4762'), 'M3', values)?.diameter).toBe(3.4);
    expect(holeFor(catalog, fam('iso4762'), 'M3', { ...values, hole_clearance_class: 'close' })?.diameter).toBe(3.2);
    expect(holeFor(catalog, fam('ball_bearing'), '608', values)?.diameter).toBe(21.95);
    expect(holeFor(catalog, fam('heat_set_insert'), 'M3', values)?.diameter).toBe(4);
    expect(holeFor(catalog, fam('iso4762'), 'M7', values)).toBeNull();
  });
});

describe('library list', () => {
  it('lists team parts first, most used first, then standard families, filtered by words and kind', () => {
    const team = [part(), part({ id: 'c_b', name: 'Bracket', category: 'structural', uses: 3 })];
    const all = libraryEntries(team, catalog);
    expect(all.slice(0, 2).map(e => e.kind === 'team' && e.c.name)).toEqual(['Bracket', 'NEMA17 mount']);
    expect(all.length).toBe(2 + catalog.families.length);
    const bearings = libraryEntries(team, catalog, '608');
    expect(bearings.map(e => e.kind === 'std' && e.f.id)).toEqual(['ball_bearing']);
    expect(libraryEntries(team, catalog, '', 'motion').map(e => e.kind)).toEqual(['team']);
  });
  it('names placed parts like the hub does and skips taken names', () => {
    expect(partName('M3x10 socket head cap screw', [])).toBe('M3x10SocketHead1');
    expect(partName('NEMA17 mount', ['NEMA17Mount1'])).toBe('NEMA17Mount2');
    expect(partName('608 deep groove ball bearing', [])).toBe('P608DeepGroove1');
  });
  it('formats sizes and reads suggestions defensively', () => {
    expect(sizeLabel([0, 0, 0, 42, 42, 5])).toBe('42 × 42 × 5 mm');
    expect(sizeLabel([])).toBe('');
    expect(partSuggestions(part({ suggestJson: '[{"setting":"min_wall","value":2,"reason":"thin"}, {"x":1}]' })))
      .toEqual([{ setting: 'min_wall', value: 2, reason: 'thin' }]);
    expect(partSuggestions(part({ suggestJson: 'oops' }))).toEqual([]);
  });
  it('encodes uploads as base64 in chunks', () => {
    const big = new Uint8Array(100_000).map((_, i) => i % 251);
    expect(Buffer.from(toBase64(big), 'base64').equals(Buffer.from(big))).toBe(true);
  });
});

describe('mock library and settings', () => {
  it('places parts, accepts suggestions and resets settings without a server', async () => {
    const store = new Store();
    const { api, stop } = startMockSwarm(store);
    try {
      expect(store.state.components.length).toBe(2);
      expect(await api.placeComponent({ family: 'ball_bearing', size: '608', name: 'P608DeepGroove1' })).toBe(true);
      expect(store.state.objects.some(o => o.name === 'P608DeepGroove1')).toBe(true);
      await api.resolveSuggestion('g_mock1', true);
      expect(store.state.settings.find(s => s.setting === 'tol_press_fit')?.num).toBe(0);
      expect(store.state.suggestions[0].status).toBe('accepted');
      await api.resetSetting('tol_clearance');
      expect(store.state.settings.some(s => s.setting === 'tol_clearance')).toBe(false);
      expect(await api.componentPreview('c_nema17_mount_9d8b6b')).toBeInstanceOf(Uint8Array);
    } finally { stop(); }
  });
});
