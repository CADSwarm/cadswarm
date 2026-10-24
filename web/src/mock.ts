// A scripted swarm so the viewer, animation and panels can be built before the module exists.
import type { Store, Obj } from './store';

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

export function startMock(store: Store): () => void {
  const members = [
    { id: 1, name: 'you', kind: 'human', client: 'phone', role: 'member', status: 'active', statusText: '', fence: 1, online: true },
    { id: 2, name: 'planner', kind: 'agent', client: 'claude', role: 'lead', status: 'active', statusText: '', fence: 1, online: false },
    { id: 3, name: 'worker1', kind: 'agent', client: 'claude', role: 'member', status: 'active', statusText: '', fence: 1, online: false },
    { id: 4, name: 'worker2', kind: 'agent', client: 'claude', role: 'member', status: 'active', statusText: '', fence: 1, online: false },
    { id: 5, name: 'reviewer', kind: 'reviewer', client: 'droid', role: 'member', status: 'active', statusText: '', fence: 1, online: false },
  ];
  const parts: [string, number, number, number, number, number, number][] = [
    ['Base', 0, 0, 0, 70, 32, 2], ['WallN', 0, 30, 2, 70, 32, 14], ['WallS', 0, 0, 2, 70, 2, 14],
    ['Standoff1', 10, 10, 2, 13, 13, 6], ['Lid', 0, 0, 16, 70, 32, 18],
  ];
  store.set({ connected: true, meId: 1, members, workspace: { bedXMm: 180, bedYMm: 180, bedZMm: 180 },
    params: [{ alias: 'wall', value: '2 mm', num: 2 }, { alias: 'gap', value: '0.5 mm', num: 0.5 }] });
  let step = 0;
  const timer = setInterval(() => {
    const [name, ...b] = parts[step % parts.length];
    const builder = 2 + (step % 3);
    const rev = Math.floor(step / parts.length) + 1;
    const objects: Obj[] = [...store.state.objects.filter(o => o.name !== name),
      { name, label: name, typeId: 'Part::Box', rev, bbox: b, volume: 1, visible: true, broken: false, usedBy: [], lastActor: builder }];
    const previews = new Map(store.state.previews);
    previews.set(name, { object: name, rev, mesh: boxStl(...(b as [number, number, number, number, number, number])) });
    store.set({
      objects, previews,
      locks: [...store.state.locks.filter(l => l.object !== name), { object: name, holder: builder }],
      pointers: [...store.state.pointers.filter(p => p.member !== builder),
        { member: builder, object: name, point: [], mode: 'editing', label: `building ${name}`, op: `o_${step}`, seq: step + 1, atMs: Date.now() }],
      activity: [...store.state.activity.slice(-50),
        { id: step, member: builder, kind: 'tool_call', summary: `create_primitive ${name}`, atMs: Date.now() }],
    });
    step++;
  }, 1800);
  return () => clearInterval(timer);
}
