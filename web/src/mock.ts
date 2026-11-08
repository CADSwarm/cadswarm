// A scripted team so the workspace, the step build and multiplayer can be built and demoed without a server.
// `?mock` runs it: the planner writes a version-2 step plan for a four-blade rotor head (parts, library
// placements, assemblies, checks), helpers and a teammate claim ready steps in dependency order and stream
// timed CAD ops, the reviewer sends one round back, then the STL is ready. The mock Api answers the panels
// and the ribbon commands locally.
import type { Api, Interference, Printability, Recommendation } from './api';
import type { Store, Obj, Task, Activity, Component, Member } from './store';
import { newId } from './ids';
import { saveBytes } from './download';
import { stripMentions } from './ui/mentions';

type Box6 = [number, number, number, number, number, number];

function boxStl(x0: number, y0: number, z0: number, x1: number, y1: number, z1: number): Uint8Array {
  const v = [[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],[x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]];
  const f = [[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]];
  return trisStl(f.map(t => t.map(k => v[k])));
}

/** A closed cylinder standing on z0 (or lying along x/y), as triangles. */
function cylStl(cx: number, cy: number, z0: number, r: number, h: number, seg = 40, axis: 'z' | 'x' | 'y' = 'z'): Uint8Array {
  const tris: number[][][] = [];
  const at = (a: number, z: number) => {
    const u = r * Math.cos(a), w = r * Math.sin(a);
    return axis === 'z' ? [cx + u, cy + w, z0 + z] : axis === 'x' ? [cx + z, cy + u, z0 + w] : [cx + u, cy + z, z0 + w];
  };
  const c0 = axis === 'z' ? [cx, cy, z0] : axis === 'x' ? [cx, cy, z0] : [cx, cy, z0];
  const c1 = axis === 'z' ? [cx, cy, z0 + h] : axis === 'x' ? [cx + h, cy, z0] : [cx, cy + h, z0];
  for (let i = 0; i < seg; i++) {
    const a = (i / seg) * Math.PI * 2, b = ((i + 1) / seg) * Math.PI * 2;
    const p0 = at(a, 0), p1 = at(b, 0), q0 = at(a, h), q1 = at(b, h);
    tris.push([p0, p1, q1], [p0, q1, q0], [c0, p1, p0], [c1, q0, q1]);
  }
  return trisStl(tris);
}

function trisStl(tris: number[][][]): Uint8Array {
  const buf = new ArrayBuffer(84 + 50 * tris.length);
  const dv = new DataView(buf);
  dv.setUint32(80, tris.length, true);
  tris.forEach((tri, i) => {
    let o = 84 + 50 * i + 12; // normals left zero; STLLoader recomputes
    for (const p of tri) for (const c of p) { dv.setFloat32(o, c, true); o += 4; }
  });
  return new Uint8Array(buf);
}

/** Concatenate binary STLs into one (the mock's "export"). */
export function mergeStl(meshes: Uint8Array[]): Uint8Array {
  const tris = meshes.reduce((n, m) => n + (m.length - 84) / 50, 0);
  const out = new Uint8Array(84 + 50 * tris);
  new DataView(out.buffer).setUint32(80, tris, true);
  let o = 84;
  for (const m of meshes) { out.set(m.subarray(84), o); o += m.length - 84; }
  return out;
}

function stlBox(m: Uint8Array): Box6 {
  const dv = new DataView(m.buffer, m.byteOffset, m.byteLength);
  const n = dv.getUint32(80, true);
  const b: Box6 = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
  for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) for (let c = 0; c < 3; c++) {
    const v = dv.getFloat32(84 + 50 * i + 12 + 12 * k + 4 * c, true);
    b[c] = Math.min(b[c], v); b[c + 3] = Math.max(b[c + 3], v);
  }
  return b.map(v => Math.round(v * 100) / 100) as Box6;
}

function moveStl(m: Uint8Array, d: number[]): Uint8Array {
  const out = m.slice();
  const dv = new DataView(out.buffer);
  const n = dv.getUint32(80, true);
  for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) for (let c = 0; c < 3; c++) {
    const o = 84 + 50 * i + 12 + 12 * k + 4 * c;
    dv.setFloat32(o, dv.getFloat32(o, true) + (d[c] ?? 0), true);
  }
  return out;
}

const DEMO_TEXT = 'A four-blade rotor head for a 450-size helicopter: mast on two bearings, swashplate driven by three servos, printable grips';
const STL_HASH = 'c0ffee'.repeat(10) + 'f00d';

// ---- the plan ----

/** One CAD op a step runs: what the feed shows, how long it takes, and the bodies it makes or touches. */
interface OpDef { kind: string; args: Record<string, unknown>; ms: number; makes?: string[] }
interface StepDef {
  n: number; id: string; name: string; kind: 'part' | 'library' | 'assembly' | 'check'; summary: string;
  objects: string[]; deps: string[]; assignee?: string; component?: Record<string, unknown>;
  params?: Record<string, number>; mates?: { type: string; a: string; b: string }[]; ops: OpDef[];
}

const sk = (name: string, ms = 1900): OpDef => ({ kind: 'create_sketch', args: { name: `SK_${name}` }, ms });
const feat = (kind: string, name: string, ms: number, makes?: string[]): OpDef => ({ kind, args: { name }, ms, makes });
const color = (name: string): OpDef => ({ kind: 'set_property', args: { name, prop: 'ShapeColor' }, ms: 400 });
const save = (name: string): OpDef => ({ kind: 'save_part', args: { name }, ms: 700 });
const mate = (type: string, a: string, b: string, ms = 1400): OpDef => ({ kind: 'mate', args: { type, a, b }, ms });
const range = (base: string, n: number) => Array.from({ length: n }, (_, i) => `${base}${i + 1}`);

const STEPS: StepDef[] = [
  { n: 1, id: 's01', name: 'Base plate', kind: 'part', objects: ['BasePlate'], deps: [], params: { thickness: 6 },
    summary: 'The base plate: a 140 × 110 mm, 6 mm aluminium-style plate with four M3 mounting holes; everything else stands on it.',
    ops: [sk('BasePlate'), feat('extrude', 'BasePlate', 2400, ['BasePlate']), feat('hole', 'BasePlate', 1700), feat('fillet', 'BasePlate', 1300), color('BasePlate'), save('BasePlate')] },
  { n: 2, id: 's02', name: 'Gearbox housing', kind: 'part', objects: ['Gearbox'], deps: ['s01'],
    summary: 'The gearbox housing: a 56 mm square shell bolted to the base, with the lower mast bearing seat on top.',
    ops: [sk('Gearbox'), feat('extrude', 'Gearbox', 2600, ['Gearbox']), feat('shell', 'Gearbox', 2100), feat('hole', 'Gearbox', 1500), color('Gearbox')] },
  { n: 3, id: 's03', name: 'Mast', kind: 'part', objects: ['Mast'], deps: ['s02'], params: { diameter: 10, length: 125 },
    summary: 'The mast: a 10 mm steel shaft rising 125 mm from the gearbox, with a thrust collar under the hub.',
    ops: [sk('Mast'), feat('revolve', 'Mast', 2700, ['Mast']), feat('chamfer', 'Mast', 1100), color('Mast'), save('Mast')] },
  { n: 4, id: 's04', name: 'Mast bearings', kind: 'library', objects: ['MastBearingLower', 'MastBearingUpper'], deps: ['s03'],
    component: { family: 'ball_bearing', size: '608' },
    summary: 'Two 608 deep-groove bearings from the library carry the mast in the gearbox and the upper bearing block.',
    ops: [{ kind: 'library_recommend', args: { need: '8 mm shaft bearing' }, ms: 900 },
      feat('insert_standard_part', 'MastBearingLower', 1100, ['MastBearingLower']),
      feat('insert_standard_part', 'MastBearingUpper', 1000, ['MastBearingUpper'])] },
  { n: 5, id: 's05', name: 'Swashplate', kind: 'part', objects: ['Swashplate'], deps: ['s03'],
    summary: 'The swashplate: a 92 mm disc sliding on the mast, with three servo balls and four link balls on its rim.',
    ops: [sk('Swashplate'), feat('revolve', 'Swashplate', 2500, ['Swashplate']), feat('hole', 'Swashplate', 1600), feat('pattern', 'Swashplate', 1700), color('Swashplate')] },
  { n: 6, id: 's06', name: 'Servos', kind: 'library', objects: range('Servo', 3), deps: ['s01'], assignee: 'maya',
    component: { id: 'c_servo_mg996r' },
    summary: 'Three standard servos from the team library stand on the base at 120° around the gearbox.',
    ops: [{ kind: 'library_search', args: { query: 'standard servo' }, ms: 800 },
      feat('insert_component', 'Servo1', 1300, ['Servo1']), feat('insert_component', 'Servo2', 1200, ['Servo2']),
      feat('insert_component', 'Servo3', 1200, ['Servo3'])] },
  { n: 7, id: 's07', name: 'Servo screws', kind: 'library', objects: range('ServoScrew', 6), deps: ['s06'], assignee: 'maya',
    component: { family: 'iso4762', size: 'M3', length: 10 },
    summary: 'Six M3 × 10 socket head screws hold the servo flanges to the base plate.',
    ops: [feat('insert_standard_part', 'ServoScrew1', 1000, ['ServoScrew1', 'ServoScrew2']),
      feat('pattern', 'ServoScrew1', 1500, ['ServoScrew3', 'ServoScrew4', 'ServoScrew5', 'ServoScrew6'])] },
  { n: 8, id: 's08', name: 'Hub', kind: 'part', objects: ['Hub'], deps: ['s03'],
    summary: 'The rotor hub: a 30 mm block clamped to the top of the mast that carries the four blade grips.',
    ops: [sk('Hub'), feat('revolve', 'Hub', 2400, ['Hub']), feat('hole', 'Hub', 1500), feat('fillet', 'Hub', 1200), color('Hub')] },
  { n: 9, id: 's09', name: 'Blade grips', kind: 'part', objects: range('BladeGrip', 4), deps: ['s08'],
    summary: 'Four printed blade grips on the hub arms, each with a pitch horn for its link.',
    ops: [sk('BladeGrip'), feat('extrude', 'BladeGrip1', 2300, ['BladeGrip1']), feat('hole', 'BladeGrip1', 1400),
      feat('pattern', 'BladeGrip1', 1900, ['BladeGrip2', 'BladeGrip3', 'BladeGrip4']), feat('fillet', 'BladeGrip1', 1500), color('BladeGrip1')] },
  { n: 10, id: 's10', name: 'Pitch links', kind: 'part', objects: range('PitchLink', 4), deps: ['s05', 's09'],
    summary: 'Four 38 mm pitch links with turnbuckles join the swashplate balls to the grip horns.',
    ops: [sk('PitchLink'), feat('revolve', 'PitchLink1', 2200, ['PitchLink1']),
      feat('pattern', 'PitchLink1', 1700, ['PitchLink2', 'PitchLink3', 'PitchLink4']), color('PitchLink1')] },
  { n: 11, id: 's11', name: 'Assembly: base, gearbox, mast', kind: 'assembly', objects: [], deps: ['s01', 's02', 's03', 's04'],
    summary: 'Base fixed; gearbox on the base; mast concentric with both bearings and turning in them.',
    mates: [{ type: 'fixed', a: 'BasePlate', b: '' }, { type: 'coincident', a: 'Gearbox.bottom', b: 'BasePlate.top' },
      { type: 'concentric', a: 'Mast.axis', b: 'MastBearingLower.bore' }, { type: 'revolute', a: 'Mast', b: 'Gearbox' }],
    ops: [mate('coincident', 'Gearbox', 'BasePlate'), mate('concentric', 'Mast', 'MastBearingLower'), mate('revolute', 'Mast', 'Gearbox', 1600)] },
  { n: 12, id: 's12', name: 'Assembly: servos, swashplate', kind: 'assembly', objects: [], deps: ['s05', 's06', 's07', 's11'],
    summary: 'Servos fixed to the base; swashplate slides on the mast and tilts on its ball.',
    mates: [{ type: 'concentric', a: 'Swashplate.bore', b: 'Mast.axis' }, { type: 'slider', a: 'Swashplate', b: 'Mast' }],
    ops: [mate('coincident', 'Servo1', 'BasePlate', 1200), mate('concentric', 'Swashplate', 'Mast'), mate('slider', 'Swashplate', 'Mast', 1700)] },
  { n: 13, id: 's13', name: 'Assembly: hub, grips, links', kind: 'assembly', objects: [], deps: ['s08', 's09', 's10', 's12'],
    summary: 'Hub fixed to the mast; each grip turns on its arm; links ball-jointed between swashplate and horns.',
    mates: [{ type: 'fixed', a: 'Hub', b: 'Mast' }, { type: 'revolute', a: 'BladeGrip1', b: 'Hub' }],
    ops: [mate('fixed', 'Hub', 'Mast', 1100), mate('revolute', 'BladeGrip1', 'Hub'), mate('concentric', 'PitchLink1', 'Swashplate', 1500)] },
  { n: 14, id: 's14', name: 'Check interference', kind: 'check', objects: [], deps: ['s13'], assignee: 'reviewer',
    summary: 'No two solids overlap anywhere in the assembly, at rest.',
    ops: [{ kind: 'interference', args: {}, ms: 3200 }] },
  { n: 15, id: 's15', name: 'Check printability', kind: 'check', objects: [], deps: ['s13'], assignee: 'reviewer',
    summary: 'Printed parts fit the 180 mm bed, are closed solids and have no wall under 1.2 mm.',
    ops: [{ kind: 'printability', args: {}, ms: 2800 }] },
  { n: 16, id: 's16', name: 'Motion: pitch sweep', kind: 'check', objects: [], deps: ['s14'], assignee: 'reviewer',
    summary: 'Tilt the swashplate ±8° and confirm the links and grips clear each other through the sweep.',
    ops: [{ kind: 'set_placement', args: { name: 'Swashplate', angle: 8 }, ms: 1300 }, { kind: 'interference', args: {}, ms: 2600 },
      { kind: 'render', args: { name: 'RotorHead' }, ms: 2200 }] },
];

/** Where each body sits (mm, Z up). */
function geometry(name: string): Uint8Array {
  const grip = (i: number) => [[15, -7, 62, 7], [-62, -7, -15, 7], [-7, 15, 7, 62], [-7, -62, 7, -15]][i];
  const link = [[44, 0], [-44, 0], [0, 44], [0, -44]];
  const servo = [[34, -20, 54, 20], [-54, -20, -34, 20], [-20, -54, 20, -34]];
  const screw = [[44, -26], [44, 26], [-44, -26], [-44, 26], [-26, -44], [26, -44]];
  const m = /(\d+)$/.exec(name);
  const i = m ? Number(m[1]) - 1 : 0;
  switch (name.replace(/\d+$/, '')) {
    case 'BasePlate': return boxStl(-70, -60, 0, 70, 60, 6);
    case 'Gearbox': return boxStl(-28, -28, 6, 28, 28, 50);
    case 'Mast': return cylStl(0, 0, 50, 5, 125, 32);
    case 'MastBearingLower': return cylStl(0, 0, 50, 11, 7);
    case 'MastBearingUpper': return cylStl(0, 0, 96, 11, 7);
    case 'Swashplate': return mergeStl([cylStl(0, 0, 110, 46, 5, 56), cylStl(0, 0, 105, 14, 15)]);
    case 'Servo': { const [x0, y0, x1, y1] = servo[i]; return boxStl(x0, y0, 6, x1, y1, 42); }
    case 'ServoScrew': { const [x, y] = screw[i]; return cylStl(x, y, 6, 2.75, 3, 16); }
    case 'Hub': return cylStl(0, 0, 150, 15, 18);
    case 'BladeGrip': { const [x0, y0, x1, y1] = grip(i); return boxStl(x0, y0, 152, x1, y1, 166); }
    case 'PitchLink': { const [x, y] = link[i]; return cylStl(x, y, 115, 1.8, 37, 12); }
    default: return boxStl(0, 0, 0, 10, 10, 10);
  }
}

// ---- the scripted team ----

const H = (id: number, name: string, kind: string, client: string, role = 'member', statusText = ''): Member =>
  ({ id, name, kind, client, role, status: 'active', statusText, fence: 1, online: kind === 'human' });

export function startMockSwarm(store: Store): { stop: () => void; api: Api } {
  const members: Member[] = [
    H(1, 'you', 'human', 'web'), H(2, 'planner', 'agent', 'claude', 'lead'), H(3, 'worker1', 'agent', 'claude'),
    H(4, 'worker2', 'agent', 'claude'), H(5, 'reviewer', 'reviewer', 'claude'), H(6, 'maya', 'human', 'desktop'),
    H(7, 'jon', 'human', 'phone'),
  ];
  const byName = (n: string) => members.find(m => m.name === n);
  const mount: Component = {
    id: 'c_nema17_mount_9d8b6b', name: 'NEMA17 motor mount', category: 'motion', tags: ['motor', 'nema17'],
    description: 'L bracket for a NEMA17 stepper', source: 'upload', sourceUrl: '', license: 'our own design', author: 'you',
    format: 'step', size: 48211, bbox: [0, 0, 0, 42, 42, 5], volume: 6300, uses: 2, updatedMs: Date.now(),
    featuresJson: JSON.stringify({ holes: [{ d: 3.4, count: 4 }, { d: 22.2, count: 1 }] }),
    suggestJson: JSON.stringify([{ setting: 'hole_clearance_class', value: 'normal', reason: '4 ISO 273 normal holes' }]),
  };
  const servo: Component = { ...mount, id: 'c_servo_mg996r', name: 'Standard servo (40 × 20 mm)', category: 'motion',
    tags: ['servo', 'rc'], description: 'Standard-size RC servo with mounting flanges', source: 'pack', uses: 5,
    bbox: [0, 0, 0, 40.7, 19.7, 42.9], featuresJson: '{}', suggestJson: '[]' };
  store.set({ connected: true, meId: 1, members: members.map(m => ({ ...m })), workspace: { bedXMm: 180, bedYMm: 180, bedZMm: 180 },
    components: [mount, servo],
    settings: [{ setting: 'tol_clearance', num: 0.25, text: '', setBy: 1, updatedMs: Date.now() }],
    suggestions: [{ id: 'g_mock1', setting: 'tol_press_fit', num: 0, text: '', component: 'c_std_ball_bearing', by: 3,
      reason: 'The 608 bearing seats are printed: printed holes come out undersize, so a nominal seat already grips.',
      status: 'pending', createdMs: Date.now() }],
    params: [{ alias: 'wall', value: '2 mm', num: 2 }, { alias: 'gap', value: '0.3 mm', num: 0.3 },
      { alias: 'mast_d', value: '10 mm', num: 10 }, { alias: 'hub_d', value: '30 mm', num: 30 },
      { alias: 'grip_len', value: '47 mm', num: 47 }, { alias: 'link_len', value: '38 mm', num: 38 }] });

  // Virtual clock: the script runs on `clock`, which catches up with real time on each tick.
  let clock = Date.now();
  let briefId = '', seq = 100, actId = 0, opSeq = 0;
  let phase: 'planning' | 'building' | 'reviewing' | 'fixing' | 'ready' | 'stopped' = 'planning';
  let phaseAt = clock;
  interface Worker { id: number; name: string; step: StepDef | null; op: number; opId: string; opEnd: number; idleUntil: number }
  let workers: Worker[] = [];

  const say = (member: number, summary: string, kind = 'tool_call'): Activity[] =>
    [...store.state.activity.slice(-80), { id: actId++, member, kind, summary, atMs: clock }];
  const brief = (status: string, extra: Partial<{ stlHash: string; planJson: string }> = {}) =>
    store.state.briefs.map(b => b.id === briefId ? { ...b, status, updatedMs: clock, ...extra } : b);
  const taskId = (s: StepDef) => `t_${briefId.slice(2, 8)}_${s.id}`;
  const taskOf = (s: StepDef) => store.state.tasks.find(t => t.id === taskId(s));
  const setTask = (id: string, patch: Partial<Task>) =>
    store.set({ tasks: store.state.tasks.map(t => t.id === id ? { ...t, ...patch, updatedMs: clock } : t) });
  const point = (member: number, object: string, mode: string, label: string) => store.set({
    pointers: [...store.state.pointers.filter(p => p.member !== member),
      { member, object, point: object ? [] : [0, 0, 120], mode, label, op: '', seq: seq++, atMs: clock }] });

  function planJson() {
    return JSON.stringify({
      version: 2, title: 'Four-blade rotor head',
      steps: STEPS.map(s => ({ n: s.n, id: s.id, name: s.name, kind: s.kind, summary: s.summary, task: taskId(s),
        objects: s.objects, depends_on: s.deps, assignee: s.assignee ?? '',
        ops: s.ops.map(o => `${o.kind}`), ...(s.component ? { component: s.component } : {}),
        ...(s.params ? { params: s.params } : {}), ...(s.mates ? { mates: s.mates } : {}) })),
      parts: STEPS.filter(s => s.kind === 'part' || s.kind === 'library').map(s => ({ name: s.name, task: taskId(s), summary: s.summary })),
    });
  }

  function newBrief(text: string, board: string) {
    briefId = newId('b_');
    phase = 'planning'; phaseAt = clock;
    workers = ['worker1', 'worker2', 'maya', 'reviewer'].map(n => ({ id: byName(n)!.id, name: n, step: null, op: 0, opId: '', opEnd: 0, idleUntil: 0 }));
    store.set({
      briefs: [{ id: briefId, by: 1, text, board, status: 'new', planJson: '', stlHash: '', updatedMs: clock }],
      objects: [], previews: new Map(), locks: [], tasks: [], reviews: [], artifacts: [], ops: [], pointers: [],
      activity: say(1, 'submitted a brief', 'say'),
    });
    point(2, '', 'pointing', 'planning');
  }

  function addOp(by: number, def: OpDef, status = 'running'): string {
    const id = `o_mock${String(opSeq++).padStart(4, '0')}`;
    store.set({ ops: [...store.state.ops.slice(-400), { id, by, kind: def.kind, status, errorCode: '', error: '',
      argsJson: JSON.stringify(def.args), resultJson: '', createdMs: clock, startedMs: clock, finishedMs: 0 }] });
    return id;
  }
  function finishOp(id: string, status = 'done', error = '') {
    store.set({ ops: store.state.ops.map(o => o.id === id ? { ...o, status, error, finishedMs: clock, resultJson: '{}' } : o) });
  }

  function makeBody(name: string, by: number, rev = 1) {
    const mesh = geometry(name);
    const b = stlBox(mesh);
    const vol = (b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2]) * (/Mast|Bearing|Hub|Link|Swash|Screw/.test(name) ? 0.785 : 0.6);
    const previews = new Map(store.state.previews);
    previews.set(name, { object: name, rev, mesh });
    store.set({ previews, objects: [...store.state.objects.filter(o => o.name !== name),
      { name, label: name, typeId: 'PartDesign::Body', rev, bbox: [...b], volume: Math.round(vol), visible: true, broken: false, usedBy: [], lastActor: by }] });
  }
  function touch(name: string, by: number) {
    const o = store.state.objects.find(x => x.name === name);
    if (!o) return;
    const previews = new Map(store.state.previews);
    const p = previews.get(name);
    if (p) previews.set(name, { ...p, rev: o.rev + 1 });
    store.set({ previews, objects: store.state.objects.map(x => x.name === name ? { ...x, rev: x.rev + 1, lastActor: by } : x) });
  }

  const doneSet = () => new Set(STEPS.filter(s => taskOf(s)?.status === 'done').map(s => s.id));
  function pick(w: Worker): StepDef | null {
    const done = doneSet();
    const ready = STEPS.filter(s => taskOf(s)?.status === 'open' && s.deps.every(d => done.has(d)));
    return ready.find(s => s.assignee === w.name)
      ?? (w.name === 'maya' || w.name === 'reviewer' ? null : ready.find(s => !s.assignee)) ?? null;
  }

  function startStep(w: Worker, s: StepDef) {
    w.step = s; w.op = 0; w.opId = '';
    setTask(taskId(s), { status: 'claimed', owner: w.id });
    if (s.objects.length) store.set({ locks: [...store.state.locks.filter(l => !s.objects.includes(l.object)), ...s.objects.map(object => ({ object, holder: w.id }))] });
    store.set({ activity: say(w.id, `claimed ${s.n}. ${s.name}`, 'status') });
  }

  function stepWorker(w: Worker): number {
    // Returns the time of this worker's next event, or Infinity when it has nothing to do.
    if (!w.step) {
      if (clock < w.idleUntil) return w.idleUntil;
      const s = pick(w);
      if (!s) return Infinity;
      startStep(w, s);
    }
    const s = w.step!;
    if (taskOf(s)?.status !== 'claimed') { w.step = null; w.idleUntil = clock + 300; return clock + 300; } // stopped
    if (!w.opId) {
      const def = s.ops[w.op];
      w.opId = addOp(w.id, def);
      // Deterministic jitter so the timings read like real ones (1.9 s, 2.3 s…), not round numbers.
      w.opEnd = clock + Math.round(def.ms * (0.85 + ((s.n * 7 + w.op * 13) % 10) / 33));
      const target = String(def.args.name ?? def.args.a ?? s.objects[0] ?? '');
      const onBody = store.state.objects.some(o => o.name === target) ? target : s.objects.find(n => store.state.objects.some(o => o.name === n)) ?? '';
      point(w.id, onBody, onBody ? 'editing' : 'pointing', `${s.n}. ${s.name}`);
      return w.opEnd;
    }
    if (clock < w.opEnd) return w.opEnd;
    const def = s.ops[w.op];
    finishOp(w.opId);
    for (const name of def.makes ?? []) makeBody(name, w.id);
    if (!def.makes && typeof def.args.name === 'string' && s.objects.includes(def.args.name)) touch(def.args.name, w.id);
    store.set({ activity: say(w.id, `${def.kind} ${String(def.args.name ?? def.args.a ?? '')}`.trim()) });
    if (s.n === 3 && w.op === 1) {
      // Someone tried to change the mast while worker1 still held it: refused, nothing changed.
      const holder = store.state.members.find(m => m.id === w.id)?.name ?? 'worker1';
      store.set({ activity: say(4, `Already locked: Mast by ${holder} (active). Message the holder.`, 'refused') });
    }
    w.op++; w.opId = '';
    if (w.op < s.ops.length) return clock;
    setTask(taskId(s), { status: 'done' });
    store.set({ locks: store.state.locks.filter(l => !s.objects.includes(l.object)),
      activity: say(w.id, `finished ${s.n}. ${s.name}`, 'say') });
    point(w.id, '', 'idle', '');
    w.step = null; w.idleUntil = clock + 350;
    return w.idleUntil;
  }

  function plan() {
    store.set({
      briefs: brief('building', { planJson: planJson() }),
      tasks: STEPS.map(s => ({ id: taskId(s), title: `${s.n}. ${s.name}`, status: 'open', owner: 0, assignee: s.assignee ?? '',
        brief: briefId, objects: s.objects, createdBy: 2, createdMs: clock, updatedMs: clock })),
      activity: say(2, `planned ${STEPS.length} steps: ${STEPS.filter(s => s.kind === 'part').length} parts, ` +
        `${STEPS.filter(s => s.kind === 'library').length} library, ${STEPS.filter(s => s.kind === 'assembly').length} assemblies, ` +
        `${STEPS.filter(s => s.kind === 'check').length} checks`, 'say'),
    });
    point(2, '', 'idle', '');
    phase = 'building'; phaseAt = clock;
  }

  /** Run the script up to `until`, event by event. */
  function advance(until: number) {
    for (let guard = 0; guard < 2000; guard++) {
      if (store.state.briefs.find(b => b.id === briefId)?.status === 'cancelled') {
        if (phase !== 'stopped') { phase = 'stopped'; store.set({ pointers: store.state.pointers.map(p => ({ ...p, mode: 'idle' })) }); }
        clock = until; return;
      }
      let next = Infinity;
      if (phase === 'planning') {
        const now = store.state.briefs.find(b => b.id === briefId);
        if (now?.status === 'new' && clock - phaseAt >= 500) store.set({ briefs: brief('planning') });
        if (clock - phaseAt >= 2600) { plan(); next = clock; } else next = clock - phaseAt < 500 ? phaseAt + 500 : phaseAt + 2600;
      } else if (phase === 'building') {
        for (const w of workers) next = Math.min(next, stepWorker(w));
        if (STEPS.every(s => taskOf(s)?.status === 'done' || taskOf(s)?.status === 'cancelled')) {
          phase = 'reviewing'; phaseAt = clock;
          store.set({ briefs: brief('reviewing'), reviews: [{ id: 'r_mock0001', brief: briefId, round: 1, passed: false, atMs: clock,
            findingsJson: JSON.stringify([{ object: 'PitchLink1', problem: 'rod end sits 0.4 mm short of the grip horn ball at full pitch.' },
              { object: 'Swashplate', problem: 'rim is 1.1 mm from Servo3 at -8° tilt; keep 2 mm.' }]) }],
            activity: say(5, 'review round 1: 2 findings', 'say') });
          point(5, 'PitchLink1', 'inspecting', 'checking the links');
          next = clock;
        }
      } else if (phase === 'reviewing') {
        if (clock - phaseAt >= 2500) {
          phase = 'fixing'; phaseAt = clock;
          const fix = addOp(4, { kind: 'set_property', args: { name: 'PitchLink1', prop: 'Height', value: 38.4 }, ms: 0 });
          workers[1].opId = fix;
          point(4, 'PitchLink1', 'editing', 'fixing link length');
        }
        next = phaseAt + 2500;
      } else if (phase === 'fixing') {
        if (clock - phaseAt >= 2200) {
          finishOp(workers[1].opId); workers[1].opId = '';
          touch('PitchLink1', 4);
          const bytes = mergeStl([...store.state.previews.values()].map(p => p.mesh));
          phase = 'ready'; phaseAt = clock;
          store.set({
            reviews: [...store.state.reviews, { id: 'r_mock0002', brief: briefId, round: 2, passed: true, findingsJson: '[]', atMs: clock }],
            briefs: brief('ready', { stlHash: STL_HASH }),
            artifacts: [{ hash: STL_HASH, name: 'rotor-head.stl', mime: 'model/stl', size: bytes.length }],
            locks: [], pointers: store.state.pointers.map(p => ({ ...p, mode: 'idle' })),
            activity: say(5, 'review round 2: passed', 'say'),
          });
        }
        next = phaseAt + 2200;
      }
      if (next > until) { clock = until; return; }
      clock = Math.max(clock, next);
    }
    clock = until;
  }

  newBrief(DEMO_TEXT, '');
  // Start part-way through so the first screen already shows a plan in progress.
  const t0 = Date.now();
  clock = t0 - 30_000; phaseAt = clock;
  store.set({ briefs: store.state.briefs.map(b => ({ ...b, updatedMs: clock })) });
  advance(t0);
  const seedMsg = (from: number, body: string, ago: number) => ({ id: newId('m_'), from, to: 'all', kind: 'chat', body, taskId: '', createdMs: t0 - ago });
  store.set({ messages: [
    seedMsg(2, `Planned ${STEPS.length} steps for the rotor head. Parts first along the load path, library parts after their hosts, then three assemblies and three checks.`, 26_000),
    seedMsg(6, 'I\'ll place the servos and their screws: steps 6 and 7 are pinned to me.', 21_000),
    seedMsg(7, 'Swashplate looks close to the servos at full tilt. Can the reviewer sweep it after step 13?', 9_000),
  ], pointers: [...store.state.pointers, { member: 7, object: 'BasePlate', point: [], mode: 'inspecting', label: 'looking at the base', op: '', seq: seq++, atMs: t0 }] });
  const timer = setInterval(() => advance(Date.now()), 200);

  const notice = (text: string) => store.set({ notice: { text, at: Date.now() } });
  const nowSec = Math.floor(Date.now() / 1000);
  const demoFiles = [
    { path: 'notes/plan.md', version: 2, size: 1840, author: 'planner', updated_at: nowSec - 600 },
    { path: 'docs/bom.csv', version: 1, size: 640, author: 'worker1', updated_at: nowSec - 2400 },
    { path: 'parts/hub/notes.md', version: 3, size: 920, author: 'worker2', updated_at: nowSec - 120 },
    { path: 'scripts/make_grip.py', version: 1, size: 2200, author: 'planner', updated_at: nowSec - 7200 },
  ];
  /** A person's own op: it shows in the feed with a real duration. */
  function myOp(kind: string, args: Record<string, unknown>, ms = 600): string {
    const id = `o_me${String(opSeq++).padStart(4, '0')}`;
    const at = Date.now();
    store.set({ ops: [...store.state.ops, { id, by: 1, kind, status: 'done', errorCode: '', error: '', argsJson: JSON.stringify(args),
      resultJson: '{}', createdMs: at, startedMs: at, finishedMs: at + ms }] });
    return id;
  }
  function overlaps(names?: string[]): Interference {
    const objs = store.state.objects.filter(o => o.visible && (!names?.length || names.includes(o.name)));
    const clashes = [];
    for (let i = 0; i < objs.length; i++) for (let j = i + 1; j < objs.length; j++) {
      const a = objs[i].bbox, b = objs[j].bbox;
      const d = [0, 1, 2].map(k => Math.min(a[k + 3], b[k + 3]) - Math.max(a[k], b[k]));
      // Bodies that are meant to nest (bearings on the mast) are not clashes.
      if (d.every(x => x > 0.5) && !/Mast|Bearing/.test(objs[i].name + objs[j].name) && !/Swash|Hub/.test(objs[i].name + objs[j].name)) {
        clashes.push({ a: objs[i].name, b: objs[j].name, volume: Math.round(d[0] * d[1] * d[2] * 10) / 10 });
      }
    }
    return { checked: objs.map(o => o.name), clashes };
  }

  const api: Api = {
    fence: () => 1,
    redeem: async () => {},
    runOp: async (kind, args, locks = []) => {
      const taken = store.state.locks.find(l => locks.includes(l.object) && l.holder !== 1);
      if (taken) { notice(`Already locked: ${taken.object} by ${store.member(taken.holder)?.name}. Message the holder.`); return null; }
      const name = String(args.name ?? '');
      if (kind === 'create_extrusion' || kind === 'create_primitive') {
        if (!name || store.state.objects.some(o => o.name === name)) { notice(`There is already a body called ${name || '(no name)'}.`); return null; }
        let mesh: Uint8Array;
        const at = (args.placement as { base?: number[] } | undefined)?.base ?? [0, 0, 0];
        if (kind === 'create_extrusion') {
          const pts = (args.points as number[][]) ?? [];
          const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
          mesh = boxStl(Math.min(...xs), Math.min(...ys), 0, Math.max(...xs), Math.max(...ys), Number(args.height) || 1);
        } else {
          const p = (args.props ?? {}) as Record<string, number>;
          mesh = args.kind === 'cylinder' ? cylStl(0, 0, 0, Number(p.Radius) || 5, Number(p.Height) || 10)
            : boxStl(0, 0, 0, Number(p.Length) || 10, Number(p.Width) || 10, Number(p.Height) || 10);
        }
        mesh = moveStl(mesh, at);
        const b = stlBox(mesh);
        const previews = new Map(store.state.previews);
        previews.set(name, { object: name, rev: 1, mesh });
        myOp(kind, args, 900);
        store.set({ previews, objects: [...store.state.objects, { name, label: String(args.label ?? name), typeId: 'Part::Feature', rev: 1,
          bbox: [...b], volume: Math.round((b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2])), visible: true, broken: false, usedBy: [], lastActor: 1 }],
          activity: say(1, `${kind} ${name}`) });
        return { name };
      }
      if (kind === 'set_placement') {
        const o = store.state.objects.find(x => x.name === name);
        const p = store.state.previews.get(name);
        const base = (args.placement as { base?: number[] } | undefined)?.base ?? [0, 0, 0];
        if (!o || !p) { notice(`No body called ${name}.`); return null; }
        const d = [base[0] - o.bbox[0], base[1] - o.bbox[1], base[2] - o.bbox[2]];
        const mesh = moveStl(p.mesh, d);
        const previews = new Map(store.state.previews);
        previews.set(name, { object: name, rev: o.rev + 1, mesh });
        myOp(kind, args, 500);
        store.set({ previews, objects: store.state.objects.map(x => x.name === name ? { ...x, rev: x.rev + 1, bbox: [...stlBox(mesh)], lastActor: 1 } : x),
          activity: say(1, `moved ${name}`) });
        return { name };
      }
      if (kind === 'boolean') {
        myOp(kind, args, 1400);
        store.set({ activity: say(1, `${String(args.kind)} ${String(args.tool)} from ${String(args.base)}`) });
        touch(String(args.base), 1);
        return { name };
      }
      myOp(kind, args);
      return {};
    },
    snapshot: async label => {
      myOp('snapshot', { label }, 700);
      store.set({ activity: say(1, `saved snapshot “${label || 'snapshot'}”`, 'status') });
      return true;
    },
    interference: async names => { myOp('interference', { names: names ?? [] }, 1800); return overlaps(names); },
    recommend: async (need): Promise<Recommendation[]> => {
      const m = /M(\d(?:\.\d)?)/i.exec(need);
      const size = m ? `M${m[1]}` : 'M3';
      return [
        { id: 'std:iso4762', name: `${size} × 10 socket head screw (ISO 4762)`, category: 'fastener', why: 'standard part; fits a 3.4 mm clearance hole', family: 'iso4762', size, length: 10 },
        { id: 'std:heat_insert', name: `${size} heat-set insert`, category: 'insert', why: 'for printed parts that are opened often', family: 'heat_insert', size },
        { id: 'c_servo_mg996r', name: servo.name, category: 'motion', why: 'the team\'s own part, used 5 times', },
      ];
    },
    submitBrief: (text, board) => { clock = Date.now(); newBrief(stripMentions(text), board); return briefId; },
    lock: async names => {
      const taken = store.state.locks.find(l => names.includes(l.object) && l.holder !== 1);
      if (taken) { notice(`Already locked: ${taken.object} by ${store.member(taken.holder)?.name}. Message the holder.`); return; }
      store.set({ locks: [...store.state.locks.filter(l => !names.includes(l.object)), ...names.map(object => ({ object, holder: 1 }))],
        activity: say(1, `locked ${names.join(', ')}`, 'status') });
    },
    release: async names => store.set({ locks: store.state.locks.filter(l => !(names.includes(l.object) && l.holder === 1)) }),
    setParam: async (alias, value) => {
      store.set({ params: store.state.params.map(p => p.alias === alias ? { ...p, value, num: parseFloat(value) } : p) });
    },
    setBed: async (xMm, yMm, zMm) => {
      store.set({ workspace: { bedXMm: xMm, bedYMm: yMm, bedZMm: zMm },
        activity: say(1, `set the printer bed to ${xMm} x ${yMm} x ${zMm} mm`, 'status') });
    },
    removeMember: async id => {
      const m = store.member(id);
      store.set({ members: store.state.members.map(x => x.id === id ? { ...x, status: 'removed' } : x),
        locks: store.state.locks.filter(l => l.holder !== id),
        activity: say(1, `removed ${m?.name ?? id} from the team`, 'status') });
    },
    cancelTask: async (id, reason) => {
      const t = store.state.tasks.find(x => x.id === id);
      store.set({ tasks: store.state.tasks.map(x => x.id === id ? { ...x, status: 'cancelled', updatedMs: Date.now() } : x),
        activity: say(1, `cancelled ${t?.title ?? id}${reason ? `: ${reason}` : ''}`, 'status') });
    },
    deleteTask: async id => {
      const t = store.state.tasks.find(x => x.id === id);
      store.set({ tasks: store.state.tasks.filter(x => x.id !== id),
        activity: say(1, `deleted ${t?.title ?? id}`, 'status') });
    },
    cancelBrief: async id => {
      const b = store.state.briefs.find(x => x.id === id);
      store.set({ briefs: store.state.briefs.map(x => x.id === id ? { ...x, status: 'cancelled', updatedMs: Date.now() } : x),
        tasks: store.state.tasks.map(t => t.brief === id
          && (t.status === 'open' || t.status === 'blocked' || t.status === 'awaiting_takeover')
          ? { ...t, status: 'cancelled' } : t),
        activity: say(1, `cancelled “${b?.text ?? id}”`, 'status') });
    },
    checkPrintability: async (bedMm?: [number, number, number]): Promise<Printability> => {
      const saved = store.state.workspace ?? { bedXMm: 180, bedYMm: 180, bedZMm: 180 };
      const bed = bedMm ? { bedXMm: bedMm[0], bedYMm: bedMm[1], bedZMm: bedMm[2] } : saved;
      const across = (a: number, b: number) => [Math.min(a, b), Math.max(a, b)] as const;
      const [bx, by] = across(bed.bedXMm, bed.bedYMm);
      const objects = store.state.objects.map(o => {
        const size = [o.bbox[3] - o.bbox[0], o.bbox[4] - o.bbox[1], o.bbox[5] - o.bbox[2]];
        const [sx, sy] = across(size[0], size[1]);
        return { name: o.name, size, fitsBed: sx <= bx && sy <= by && size[2] <= bed.bedZMm,
          closedSolid: o.volume > 0, thinWalls: [] };
      });
      myOp('printability', {}, 1500);
      return { ok: objects.length > 0 && objects.every(o => o.fitsBed && o.closedSolid), objects };
    },
    movePointer: (object, point, mode, label) => store.set({
      pointers: [...store.state.pointers.filter(p => p.member !== 1), { member: 1, object, point, mode, label, op: '', seq: seq++, atMs: Date.now() }] }),
    logActivity: (kind, summary) => store.set({ activity: say(1, summary, kind) }),
    requestHandoff: (note, trigger) => {
      const id = newId('h_');
      store.set({ handoffs: [...store.state.handoffs, { id, from: 1, note, trigger, status: trigger === 'now' ? 'open' : 'waiting', taker: 0, createdMs: Date.now() }] });
      return id;
    },
    cancelHandoff: id => store.set({ handoffs: store.state.handoffs.map(h => h.id === id ? { ...h, status: 'cancelled' } : h) }),
    sendCommand: (to, body, taskId = '') => api.sendMessage(to, body, 'command', taskId),
    sendMessage: (to, body, kind, taskId = '') => {
      // A helper reads a command, says how it will do it, then marks it done: the same receipts a real helper leaves.
      const id = newId('m_');
      const target = to === 'all' ? null : members.find(m => m.name === to);
      store.set({ messages: [...store.state.messages, { id, from: 1, to: target ? `member:${target.id}` : 'all',
        kind: kind === 'command' ? 'steer' : kind, body, taskId, createdMs: Date.now() }] });
      if (kind !== 'command') return id;
      const helperIds = target ? [target.id] : members.filter(m => m.kind === 'agent').map(m => m.id);
      const mark = (state: string) => store.set({ receipts: [
        ...store.state.receipts.filter(r => !(r.messageId === id && helperIds.includes(r.member))),
        ...helperIds.map(member => ({ messageId: id, member, state }))] });
      setTimeout(() => mark('delivered'), 1200);
      setTimeout(() => {
        mark('consumed');
        const by = helperIds[0];
        store.set({ messages: [...store.state.messages, { id: newId('m_'), from: by, to: 'member:1', kind: 'message',
          body: `Done: ${body.length > 60 ? body.slice(0, 57) + '…' : body}`, taskId, createdMs: Date.now() }] });
        if (taskId) store.set({ tasks: store.state.tasks.map(t => t.id === taskId ? { ...t, status: 'done', owner: by, updatedMs: Date.now() } : t) });
      }, 4500);
      return id;
    },
    sendChat: body => api.sendMessage('all', body, 'chat'),
    createTask: async ({ title, detail: _detail, objects, assignee }) => {
      const id = newId('t_');
      store.set({ tasks: [...store.state.tasks, { id, title, status: 'open', owner: 0, assignee, brief: '', objects,
        createdBy: store.state.meId ?? 0, createdMs: Date.now(), updatedMs: Date.now() }] });
      return id;
    },
    downloadArtifact: async (hash, filename) => {
      if (hash !== STL_HASH) throw new Error('Unknown export.');
      saveBytes(mergeStl([...store.state.previews.values()].map(p => p.mesh)), 'model/stl', filename);
    },
    placeComponent: async req => {
      const b: Box6 = req.family === 'ball_bearing' ? [80, 0, 0, 102, 22, 7] : req.family ? [80, 30, 0, 85.5, 35.5, 13] : [80, -60, 0, 120, -40, 40];
      const objects: Obj[] = [...store.state.objects, { name: req.name, label: req.label ?? req.name, typeId: 'Part::Feature',
        rev: 1, bbox: [...b], volume: 1, visible: true, broken: false, usedBy: [], lastActor: 1 }];
      const previews = new Map(store.state.previews);
      previews.set(req.name, { object: req.name, rev: 1, mesh: req.family === 'ball_bearing' ? cylStl(91, 11, 0, 11, 7) : boxStl(...b) });
      myOp(req.family ? 'insert_standard_part' : 'insert_component', { name: req.name }, 1100);
      store.set({ objects, previews, locks: [...store.state.locks, { object: req.name, holder: 1 }],
        components: store.state.components.map(c => c.id === req.component ? { ...c, uses: c.uses + 1 } : c),
        activity: say(1, `placed ${req.label ?? req.name}`, 'status') });
      return true;
    },
    addComponent: async (filename, bytes, meta) => {
      store.set({ components: [...store.state.components, { id: newId('c_').toLowerCase(), name: meta.name || filename,
        category: meta.category, tags: meta.tags, description: meta.description, source: 'upload', sourceUrl: '',
        license: meta.license, author: 'you', format: filename.split('.').pop()?.toLowerCase() ?? 'step', size: bytes.length,
        bbox: [], volume: 0, featuresJson: '{}', suggestJson: '[]', uses: 0, updatedMs: Date.now() }] });
      return true;
    },
    importComponentUrl: async (url, meta) => {
      notice(`The demo can't download ${url}; in a real build the host fetches it.`);
      return !!meta;
    },
    setSetting: async (setting, num, text) => store.set({ settings: [...store.state.settings.filter(s => s.setting !== setting),
      { setting, num, text, setBy: 1, updatedMs: Date.now() }] }),
    resetSetting: async setting => store.set({ settings: store.state.settings.filter(s => s.setting !== setting) }),
    resolveSuggestion: async (id, accept) => {
      const g = store.state.suggestions.find(x => x.id === id);
      store.set({ suggestions: store.state.suggestions.map(x => x.id === id ? { ...x, status: accept ? 'accepted' : 'dismissed' } : x),
        settings: g && accept ? [...store.state.settings.filter(s => s.setting !== g.setting),
          { setting: g.setting, num: g.num, text: g.text, setBy: 1, updatedMs: Date.now() }] : store.state.settings });
    },
    componentPreview: async id => id.startsWith('c_nema17') ? boxStl(0, 0, 0, 42, 42, 5) : id.startsWith('c_servo') ? boxStl(0, 0, 0, 40.7, 19.7, 42.9) : null,
    listFiles: async () => ({ files: demoFiles, folders: ['notes', 'docs', 'parts', 'parts/hub', 'scripts'] }),
    fileHistory: async path => {
      const file = demoFiles.find(f => f.path === path);
      if (!file) return null;
      return { path, current: file, older_versions: Array.from({ length: Math.max(0, file.version - 1) },
        (_, i) => ({ version: i + 1, size: Math.max(200, file.size - 120 * (file.version - i - 1)),
          author: i ? 'worker1' : 'planner', saved_at: file.updated_at - 3600 * (file.version - i - 1),
          reason: i ? 'replaced' : 'first draft' })) };
    },
  };
  return { stop: () => clearInterval(timer), api };
}

export function startMock(store: Store): () => void {
  return startMockSwarm(store).stop;
}

/** The demo plan's step count (tests read it instead of hard-coding the script). */
export const MOCK_STEPS = STEPS.length;
