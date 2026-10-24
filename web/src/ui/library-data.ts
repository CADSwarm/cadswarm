// Pure helpers for the Library and Settings tabs: the standard-parts catalog and the settings spec
// (both copied into public/ from ../cadswarm by scripts/copy-boards.mjs), search, hole sizes, part names.
// Mirrors cadswarm/library/standard.py and cadswarm/settings.py closely enough for the phone UI; the
// host and the module stay the authority (they re-check every value).
import type { Component, Setting } from '../store';

export interface StdFamily {
  id: string; name: string; standard: string; category: string; shape: string; tags: string[];
  params: string[]; sizes: Record<string, Record<string, number>>; lengths: number[];
}
export interface Catalog { families: StdFamily[]; clearance: Record<string, number[]>; tapDrill: Record<string, number>; selfTap: Record<string, number> }
export interface SettingSpec {
  key: string; group: string; label: string; kind: 'num' | 'choice'; unit: string; default: number | string;
  min?: number; max?: number; step?: number; choices?: string[]; param?: boolean; help?: string;
}
export type Values = Record<string, number | string>;

const obj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {});

export function parseCatalog(json: unknown): Catalog {
  const root = obj(json);
  const fams = obj(root.families);
  const nums = (v: unknown) => Object.fromEntries(Object.entries(obj(v)).filter(([k, x]) => !k.startsWith('_') && typeof x === 'number')) as Record<string, number>;
  return {
    families: Object.entries(fams).map(([id, f]) => {
      const o = obj(f);
      return {
        id, name: String(o.name ?? id), standard: String(o.standard ?? ''), category: String(o.category ?? 'other'),
        shape: String(o.shape ?? ''), tags: Array.isArray(o.tags) ? o.tags.map(String) : [],
        params: Array.isArray(o.params) ? o.params.map(String) : ['size'],
        sizes: obj(o.sizes) as Record<string, Record<string, number>>,
        lengths: Array.isArray(o.lengths) ? o.lengths.filter((x): x is number => typeof x === 'number') : [],
      };
    }),
    clearance: Object.fromEntries(Object.entries(obj(root.clearance_holes)).filter(([k, v]) => !k.startsWith('_') && Array.isArray(v))) as Record<string, number[]>,
    tapDrill: nums(root.tap_drill),
    selfTap: nums(root.self_tapping_hole),
  };
}

export function parseSpec(json: unknown): SettingSpec[] {
  const list = obj(json).settings;
  return Array.isArray(list) ? list.filter(s => obj(s).key).map(s => s as SettingSpec) : [];
}

/** Every setting's current value: defaults overlaid with the team's rows. */
export function effective(spec: SettingSpec[], rows: Setting[]): Values {
  const out: Values = Object.fromEntries(spec.map(s => [s.key, s.default]));
  for (const r of rows) {
    const s = spec.find(x => x.key === r.setting);
    if (s) out[s.key] = s.kind === 'choice' ? r.text : r.num;
  }
  return out;
}

/** {num, text} for set_setting / suggest_setting, or an error to show next to the field. */
export function checkValue(s: SettingSpec, raw: string): { num: number; text: string } | { error: string } {
  if (s.kind === 'choice') return s.choices?.includes(raw) ? { num: 0, text: raw } : { error: `Pick one of ${s.choices?.join(', ')}` };
  const v = Number(raw.replace(',', '.').trim());
  if (!Number.isFinite(v) || raw.trim() === '') return { error: 'Type a number' };
  if (s.min != null && v < s.min) return { error: `At least ${s.min} ${s.unit}` };
  if (s.max != null && v > s.max) return { error: `At most ${s.max} ${s.unit}` };
  return { num: v, text: '' };
}

const CLASS = { close: 0, normal: 1, loose: 2 } as const;

/** The hole a mating part needs for a standard part with the team's fits (standard.py hole_for). */
export function holeFor(cat: Catalog, fam: StdFamily, size: string, values: Values): { diameter: number; why: string } | null {
  const d = fam.sizes[size];
  if (!d) return null;
  if (fam.shape === 'insert') return { diameter: d.hole_d, why: `${size} insert hole, ${d.hole_depth} mm deep` };
  if (fam.shape === 'bearing' || fam.shape === 'disc' || fam.shape === 'pin') {
    const fitKey = fam.shape === 'pin' ? 'tol_slip_fit' : 'tol_press_fit';
    const od = d.od ?? d.d;
    const fit = Number(values[fitKey] ?? 0);
    return { diameter: Math.round((od + fit) * 1000) / 1000, why: `${od} mm + ${fitKey} ${fit >= 0 ? '+' : ''}${fit} mm` };
  }
  const row = cat.clearance[size];
  if (!row) return null;
  const cls = String(values.hole_clearance_class ?? 'normal') as keyof typeof CLASS;
  return { diameter: row[CLASS[cls] ?? 1], why: `ISO 273 ${cls} clearance hole` };
}

/** Library entries to list: the team's parts first (most used first), then standard families. */
export function libraryEntries(team: Component[], cat: Catalog, query = '', category = ''): Array<{ kind: 'team'; c: Component } | { kind: 'std'; f: StdFamily }> {
  const words = query.toLowerCase().split(/[^a-z0-9.]+/).filter(Boolean);
  const hit = (text: string) => words.every(w => text.includes(w));
  const teamOut = team
    .filter(c => (!category || c.category === category) && hit(`${c.name} ${c.tags.join(' ')} ${c.category} ${c.description}`.toLowerCase()))
    .sort((a, b) => b.uses - a.uses || a.name.localeCompare(b.name))
    .map(c => ({ kind: 'team' as const, c }));
  const stdOut = cat.families
    .filter(f => (!category || f.category === category) && hit(`${f.name} ${f.tags.join(' ')} ${f.category} ${f.standard} ${Object.keys(f.sizes).join(' ')}`.toLowerCase()))
    .map(f => ({ kind: 'std' as const, f }));
  return [...teamOut, ...stdOut];
}

/** A free FreeCAD object name for a placed part: 'M3x10 socket head cap screw' -> 'M3x10SocketHead1' (space_library.part_name). */
export function partName(label: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  const words = (label.match(/[A-Za-z0-9]+/g) ?? ['Part']).slice(0, 3);
  let base = words.map(w => w[0].toUpperCase() + w.slice(1)).join('').slice(0, 24);
  if (/^\d/.test(base)) base = 'P' + base;
  let n = 1;
  while (used.has(`${base}${n}`)) n++;
  return `${base}${n}`;
}

export function sizeLabel(bbox: number[]): string {
  if (bbox.length !== 6) return '';
  return [0, 1, 2].map(i => +(bbox[i + 3] - bbox[i]).toPrecision(4)).join(' × ') + ' mm';
}

/** Settings a library part implies (from its suggest_json), skipping malformed entries. */
export function partSuggestions(c: Component): Array<{ setting: string; value: number | string; reason: string }> {
  try {
    const v = JSON.parse(c.suggestJson || '[]');
    return Array.isArray(v) ? v.filter(s => s && typeof s.setting === 'string' && typeof s.reason === 'string') : [];
  } catch { return []; }
}

export async function loadJson<T>(path: string, parse: (j: unknown) => T, fetchFn: typeof fetch = fetch): Promise<T> {
  try {
    const r = await fetchFn(path, { cache: 'no-cache' });
    return parse(r.ok ? await r.json() : null);
  } catch { return parse(null); }
}

/** Base64 of a file's bytes, in chunks so big files don't blow the call stack. */
export function toBase64(bytes: Uint8Array): string {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
