// Step plans (Brief.plan_json version 2, with the version-1 `parts` fallback) and the progress every client
// shows: "3 of 20 done · now: Pitch link", per-step state, and the CAD ops each step ran with their timings.
// Pure: no DOM, no network. Mirrors cadswarm/step_plan.py so the app and host read a plan the same way.
// Contract: docs/superpowers/specs/2026-11-06-step-build-and-pro-ui-design.md ("Progress model").
import type { Op, Task } from './store';

export type StepKind = 'part' | 'library' | 'assembly' | 'check';
export const STEP_KINDS: readonly StepKind[] = ['part', 'library', 'assembly', 'check'];

export interface Mate { type: string; a: string; b: string }
export interface Port { name: string; type: string }

export interface PlanStep {
  n: number; id: string; name: string; kind: StepKind; summary: string; task: string;
  objects: string[]; dependsOn: string[]; ops: string[]; assignee: string;
  /** Library steps: the chosen part (`{id}` or `{family, size, length}`), as the planner wrote it. */
  component: Record<string, unknown> | null;
  /** Optional extras a planner may add: named key sizes (mm), checkable targets, interfaces, mates. */
  params: Record<string, number | string>; targets: Record<string, unknown>; ports: Port[]; mates: Mate[];
}

export interface Plan { version: number; title: string; steps: PlanStep[] }

/** done: task done · current: claimed · ready: open, dependencies done · waiting: open, dependencies not done. */
export type StepState = 'done' | 'current' | 'ready' | 'waiting' | 'blocked' | 'cancelled' | 'missing';

export interface OpLine {
  id: string; kind: string; label: string; target: string; status: string;
  startedMs: number; finishedMs: number;
  /** finished - started, or null while it runs (or if the host never stamped it). */
  durationMs: number | null; running: boolean; failed: boolean; error: string;
}

export interface StepView extends PlanStep {
  state: StepState; taskRow: Task | undefined; owner: number;
  /** Names of the unfinished steps an open step waits for. */
  waitingFor: string[]; opLines: OpLine[];
}

export interface Progress {
  title: string; steps: StepView[]; total: number; done: number; current: StepView[];
  /** "N of M done · now: X, Y" */
  line: string; fraction: number;
}

const str = (v: unknown) => (v == null ? '' : String(v).trim());
const list = (v: unknown): unknown[] => v == null || v === '' ? [] : Array.isArray(v) ? v.filter(x => x != null && str(x) !== '') : [v];
const int = (v: unknown): number | null => {
  if (typeof v === 'boolean') return null;
  const f = Number(v);
  return Number.isFinite(f) && Number.isInteger(f) && str(v) !== '' ? f : null;
};
const obj = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
const KIND_OF = (k: unknown): StepKind => {
  const s = str(k).toLowerCase();
  return (STEP_KINDS as readonly string[]).includes(s) ? s as StepKind : 'part';
};

function toStep(raw: unknown, index: number): PlanStep {
  const r = obj(raw);
  const bare = typeof raw === 'string' ? raw : '';
  const n = int(r.n) ?? index + 1;
  const params: Record<string, number | string> = {};
  for (const [k, v] of Object.entries(obj(r.params))) if (typeof v === 'number' || typeof v === 'string') params[k] = v;
  const comp = r.component;
  return {
    n, id: str(r.id) || `s${String(n).padStart(2, '0')}`,
    name: str(r.name ?? r.title) || bare, kind: KIND_OF(r.kind), summary: str(r.summary),
    task: str(r.task ?? r.task_id),
    objects: list(r.objects).map(str), dependsOn: list(r.depends_on ?? r.deps).map(d => str(d)),
    ops: list(r.ops).map(str), assignee: str(r.assignee),
    component: comp && typeof comp === 'object' ? obj(comp) : str(comp) ? { id: str(comp) } : null,
    params, targets: obj(r.targets),
    ports: list(r.ports).map(p => ({ name: str(obj(p).name ?? p), type: str(obj(p).type) })).filter(p => p.name),
    mates: list(r.mates).map(m => ({ type: str(obj(m).type), a: str(obj(m).a), b: str(obj(m).b) })).filter(m => m.type || m.a),
  };
}

/** Dependencies may name a step by id or by number; keep ids. */
function resolveDeps(steps: PlanStep[]) {
  const ids = new Set(steps.map(s => s.id));
  const byN = new Map(steps.map(s => [s.n, s.id]));
  for (const s of steps) {
    const out: string[] = [];
    for (const d of s.dependsOn) {
      const ref = ids.has(d) ? d : int(d) != null && byN.has(int(d)!) ? byN.get(int(d)!)! : d;
      if (!out.includes(ref)) out.push(ref);
    }
    s.dependsOn = out;
  }
}

/** "3. Pitch link" → [3, "Pitch link"]. */
export function titleNumber(title: string): [number | null, string] {
  const m = /^\s*(\d+)[.)]\s*(.*)$/.exec(title);
  return m ? [Number(m[1]), m[2].trim()] : [null, title.trim()];
}

/**
 * The plan of a brief, version 2 or 1. Without a readable plan the brief's own tasks become the steps
 * (numbered by a "3. Name" title, else in the order they were made), so a build always has a step list.
 */
export function parsePlan(planJson: string, tasks: Task[] = [], briefId = ''): Plan {
  let p: Record<string, unknown> = {};
  try { p = obj(planJson ? JSON.parse(planJson) : {}); } catch { p = {}; }
  let raw: unknown[] | null = Array.isArray(p.steps) ? p.steps : null;
  if (!raw && Array.isArray(p.parts)) {
    raw = p.parts.map((part, i) => {
      const r = obj(part);
      return typeof part === 'string' ? { n: i + 1, name: part }
        : { n: i + 1, name: r.name, task: r.task, summary: r.summary, objects: r.objects };
    });
  }
  if (!raw && briefId) {
    const mine = tasks.filter(t => t.brief === briefId)
      .map((t, i) => ({ t, i, n: titleNumber(t.title)[0] }))
      .sort((a, b) => (a.n ?? 1e9) - (b.n ?? 1e9) || (a.t.createdMs ?? 0) - (b.t.createdMs ?? 0) || a.i - b.i);
    raw = mine.map(({ t }, i) => ({ n: i + 1, name: titleNumber(t.title)[1] || t.title, task: t.id, objects: t.objects }));
  }
  const steps = (raw ?? []).map(toStep);
  resolveDeps(steps);
  steps.sort((a, b) => a.n - b.n);
  // A v1 part without an explicit task is matched to the brief's task that names it.
  for (const s of steps) {
    if (s.task) continue;
    const t = tasks.find(x => x.brief === briefId && (titleNumber(x.title)[0] === s.n
      || x.objects.includes(s.name) || titleNumber(x.title)[1] === s.name));
    if (t) s.task = t.id;
  }
  return { version: int(p.version) ?? (Array.isArray(p.steps) ? 2 : 1), title: str(p.title), steps };
}

// ---- ops ----

/** `create_sketch` → `Create sketch`. */
export function humanise(kind: string): string {
  const t = str(kind).replace(/[-_]+/g, ' ').trim();
  return t ? t[0].toUpperCase() + t.slice(1) : '';
}

const TARGET_KEYS = ['name', 'base', 'tool', 'target', 'object', 'objects', 'names', 'targets', 'a', 'b'];

/** Every object name an op's arguments mention, in argument order. */
export function opTargets(argsJson: string): string[] {
  let a: Record<string, unknown> = {};
  try { a = obj(argsJson ? JSON.parse(argsJson) : {}); } catch { return []; }
  const out: string[] = [];
  for (const k of TARGET_KEYS) {
    const v = a[k];
    for (const x of typeof v === 'string' ? [v] : Array.isArray(v) ? v : []) {
      if (typeof x === 'string' && x && !out.includes(x)) out.push(x);
    }
  }
  return out;
}

const BOOLEAN_WORD: Record<string, string> = { cut: 'Cut', fuse: 'Union', common: 'Intersect' };
const COLOR_PROPS = ['ShapeColor', 'Color', 'DiffuseColor', 'color'];

/** The feed's words for an op, from the fixed vocabulary: "Create sketch", "Extrude", "Set color"… */
export function opLabel(kind: string, argsJson = ''): string {
  let a: Record<string, unknown> = {};
  try { a = obj(argsJson ? JSON.parse(argsJson) : {}); } catch { /* keep {} */ }
  switch (kind) {
    case 'create_extrusion': return 'Extrude';
    case 'create_primitive': return `Create ${str(a.kind) || 'solid'}`;
    case 'boolean': return BOOLEAN_WORD[str(a.kind)] ?? 'Boolean';
    case 'set_property': return COLOR_PROPS.includes(str(a.prop)) ? 'Set color' : `Set ${str(a.prop) || 'property'}`;
    case 'set_props': return COLOR_PROPS.some(p => p in obj(a.props)) ? 'Set color' : 'Set properties';
    case 'set_placement': return 'Place';
    case 'insert_standard_part': return 'Place standard part';
    case 'insert_component': return 'Insert component';
    case 'interference': return 'Check interference';
    case 'printability': return 'Check printability';
    case 'run_part_script': return 'Build';
    case 'set_param': return `Set ${str(a.alias) || 'parameter'}`;
    case 'library_recommend': return 'Recommend parts';
    case 'library_search': return 'Search library';
    case 'mate': return str(a.type) ? `Mate ${str(a.type)}` : 'Mate';
    default: return humanise(kind);
  }
}

export function opLine(op: Op): OpLine {
  const started = op.startedMs ?? 0, finished = op.finishedMs ?? 0;
  const running = op.status === 'queued' || op.status === 'running' || (!finished && op.status !== 'rejected' && op.status !== 'failed');
  const failed = op.status === 'failed' || op.status === 'rejected';
  return {
    id: op.id, kind: op.kind, label: opLabel(op.kind, op.argsJson), target: opTargets(op.argsJson)[0] ?? '',
    status: op.status, startedMs: started, finishedMs: finished,
    durationMs: !running && started && finished >= started ? finished - started : null,
    running, failed, error: op.error,
  };
}

/** 0.4 s · 1.9 s · 12 s · 1 min 05 s */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '';
  if (ms < 10_000) return `${(Math.round(ms / 100) / 10).toFixed(1)} s`;
  if (ms < 60_000) return `${Math.round(ms / 1000)} s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
}

// ---- progress ----

const stamp = (o: Op) => o.startedMs || o.createdMs || 0;

/**
 * Which step each op belongs to. First the owner window: an op by a step's task owner, made after the task
 * was created and no later than its report (the done task's last update) or, for the task being worked on,
 * any time after the owner's previous report. Ops outside every window go to the step that lists an object
 * they name (a reviewer's fix, a person's edit).
 */
export function attributeOps(steps: PlanStep[], tasks: Task[], ops: Op[]): Map<string, Op[]> {
  const out = new Map<string, Op[]>(steps.map(s => [s.id, []]));
  const taskOf = new Map(tasks.map(t => [t.id, t]));
  const owned = steps.map(s => ({ s, t: taskOf.get(s.task) })).filter(x => x.t && x.t.owner);
  const objectStep = new Map<string, string>();
  for (const s of steps) for (const o of s.objects) if (!objectStep.has(o)) objectStep.set(o, s.id);
  for (const op of [...ops].sort((a, b) => stamp(a) - stamp(b))) {
    const t = stamp(op);
    let best: { id: string; end: number } | null = null;
    for (const { s, t: task } of owned) {
      if (task!.owner !== op.by || (task!.createdMs ?? 0) > t) continue;
      const end = task!.status === 'done' ? task!.updatedMs ?? 0 : Infinity;
      if (task!.status !== 'done' && task!.status !== 'claimed' && task!.status !== 'awaiting_takeover') continue;
      if (t <= end && (!best || end < best.end)) best = { id: s.id, end };
    }
    const id = best?.id ?? opTargets(op.argsJson).map(n => objectStep.get(n)).find(Boolean);
    if (id) out.get(id)!.push(op);
  }
  return out;
}

const STATE_OF: Record<string, StepState> = {
  done: 'done', claimed: 'current', awaiting_takeover: 'current', blocked: 'blocked', cancelled: 'cancelled',
};

export function progress(plan: Plan, tasks: Task[], ops: Op[] = []): Progress {
  const taskOf = new Map(tasks.map(t => [t.id, t]));
  const byId = new Map(plan.steps.map(s => [s.id, s]));
  const status = (s: PlanStep) => taskOf.get(s.task)?.status ?? 'missing';
  const opsBy = attributeOps(plan.steps, tasks, ops);
  const steps: StepView[] = plan.steps.map(s => {
    const st = status(s);
    const waitingFor = s.dependsOn.filter(d => { const dep = byId.get(d); return !dep || status(dep) !== 'done'; })
      .map(d => byId.get(d)?.name ?? d);
    const state: StepState = STATE_OF[st] ?? (st === 'open' ? (waitingFor.length ? 'waiting' : 'ready') : 'missing');
    const row = taskOf.get(s.task);
    return { ...s, state, taskRow: row, owner: row?.owner ?? 0, waitingFor: st === 'open' ? waitingFor : [],
      opLines: (opsBy.get(s.id) ?? []).map(opLine) };
  });
  const done = steps.filter(s => s.state === 'done').length;
  const current = steps.filter(s => s.state === 'current');
  const total = steps.length;
  return { title: plan.title, steps, total, done, current, line: summaryLine(done, total, current.map(s => s.name)),
    fraction: total ? done / total : 0 };
}

export function summaryLine(done: number, total: number, current: string[]): string {
  return `${done} of ${total} done` + (current.length ? ` · now: ${current.join(', ')}` : '');
}

/** The step a model object was built in (first step listing it). */
export function stepOfObject(plan: Plan, name: string): PlanStep | undefined {
  return plan.steps.find(s => s.objects.includes(name));
}

/** How many leading steps are all done: where the feature tree draws its rollback bar. */
export function rollbackIndex(steps: Pick<StepView, 'state'>[]): number {
  const i = steps.findIndex(s => s.state !== 'done');
  return i < 0 ? steps.length : i;
}
