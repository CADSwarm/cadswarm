# CadSwarm Web App Implementation Plan (Person D)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A phone-first web app that pairs by scanning a QR, lets a person submit a brief, and shows the swarm building live: object tree, tasks, activity, a three.js view with animated pointers for every agent and person, lock tints, edit pulses, parameter changes, the STL download, and handing work off to an AI.

**Architecture:** Vite + TypeScript, no UI framework. One `Store` holds plain state and emits change events; it is fed either by `api.ts` (the SpacetimeDB TypeScript SDK, the only file touching generated bindings) or by `mock.ts` (scripted demo data, so the viewer and animation can be built before M0). Pure logic (tickets, colours, spring motion, pointer ordering, edit pulses) lives in small modules with Vitest tests.

**Tech Stack:** Vite, TypeScript, `spacetimedb` (TS SDK), `three` (+ `STLLoader`, `CSS2DRenderer`, `OrbitControls`), Vitest. Deployed as static files to GitHub Pages.

Read first: `docs/superpowers/plans/2026-10-23-cadswarm-00-overview.md` (contract) and the spec section "Live presence: pointers, edits and animation".

## Global Constraints

- The ticket arrives in the URL fragment `#t=<base32>` and is removed from the address bar immediately after reading; never send it anywhere except `redeem_invite`.
- The SpacetimeDB token is stored in `localStorage` under `cadswarm:<uri>:<db>`; every access wrapped in try/catch.
- Pointers: at most 5 `move_pointer` calls per second, only when the pointer moved, none after 3 s without input.
- Animation: critically damped spring; `prefers-reduced-motion` snaps pointers and replaces pulses with static outlines.
- Layout works at 360 px wide with 16 px gutters; the 3D view is the main surface; panels collapse into tabs on phones.
- Member colour = hue from the member id (`(id * 137.508) mod 360`), saturation 70 %, lightness 55 % (light theme) / 65 % (dark).
- Colour strings use the comma form `hsl(h, s%, l%)`; three.js does not parse the space-separated form (every tint would render white).
- The Vite 8 / TypeScript 6 scaffold enables `erasableSyntaxOnly`: no constructor parameter properties (`constructor(private x: T)`); declare fields and assign them in the constructor.
- Status: Tasks 1–4 were built on `feat/web` (a186bce..aeb8010, 11 tests) with these fixes and a static edge outline for reduced motion; Task 5 waits for the M0 bindings.

## File structure

| File | Responsibility |
|---|---|
| `web/index.html`, `web/src/style.css` | Shell and styles (light and dark tokens) |
| `web/src/main.ts` | Boot: read ticket or stored session, pick real or mock data, mount UI |
| `web/src/ticket.ts` | Base32 + ticket decode (pure) |
| `web/src/session.ts` | Stored token per database |
| `web/src/ids.ts` | Client ids (`o_`, `b_`, ...) |
| `web/src/store.ts` | `Store`: state + `subscribe(fn)` |
| `web/src/api.ts` | SDK connection, subscriptions → Store, reducer wrappers |
| `web/src/mock.ts` | Scripted fake swarm for development |
| `web/src/color.ts` | Member colours (pure) |
| `web/src/viewer/spring.ts` | Spring integrator (pure) |
| `web/src/viewer/scene.ts` | three.js scene: meshes per object, cross-fade, tints, pulses |
| `web/src/viewer/pointers.ts` | Pointer glyphs, labels, spring motion, seq ordering |
| `web/src/ui/pair.ts`, `brief.ts`, `panels.ts`, `params.ts`, `handoff.ts` | Screens and panels |
| `web/test/*.test.ts` | Vitest tests |
| `.github/workflows/pages.yml` | Deploy to GitHub Pages |

---

### Task 1: Scaffold, tickets, colours and ids

**Files:**
- Create: `web/` (Vite), `web/src/ticket.ts`, `web/src/color.ts`, `web/src/ids.ts`, `web/test/pure.test.ts`

**Interfaces:**
- Produces: `decodeTicket(s: string): Ticket` (`{v, uri, db, token, kind, role, host_addr}`), `memberColor(id: number, dark: boolean): string`, `newId(prefix: 'o_'|'t_'|'m_'|'b_'|'r_'|'h_'): string`.

- [ ] **Step 1: Scaffold**

```powershell
git switch -c feat/web
npm create vite@latest web -- --template vanilla-ts
cd web
npm install
npm install three spacetimedb@~2.13.0
npm install -D vitest @types/three
cd ..
```
Add to `web/package.json` scripts: `"test": "vitest run"`. Set `base: './'` in `web/vite.config.ts` (create it if absent):
```ts
import { defineConfig } from 'vite';
export default defineConfig({ base: './' });
```

- [ ] **Step 2: Write the failing tests `web/test/pure.test.ts`**

```ts
import { describe, expect, it } from 'vitest';
import { decodeTicket } from '../src/ticket';
import { memberColor } from '../src/color';
import { newId } from '../src/ids';

// Produced by the Rust host / Python ticket.encode for this payload:
const payload = { v: 1, uri: 'http://127.0.0.1:3000', db: 'cadswarm', token: 'ab'.repeat(32), kind: 'human', role: 'member', host_addr: '' };

function b32(bytes: Uint8Array): string {
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = 0, value = 0, out = '';
  for (const b of bytes) { value = (value << 8) | b; bits += 8; while (bits >= 5) { out += A[(value >>> (bits - 5)) & 31]; bits -= 5; } }
  if (bits > 0) out += A[(value << (5 - bits)) & 31];
  return out;
}

describe('tickets', () => {
  it('decodes base32 JSON case-insensitively', () => {
    const s = b32(new TextEncoder().encode(JSON.stringify(payload)));
    expect(decodeTicket(s)).toEqual(payload);
    expect(decodeTicket(s.toLowerCase())).toEqual(payload);
  });
  it('rejects garbage with a readable error', () => {
    expect(() => decodeTicket('!!!')).toThrow(/ticket/i);
  });
});

describe('colours and ids', () => {
  it('gives each member a stable, distinct colour', () => {
    expect(memberColor(1, false)).toBe(memberColor(1, false));
    expect(memberColor(1, false)).not.toBe(memberColor(2, false));
    expect(memberColor(3, true)).toMatch(/^hsl\(/);
  });
  it('makes ids with the contract prefixes and lengths', () => {
    expect(newId('o_')).toMatch(/^o_[0-9a-f]{12}$/);
    expect(newId('b_')).toMatch(/^b_[0-9a-f]{8}$/);
  });
});
```

- [ ] **Step 3: Run to see them fail**

Run: `npm --prefix web test`
Expected: failures resolving `../src/ticket`, `../src/color`, `../src/ids`.

- [ ] **Step 4: Implement**

`web/src/ticket.ts`:
```ts
export interface Ticket { v: number; uri: string; db: string; token: string; kind: string; role: string; host_addr: string }

const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

export function base32Decode(s: string): Uint8Array {
  const clean = s.replace(/\s+/g, '').replace(/=+$/, '').toUpperCase();
  const out: number[] = [];
  let bits = 0, value = 0;
  for (const ch of clean) {
    const i = ALPHABET.indexOf(ch);
    if (i < 0) throw new Error('This ticket is not valid (bad character).');
    value = (value << 5) | i;
    bits += 5;
    if (bits >= 8) { out.push((value >>> (bits - 8)) & 255); bits -= 8; }
  }
  return new Uint8Array(out);
}

export function decodeTicket(s: string): Ticket {
  try {
    const t = JSON.parse(new TextDecoder().decode(base32Decode(s)));
    if (!t.uri || !t.db || !t.token) throw new Error('missing fields');
    return { host_addr: '', ...t };
  } catch (e) {
    throw new Error(`This ticket could not be read: ${(e as Error).message}`);
  }
}
```
`web/src/color.ts`:
```ts
export function memberHue(id: number): number { return (id * 137.508) % 360; }
export function memberColor(id: number, dark: boolean): string {
  // Comma form: three.js Color does not parse the space-separated hsl() syntax.
  return `hsl(${memberHue(id).toFixed(1)}, 70%, ${dark ? 65 : 55}%)`;
}
```
`web/src/ids.ts`:
```ts
const LEN: Record<string, number> = { o_: 12, t_: 8, m_: 12, b_: 8, r_: 8, h_: 8 };
export function newId(prefix: keyof typeof LEN | string): string {
  const n = LEN[prefix] ?? 12;
  const bytes = crypto.getRandomValues(new Uint8Array(Math.ceil(n / 2)));
  return prefix + Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('').slice(0, n);
}
```

- [ ] **Step 5: Run the tests**

Run: `npm --prefix web test`
Expected: `5 passed`.

- [ ] **Step 6: Commit**

```powershell
git add web
git commit -m "feat(web): scaffold, ticket decoding, member colours, ids"
```

---

### Task 2: Store and mock swarm

**Files:**
- Create: `web/src/store.ts`, `web/src/mock.ts`, `web/test/store.test.ts`

**Interfaces:**
- Produces: row types `Member, Lock, Obj, Param, Task, Activity, Pointer, Brief, Review, Preview, Artifact, Handoff, Workspace` (field names exactly as the contract's columns, camelCase as the TS SDK generates them: `typeId`, `bboxMs`... see Step 1), `class Store { state: State; set(partial); subscribe(fn): () => void; me(): Member | undefined; holderOf(obj): Member | undefined; activePointers(): Pointer[] }`, `startMock(store): () => void`.

- [ ] **Step 1: Write `web/src/store.ts`**

```ts
// Plain state the UI renders. api.ts and mock.ts both write into it; nothing else does.
export interface Member { id: number; name: string; kind: string; client: string; role: string; status: string; statusText: string; fence: number; online: boolean }
export interface Lock { object: string; holder: number }
export interface Obj { name: string; label: string; typeId: string; rev: number; bbox: number[]; volume: number; visible: boolean; broken: boolean; usedBy: string[]; lastActor: number }
export interface Param { alias: string; value: string; num: number }
export interface Task { id: string; title: string; status: string; owner: number; assignee: string; brief: string; objects: string[] }
export interface Activity { id: number; member: number; kind: string; summary: string; atMs: number }
export interface Pointer { member: number; object: string; point: number[]; mode: string; label: string; op: string; seq: number; atMs: number }
export interface Brief { id: string; by: number; text: string; board: string; status: string; planJson: string; stlHash: string; updatedMs: number }
export interface Review { id: string; brief: string; round: number; passed: boolean; findingsJson: string }
export interface Preview { object: string; rev: number; mesh: Uint8Array }
export interface Artifact { hash: string; name: string; mime: string; size: number }
export interface Handoff { id: string; from: number; note: string; trigger: string; status: string; taker: number; createdMs: number }
export interface Workspace { bedXMm: number; bedYMm: number; bedZMm: number }
export interface Op { id: string; by: number; kind: string; status: string; errorCode: string; error: string; argsJson: string }

export interface State {
  connected: boolean; meId: number | null; workspace: Workspace | null;
  members: Member[]; locks: Lock[]; objects: Obj[]; params: Param[]; tasks: Task[]; ops: Op[];
  activity: Activity[]; pointers: Pointer[]; briefs: Brief[]; reviews: Review[];
  previews: Map<string, Preview>; artifacts: Artifact[]; handoffs: Handoff[];
}

export function emptyState(): State {
  return { connected: false, meId: null, workspace: null, members: [], locks: [], objects: [], params: [], tasks: [], ops: [],
    activity: [], pointers: [], briefs: [], reviews: [], previews: new Map(), artifacts: [], handoffs: [] };
}

export class Store {
  state: State = emptyState();
  private fns = new Set<(s: State) => void>();
  private queued = false;

  set(partial: Partial<State>) {
    Object.assign(this.state, partial);
    if (this.queued) return;
    this.queued = true;
    queueMicrotask(() => { this.queued = false; for (const f of this.fns) f(this.state); });
  }
  subscribe(fn: (s: State) => void): () => void { this.fns.add(fn); fn(this.state); return () => this.fns.delete(fn); }
  member(id: number) { return this.state.members.find(m => m.id === id); }
  me() { return this.state.meId == null ? undefined : this.member(this.state.meId); }
  holderOf(object: string) { const l = this.state.locks.find(x => x.object === object); return l ? this.member(l.holder) : undefined; }
  /** Pointers of live members that are not idle. */
  activePointers() {
    return this.state.pointers.filter(p => p.mode !== 'idle' && this.member(p.member)?.status === 'active');
  }
}
```

- [ ] **Step 2: Write the failing test `web/test/store.test.ts`**

```ts
import { describe, expect, it } from 'vitest';
import { Store } from '../src/store';

const m = (id: number, status = 'active') => ({ id, name: `m${id}`, kind: 'agent', client: '', role: 'member', status, statusText: '', fence: 1, online: false });

describe('Store', () => {
  it('batches updates and notifies subscribers once per tick', async () => {
    const s = new Store();
    let calls = 0;
    s.subscribe(() => calls++);
    s.set({ connected: true });
    s.set({ meId: 1 });
    await Promise.resolve();
    expect(calls).toBe(2); // the immediate call on subscribe + one batched notification
  });
  it('resolves lock holders and hides idle or stale pointers', () => {
    const s = new Store();
    s.set({ members: [m(1), m(2, 'stale')], locks: [{ object: 'Lid', holder: 1 }],
      pointers: [
        { member: 1, object: 'Lid', point: [], mode: 'editing', label: '', op: '', seq: 1, atMs: 0 },
        { member: 2, object: 'Base', point: [], mode: 'pointing', label: '', op: '', seq: 1, atMs: 0 },
      ] });
    expect(s.holderOf('Lid')?.name).toBe('m1');
    expect(s.activePointers().map(p => p.member)).toEqual([1]);
  });
});
```

- [ ] **Step 3: Run the tests**

Run: `npm --prefix web test`
Expected: all pass (store tests included).

- [ ] **Step 4: Write `web/src/mock.ts`**

```ts
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
```

- [ ] **Step 5: Commit**

```powershell
git add web/src/store.ts web/src/mock.ts web/test/store.test.ts
git commit -m "feat(web): store and a scripted mock swarm"
```

---

### Task 3: Spring motion and pointer ordering (pure)

**Files:**
- Create: `web/src/viewer/spring.ts`, `web/test/spring.test.ts`

**Interfaces:**
- Produces: `class Spring3 { pos: [n,n,n]; vel: [n,n,n]; target: [n,n,n]; constructor(start, omega = 9); step(dt): void; snap(): void; settled(eps = 0.01): boolean }`; `acceptSeq(lastSeen: Map<number, number>, member: number, seq: number): boolean`.

- [ ] **Step 1: Write the failing tests**

`web/test/spring.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { Spring3, acceptSeq } from '../src/viewer/spring';

describe('Spring3', () => {
  it('glides to the target without overshooting (critically damped)', () => {
    const s = new Spring3([0, 0, 0]);
    s.target = [10, 0, 0];
    let max = 0;
    for (let i = 0; i < 120; i++) { s.step(1 / 60); max = Math.max(max, s.pos[0]); }
    expect(s.pos[0]).toBeCloseTo(10, 1);
    expect(max).toBeLessThanOrEqual(10.0001);
  });
  it('is stable for large frame gaps', () => {
    const s = new Spring3([0, 0, 0]);
    s.target = [5, 5, 5];
    s.step(0.5);
    expect(Number.isFinite(s.pos[0])).toBe(true);
    expect(Math.abs(s.pos[0])).toBeLessThan(6);
  });
  it('snap jumps straight to the target (reduced motion)', () => {
    const s = new Spring3([0, 0, 0]);
    s.target = [1, 2, 3];
    s.snap();
    expect(s.pos).toEqual([1, 2, 3]);
    expect(s.settled()).toBe(true);
  });
});

describe('acceptSeq', () => {
  it('drops pointer updates that arrive out of order', () => {
    const seen = new Map<number, number>();
    expect(acceptSeq(seen, 1, 5)).toBe(true);
    expect(acceptSeq(seen, 1, 4)).toBe(false);
    expect(acceptSeq(seen, 1, 6)).toBe(true);
    expect(acceptSeq(seen, 2, 1)).toBe(true);
  });
});
```

- [ ] **Step 2: Run to see them fail**

Run: `npm --prefix web test`
Expected: cannot resolve `../src/viewer/spring`.

- [ ] **Step 3: Implement `web/src/viewer/spring.ts`**

```ts
type V3 = [number, number, number];

/** Critically damped spring (exact solution per step, so any dt is stable and it never overshoots). */
export class Spring3 {
  pos: V3; vel: V3 = [0, 0, 0]; target: V3;
  constructor(start: V3, private omega = 9) { this.pos = [...start] as V3; this.target = [...start] as V3; }
  step(dt: number) {
    const w = this.omega, e = Math.exp(-w * dt);
    for (let i = 0; i < 3; i++) {
      const x = this.pos[i] - this.target[i];
      const v = this.vel[i];
      const temp = (v + w * x) * dt;
      this.vel[i] = (v - w * temp) * e;
      this.pos[i] = this.target[i] + (x + temp) * e;
    }
  }
  snap() { this.pos = [...this.target] as V3; this.vel = [0, 0, 0]; }
  settled(eps = 0.01) {
    return this.pos.every((p, i) => Math.abs(p - this.target[i]) < eps) && this.vel.every(v => Math.abs(v) < eps);
  }
}

export function acceptSeq(lastSeen: Map<number, number>, member: number, seq: number): boolean {
  const prev = lastSeen.get(member) ?? -1;
  if (seq <= prev) return false;
  lastSeen.set(member, seq);
  return true;
}
```

- [ ] **Step 4: Run the tests**

Run: `npm --prefix web test`
Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add web/src/viewer/spring.ts web/test/spring.test.ts
git commit -m "feat(web): critically damped spring and pointer seq ordering"
```

---

### Task 4: The 3D viewer with pointers and edit effects

**Files:**
- Create: `web/src/viewer/scene.ts`, `web/src/viewer/pointers.ts`
- Modify: `web/src/main.ts`, `web/index.html`, `web/src/style.css`

**Interfaces:**
- Consumes: `Store`, `Spring3`, `acceptSeq`, `memberColor`.
- Produces: `mountViewer(el: HTMLElement, store: Store, onPick: (object: string, point: [n,n,n] | null) => void): void`.

- [ ] **Step 1: Write `web/src/viewer/scene.ts`**

```ts
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import type { Store, State } from '../store';
import { memberColor } from '../color';
import { Pointers } from './pointers';

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;

interface Part { mesh: THREE.Mesh; rev: number; fadeIn: number; flash: number; shake: number }

export function mountViewer(el: HTMLElement, store: Store, onPick: (object: string, point: [number, number, number] | null) => void) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  el.appendChild(renderer.domElement);
  const labels = new CSS2DRenderer();
  labels.domElement.className = 'labels';
  el.appendChild(labels.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 5000);
  camera.position.set(120, -160, 140);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = !reduced();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(80, -60, 160);
  scene.add(sun);
  const grid = new THREE.GridHelper(200, 20, 0x888888, 0xcccccc);
  grid.rotation.x = Math.PI / 2;
  scene.add(grid);

  const loader = new STLLoader();
  const parts = new Map<string, Part>();
  const pointers = new Pointers(scene, store);
  let framed = false;

  function material(state: State, name: string) {
    const holder = state.locks.find(l => l.object === name);
    const base = new THREE.Color(dark() ? 0x9aa7b4 : 0xb8c2cc);
    if (holder) base.lerp(new THREE.Color(memberColor(holder.holder, dark())), 0.35); // lock tint
    return new THREE.MeshStandardMaterial({ color: base, roughness: 0.55, metalness: 0.05, transparent: true });
  }

  function sync(state: State) {
    for (const [name, p] of parts) {
      if (!state.objects.some(o => o.name === name && o.visible)) { scene.remove(p.mesh); parts.delete(name); }
    }
    for (const o of state.objects) {
      const pv = state.previews.get(o.name);
      if (!o.visible || !pv || pv.mesh.length < 84) continue;
      const existing = parts.get(o.name);
      if (existing && existing.rev === pv.rev) {
        (existing.mesh.material as THREE.MeshStandardMaterial).color.copy(material(state, o.name).color);
        continue;
      }
      const geom = loader.parse(pv.mesh.slice().buffer);
      geom.computeVertexNormals();
      const mesh = new THREE.Mesh(geom, material(state, o.name));
      mesh.userData.object = o.name;
      if (existing) scene.remove(existing.mesh);
      parts.set(o.name, { mesh, rev: pv.rev, fadeIn: reduced() ? 1 : 0, flash: existing ? 1 : 0, shake: 0 });
      scene.add(mesh);
    }
    for (const op of state.ops) { // a rejected op shakes the object it targeted
      if (op.status === 'rejected' && !shaken.has(op.id)) {
        shaken.add(op.id);
        const name = (() => { try { return JSON.parse(op.argsJson).name as string; } catch { return ''; } })();
        const p = parts.get(name);
        if (p && !reduced()) p.shake = 1;
      }
    }
    if (!framed && parts.size) { frame(); framed = true; }
  }
  const shaken = new Set<string>();

  function frame() {
    const box = new THREE.Box3();
    for (const p of parts.values()) box.expandByObject(p.mesh);
    const c = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3()).length();
    controls.target.copy(c);
    camera.position.copy(c.clone().add(new THREE.Vector3(0.7, -0.9, 0.8).multiplyScalar(size * 1.2)));
  }

  store.subscribe(sync);

  const ray = new THREE.Raycaster();
  renderer.domElement.addEventListener('pointerup', ev => {
    const r = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1), camera);
    const hit = ray.intersectObjects([...parts.values()].map(p => p.mesh))[0];
    onPick(hit ? hit.object.userData.object : '', hit ? [hit.point.x, hit.point.y, hit.point.z] : null);
  });

  function resize() {
    const w = el.clientWidth, h = el.clientHeight;
    renderer.setSize(w, h); labels.setSize(w, h);
    camera.aspect = w / Math.max(h, 1); camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(el);
  resize();

  let last = performance.now();
  renderer.setAnimationLoop(now => {
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    const state = store.state;
    for (const [name, p] of parts) {
      const mat = p.mesh.material as THREE.MeshStandardMaterial;
      p.fadeIn = Math.min(1, p.fadeIn + dt * 2.5);                    // cross-fade a new revision in
      mat.opacity = p.fadeIn;
      const editing = state.pointers.find(x => x.object === name && x.mode === 'editing');
      const pulse = editing && !reduced() ? 0.25 + 0.25 * Math.sin(now / 180) : 0;
      p.flash = Math.max(0, p.flash - dt * 1.5);                      // one flash when an edit lands
      const glow = editing ? new THREE.Color(memberColor(editing.member, dark())) : new THREE.Color(0xffffff);
      mat.emissive.copy(glow).multiplyScalar(Math.max(pulse, p.flash * 0.6));
      p.shake = Math.max(0, p.shake - dt * 2.5);                      // rejected edit
      p.mesh.position.x = p.shake ? Math.sin(now / 25) * 1.2 * p.shake : 0;
    }
    pointers.update(dt, name => parts.get(name)?.mesh ?? null);
    controls.update();
    renderer.render(scene, camera);
    labels.render(scene, camera);
  });
}
```

- [ ] **Step 2: Write `web/src/viewer/pointers.ts`**

```ts
import * as THREE from 'three';
import { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import type { Store } from '../store';
import { memberColor } from '../color';
import { Spring3, acceptSeq } from './spring';

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;
const GLYPH: Record<string, string> = { human: '➤', agent: '◆', reviewer: '⌕', host: '■' };

interface Glyph { group: THREE.Group; spring: Spring3; tag: HTMLDivElement; line: THREE.Line; fade: number }

export class Pointers {
  private glyphs = new Map<number, Glyph>();
  private seen = new Map<number, number>();
  constructor(private scene: THREE.Scene, private store: Store) {}

  private make(member: number): Glyph {
    const m = this.store.member(member);
    const color = memberColor(member, dark());
    const tag = document.createElement('div');
    tag.className = 'pointer-tag';
    tag.style.setProperty('--c', color);
    const label = new CSS2DObject(tag);
    const group = new THREE.Group();
    const dot = new THREE.Mesh(new THREE.SphereGeometry(1.6, 16, 12), new THREE.MeshBasicMaterial({ color }));
    group.add(dot, label);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.6 }));
    this.scene.add(group, line);
    tag.dataset.glyph = GLYPH[m?.kind ?? 'agent'] ?? '◆';
    return { group, spring: new Spring3([0, 0, 40]), tag, line, fade: 0 };
  }

  update(dt: number, meshOf: (name: string) => THREE.Mesh | null) {
    const live = new Set<number>();
    for (const p of this.store.activePointers()) {
      if (p.member === this.store.state.meId) continue;
      live.add(p.member);
      const g = this.glyphs.get(p.member) ?? this.make(p.member);
      this.glyphs.set(p.member, g);
      if (acceptSeq(this.seen, p.member, p.seq) || g.fade === 0) {
        const target = p.point.length === 3 ? new THREE.Vector3().fromArray(p.point) : this.anchor(meshOf(p.object));
        if (target) g.spring.target = [target.x, target.y, target.z];
        if (reduced()) g.spring.snap();
      }
      g.spring.step(dt);
      g.group.position.set(...g.spring.pos);
      const m = this.store.member(p.member);
      g.tag.textContent = `${g.tag.dataset.glyph} ${m?.name ?? p.member}${p.label ? ' · ' + p.label : ''}`;
      g.tag.classList.toggle('editing', p.mode === 'editing');
      const mesh = meshOf(p.object);
      g.line.visible = p.mode === 'editing' && !!mesh;
      if (mesh) {
        const c = new THREE.Box3().setFromObject(mesh).getCenter(new THREE.Vector3());
        (g.line.geometry as THREE.BufferGeometry).setFromPoints([g.group.position.clone(), c]);
      }
      g.fade = Math.min(1, g.fade + dt * 4);
      g.tag.style.opacity = String(g.fade);
    }
    for (const [id, g] of this.glyphs) {
      if (live.has(id)) continue;
      g.fade = Math.max(0, g.fade - dt);                                // idle pointers fade out over a second
      g.tag.style.opacity = String(g.fade);
      if (g.fade === 0) { this.scene.remove(g.group, g.line); g.tag.remove(); this.glyphs.delete(id); }
    }
  }

  /** The point a pointer rests on: just above the object's bounding box centre. */
  private anchor(mesh: THREE.Mesh | null): THREE.Vector3 | null {
    if (!mesh) return null;
    const b = new THREE.Box3().setFromObject(mesh);
    const c = b.getCenter(new THREE.Vector3());
    return c.setZ(b.max.z + 3);
  }
}
```

- [ ] **Step 3: Mount it from `web/src/main.ts` with the mock**

```ts
import './style.css';
import { Store } from './store';
import { startMock } from './mock';
import { mountViewer } from './viewer/scene';

const store = new Store();
const app = document.querySelector<HTMLDivElement>('#app')!;
app.innerHTML = `<main class="layout"><section id="viewer" class="viewer"></section><aside id="panels" class="panels"></aside></main>`;
mountViewer(document.getElementById('viewer')!, store, () => {});
if (new URLSearchParams(location.search).has('mock')) startMock(store);
```
`web/src/style.css` (replace):
```css
:root { --bg: #f6f7f9; --fg: #15191e; --muted: #5d6874; --panel: #ffffff; --line: #e1e5ea; --accent: #2f6fde;
        font-family: system-ui, -apple-system, Segoe UI, sans-serif; color: var(--fg); background: var(--bg); }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --bg: #0f1216; --fg: #e8ecf1; --muted: #9aa5b1; --panel: #171b21; --line: #262c34; --accent: #6b9cff; }
}
:root[data-theme="dark"] { --bg: #0f1216; --fg: #e8ecf1; --muted: #9aa5b1; --panel: #171b21; --line: #262c34; --accent: #6b9cff; }
html, body, #app { margin: 0; height: 100%; background: var(--bg); }
.layout { display: grid; grid-template-columns: 1fr 360px; height: 100%; }
.viewer { position: relative; min-height: 0; }
.viewer canvas, .viewer .labels { position: absolute; inset: 0; }
.viewer .labels { pointer-events: none; }
.panels { background: var(--panel); border-left: 1px solid var(--line); overflow: auto; padding: 16px; }
.pointer-tag { transform: translate(10px, -14px); padding: 2px 8px; border-radius: 999px; font: 600 12px/1.6 system-ui;
               color: #fff; background: var(--c); white-space: nowrap; box-shadow: 0 1px 4px rgb(0 0 0 / .25); }
.pointer-tag.editing { outline: 2px solid color-mix(in srgb, var(--c) 40%, transparent); }
@media (max-width: 760px) {
  .layout { grid-template-columns: 1fr; grid-template-rows: 55vh auto; }
  .panels { border-left: 0; border-top: 1px solid var(--line); padding: 16px; }
}
```

- [ ] **Step 4: Check it by eye**

Run: `npm --prefix web run dev`, open `http://localhost:5173/?mock`.
Expected: parts appear one by one and fade in; coloured agent pointers glide between parts with name tags; the part being built pulses in the builder's colour and flashes when it lands; locked parts are tinted. With the OS "reduce motion" setting on, pointers jump and nothing pulses. Check at 360 px wide in the browser's device toolbar.

- [ ] **Step 5: Commit**

```powershell
git add web
git commit -m "feat(web): three.js viewer with animated pointers, lock tints and edit effects"
```

---

### Task 5: Real data: pairing, subscriptions, reducers (needs M0 bindings)

**Files:**
- Create: `web/src/session.ts`, `web/src/api.ts`, `web/src/ui/pair.ts`
- Modify: `web/src/main.ts`

**Interfaces:**
- Consumes: `web/src/module_bindings/` (generated by A).
- Produces: `connect(store, uri, db, token?): Promise<Api>`; `interface Api { fence(): number; redeem(token, name): Promise<void>; submitBrief(text, board): string; lock(names): void; release(names): void; setParam(alias, value): void; movePointer(object, point, mode, label): void; logActivity(kind, summary, target): void; requestHandoff(note, trigger): string; cancelHandoff(id): void; downloadArtifact(hash, filename): Promise<void> }`; `mountPair(el, ticket, onDone)`.

- [ ] **Step 1: Read the generated API**

`git fetch origin; git rebase origin/main`, then open `web/src/module_bindings/index.ts`. Write down: the builder's database method (`withDatabaseName` in 2.x docs; `withModuleName` in 1.x), how views are reached (expected `conn.db.vMember`), whether table handles expose `onInsert`/`onDelete`/`onUpdate` and `iter()`, whether a subscription may filter a view (`SELECT * FROM v_artifact_blob WHERE hash = '…'`; if not, subscribe to the whole view only for the download and unsubscribe after), and whether reducers take positional args (`conn.reducers.lockObjects(fence, names)`) or one object (`conn.reducers.lockObjects({ fence, names })`). Only `api.ts` changes if these differ from the code below.

- [ ] **Step 2: Write `web/src/session.ts`**

```ts
const key = (uri: string, db: string) => `cadswarm:${uri}:${db}`;
export interface Saved { uri: string; db: string; token: string }
export function loadSession(uri: string, db: string): string | null {
  try { return localStorage.getItem(key(uri, db)); } catch { return null; }
}
export function saveSession(s: Saved) { try { localStorage.setItem(key(s.uri, s.db), s.token); localStorage.setItem('cadswarm:last', JSON.stringify({ uri: s.uri, db: s.db })); } catch { /* private mode */ } }
export function lastDb(): { uri: string; db: string } | null {
  try { return JSON.parse(localStorage.getItem('cadswarm:last') ?? 'null'); } catch { return null; }
}
```

- [ ] **Step 3: Write `web/src/api.ts`**

```ts
// The only file that touches the generated SpacetimeDB bindings.
import { DbConnection } from './module_bindings';
import type { Store, State } from './store';
import { newId } from './ids';
import { saveSession } from './session';

const VIEWS = ['v_me', 'v_workspace', 'v_member', 'v_lock', 'v_object', 'v_param', 'v_op', 'v_task', 'v_activity',
  'v_pointer', 'v_brief', 'v_review', 'v_preview', 'v_artifact', 'v_handoff'];

export interface Api {
  fence(): number;
  redeem(token: string, name: string): Promise<void>;
  submitBrief(text: string, board: string): string;
  lock(names: string[]): void;
  release(names: string[]): void;
  setParam(alias: string, value: string): void;
  movePointer(object: string, point: number[], mode: string, label: string): void;
  logActivity(kind: string, summary: string, target: string[]): void;
  requestHandoff(note: string, trigger: 'now' | 'on_leave'): string;
  cancelHandoff(id: string): void;
  downloadArtifact(hash: string, filename: string): Promise<void>;
}

export function connect(store: Store, uri: string, db: string, token: string | null): Promise<Api> {
  return new Promise((resolve, reject) => {
    const conn = DbConnection.builder()
      .withUri(uri)
      .withDatabaseName(db)
      .withToken(token ?? undefined)
      .withAutomaticReconnect()
      .onConnect((c: any, _identity: any, tok: string) => {
        saveSession({ uri, db, token: tok });
        c.subscriptionBuilder().onApplied(() => { pull(); resolve(api); }).subscribe(VIEWS.map(v => `SELECT * FROM ${v}`));
        store.set({ connected: true });
      })
      .onDisconnect(() => store.set({ connected: false }))
      .onConnectError((_c: any, e: unknown) => reject(e))
      .build();

    const all = (h: any) => Array.from(h.iter()) as any[];
    let scheduled = false;
    function pull() {
      const me = all(conn.db.vMe)[0];
      const ws = all(conn.db.vWorkspace)[0];
      const previews = new Map<string, any>();
      for (const p of all(conn.db.vPreview)) previews.set(p.object, { object: p.object, rev: p.rev, mesh: new Uint8Array(p.mesh) });
      store.set({
        meId: me ? Number(me.id) : null,
        workspace: ws ? { bedXMm: ws.bedXMm, bedYMm: ws.bedYMm, bedZMm: ws.bedZMm } : null,
        members: all(conn.db.vMember).map(m => ({ ...m, id: Number(m.id), fence: Number(m.fence) })),
        locks: all(conn.db.vLock).map(l => ({ object: l.object, holder: Number(l.holder) })),
        objects: all(conn.db.vObject).map(o => ({ ...o, lastActor: Number(o.lastActor) })),
        params: all(conn.db.vParam),
        ops: all(conn.db.vOp).map(o => ({ ...o, by: Number(o.by) })),
        tasks: all(conn.db.vTask).map(t => ({ ...t, owner: Number(t.owner) })),
        activity: all(conn.db.vActivity).map(a => ({ ...a, id: Number(a.id), member: Number(a.member), atMs: Number(a.atMs) })).sort((a, b) => a.id - b.id),
        pointers: all(conn.db.vPointer).map(p => ({ ...p, member: Number(p.member), seq: Number(p.seq), atMs: Number(p.atMs) })),
        briefs: all(conn.db.vBrief).map(b => ({ ...b, by: Number(b.by), updatedMs: Number(b.updatedMs) })),
        reviews: all(conn.db.vReview),
        previews,
        artifacts: all(conn.db.vArtifact).map(a => ({ hash: a.hash, name: a.name, mime: a.mime, size: Number(a.size) })),
        handoffs: all(conn.db.vHandoff).map(h => ({ ...h, from: Number(h.from), taker: Number(h.taker), createdMs: Number(h.createdMs) })),
      } as Partial<State>);
    }
    function schedule() { if (!scheduled) { scheduled = true; requestAnimationFrame(() => { scheduled = false; pull(); }); } }
    for (const v of VIEWS) {
      const h = (conn.db as any)[v.replace(/_(\w)/g, (_, c) => c.toUpperCase())];
      h?.onInsert?.(schedule); h?.onDelete?.(schedule); h?.onUpdate?.(schedule);
    }

    const fence = () => store.me()?.fence ?? 0;
    const r = conn.reducers as any;
    const api: Api = {
      fence,
      redeem: (tok, name) => new Promise((ok, fail) => {
        r.redeemInvite(tok, name, 'phone', '');
        const t0 = Date.now();
        const poll = setInterval(() => {
          pull();
          if (store.state.meId != null) { clearInterval(poll); ok(); }
          else if (Date.now() - t0 > 10000) { clearInterval(poll); fail(new Error('The invite was not accepted (used or expired?)')); }
        }, 200);
      }),
      submitBrief: (text, board) => { const id = newId('b_'); r.submitBrief(fence(), id, text, board); return id; },
      lock: names => r.lockObjects(fence(), names),
      release: names => r.releaseObjects(fence(), names, false),
      setParam: (alias, value) => {
        r.lockObjects(fence(), ['Params']);
        r.submitOp(fence(), newId('o_'), 'set_param', JSON.stringify({ alias, value }), '');
        setTimeout(() => r.releaseObjects(fence(), ['Params'], false), 4000);
      },
      movePointer: (object, point, mode, label) => r.movePointer(fence(), object, point, mode, label),
      logActivity: (kind, summary, target) => r.logActivity(fence(), kind, summary, target),
      requestHandoff: (note, trigger) => { const id = newId('h_'); r.requestHandoff(fence(), id, note, trigger); return id; },
      cancelHandoff: id => r.cancelHandoff(fence(), id),
      // Export bytes are never subscribed (plan 00): subscribe to the one blob, save it, unsubscribe.
      downloadArtifact: (hash, filename) => new Promise<void>((ok, fail) => {
        if (!/^[0-9a-f]{64}$/.test(hash)) { fail(new Error('Unknown export')); return; }
        const sub = conn.subscriptionBuilder().onApplied(() => {
          const row = all(conn.db.vArtifactBlob).find(b => b.hash === hash);
          sub.unsubscribe();
          if (!row) { fail(new Error('This export is too large to store; get it from the host')); return; }
          const mime = store.state.artifacts.find(a => a.hash === hash)?.mime ?? 'application/octet-stream';
          const url = URL.createObjectURL(new Blob([new Uint8Array(row.bytes)], { type: mime }));
          const link = document.createElement('a');
          link.href = url; link.download = filename; link.click();
          setTimeout(() => URL.revokeObjectURL(url), 10000);
          ok();
        }).subscribe([`SELECT * FROM v_artifact_blob WHERE hash = '${hash}'`]);
      }),
    };
  });
}
```

- [ ] **Step 4: Write `web/src/ui/pair.ts`**

```ts
import type { Ticket } from '../ticket';

export function mountPair(el: HTMLElement, t: Ticket, join: (name: string) => Promise<void>) {
  el.innerHTML = `
    <form class="pair">
      <h1>Join the build</h1>
      <p>You're joining <b>${t.db}</b>. Pick a name your teammates will see.</p>
      <input name="name" required maxlength="40" pattern="[A-Za-z0-9_-]+" autocomplete="nickname" value="guest-${Math.floor(Math.random() * 900 + 100)}">
      <button type="submit">Join</button>
      <p class="err" role="alert"></p>
    </form>`;
  const form = el.querySelector('form')!;
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const btn = form.querySelector('button')!;
    btn.disabled = true;
    try { await join((form.elements.namedItem('name') as HTMLInputElement).value); }
    catch (err) { form.querySelector('.err')!.textContent = (err as Error).message; btn.disabled = false; }
  });
}
```

- [ ] **Step 5: Boot real or mock data in `web/src/main.ts`**

Replace the last two lines of `main.ts` with:
```ts
import { decodeTicket } from './ticket';
import { connect } from './api';
import { loadSession, lastDb } from './session';
import { mountPair } from './ui/pair';
import { mountPanels } from './ui/panels';

async function boot() {
  if (new URLSearchParams(location.search).has('mock')) { startMock(store); mountPanels(document.getElementById('panels')!, store, null); return; }
  const frag = new URLSearchParams(location.hash.slice(1)).get('t');
  history.replaceState(null, '', location.pathname + location.search); // never leave the ticket in the address bar
  const t = frag ? decodeTicket(frag) : null;
  const target = t ? { uri: t.uri, db: t.db } : lastDb();
  if (!target) { document.getElementById('panels')!.innerHTML = '<p>Scan the invite QR from the host to join.</p>'; return; }
  const api = await connect(store, target.uri, target.db, loadSession(target.uri, target.db));
  if (store.state.meId == null && t) {
    await new Promise<void>(done => mountPair(document.getElementById('panels')!, t, async name => { await api.redeem(t.token, name); done(); }));
  }
  mountPanels(document.getElementById('panels')!, store, api);
}
boot().catch(e => { document.getElementById('panels')!.innerHTML = `<p class="err">${(e as Error).message}</p>`; });
```
Create `web/src/ui/panels.ts` with a stub until Task 6:
```ts
import type { Store } from '../store';
import type { Api } from '../api';
export function mountPanels(el: HTMLElement, _store: Store, _api: Api | null) { el.textContent = 'connected'; }
```
In `mountViewer(...)`'s pick callback in `main.ts`, wire human pointers (throttled to 5/s, only when moved):
```ts
let lastSent = 0, lastKey = '';
const onPick = (object: string, point: [number, number, number] | null) => {
  const key = object + (point ?? []).map(v => v.toFixed(1)).join(',');
  if (!apiRef || key === lastKey || performance.now() - lastSent < 200) return;
  lastKey = key; lastSent = performance.now();
  apiRef.movePointer(object, point ?? [], object ? 'inspecting' : 'pointing', object ? `looking at ${object}` : '');
};
```
(`apiRef` is a module-level `let apiRef: Api | null = null` set after `connect` resolves; pass `onPick` to `mountViewer`.)

- [ ] **Step 6: Pair against A's server**

With the host running (`cargo run -- invite --kind human` prints a `http://localhost:5173/#t=...` link), open the link. Expected: the join form; after joining, `connected` and the viewer shows whatever objects exist; reloading reconnects without the ticket.

- [ ] **Step 7: Commit**

```powershell
git add web
git commit -m "feat(web): pair by ticket, live subscriptions, reducer wrappers, human pointers"
```

---

### Task 6: Panels: brief, tree, tasks, activity, params, review card, STL download

**Files:**
- Modify: `web/src/ui/panels.ts`
- Create: `web/src/ui/brief.ts`, `web/src/ui/params.ts`

**Interfaces:**
- Consumes: `Store`, `Api`, `boards/boards.json` (copied into `web/public/boards.json` at build time).
- Produces: `mountPanels(el, store, api)`.

- [ ] **Step 1: Copy the board library at build time**

Add to `web/package.json` scripts: `"prebuild": "node -e \"require('fs').copyFileSync('../boards/boards.json','public/boards.json')\""` and `"predev": "npm run prebuild"`.

- [ ] **Step 2: Write `web/src/ui/brief.ts`**

```ts
import type { Api } from '../api';

export async function mountBrief(el: HTMLElement, api: Api | null) {
  const boards: Record<string, { label: string }> = await fetch('./boards.json').then(r => r.json()).catch(() => ({}));
  el.innerHTML = `
    <form class="brief">
      <label>What should we build?
        <textarea name="text" required rows="3" placeholder="A case for my ESP32-S3 with a USB-C cutout and a snap-on lid"></textarea>
      </label>
      <label>Board
        <select name="board"><option value="">None</option>${Object.entries(boards).map(([id, b]) => `<option value="${id}">${b.label}</option>`).join('')}</select>
      </label>
      <button type="submit">Build it</button>
    </form>`;
  el.querySelector('form')!.addEventListener('submit', e => {
    e.preventDefault();
    const f = e.target as HTMLFormElement;
    api?.submitBrief((f.elements.namedItem('text') as HTMLTextAreaElement).value, (f.elements.namedItem('board') as HTMLSelectElement).value);
    f.reset();
  });
}
```

- [ ] **Step 3: Write `web/src/ui/params.ts`**

```ts
import type { Api } from '../api';
import type { Store } from '../store';

export function renderParams(el: HTMLElement, store: Store, api: Api | null) {
  const editable = ['wall', 'gap', 'standoff_h', 'clear_top'];
  el.innerHTML = store.state.params.filter(p => editable.includes(p.alias)).map(p => `
    <label class="param">${p.alias}<input data-alias="${p.alias}" inputmode="decimal" value="${p.num}"> mm</label>`).join('')
    || '<p class="muted">Dimensions appear once a brief starts.</p>';
  el.querySelectorAll<HTMLInputElement>('input[data-alias]').forEach(inp => inp.addEventListener('change', () => {
    const alias = inp.dataset.alias!;
    api?.setParam(alias, `${inp.value} mm`);
    api?.logActivity('status', `changed ${alias} to ${inp.value} mm`, ['Params']);
  }));
}
```

- [ ] **Step 4: Replace `web/src/ui/panels.ts`**

```ts
import type { Store, State } from '../store';
import type { Api } from '../api';
import { memberColor } from '../color';
import { mountBrief } from './brief';
import { renderParams } from './params';
import { mountHandoff } from './handoff';

const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;
const esc = (s: string) => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));

export function mountPanels(el: HTMLElement, store: Store, api: Api | null) {
  el.innerHTML = `
    <section id="brief"></section>
    <section id="status" class="card"></section>
    <section id="handoff"></section>
    <h2>Dimensions</h2><section id="params"></section>
    <h2>Parts</h2><ul id="tree" class="list"></ul>
    <h2>Tasks</h2><ul id="tasks" class="list"></ul>
    <h2>Activity</h2><ol id="activity" class="list feed"></ol>`;
  mountBrief(el.querySelector('#brief')!, api);
  mountHandoff(el.querySelector('#handoff')!, store, api);
  store.subscribe(s => render(s));

  function chip(id: number) {
    const m = store.member(id);
    return m ? `<span class="chip" style="--c:${memberColor(id, dark())}">${esc(m.name)}</span>` : '';
  }

  function render(s: State) {
    const brief = [...s.briefs].sort((a, b) => b.updatedMs - a.updatedMs)[0];
    const review = brief && s.reviews.filter(r => r.brief === brief.id).sort((a, b) => b.round - a.round)[0];
    el.querySelector('#status')!.innerHTML = brief ? `
      <p class="muted">${esc(brief.text)}</p>
      <p><b>${brief.status}</b>${review ? ` · review ${review.round}: ${review.passed ? 'passed' : 'changes needed'}` : ''}</p>
      ${brief.stlHash && api ? '<button class="button" id="stl">Download STL</button>' : ''}` : '<p class="muted">No brief yet.</p>';
    el.querySelector<HTMLButtonElement>('#stl')?.addEventListener('click', () => {
      api!.downloadArtifact(brief!.stlHash, `cadswarm-${brief!.id}.stl`).catch(e => alert(String(e.message ?? e)));
    });
    renderParams(el.querySelector('#params')!, store, api);
    el.querySelector('#tree')!.innerHTML = s.objects.filter(o => o.name !== 'Params').map(o => {
      const h = s.locks.find(l => l.object === o.name);
      const editing = s.pointers.find(p => p.object === o.name && p.mode === 'editing');
      return `<li class="${editing ? 'editing' : ''}">${esc(o.label)} <span class="muted">rev ${o.rev}</span> ${h ? chip(h.holder) : ''}</li>`;
    }).join('');
    el.querySelector('#tasks')!.innerHTML = s.tasks.map(t =>
      `<li><span class="status ${t.status}">${t.status}</span> ${esc(t.title)} ${t.owner ? chip(t.owner) : ''}</li>`).join('');
    el.querySelector('#activity')!.innerHTML = s.activity.slice(-40).reverse().map(a =>
      `<li>${chip(a.member)} ${esc(a.summary)}</li>`).join('');
  }
}
```
Append to `web/src/style.css`:
```css
h2 { font: 600 13px/1.4 system-ui; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 20px 0 8px; }
.card { border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; margin: 12px 0; }
.list { list-style: none; margin: 0; padding: 0; } .list li { padding: 6px 0; border-bottom: 1px solid var(--line); }
.list li.editing { background: color-mix(in srgb, var(--accent) 10%, transparent); }
.chip { display: inline-block; padding: 0 8px; border-radius: 999px; color: #fff; background: var(--c); font: 600 12px/1.7 system-ui; }
.muted { color: var(--muted); } .err { color: #d0342c; }
.status { font: 600 11px/1 system-ui; text-transform: uppercase; color: var(--muted); }
textarea, input, select { width: 100%; box-sizing: border-box; font: inherit; padding: 10px; border-radius: 10px; border: 1px solid var(--line); background: var(--panel); color: var(--fg); }
button, .button { display: inline-block; margin-top: 10px; padding: 10px 16px; border: 0; border-radius: 10px; background: var(--accent); color: #fff; font: 600 15px system-ui; text-decoration: none; }
.param { display: grid; grid-template-columns: 1fr 90px auto; align-items: center; gap: 8px; margin: 6px 0; }
.param input { width: 90px; }
```

- [ ] **Step 5: Check it**

With the mock (`?mock`) and then against the host with a real brief. Expected: submitting a brief shows its status; parts appear in the tree with their holder's chip and highlight while being edited; tasks and activity update live; changing `wall` updates parts and the activity feed; when the brief is ready, "Download STL" saves a file that opens in a slicer.

- [ ] **Step 6: Commit**

```powershell
git add web
git commit -m "feat(web): brief, status, params, parts, tasks, activity and STL download"
```

---

### Task 7 (P1): Hand off to an AI

**Files:**
- Create: `web/src/ui/handoff.ts`

**Interfaces:**
- Consumes: `Api.requestHandoff`, `Api.cancelHandoff`, `Store.state.handoffs` (contract table `handoff`).
- Produces: `mountHandoff(el, store, api)`.

- [ ] **Step 1: Write `web/src/ui/handoff.ts`**

```ts
import type { Store } from '../store';
import type { Api } from '../api';

export function mountHandoff(el: HTMLElement, store: Store, api: Api | null) {
  el.innerHTML = `
    <details class="card handoff">
      <summary>Hand your work to an AI</summary>
      <label>Where you left off (optional)
        <textarea name="note" rows="2" placeholder="Making the lid lip 1 mm thinner so it snaps in"></textarea></label>
      <button type="button" data-now>Hand off now</button>
      <label class="row"><input type="checkbox" data-arm> If I leave, let an AI continue my work</label>
      <p class="muted" data-state></p>
    </details>`;
  const note = el.querySelector<HTMLTextAreaElement>('textarea')!;
  let armed: string | null = null;
  el.querySelector('[data-now]')!.addEventListener('click', () => {
    api?.requestHandoff(note.value, 'now');
    note.value = '';
  });
  el.querySelector<HTMLInputElement>('[data-arm]')!.addEventListener('change', e => {
    if (!api) return;
    if ((e.target as HTMLInputElement).checked) armed = api.requestHandoff(note.value, 'on_leave');
    else if (armed) { api.cancelHandoff(armed); armed = null; }
  });
  store.subscribe(s => {
    const mine = s.handoffs.filter(h => h.from === s.meId).sort((a, b) => b.createdMs - a.createdMs)[0];
    const taker = mine?.taker ? store.member(mine.taker)?.name : null;
    el.querySelector('[data-state]')!.textContent = !mine ? '' :
      mine.status === 'waiting' ? 'Armed: an AI will continue if you leave.' :
      mine.status === 'open' ? 'Finding an AI to continue…' :
      mine.status === 'accepted' ? `${taker ?? 'An AI'} picked up where you left off.` : '';
  });
}
```

- [ ] **Step 2: Check it**

Against a running host with Task 8 of the host plan done: lock a part from the phone (tap it, lock), hand off with a note. Expected: "Finding an AI…", then "<agent> picked up where you left off"; the agent's pointer glides to the part you were on; its activity mentions your note; your lock chip on that part changes to the agent's colour.

- [ ] **Step 3: Commit**

```powershell
git add web/src/ui/handoff.ts
git commit -m "feat(web): hand work off to an AI, now or when you leave"
```

---

### Task 8: Deploy to GitHub Pages

**Files:**
- Create: `.github/workflows/pages.yml`

**Interfaces:**
- Produces: the public web app URL used in host invites (`cadswarm-host init --web-url <url>`).

- [ ] **Step 1: Write the workflow**

```yaml
name: Deploy web app
on:
  push:
    branches: [main]
    paths: ["web/**", "boards/**"]
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: npm ci && npm run build
        working-directory: web
      - uses: actions/upload-pages-artifact@v3
        with: { path: web/dist }
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: github-pages
    steps:
      - uses: actions/deploy-pages@v4
```

- [ ] **Step 2: Enable Pages and deploy**

The repo owner enables Pages with source "GitHub Actions" in the repo settings (the repo is private; Pages on a private repo needs a paid plan: if unavailable, run `npx vercel deploy web/dist` or serve `web/dist` from any static host instead). Push to `main`. Expected: the workflow publishes; opening `<pages URL>/?mock` shows the mock swarm on a phone.

- [ ] **Step 3: Commit**

```powershell
git add .github/workflows/pages.yml
git commit -m "ci: deploy the web app to GitHub Pages"
```
