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
/** A short message for the person (a refused reducer, a failed download). `at` makes repeats distinct. */
export interface Notice { text: string; at: number }
export interface Op { id: string; by: number; kind: string; status: string; errorCode: string; error: string; argsJson: string; resultJson: string }
/** A team message. `to` is "all", "member:<id>" or "@name" (not joined yet); a person's command is stored as kind "steer". */
export interface Message { id: string; from: number; to: string; kind: string; body: string; taskId: string; createdMs: number }
/** Who has read (delivered) or handled (consumed) a message. */
export interface Receipt { messageId: string; member: number; state: string }
/** A part in the team's components library (the bytes stay on the host; previews are fetched one at a time). */
export interface Component {
  id: string; name: string; category: string; tags: string[]; description: string; source: string; sourceUrl: string;
  license: string; author: string; format: string; size: number; bbox: number[]; volume: number; featuresJson: string;
  suggestJson: string; uses: number; updatedMs: number;
}
/** A team setting a person changed from its default (settings_spec.json); missing = default. */
export interface Setting { setting: string; num: number; text: string; setBy: number; updatedMs: number }
export interface Suggestion {
  id: string; setting: string; num: number; text: string; reason: string; component: string; by: number;
  status: string; createdMs: number;
}

export interface State {
  connected: boolean; meId: number | null; workspace: Workspace | null;
  members: Member[]; locks: Lock[]; objects: Obj[]; params: Param[]; tasks: Task[]; ops: Op[];
  activity: Activity[]; pointers: Pointer[]; briefs: Brief[]; reviews: Review[];
  previews: Map<string, Preview>; artifacts: Artifact[]; handoffs: Handoff[];
  messages: Message[]; receipts: Receipt[]; components: Component[]; settings: Setting[]; suggestions: Suggestion[];
  notice: Notice | null;
}

export function emptyState(): State {
  return { connected: false, meId: null, workspace: null, members: [], locks: [], objects: [], params: [], tasks: [], ops: [],
    activity: [], pointers: [], briefs: [], reviews: [], previews: new Map(), artifacts: [], handoffs: [],
    messages: [], receipts: [], components: [], settings: [], suggestions: [], notice: null };
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
    return this.state.pointers.filter(p =>
      p.mode !== 'idle' && (p.object !== '' || p.point.length === 3) &&
      this.member(p.member)?.status === 'active');
  }
}
