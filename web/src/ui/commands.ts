// Every ribbon command, in one registry the ribbon, the command search, the phone overflow menu and the
// Properties panel read. A command runs a real host op where one exists (api.runOp, placeComponent,
// interference, printability, snapshot…), changes only this browser's view, or sends the AI helpers one
// precise instruction naming the selected bodies. The helpers at the top are pure and unit-tested.
import type { Api } from '../api';
import type { Member, State, Store } from '../store';
import { freeHelper } from './helpers';
import { parsePlan, stepOfObject } from '../steps';
import { view, type Axis, type DisplayStyle } from '../viewstate';
import { setTheme, theme } from '../theme';
import { esc } from './esc';

export type RibbonTab = 'features' | 'sketch' | 'evaluate' | 'assembly' | 'library' | 'ai' | 'view';
export const RIBBON_TABS: [RibbonTab, string][] = [['features', 'Features'], ['sketch', 'Sketch'], ['evaluate', 'Evaluate'],
  ['assembly', 'Assembly'], ['library', 'Library'], ['ai', 'AI Build'], ['view', 'View']];

/** op: a host op runs it · ai: an instruction for the helpers · view: this browser only · query: reads the model. */
export type Mode = 'op' | 'ai' | 'view' | 'query';
export const MODE_WORDS: Record<Mode, string> = {
  op: 'Runs on the host', ai: 'Sent to an AI helper', view: 'Your view only', query: 'Reads the live model',
};

export type Value = string | number | boolean;
export type Values = Record<string, Value>;

export interface Field {
  key: string; label: string;
  kind: 'number' | 'text' | 'textarea' | 'select' | 'body' | 'check' | 'range' | 'xyz';
  value?: Value; unit?: string; options?: [string, string][]; min?: number; max?: number; step?: number;
  hint?: string; group?: string;
  /** Only shown (and sent) when this returns true for the current values. */
  when?: (v: Values) => boolean;
}

export interface Ctx {
  store: Store; api: Api | null; selection: string[];
  notify(text: string): void;
  /** Left pane / task pane navigation. */
  openLeft(tab: 'tree' | 'props' | 'params' | 'library'): void;
  openTask(tab: 'build' | 'team' | 'review'): void;
  focusPrompt(): void;
  libraryQuery(q: string): void;
}

/** What a command's OK produced: HTML shown in the panel (it then stays open), or nothing (it closes). */
export interface Result { html: string }

export interface Command {
  id: string; label: string; tab: RibbonTab; group: string; icon: string; tip: string;
  mode: Mode | ((v: Values) => Mode);
  fields?: (ctx: Ctx) => Field[];
  run?: (ctx: Ctx, v: Values) => Promise<Result | void> | Result | void;
  /** Runs on every change while the panel is open (a live preview), and `cancel` undoes it. */
  live?: (ctx: Ctx, v: Values) => void;
  cancel?: (ctx: Ctx) => void;
  /** Disabled with this reason, or null. */
  disabled?: (ctx: Ctx) => string | null;
  /** Shown pressed (display style, section on…). */
  active?: () => boolean;
  small?: boolean;
  /** The OK button's words when it does not run an op ("Send to helpers"). */
  okLabel?: string;
  keys?: string;
}

// ---- pure helpers ----

const fmt = (v: Value, unit = '') => typeof v === 'boolean' ? (v ? 'yes' : 'no') : `${v}${unit && v !== '' ? ` ${unit}` : ''}`;

/**
 * One precise instruction: what to do, on which bodies, with every value the person typed, and what to
 * report. Helpers read this verbatim (it is also what shows in Chat).
 */
export function instruction(what: string, bodies: string[], details: [string, string][], note = ''): string {
  const on = bodies.length ? ` on ${bodies.join(', ')}` : '';
  const list = details.filter(([, v]) => v !== '' && v != null).map(([k, v]) => `${k}: ${v}`).join('; ');
  return `${what}${on}${list ? ` (${list})` : ''}.${note ? ` ${note}` : ''}` +
    ' Keep everything else as it is, then tell me in one line what you changed.';
}

/** The helper to send an instruction about `bodies` to: the AI helper who built them, else a free one. */
export function helperFor(s: Pick<State, 'members' | 'tasks' | 'objects' | 'briefs'>, bodies: string[]): Member | undefined {
  const live = (m?: Member) => m && m.kind === 'agent' && m.status === 'active' ? m : undefined;
  const brief = [...s.briefs].sort((a, b) => b.updatedMs - a.updatedMs)[0];
  const plan = brief ? parsePlan(brief.planJson, s.tasks as never, brief.id) : null;
  for (const b of bodies) {
    const step = plan ? stepOfObject(plan, b) : undefined;
    const owner = step ? s.tasks.find(t => t.id === step.task)?.owner : undefined;
    const m = live(s.members.find(x => x.id === owner))
      ?? live(s.members.find(x => x.id === s.objects.find(o => o.name === b)?.lastActor));
    if (m) return m;
  }
  return freeHelper(s as State);
}

/** Commands matching what was typed in the command search, best first. */
export function searchCommands<T extends Pick<Command, 'label' | 'tab' | 'group' | 'tip'>>(cmds: T[], query: string, limit = 12): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const score = (c: T) => {
    const label = c.label.toLowerCase();
    if (label === q) return 100;
    if (label.startsWith(q)) return 80;
    if (label.split(/[\s/]+/).some(w => w.startsWith(q))) return 60;
    if (label.includes(q)) return 40;
    if (`${c.group} ${c.tab}`.toLowerCase().includes(q)) return 20;
    if (c.tip.toLowerCase().includes(q)) return 10;
    return 0;
  };
  return cmds.map(c => [score(c), c] as const).filter(([s]) => s > 0).sort((a, b) => b[0] - a[0]).slice(0, limit).map(([, c]) => c);
}

/** A rectangle's corner points for create_extrusion. */
export function rectPoints(x: number, y: number, w: number, l: number): [number, number][] {
  return [[x, y], [x + w, y], [x + w, y + l], [x, y + l]];
}

/** "Boss1", "Boss2"… the first free name. */
export function freeName(base: string, taken: Iterable<string>): string {
  const set = new Set(taken);
  for (let i = 1; ; i++) if (!set.has(`${base}${i}`)) return `${base}${i}`;
}

// ---- running ----

const num = (v: Value | undefined, d = 0) => { const n = Number(v); return Number.isFinite(n) ? n : d; };
const str = (v: Value | undefined) => (v == null ? '' : String(v).trim());
const bodies = (v: Values, ...keys: string[]) => keys.map(k => str(v[k])).filter(Boolean);
const objNames = (s: State) => s.objects.filter(o => o.visible && o.name !== 'Params').map(o => o.name);

/** Send one instruction to the right helper as a task, the way Chat's @helper does. */
export async function sendToHelpers(ctx: Ctx, title: string, text: string, objects: string[], to?: string) {
  const { api, store } = ctx;
  if (!api) { ctx.notify('Not connected to a team yet.'); return; }
  const helper = to ? store.state.members.find(m => m.name === to) : helperFor(store.state, objects);
  if (!helper) { api.sendCommand('all', text); ctx.notify(`Sent to every helper: ${title}`); return; }
  try {
    const taskId = await api.createTask({ title, detail: text, objects, assignee: helper.name });
    api.sendCommand(helper.name, text, taskId);
  } catch {
    api.sendCommand(helper.name, text);
  }
  ctx.notify(`Sent to ${helper.name}: ${title}. Follow it in Team.`);
}

const sel = (ctx: Ctx, i = 0) => ctx.selection[i] ?? '';
const body = (key: string, label: string, ctx: Ctx, i = 0, group?: string): Field =>
  ({ key, label, kind: 'body', value: sel(ctx, i), group, hint: 'Pick in the 3D view or the feature tree' });
const mm = (key: string, label: string, value: number, group?: string, extra: Partial<Field> = {}): Field =>
  ({ key, label, kind: 'number', unit: 'mm', value, step: 0.5, group, ...extra });
const deg = (key: string, label: string, value: number, group?: string): Field =>
  ({ key, label, kind: 'number', unit: '°', value, step: 1, group });
const select = (key: string, label: string, options: [string, string][], value?: string, group?: string): Field =>
  ({ key, label, kind: 'select', options, value: value ?? options[0][0], group });
const text = (key: string, label: string, value = '', group?: string, hint?: string): Field => ({ key, label, kind: 'text', value, group, hint });

/** A command whose OK sends an instruction about the picked bodies. */
function aiCommand(c: Omit<Command, 'mode' | 'run'> & { verb: string; note?: string; bodyKeys?: string[] }): Command {
  return {
    ...c, mode: 'ai', okLabel: 'Send to helpers',
    run: async (ctx, v) => {
      const fields = c.fields?.(ctx) ?? [];
      const keys = c.bodyKeys ?? fields.filter(f => f.kind === 'body').map(f => f.key);
      const picked = bodies(v, ...keys);
      const details = fields.filter(f => f.kind !== 'body' && (!f.when || f.when(v)))
        .map(f => [f.label.toLowerCase(), f.kind === 'select' ? (f.options?.find(o => o[0] === v[f.key])?.[1] ?? str(v[f.key])) : fmt(v[f.key], f.unit)] as [string, string]);
      await sendToHelpers(ctx, `${c.label}${picked.length ? ` ${picked.join(', ')}` : ''}`, instruction(c.verb, picked, details, c.note), picked);
    },
  };
}

const PLANES: [string, string][] = [['xy', 'Top plane (XY)'], ['xz', 'Front plane (XZ)'], ['yz', 'Right plane (YZ)'], ['face', 'A face of the selected body']];
const STD: [string, string][] = [['iso4762', 'Socket head screw (ISO 4762)'], ['iso7380', 'Button head screw (ISO 7380)'],
  ['iso4032', 'Hex nut (ISO 4032)'], ['iso7089', 'Washer (ISO 7089)'], ['heat_set_insert', 'Heat-set insert'],
  ['ball_bearing', 'Ball bearing'], ['iso8734', 'Dowel pin (ISO 8734)'], ['magnet_disc', 'Disc magnet']];

function table(rows: [string, string][]): string {
  return `<table class="result-table">${rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</table>`;
}

/** Points collected by Measure, and the listener while it is active. */
let measurePts: [number, number, number][] = [];
let measureOff: (() => void) | null = null;

function sketchEntity(id: string, label: string, icon: string, dims: Field[], tip: string): Command {
  return aiCommand({ id, label, tab: 'sketch', group: 'Draw', icon, tip, small: false, verb: `In the sketch, draw a ${label.toLowerCase()}`,
    fields: ctx => [text('sketch', 'Sketch', ctx.selection[0] ? `SK_${ctx.selection[0]}` : 'SK_1', 'Sketch'), ...dims,
      text('where', 'Location', '', 'Placement', 'e.g. centred on the origin, 5 mm from the left edge')] });
}

export function commands(): Command[] {
  return [
    // ---------------- Features ----------------
    {
      id: 'extrude', label: 'Extrude', tab: 'features', group: 'Sketch-based', icon: 'extrude', keys: 'E',
      tip: 'Grow a profile into a solid. A typed rectangle runs on the host; a sketch or face goes to a helper.',
      mode: v => v.profile === 'rect' ? 'op' : 'ai',
      fields: ctx => [
        select('profile', 'Profile', [['rect', 'Rectangle (new body)'], ['sketch', 'Sketch or face of a body']], ctx.selection[0] ? 'sketch' : 'rect', 'Profile'),
        body('body', 'Body', ctx, 0, 'Profile'),
        { ...text('sketch', 'Sketch / face', '', 'Profile', 'e.g. SK_Hub, or “top face”'), when: v => v.profile !== 'rect' },
        { ...text('name', 'Name', freeName('Boss', objNames(ctx.store.state)), 'Profile'), when: v => v.profile === 'rect' },
        { ...mm('w', 'Width (X)', 40, 'Profile'), when: v => v.profile === 'rect' },
        { ...mm('l', 'Length (Y)', 20, 'Profile'), when: v => v.profile === 'rect' },
        { key: 'at', label: 'Corner at', kind: 'xyz', value: '0, 0, 0', unit: 'mm', group: 'Profile', when: v => v.profile === 'rect' },
        select('end', 'End condition', [['blind', 'Blind'], ['through', 'Through all'], ['mid', 'Mid plane']], 'blind', 'Direction 1'),
        mm('depth', 'Depth', 10, 'Direction 1'),
        { ...deg('draft', 'Draft', 0, 'Direction 1'), when: v => v.profile !== 'rect' },
        { key: 'merge', label: 'Merge result', kind: 'check', value: true, group: 'Direction 1', when: v => v.profile !== 'rect' },
      ],
      run: async (ctx, v) => {
        if (v.profile === 'rect') {
          const [x, y, z] = str(v.at).split(/[,\s]+/).map(Number);
          const depth = num(v.depth, 10);
          const zz = v.end === 'mid' ? (z || 0) - depth / 2 : z || 0;
          const ok = await ctx.api?.runOp('create_extrusion', { name: str(v.name), label: str(v.name),
            points: rectPoints(x || 0, y || 0, num(v.w, 40), num(v.l, 20)), height: depth, placement: { base: [0, 0, zz] } });
          if (ok) { ctx.notify(`Extruded ${str(v.name)}`); view.select([str(v.name)]); }
          return;
        }
        const picked = bodies(v, 'body');
        await sendToHelpers(ctx, `Extrude ${str(v.sketch) || picked.join(', ')}`, instruction('Extrude', picked, [
          ['profile', str(v.sketch) || 'the selected sketch'], ['end condition', str(v.end)], ['depth', fmt(v.depth, 'mm')],
          ['draft', fmt(v.draft, '°')], ['merge with the body', fmt(!!v.merge)]]), picked);
      },
    },
    aiCommand({ id: 'revolve', label: 'Revolve', tab: 'features', group: 'Sketch-based', icon: 'revolve', verb: 'Revolve a profile',
      tip: 'Sweep a sketch profile around an axis.',
      fields: ctx => [body('body', 'Body', ctx, 0, 'Profile'), text('sketch', 'Sketch', '', 'Profile', 'e.g. SK_Mast'),
        select('axis', 'Axis', [['z', 'Z axis'], ['x', 'X axis'], ['y', 'Y axis'], ['edge', 'A sketch line']], 'z', 'Axis'),
        deg('angle', 'Angle', 360, 'Axis')] }),
    {
      id: 'cut', label: 'Cut', tab: 'features', group: 'Sketch-based', icon: 'cut', tip: 'Remove material: subtract a body (runs on the host) or cut-extrude a sketch (a helper).',
      mode: v => v.how === 'body' ? 'op' : 'ai',
      fields: ctx => [
        select('how', 'Cut with', [['body', 'Another body (subtract)'], ['sketch', 'A sketch (cut-extrude)']], ctx.selection.length > 1 ? 'body' : 'sketch', 'Cut'),
        body('base', 'From body', ctx, 0, 'Cut'),
        { ...body('tool', 'Tool body', ctx, 1, 'Cut'), when: v => v.how === 'body' },
        { ...text('name', 'Result name', freeName('Cut', objNames(ctx.store.state)), 'Cut'), when: v => v.how === 'body' },
        { ...text('sketch', 'Sketch', '', 'Cut', 'e.g. SK_Slot'), when: v => v.how !== 'body' },
        { ...select('end', 'End condition', [['blind', 'Blind'], ['through', 'Through all']], 'through', 'Cut'), when: v => v.how !== 'body' },
        { ...mm('depth', 'Depth', 5, 'Cut'), when: v => v.how !== 'body' && v.end === 'blind' },
      ],
      run: async (ctx, v) => {
        if (v.how === 'body') {
          const [base, tool] = bodies(v, 'base', 'tool');
          if (!base || !tool) { ctx.notify('Pick the body to cut and the tool body.'); return; }
          const ok = await ctx.api?.runOp('boolean', { kind: 'cut', base, tool, name: str(v.name) }, [base, tool]);
          if (ok) ctx.notify(`Cut ${tool} from ${base}`);
          return;
        }
        const picked = bodies(v, 'base');
        await sendToHelpers(ctx, `Cut ${picked.join(', ')}`, instruction('Cut-extrude', picked, [['sketch', str(v.sketch) || 'the selected sketch'],
          ['end condition', str(v.end)], ...(v.end === 'blind' ? [['depth', fmt(v.depth, 'mm')] as [string, string]] : [])]), picked);
      },
    },
    aiCommand({ id: 'fillet', label: 'Fillet', tab: 'features', group: 'Dress-up', icon: 'fillet', verb: 'Fillet', keys: 'F',
      tip: 'Round edges with a constant radius.',
      fields: ctx => [body('body', 'Body', ctx, 0, 'Items'), text('edges', 'Edges', 'all outside edges', 'Items', 'which edges, in words'),
        mm('radius', 'Radius', 2, 'Fillet parameters')] }),
    aiCommand({ id: 'chamfer', label: 'Chamfer', tab: 'features', group: 'Dress-up', icon: 'chamfer', verb: 'Chamfer',
      tip: 'Bevel edges by a distance.',
      fields: ctx => [body('body', 'Body', ctx, 0, 'Items'), text('edges', 'Edges', 'top outside edges', 'Items'),
        mm('distance', 'Distance', 1, 'Chamfer parameters'), deg('angle', 'Angle', 45, 'Chamfer parameters')] }),
    aiCommand({ id: 'shell', label: 'Shell', tab: 'features', group: 'Dress-up', icon: 'shell', verb: 'Shell',
      tip: 'Hollow a body to a wall thickness, leaving chosen faces open.',
      fields: ctx => [body('body', 'Body', ctx, 0, 'Items'), text('faces', 'Faces to remove', 'top face', 'Items'),
        mm('thickness', 'Wall thickness', 2, 'Parameters')] }),
    aiCommand({ id: 'hole', label: 'Hole', tab: 'features', group: 'Holes', icon: 'hole', verb: 'Add holes',
      tip: 'Clearance, tapped, counterbored or countersunk holes sized from the team settings.',
      fields: ctx => [body('body', 'Body', ctx, 0, 'Position'), text('where', 'Positions', '4 holes, 5 mm in from each corner', 'Position'),
        select('type', 'Hole type', [['clearance', 'Clearance'], ['tapped', 'Tapped'], ['counterbore', 'Counterbore'], ['countersink', 'Countersink'], ['insert', 'Heat-set insert']], 'clearance', 'Hole spec'),
        select('size', 'Size', ['M2', 'M2.5', 'M3', 'M4', 'M5', 'M6', 'M8'].map(s => [s, s] as [string, string]), 'M3', 'Hole spec'),
        select('end', 'End condition', [['through', 'Through all'], ['blind', 'Blind']], 'through', 'Hole spec'),
        { ...mm('depth', 'Depth', 8, 'Hole spec'), when: v => v.end === 'blind' }] }),
    aiCommand({ id: 'pattern', label: 'Pattern', tab: 'features', group: 'Patterns', icon: 'pattern', verb: 'Pattern',
      tip: 'Repeat a feature or body in a line or around an axis.',
      fields: ctx => [body('body', 'Body / feature', ctx, 0, 'Items'),
        select('type', 'Type', [['circular', 'Circular'], ['linear', 'Linear']], 'circular', 'Pattern'),
        { key: 'count', label: 'Instances', kind: 'number', value: 4, step: 1, min: 2, group: 'Pattern' },
        { ...deg('angle', 'Total angle', 360, 'Pattern'), when: v => v.type === 'circular' },
        { ...select('axis', 'Axis', [['z', 'Z axis'], ['x', 'X axis'], ['y', 'Y axis']], 'z', 'Pattern'), when: v => v.type === 'circular' },
        { ...mm('spacing', 'Spacing', 20, 'Pattern'), when: v => v.type === 'linear' },
        { ...select('dir', 'Direction', [['x', 'X'], ['y', 'Y'], ['z', 'Z']], 'x', 'Pattern'), when: v => v.type === 'linear' }] }),
    aiCommand({ id: 'mirror', label: 'Mirror', tab: 'features', group: 'Patterns', icon: 'mirror', verb: 'Mirror',
      tip: 'Mirror a feature or body about a plane.',
      fields: ctx => [body('body', 'Body / feature', ctx, 0, 'Items'),
        select('plane', 'Mirror plane', [['yz', 'Right plane (YZ)'], ['xz', 'Front plane (XZ)'], ['xy', 'Top plane (XY)']], 'yz', 'Plane'),
        { key: 'copy', label: 'Keep the original', kind: 'check', value: true, group: 'Plane' }] }),
    {
      id: 'boolean', label: 'Boolean', tab: 'features', group: 'Bodies', icon: 'boolean', mode: 'op',
      tip: 'Combine two bodies: union, subtract or intersect. Runs on the host; both bodies are locked while it runs.',
      fields: ctx => [select('kind', 'Operation', [['fuse', 'Union'], ['cut', 'Subtract'], ['common', 'Intersect']], 'fuse', 'Bodies'),
        body('base', 'Main body', ctx, 0, 'Bodies'), body('tool', 'Tool body', ctx, 1, 'Bodies'),
        text('name', 'Result name', freeName('Combine', objNames(ctx.store.state)), 'Result')],
      run: async (ctx, v) => {
        const [base, tool] = bodies(v, 'base', 'tool');
        if (!base || !tool || base === tool) { ctx.notify('Pick two different bodies.'); return; }
        const ok = await ctx.api?.runOp('boolean', { kind: str(v.kind), base, tool, name: str(v.name) }, [base, tool]);
        if (ok) ctx.notify(`${str(v.name)} made from ${base} and ${tool}`);
      },
    },
    {
      id: 'box', label: 'Box', tab: 'features', group: 'Primitives', icon: 'box', mode: 'op', small: true,
      tip: 'A box body with typed sizes. Runs on the host.',
      fields: ctx => [text('name', 'Name', freeName('Box', objNames(ctx.store.state)), 'Size'),
        mm('Length', 'Length (X)', 20, 'Size'), mm('Width', 'Width (Y)', 20, 'Size'), mm('Height', 'Height (Z)', 10, 'Size'),
        { key: 'at', label: 'Position', kind: 'xyz', value: '0, 0, 0', unit: 'mm', group: 'Placement' }],
      run: async (ctx, v) => {
        const base = str(v.at).split(/[,\s]+/).map(Number).map(n => n || 0);
        const ok = await ctx.api?.runOp('create_primitive', { kind: 'box', name: str(v.name), label: str(v.name),
          props: { Length: num(v.Length, 20), Width: num(v.Width, 20), Height: num(v.Height, 10) }, placement: { base } });
        if (ok) view.select([str(v.name)]);
      },
    },
    {
      id: 'cylinder', label: 'Cylinder', tab: 'features', group: 'Primitives', icon: 'cylinder', mode: 'op', small: true,
      tip: 'A cylinder body with typed sizes. Runs on the host.',
      fields: ctx => [text('name', 'Name', freeName('Cylinder', objNames(ctx.store.state)), 'Size'),
        mm('Radius', 'Radius', 10, 'Size'), mm('Height', 'Height', 20, 'Size'),
        { key: 'at', label: 'Position', kind: 'xyz', value: '0, 0, 0', unit: 'mm', group: 'Placement' }],
      run: async (ctx, v) => {
        const base = str(v.at).split(/[,\s]+/).map(Number).map(n => n || 0);
        const ok = await ctx.api?.runOp('create_primitive', { kind: 'cylinder', name: str(v.name), label: str(v.name),
          props: { Radius: num(v.Radius, 10), Height: num(v.Height, 20) }, placement: { base } });
        if (ok) view.select([str(v.name)]);
      },
    },

    // ---------------- Sketch ----------------
    aiCommand({ id: 'sketch', label: 'Sketch', tab: 'sketch', group: 'Sketch', icon: 'sketch', verb: 'Start a sketch', keys: 'S',
      tip: 'Start a sketch on a plane or a face.',
      fields: ctx => [select('plane', 'Plane', PLANES, ctx.selection[0] ? 'face' : 'xy', 'Placement'), body('body', 'On body', ctx, 0, 'Placement'),
        text('name', 'Sketch name', ctx.selection[0] ? `SK_${ctx.selection[0]}` : 'SK_1', 'Placement'),
        { key: 'what', label: 'What to draw', kind: 'textarea', value: '', group: 'Content', hint: 'e.g. a 40 × 20 rectangle centred, with 3 mm corner radii' }] }),
    sketchEntity('line', 'Line', 'line', [mm('len', 'Length', 30, 'Size'), deg('ang', 'Angle', 0, 'Size')], 'A straight line segment.'),
    sketchEntity('rect', 'Rectangle', 'rect', [mm('w', 'Width', 40, 'Size'), mm('h', 'Height', 20, 'Size'), mm('r', 'Corner radius', 0, 'Size')], 'A rectangle by its sizes.'),
    sketchEntity('circle', 'Circle', 'circle', [mm('d', 'Diameter', 20, 'Size')], 'A circle by its diameter.'),
    sketchEntity('arc', 'Arc', 'arc', [mm('r', 'Radius', 15, 'Size'), deg('a', 'Sweep', 90, 'Size')], 'A three-point or centre arc.'),
    sketchEntity('slot', 'Slot', 'slot', [mm('len', 'Length', 30, 'Size'), mm('w', 'Width', 6, 'Size')], 'A straight slot.'),
    sketchEntity('polygon', 'Polygon', 'polygon', [{ key: 'sides', label: 'Sides', kind: 'number', value: 6, step: 1, group: 'Size' }, mm('d', 'Across flats', 10, 'Size')], 'A regular polygon.'),
    aiCommand({ id: 'trim', label: 'Trim', tab: 'sketch', group: 'Modify', icon: 'trim', small: true, verb: 'In the sketch, trim', tip: 'Trim sketch entities to where they meet.',
      fields: () => [text('sketch', 'Sketch', 'SK_1', 'Items'), text('what', 'Entities', 'the overhanging line ends', 'Items')] }),
    aiCommand({ id: 'offset', label: 'Offset', tab: 'sketch', group: 'Modify', icon: 'offset', small: true, verb: 'In the sketch, offset', tip: 'Offset sketch entities by a distance.',
      fields: () => [text('sketch', 'Sketch', 'SK_1', 'Items'), text('what', 'Entities', 'the outer profile', 'Items'), mm('d', 'Distance', 2, 'Parameters')] }),
    aiCommand({ id: 'dimension', label: 'Dimension', tab: 'sketch', group: 'Constrain', icon: 'dimension', verb: 'In the sketch, dimension', keys: 'D',
      tip: 'Drive a sketch entity by a size; it can reference a shared parameter.',
      fields: () => [text('sketch', 'Sketch', 'SK_1', 'Items'), text('what', 'Entity', 'the outer width', 'Items'),
        text('value', 'Value', '40 mm', 'Value', 'a number, or a parameter like =hub_d')] }),
    aiCommand({ id: 'constraint', label: 'Constraint', tab: 'sketch', group: 'Constrain', icon: 'constraint', verb: 'In the sketch, constrain',
      tip: 'Add a geometric relation between sketch entities.',
      fields: () => [text('sketch', 'Sketch', 'SK_1', 'Items'),
        select('type', 'Relation', [['coincident', 'Coincident'], ['horizontal', 'Horizontal'], ['vertical', 'Vertical'], ['tangent', 'Tangent'],
          ['equal', 'Equal'], ['parallel', 'Parallel'], ['perpendicular', 'Perpendicular'], ['symmetric', 'Symmetric'], ['fix', 'Fix']], 'coincident', 'Relation'),
        text('what', 'Entities', '', 'Relation')] }),
    aiCommand({ id: 'finish', label: 'Finish sketch', tab: 'sketch', group: 'Exit', icon: 'finish', verb: 'Close the sketch and make sure it is fully constrained',
      tip: 'Close the sketch; it must be fully constrained.', fields: () => [text('sketch', 'Sketch', 'SK_1', 'Sketch')] }),

    // ---------------- Evaluate ----------------
    {
      id: 'measure', label: 'Measure', tab: 'evaluate', group: 'Measure', icon: 'measure', mode: 'view', keys: 'M',
      tip: 'Click two points in the 3D view to measure the distance between them.',
      fields: () => [{ key: 'info', label: 'Click two points on the model', kind: 'text', value: '', group: 'Selections', hint: 'distances are straight-line, in mm' }],
      live: ctx => {
        if (measureOff) return;
        measurePts = [];
        view.set({ measuring: true });
        const h = (e: Event) => {
          const p = (e as CustomEvent<{ point: [number, number, number] | null }>).detail.point;
          if (!p) return;
          measurePts = measurePts.length >= 2 ? [p] : [...measurePts, p];
          dispatchEvent(new CustomEvent('cadswarm:measure', { detail: measureResult() }));
          void ctx;
        };
        addEventListener('cadswarm:pick', h);
        measureOff = () => { removeEventListener('cadswarm:pick', h); measureOff = null; view.set({ measuring: false }); };
      },
      cancel: () => measureOff?.(),
      run: () => { const html = measureResult(); return html ? { html } : undefined; },
      okLabel: 'Done',
    },
    {
      id: 'mass', label: 'Mass properties', tab: 'evaluate', group: 'Measure', icon: 'mass', mode: 'query',
      tip: 'Volume, size, centre and mass of the selected bodies (all bodies when none is picked).',
      fields: ctx => [{ key: 'bodies', label: 'Bodies', kind: 'text', value: ctx.selection.join(', '), group: 'Selection', hint: 'empty = whole model' },
        select('rho', 'Material', [['1.24', 'PLA (1.24 g/cm³)'], ['1.27', 'PETG (1.27 g/cm³)'], ['1.04', 'ABS (1.04 g/cm³)'],
          ['2.70', 'Aluminium (2.70 g/cm³)'], ['7.85', 'Steel (7.85 g/cm³)']], '1.24', 'Material')],
      run: (ctx, v) => {
        const names = str(v.bodies).split(/[,\s]+/).filter(Boolean);
        const objs = ctx.store.state.objects.filter(o => o.visible && (!names.length || names.includes(o.name)));
        if (!objs.length) return { html: '<p class="muted">Nothing to measure yet.</p>' };
        const vol = objs.reduce((n, o) => n + o.volume, 0);
        const b = [0, 1, 2].map(k => Math.min(...objs.map(o => o.bbox[k]))).concat([0, 1, 2].map(k => Math.max(...objs.map(o => o.bbox[k + 3]))));
        const f = (n: number) => (Math.round(n * 10) / 10).toLocaleString();
        return { html: table([['Bodies', String(objs.length)], ['Volume', `${f(vol / 1000)} cm³`],
          ['Mass', `${f(vol / 1000 * num(v.rho, 1.24))} g`], ['Bounding box', `${f(b[3] - b[0])} × ${f(b[4] - b[1])} × ${f(b[5] - b[2])} mm`],
          ['Box centre', `${f((b[0] + b[3]) / 2)}, ${f((b[1] + b[4]) / 2)}, ${f((b[2] + b[5]) / 2)} mm`]]) };
      },
      okLabel: 'Calculate',
    },
    {
      id: 'interference', label: 'Interference', tab: 'evaluate', group: 'Check', icon: 'interference', mode: 'query',
      tip: 'Find solids that overlap. Runs on the host.',
      fields: ctx => [{ key: 'bodies', label: 'Bodies', kind: 'text', value: ctx.selection.length > 1 ? ctx.selection.join(', ') : '', group: 'Selection', hint: 'empty = every visible body' }],
      run: async (ctx, v) => {
        const r = await ctx.api?.interference(str(v.bodies).split(/[,\s]+/).filter(Boolean));
        if (!r) return;
        if (r.clashes.length) view.select(r.clashes.flatMap(c => [c.a, c.b]));
        return { html: r.clashes.length
          ? `<p class="result-head warn">${r.clashes.length} interference${r.clashes.length > 1 ? 's' : ''}</p>` + table(r.clashes.map(c => [`${c.a} ↔ ${c.b}`, `${c.volume} mm³`]))
          : `<p class="result-head ok">No interference between ${r.checked.length} bodies.</p>` };
      },
      okLabel: 'Calculate',
    },
    {
      id: 'section', label: 'Section view', tab: 'evaluate', group: 'Check', icon: 'section', mode: 'view',
      tip: 'Cut the view with a plane to look inside. Changes only your view.', active: () => view.get().section.on,
      fields: () => {
        const s = view.get().section;
        return [select('axis', 'Plane', [['z', 'Top plane (Z)'], ['y', 'Front plane (Y)'], ['x', 'Right plane (X)']], s.axis, 'Section plane'),
          { key: 'at', label: 'Offset', kind: 'range', value: Math.round(s.at * 100), min: 0, max: 100, step: 1, unit: '%', group: 'Section plane' },
          { key: 'flip', label: 'Flip side', kind: 'check', value: s.flip, group: 'Section plane' }];
      },
      live: (_ctx, v) => view.set({ section: { on: true, axis: (str(v.axis) || 'z') as Axis, at: num(v.at, 50) / 100, flip: !!v.flip } }),
      cancel: () => view.set({ section: { ...view.get().section, on: false } }),
    },
    aiCommand({ id: 'fea', label: 'Simulation', tab: 'evaluate', group: 'Analyse', icon: 'fea', verb: 'Run a static stress analysis (FEA) and publish the report',
      tip: 'Static stress on a body: a helper sets fixtures and loads and runs the solver on the host.',
      fields: ctx => [body('body', 'Body', ctx, 0, 'Study'),
        select('material', 'Material', [['PLA', 'PLA'], ['PETG', 'PETG'], ['ABS', 'ABS'], ['Aluminium', 'Aluminium 6061'], ['Steel', 'Steel']], 'PETG', 'Study'),
        text('fixed', 'Fixed faces', 'the bottom face', 'Fixtures'), text('load', 'Load', '50 N downward on the top face', 'Loads'),
        { key: 'sf', label: 'Target safety factor', kind: 'number', value: 2, step: 0.5, group: 'Loads' }] }),
    {
      id: 'printability', label: 'Printability', tab: 'evaluate', group: 'Analyse', icon: 'print', mode: 'query',
      tip: 'Do the parts fit the bed, are they closed solids, are any walls too thin? Runs on the host.',
      fields: ctx => {
        const w = ctx.store.state.workspace;
        return [mm('x', 'Bed X', w?.bedXMm ?? 180, 'Printer bed'), mm('y', 'Bed Y', w?.bedYMm ?? 180, 'Printer bed'), mm('z', 'Bed Z', w?.bedZMm ?? 180, 'Printer bed')];
      },
      run: async (ctx, v) => {
        const r = await ctx.api?.checkPrintability([num(v.x, 180), num(v.y, 180), num(v.z, 180)]);
        if (!r) return;
        const bad = r.objects.filter(o => !o.fitsBed || !o.closedSolid || o.thinWalls.length);
        return { html: `<p class="result-head ${r.ok ? 'ok' : 'warn'}">${r.ok ? `All ${r.objects.length} parts print` : `${bad.length} part${bad.length === 1 ? '' : 's'} need attention`}</p>`
          + (bad.length ? table(bad.map(o => [o.name, [!o.fitsBed && 'too big for the bed', !o.closedSolid && 'not a closed solid',
            o.thinWalls.length && `thin walls ${o.thinWalls.map(t => t.toFixed(1)).join(', ')} mm`].filter(Boolean).join(' · ')])) : '') };
      },
      okLabel: 'Check',
    },

    // ---------------- Assembly ----------------
    {
      id: 'insert', label: 'Insert component', tab: 'assembly', group: 'Components', icon: 'insert', mode: 'op',
      tip: 'Place a part from the team library into the model. Runs on the host.',
      fields: ctx => {
        const comps = ctx.store.state.components;
        return [select('component', 'Component', comps.length ? comps.map(c => [c.id, c.name] as [string, string]) : [['', 'The library is empty']], comps[0]?.id ?? '', 'Part'),
          text('name', 'Name in the model', freeName('Component', objNames(ctx.store.state)), 'Part')];
      },
      run: async (ctx, v) => {
        if (!str(v.component)) { ctx.notify('Add a part to the library first.'); ctx.openLeft('library'); return; }
        const ok = await ctx.api?.placeComponent({ component: str(v.component), name: str(v.name) });
        if (ok) { ctx.notify(`Inserted ${str(v.name)}`); view.select([str(v.name)]); }
      },
    },
    aiCommand({ id: 'mate', label: 'Mate', tab: 'assembly', group: 'Components', icon: 'mate', verb: 'Add a mate', keys: 'Shift+M',
      tip: 'Relate two components: coincident, concentric, distance, angle, revolute, slider, fixed.',
      fields: ctx => [select('type', 'Mate type', [['coincident', 'Coincident'], ['concentric', 'Concentric'], ['distance', 'Distance'],
        ['angle', 'Angle'], ['parallel', 'Parallel'], ['revolute', 'Revolute (hinge)'], ['slider', 'Slider'], ['fixed', 'Fixed']], 'concentric', 'Mate'),
        body('a', 'Component A', ctx, 0, 'Entities'), text('fa', 'Feature of A', 'axis', 'Entities'),
        body('b', 'Component B', ctx, 1, 'Entities'), text('fb', 'Feature of B', 'bore', 'Entities'),
        { ...mm('d', 'Distance', 0, 'Mate'), when: v => v.type === 'distance' },
        { ...deg('ang', 'Angle', 90, 'Mate'), when: v => v.type === 'angle' }],
      note: 'Place the parts with the host so the mate holds; exactly one component stays fixed.' }),
    {
      id: 'move', label: 'Move', tab: 'assembly', group: 'Components', icon: 'move', mode: 'op',
      tip: 'Set a body’s placement (position and rotation). Runs on the host and locks the body while it moves.',
      fields: ctx => {
        const o = ctx.store.state.objects.find(x => x.name === ctx.selection[0]);
        return [body('body', 'Body', ctx, 0, 'Body'),
          { key: 'at', label: 'Position', kind: 'xyz', value: o ? o.bbox.slice(0, 3).map(n => Math.round(n * 10) / 10).join(', ') : '0, 0, 0', unit: 'mm', group: 'Placement' },
          select('axis', 'Rotate about', [['0,0,1', 'Z axis'], ['1,0,0', 'X axis'], ['0,1,0', 'Y axis']], '0,0,1', 'Placement'), deg('angle', 'Angle', 0, 'Placement')];
      },
      run: async (ctx, v) => {
        const name = str(v.body);
        if (!name) { ctx.notify('Pick the body to move.'); return; }
        const base = str(v.at).split(/[,\s]+/).map(Number).map(n => n || 0);
        const ok = await ctx.api?.runOp('set_placement', { name, placement: { base, rotation: { axis: str(v.axis).split(',').map(Number), angle: num(v.angle) } } }, [name]);
        if (ok) ctx.notify(`Moved ${name}`);
      },
    },
    {
      id: 'explode', label: 'Explode', tab: 'assembly', group: 'Views', icon: 'explode', mode: 'view',
      tip: 'Pull the components apart to see how they go together. Changes only your view.', active: () => view.get().explode > 0,
      fields: () => [{ key: 'f', label: 'Distance', kind: 'range', value: Math.round(view.get().explode * 100) || 60, min: 0, max: 100, step: 1, unit: '%', group: 'Explode' }],
      live: (_c, v) => {
        const was = view.get().explode;
        view.set({ explode: num(v.f) / 100 });
        if (!was && num(v.f)) setTimeout(() => view.look('fit'), 60); // frame the spread-out assembly
      },
      cancel: () => view.set({ explode: 0 }),
    },

    // ---------------- Library ----------------
    {
      id: 'libsearch', label: 'Search library', tab: 'library', group: 'Find', icon: 'search', mode: 'view',
      tip: 'Search the team library and the standard parts.',
      fields: () => [text('q', 'Search for', '', 'Search', 'bearing, M3 screw, servo…')],
      run: (ctx, v) => { ctx.libraryQuery(str(v.q)); },
      okLabel: 'Search',
    },
    {
      id: 'recommend', label: 'Recommend', tab: 'library', group: 'Find', icon: 'recommend', mode: 'query',
      tip: 'Describe what a part must do; the library suggests what fits. Runs on the host.',
      fields: () => [{ key: 'need', label: 'What it must do', kind: 'textarea', value: 'hold the servo to a 6 mm plate (M3)', group: 'Need' }],
      run: async (ctx, v) => {
        const recs = await ctx.api?.recommend(str(v.need));
        if (!recs) return;
        return { html: recs.length ? `<ul class="rec-list">${recs.map(r => `<li><b>${esc(r.name)}</b><span>${esc(r.why)}</span>
            ${r.family ? `<button type="button" class="mini-btn" data-place-std="${esc(r.family)}" data-size="${esc(r.size ?? '')}" data-length="${r.length ?? ''}">Insert</button>`
              : r.id.startsWith('c_') ? `<button type="button" class="mini-btn" data-place-comp="${esc(r.id)}">Insert</button>` : ''}</li>`).join('')}</ul>`
          : '<p class="muted">Nothing in the library fits. Model it, or add a STEP file to the library.</p>' };
      },
      okLabel: 'Recommend',
    },
    {
      id: 'standard', label: 'Standard parts', tab: 'library', group: 'Place', icon: 'screw', mode: 'op',
      tip: 'Screws, nuts, washers, inserts, bearings, pins and magnets, generated to standard sizes. Runs on the host.',
      fields: ctx => [select('family', 'Family', STD, 'iso4762', 'Part'),
        text('size', 'Size', 'M3', 'Part', 'M3, 608, 6x3…'), { ...mm('length', 'Length', 10, 'Part'), when: v => /^iso(4762|7380|8734)$/.test(str(v.family)) },
        text('name', 'Name in the model', freeName('Std', objNames(ctx.store.state)), 'Part')],
      run: async (ctx, v) => {
        const ok = await ctx.api?.placeComponent({ family: str(v.family), size: str(v.size), name: str(v.name),
          ...(/^iso(4762|7380|8734)$/.test(str(v.family)) ? { length: num(v.length, 10) } : {}) });
        if (ok) { ctx.notify(`Placed ${str(v.name)}`); view.select([str(v.name)]); }
      },
    },
    {
      id: 'libtab', label: 'Browse library', tab: 'library', group: 'Place', icon: 'library', mode: 'view',
      tip: 'Open the Library tab: team parts with thumbnails, uploads and links.',
      run: ctx => { ctx.openLeft('library'); },
    },

    // ---------------- AI Build ----------------
    { id: 'newbuild', label: 'New build', tab: 'ai', group: 'Plan', icon: 'spark', mode: 'ai', tip: 'Describe a part or mechanism; the team plans it step by step and builds it.',
      run: ctx => { ctx.openTask('build'); ctx.focusPrompt(); } },
    { id: 'plan', label: 'Plan', tab: 'ai', group: 'Plan', icon: 'plan', mode: 'view', tip: 'Show the numbered step plan and its progress.',
      run: ctx => { ctx.openTask('build'); } },
    aiCommand({ id: 'runnext', label: 'Run next', tab: 'ai', group: 'Run', icon: 'next', verb: 'Take the next ready step with next_step and build it completely (sketch, feature, colour, verify, report)',
      tip: 'Ask a free helper to take the next ready step.', fields: () => [] }),
    aiCommand({ id: 'runall', label: 'Run all ready', tab: 'ai', group: 'Run', icon: 'runall', verb: 'Every free helper: take one ready step each with next_step and build it',
      tip: 'Every free helper takes a ready step.', fields: () => [], bodyKeys: [] }),
    {
      id: 'pause', label: 'Pause', tab: 'ai', group: 'Run', icon: 'pause', mode: 'ai', okLabel: 'Send to helpers',
      tip: 'Helpers finish the op they are running, report, and wait.',
      run: ctx => { ctx.api?.sendCommand('all', 'Pause the build: finish the op you are running, report_result for your step, and take no new step until I say continue.'); ctx.notify('Asked every helper to pause.'); },
    },
    {
      id: 'stop', label: 'Stop build', tab: 'ai', group: 'Run', icon: 'stop', mode: 'op',
      tip: 'Cancel the current build; steps nobody started are cancelled with it.',
      disabled: ctx => activeBrief(ctx.store.state) ? null : 'No build is running',
      run: async ctx => {
        const b = activeBrief(ctx.store.state);
        if (!b || !confirm(`Stop “${b.text}”? Steps nobody started are cancelled.`)) return;
        await ctx.api?.cancelBrief(b.id);
      },
    },
    aiCommand({ id: 'replan', label: 'Re-plan', tab: 'ai', group: 'Steer', icon: 'replan', verb: 'Re-plan the build from a step: keep the done steps, revise only that step and the ones after it',
      tip: 'Ask the planner to revise the plan from a step on.',
      fields: ctx => [select('from', 'From step', stepOptions(ctx.store.state), undefined, 'Re-plan'),
        { key: 'why', label: 'Why', kind: 'textarea', value: '', group: 'Re-plan', hint: 'what should change' }] }),
    aiCommand({ id: 'assign', label: 'Assign', tab: 'ai', group: 'Steer', icon: 'assign', verb: 'Pin a step of the plan to a teammate (update the step assignee)',
      tip: 'Pin a step to a person or helper.',
      fields: ctx => [select('step', 'Step', stepOptions(ctx.store.state), undefined, 'Assign'),
        select('to', 'Teammate', ctx.store.state.members.filter(m => m.status === 'active' && m.kind !== 'host').map(m => [m.name, m.name] as [string, string]), undefined, 'Assign')] }),
    {
      id: 'review', label: 'Review', tab: 'ai', group: 'Steer', icon: 'review', mode: 'ai', okLabel: 'Send to reviewer',
      tip: 'Ask the reviewer to check the model now, or open the Review pane.',
      fields: () => [select('scope', 'Check', [['all', 'The whole model'], ['selection', 'The selected bodies']], 'all', 'Review'),
        text('focus', 'Look especially at', 'fit between moving parts', 'Review')],
      run: async (ctx, v) => {
        const picked = v.scope === 'selection' ? ctx.selection : [];
        const reviewer = ctx.store.state.members.find(m => m.kind === 'reviewer' && m.status === 'active');
        const msg = instruction('Review the model', picked, [['focus', str(v.focus)]], 'Report findings as a review round.');
        if (reviewer) await sendToHelpers(ctx, 'Review', msg, picked, reviewer.name); else await sendToHelpers(ctx, 'Review', msg, picked);
        ctx.openTask('review');
      },
    },

    // ---------------- View ----------------
    ...([['fit', 'Fit', 'fit', 'Zoom to fit the whole model (F)'], ['front', 'Front', 'front', 'Look along +Y'], ['top', 'Top', 'top', 'Look down the Z axis'],
      ['right', 'Right', 'right', 'Look along -X'], ['iso', 'Isometric', 'iso', 'The standard 3D view']] as const).map(([id, label, ic, tip]): Command =>
      ({ id: `view-${id}`, label, tab: 'view', group: 'Orientation', icon: ic, tip, mode: 'view', run: () => view.look(id) })),
    ...([['shaded', 'Shaded', 'Faces only'], ['edges', 'Shaded with edges', 'Faces and their edges'], ['wireframe', 'Wireframe', 'Edges only, faces ghosted']] as const)
      .map(([id, label, tip]): Command => ({ id: `display-${id}`, label, tab: 'view', group: 'Display style', icon: id, tip, mode: 'view',
        active: () => view.get().display === id, run: () => view.set({ display: id as DisplayStyle }) })),
    { id: 'hide', label: 'Hide / show', tab: 'view', group: 'Visibility', icon: 'eyeoff', mode: 'view', tip: 'Hide the selected bodies (or show them again). Your view only.',
      disabled: ctx => ctx.selection.length ? null : 'Select a body first', run: ctx => view.toggleHidden(ctx.selection) },
    { id: 'showall', label: 'Show all', tab: 'view', group: 'Visibility', icon: 'eye', mode: 'view', tip: 'Show every hidden body.',
      run: () => view.set({ hidden: new Set() }) },
    {
      id: 'follow', label: 'Follow', tab: 'view', group: 'Team', icon: 'follow', mode: 'view', tip: 'Follow a teammate: your camera goes where they work.',
      active: () => view.get().follow > 0,
      fields: ctx => [select('who', 'Teammate', ctx.store.state.members.filter(m => m.id !== ctx.store.state.meId && m.status === 'active' && m.kind !== 'host')
        .map(m => [String(m.id), m.name] as [string, string]), undefined, 'Follow')],
      run: (_c, v) => view.set({ follow: num(v.who) }), okLabel: 'Follow',
    },
    { id: 'theme', label: 'Dark theme', tab: 'view', group: 'Workspace', icon: 'moon', mode: 'view', tip: 'Switch between the light and dark workspace.',
      active: () => theme() === 'dark', run: () => setTheme(theme() === 'dark' ? 'light' : 'dark') },
    { id: 'taskpane', label: 'Task pane', tab: 'view', group: 'Workspace', icon: 'panel', mode: 'view', tip: 'Show or hide the task pane on the right.',
      active: () => !document.querySelector('.ws')?.classList.contains('task-collapsed'),
      run: () => { document.querySelector('.ws')?.classList.toggle('task-collapsed'); dispatchEvent(new Event('resize')); } },

    // ---------------- Quick access (title bar) ----------------
    { id: 'qa-new', label: 'New build brief', tab: 'ai', group: 'Quick access', icon: 'new', mode: 'ai', tip: 'Start a new build from a description',
      run: ctx => { ctx.openTask('build'); ctx.focusPrompt(); } },
    { id: 'qa-save', label: 'Save snapshot', tab: 'view', group: 'Quick access', icon: 'save', mode: 'op', tip: 'Save a dated copy of the model on the host',
      fields: () => [text('label', 'Label', 'checkpoint', 'Snapshot', 'kept in the host’s snapshots folder')],
      run: async (ctx, v) => { if (await ctx.api?.snapshot(str(v.label))) ctx.notify(`Snapshot “${str(v.label)}” saved on the host.`); } },
    aiCommand({ id: 'qa-undo', label: 'Undo', tab: 'view', group: 'Quick access', icon: 'undo', verb: 'Undo the last change you made to the model (revert your most recent op)',
      tip: 'The model is shared, so undo asks the helper who made the last change to revert it',
      fields: ctx => [body('body', 'Body', ctx, 0, 'Undo')], note: 'Only revert your own change; if a teammate changed it since, ask them first.' }),
    aiCommand({ id: 'qa-redo', label: 'Redo', tab: 'view', group: 'Quick access', icon: 'redo', verb: 'Redo the change you just reverted',
      tip: 'Ask the helper to re-apply the change it reverted', fields: ctx => [body('body', 'Body', ctx, 0, 'Redo')] }),
    aiCommand({ id: 'qa-rebuild', label: 'Rebuild', tab: 'view', group: 'Quick access', icon: 'rebuild', keys: 'Ctrl+B',
      verb: 'Recompute the whole model and fix any feature that fails to rebuild', tip: 'Recompute the model and fix broken features',
      fields: () => [], bodyKeys: [] }),
  ];
}

function measureResult(): string {
  if (measurePts.length < 2) return measurePts.length ? '<p class="muted">First point picked. Click the second point.</p>' : '';
  const [a, b] = measurePts;
  const d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const f = (n: number) => (Math.round(n * 100) / 100).toFixed(2);
  return table([['Distance', `${f(Math.hypot(...d))} mm`], ['ΔX', `${f(d[0])} mm`], ['ΔY', `${f(d[1])} mm`], ['ΔZ', `${f(d[2])} mm`]]);
}

function activeBrief(s: State) {
  return [...s.briefs].sort((a, b) => b.updatedMs - a.updatedMs).find(b => ['new', 'planning', 'building', 'reviewing'].includes(b.status));
}

function stepOptions(s: State): [string, string][] {
  const b = [...s.briefs].sort((x, y) => y.updatedMs - x.updatedMs)[0];
  const steps = b ? parsePlan(b.planJson, s.tasks, b.id).steps : [];
  return steps.length ? steps.map(st => [`${st.n}. ${st.name}`, `${st.n}. ${st.name}`]) : [['', 'No plan yet']];
}

export const modeOf = (c: Command, v: Values): Mode => typeof c.mode === 'function' ? c.mode(v) : c.mode;
