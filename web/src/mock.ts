// A scripted swarm so the viewer, animation and panels can be built and demoed without a server.
// `?mock` runs it: a brief is planned, five parts are built by agents, the reviewer sends one round back,
// then the STL is ready. The mock Api answers the panels (brief, lock, params, download) locally.
import type { Api, Printability } from './api';
import type { Store, Obj, Task, Activity, Component } from './store';
import { newId } from './ids';
import { saveBytes } from './download';

function boxStl(x0: number, y0: number, z0: number, x1: number, y1: number, z1: number): Uint8Array {
  const v = [[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],[x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]];
  const f = [[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]];
  const buf = new ArrayBuffer(84 + 50 * f.length);
  const dv = new DataView(buf);
  dv.setUint32(80, f.length, true);
  f.forEach((tri, i) => {
    let o = 84 + 50 * i + 12; // normals left zero; STLLoader recomputes
    for (const k of tri) for (const c of v[k]) { dv.setFloat32(o, c, true); o += 4; }
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

const DEMO_TEXT = 'A case for my ESP32-S3 DevKitC with a USB-C cutout, four standoffs and a snap-on lid';
const STL_HASH = 'c0ffee'.repeat(10) + 'f00d';
type PartDef = [string, number, number, number, number, number, number];

export function startMockSwarm(store: Store): { stop: () => void; api: Api } {
  const members = [
    { id: 1, name: 'you', kind: 'human', client: 'phone', role: 'member', status: 'active', statusText: '', fence: 1, online: true },
    { id: 2, name: 'planner', kind: 'agent', client: 'claude', role: 'lead', status: 'active', statusText: '', fence: 1, online: false },
    { id: 3, name: 'worker1', kind: 'agent', client: 'claude', role: 'member', status: 'active', statusText: '', fence: 1, online: false },
    { id: 4, name: 'worker2', kind: 'agent', client: 'claude', role: 'member', status: 'active', statusText: '', fence: 1, online: false },
    { id: 5, name: 'reviewer', kind: 'reviewer', client: 'droid', role: 'member', status: 'active', statusText: '', fence: 1, online: false },
  ];
  const parts: PartDef[] = [
    ['Base', 0, 0, 0, 70, 32, 2], ['WallN', 0, 30, 2, 70, 32, 14], ['WallS', 0, 0, 2, 70, 2, 14],
    ['Standoff1', 10, 10, 2, 13, 13, 6], ['Lid', 0, 0, 16, 70, 32, 18],
  ];
  const mount: Component = {
    id: 'c_nema17_mount_9d8b6b', name: 'NEMA17 motor mount', category: 'motion', tags: ['motor', 'nema17'],
    description: 'L bracket for a NEMA17 stepper', source: 'upload', sourceUrl: '', license: 'our own design', author: 'you',
    format: 'step', size: 48211, bbox: [0, 0, 0, 42, 42, 5], volume: 6300, uses: 2, updatedMs: Date.now(),
    featuresJson: JSON.stringify({ holes: [{ d: 3.4, count: 4 }, { d: 22.2, count: 1 }] }),
    suggestJson: JSON.stringify([{ setting: 'hole_clearance_class', value: 'normal', reason: '4 ISO 273 normal holes' }]),
  };
  store.set({ connected: true, meId: 1, members, workspace: { bedXMm: 180, bedYMm: 180, bedZMm: 180 },
    components: [mount, { ...mount, id: 'c_esp32_s3_case_1a2b3c', name: 'ESP32-S3 case (last build)', category: 'enclosure',
      tags: ['esp32', 'case'], description: '', source: 'pack', uses: 0, bbox: [0, 0, 0, 70, 32, 18], featuresJson: '{}', suggestJson: '[]' }],
    settings: [{ setting: 'tol_clearance', num: 0.25, text: '', setBy: 1, updatedMs: Date.now() }],
    suggestions: [{ id: 'g_mock1', setting: 'tol_press_fit', num: 0, text: '', component: 'c_std_ball_bearing', by: 3,
      reason: 'The 608 bearing seats are printed: printed holes come out undersize, so a nominal seat already grips.',
      status: 'pending', createdMs: Date.now() }],
    params: [{ alias: 'wall', value: '2 mm', num: 2 }, { alias: 'gap', value: '0.5 mm', num: 0.5 },
      { alias: 'standoff_h', value: '4 mm', num: 4 }, { alias: 'clear_top', value: '17 mm', num: 17 }] });

  let step = 0, briefId = '', seq = 100, actId = 0;
  const say = (member: number, summary: string, kind = 'tool_call'): Activity[] =>
    [...store.state.activity.slice(-60), { id: actId++, member, kind, summary, atMs: Date.now() }];
  const workerOf = (i: number) => 3 + (i % 2);

  function newBrief(text: string, board: string) {
    briefId = newId('b_');
    step = 0;
    store.set({
      briefs: [{ id: briefId, by: 1, text, board, status: 'new', planJson: '', stlHash: '', updatedMs: Date.now() }],
      objects: [], previews: new Map(), locks: [], tasks: [], reviews: [], artifacts: [], ops: [], pointers: [],
      activity: say(1, 'submitted a brief', 'say'),
    });
  }
  const brief = (status: string, extra: Partial<{ stlHash: string; planJson: string }> = {}) =>
    store.state.briefs.map(b => b.id === briefId ? { ...b, status, updatedMs: Date.now(), ...extra } : b);

  function build(i: number, rev: number) {
    const [name, ...b] = parts[i];
    const builder = workerOf(i);
    const objects: Obj[] = [...store.state.objects.filter(o => o.name !== name),
      { name, label: name, typeId: 'Part::Box', rev, bbox: b, volume: 1, visible: true, broken: false, usedBy: [], lastActor: builder }];
    const previews = new Map(store.state.previews);
    previews.set(name, { object: name, rev, mesh: boxStl(...(b as [number, number, number, number, number, number])) });
    const tasks: Task[] = store.state.tasks.map((t, k) =>
      t.status === 'cancelled' ? t
        : k < i ? { ...t, status: 'done' } : k === i ? { ...t, status: 'claimed', owner: builder } : t);
    store.set({
      objects, previews, tasks,
      locks: [...store.state.locks.filter(l => l.object !== name), { object: name, holder: builder }],
      pointers: [...store.state.pointers.filter(p => p.member !== builder && p.member !== 2),
        { member: builder, object: name, point: [], mode: 'editing', label: `building ${name}`, op: `o_${step}`, seq: seq++, atMs: Date.now() }],
      ops: [...store.state.ops.slice(-20), { id: `o_mock${step}`, by: builder, kind: 'create_primitive', status: 'done', errorCode: '', error: '', argsJson: JSON.stringify({ name }), resultJson: '{}' }],
      activity: say(builder, `create_primitive ${name}`),
    });
  }

  function tick() {
    const n = parts.length;
    // A stopped request stays stopped: the scripted build does not resurrect it.
    if (store.state.briefs.find(b => b.id === briefId)?.status === 'cancelled') {
      store.set({ pointers: store.state.pointers.map(p => ({ ...p, mode: 'idle' })) });
      return;
    }
    if (step === 0) {
      store.set({
        briefs: brief('planning'),
        tasks: parts.map(([name], i) => ({ id: `t_mock000${i}`, title: `Model the ${name}`, status: 'open', owner: 0,
          assignee: members[workerOf(i) - 1].name, brief: briefId, objects: [name] })),
        pointers: [{ member: 2, object: '', point: [35, 16, 30], mode: 'pointing', label: 'planning', op: '', seq: seq++, atMs: Date.now() }],
        activity: say(2, `planned ${n} tasks`, 'say'),
      });
    } else if (step <= n) {
      build(step - 1, 1);
      store.set({ briefs: brief('building') });
      if (step === 2) {
        // A helper tried to change Base while worker1 still held it: refused, nothing changed.
        store.set({ activity: say(4, 'Already locked: Base by worker1 (active). Message the holder.', 'refused') });
      }
    } else if (step === n + 1) {
      store.set({
        briefs: brief('reviewing'),
        tasks: store.state.tasks.map(t => ({ ...t, status: 'done' })),
        pointers: [...store.state.pointers.map(p => ({ ...p, mode: 'idle' })),
          { member: 5, object: 'Standoff1', point: [], mode: 'inspecting', label: 'checking holes', op: '', seq: seq++, atMs: Date.now() }],
        reviews: [{ id: 'r_mock0001', brief: briefId, round: 1, passed: false, atMs: Date.now(),
          findingsJson: JSON.stringify([{ message: 'Standoff1 hole is 2.4 mm; M2.5 screws need 2.7 mm.' }, { message: 'USB-C cutout sits 0.6 mm too low.' }]) }],
        activity: say(5, 'review round 1: 2 findings', 'say'),
      });
    } else if (step === n + 2) {
      build(3, 2);
      store.set({
        tasks: store.state.tasks.map(t => ({ ...t, status: 'done' })),
        // The reviewer keeps its own arrow and moves it to whatever it re-checks.
        pointers: [...store.state.pointers.filter(p => p.member !== 5),
          { member: 5, object: 'Lid', point: [], mode: 'inspecting', label: 're-checking the lid', op: '', seq: seq++, atMs: Date.now() }],
      });
    } else if (step === n + 3) {
      store.set({
        reviews: [...store.state.reviews, { id: 'r_mock0002', brief: briefId, round: 2, passed: true, findingsJson: '[]',
          atMs: Date.now() }],
        activity: say(5, 'review round 2: passed', 'say'),
      });
    } else if (step === n + 4) {
      const bytes = mergeStl([...store.state.previews.values()].map(p => p.mesh));
      store.set({
        briefs: brief('ready', { stlHash: STL_HASH }),
        artifacts: [{ hash: STL_HASH, name: 'model-12.stl', mime: 'model/stl', size: bytes.length }],
        locks: [], pointers: store.state.pointers.map(p => ({ ...p, mode: 'idle' })),
        activity: say(2, 'exported model-12.stl', 'tool_result'),
      });
    } else {
      // The brief is ready: agents are done, so nobody edits or points any more. Keep only a quiet heartbeat.
      store.set({ pointers: store.state.pointers.map(p => ({ ...p, mode: 'idle' })) });
    }
    step++;
  }

  newBrief(DEMO_TEXT, 'esp32-s3-devkitc-1');
  for (let i = 0; i < 4; i++) tick(); // start mid-build so the first screen is not empty
  const timer = setInterval(tick, 1800);

  const notice = (text: string) => store.set({ notice: { text, at: Date.now() } });
  const nowSec = Math.floor(Date.now() / 1000);
  const demoFiles = [
    { path: 'notes/plan.md', version: 2, size: 1840, author: 'planner', updated_at: nowSec - 600 },
    { path: 'docs/bom.csv', version: 1, size: 640, author: 'worker1', updated_at: nowSec - 2400 },
    { path: 'parts/base/notes.md', version: 3, size: 920, author: 'worker2', updated_at: nowSec - 120 },
    { path: 'scripts/make_lid.py', version: 1, size: 2200, author: 'planner', updated_at: nowSec - 7200 },
  ];
  const api: Api = {
    fence: () => 1,
    redeem: async () => {},
    submitBrief: (text, board) => { newBrief(text, board); return briefId; },
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
    cancelTask: async (id, reason) => {
      const t = store.state.tasks.find(x => x.id === id);
      store.set({ tasks: store.state.tasks.map(x => x.id === id ? { ...x, status: 'cancelled' } : x),
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
    checkPrintability: async (): Promise<Printability> => {
      const bed = store.state.workspace ?? { bedXMm: 180, bedYMm: 180, bedZMm: 180 };
      const across = (a: number, b: number) => [Math.min(a, b), Math.max(a, b)] as const;
      const [bx, by] = across(bed.bedXMm, bed.bedYMm);
      const objects = store.state.objects.map(o => {
        const size = [o.bbox[3] - o.bbox[0], o.bbox[4] - o.bbox[1], o.bbox[5] - o.bbox[2]];
        const [sx, sy] = across(size[0], size[1]);
        return { name: o.name, size, fitsBed: sx <= bx && sy <= by && size[2] <= bed.bedZMm,
          closedSolid: o.volume > 0, thinWalls: [] };
      });
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
    sendCommand: (to, body, taskId = '') => {
      // A helper reads it, says how it will do it, then marks it done: the same receipts a real helper leaves.
      const id = newId('m_');
      const target = to === 'all' ? null : members.find(m => m.name === to);
      const helperIds = target ? [target.id] : members.filter(m => m.kind === 'agent').map(m => m.id);
      store.set({ messages: [...store.state.messages, { id, from: 1, to: target ? `member:${target.id}` : 'all', kind: 'steer',
        body, taskId, createdMs: Date.now() }] });
      const mark = (state: string) => store.set({ receipts: [
        ...store.state.receipts.filter(r => !(r.messageId === id && helperIds.includes(r.member))),
        ...helperIds.map(member => ({ messageId: id, member, state }))] });
      setTimeout(() => mark('delivered'), 1200);
      setTimeout(() => {
        mark('consumed');
        const by = helperIds[0];
        store.set({ messages: [...store.state.messages, { id: newId('m_'), from: by, to: 'member:1', kind: 'message',
          body: `Done: ${body.length > 60 ? body.slice(0, 57) + '…' : body}`, taskId, createdMs: Date.now() }] });
      }, 4500);
      return id;
    },
    downloadArtifact: async (hash, filename) => {
      if (hash !== STL_HASH) throw new Error('Unknown export.');
      saveBytes(mergeStl([...store.state.previews.values()].map(p => p.mesh)), 'model/stl', filename);
    },
    placeComponent: async req => {
      const b = req.family === 'ball_bearing' ? [0, 0, 0, 22, 22, 7] : [0, 0, -10, 5.5, 5.5, 3];
      const objects: Obj[] = [...store.state.objects, { name: req.name, label: req.label ?? req.name, typeId: 'Part::Feature',
        rev: 1, bbox: b, volume: 1, visible: true, broken: false, usedBy: [], lastActor: 1 }];
      const previews = new Map(store.state.previews);
      previews.set(req.name, { object: req.name, rev: 1, mesh: boxStl(...(b as [number, number, number, number, number, number])) });
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
    componentPreview: async id => id.startsWith('c_nema17') ? boxStl(0, 0, 0, 42, 42, 5) : id.startsWith('c_esp32') ? boxStl(0, 0, 0, 70, 32, 18) : null,
    listFiles: async () => ({ files: demoFiles, folders: ['notes', 'docs', 'parts', 'parts/base', 'scripts'] }),
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
