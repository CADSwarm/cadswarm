// The only file that touches the generated SpacetimeDB bindings (web/src/module_bindings, SDK 2.13.0).
//
// Binding shapes this file relies on (checked against the generated code):
// - builder: DbConnection.builder().withUri().withDatabaseName().withToken().withAutomaticReconnect()
// - views:   conn.db.vMe, conn.db.vMember, ... (camelCase accessors; snake_case aliases are deprecated)
// - rows:    camelCase fields (statusText, bedXMm, stlHash, atMs); u64 columns arrive as bigint, u32 as number,
//            Vec<u8> as Uint8Array
// - reducers: conn.reducers.lockObjects({ fence, names }): one object argument, returns a Promise that
//            rejects with Error("<code>: <message>") when the reducer returns Err
import type { Infer } from 'spacetimedb';
import { DbConnection } from './module_bindings';
import type VMemberRow from './module_bindings/v_member_table';
import type VObjectRow from './module_bindings/v_object_table';
import type VTaskRow from './module_bindings/v_task_table';
import type VActivityRow from './module_bindings/v_activity_table';
import type VPointerRow from './module_bindings/v_pointer_table';
import type VBriefRow from './module_bindings/v_brief_table';
import type VReviewRow from './module_bindings/v_review_table';
import type VArtifactRow from './module_bindings/v_artifact_table';
import type VHandoffRow from './module_bindings/v_handoff_table';
import type VOpRow from './module_bindings/v_op_table';
import type VMessageRow from './module_bindings/v_message_table';
import type VComponentRow from './module_bindings/v_component_table';
import type VSettingRow from './module_bindings/v_setting_table';
import type VSuggestionRow from './module_bindings/v_suggestion_table';
import type {
  Activity, Artifact, Brief, Component, Handoff, Member, Message, Obj, Op, Pointer, Preview, Review, Setting, State,
  Store, Suggestion, Task,
} from './store';
import { newId } from './ids';
import { saveSession } from './session';
import { saveBytes } from './download';

const VIEWS = ['v_me', 'v_workspace', 'v_member', 'v_lock', 'v_object', 'v_param', 'v_op', 'v_task', 'v_activity',
  'v_pointer', 'v_brief', 'v_review', 'v_preview', 'v_artifact', 'v_handoff', 'v_message', 'v_receipt', 'v_component',
  'v_setting', 'v_suggestion'] as const;

/** What placing a library part needs: a team part's id, or a standard family + size (+ length). */
export interface PlaceRequest { component?: string; family?: string; size?: string; length?: number; name: string; label?: string }
export interface PartMeta { name: string; category: string; tags: string[]; description: string; license: string }

/** A file in the team's project folder (the host's files/ directory); `updated_at` is epoch seconds. */
export interface ProjectFile {
  path: string; version: number; size: number; author: string; updated_at: number;
}
export interface ProjectListing { files: ProjectFile[]; folders: string[] }
/** One replaced copy kept in history. */
export interface FileVersion { version: number; size: number; author: string; saved_at: number; reason: string }
export interface FileHistory { path: string; current: ProjectFile | null; older_versions: FileVersion[] }

/** One part as the worker measured it. */
export interface PrintPart {
  name: string; size: number[]; fitsBed: boolean; closedSolid: boolean;
  /** Distances (mm) of walls that look too thin to print. */
  thinWalls: number[];
}
export interface Printability { ok: boolean; objects: PrintPart[] }

/** The worker answers in snake_case; take whatever fields arrive and name them for the UI. */
export function toPrintability(v: Record<string, unknown> | null): Printability {
  const list = Array.isArray(v?.objects) ? v.objects : [];
  const objects = list.filter(o => o && typeof o === 'object').map(o => {
    const r = o as Record<string, unknown>;
    const thin = Array.isArray(r.thin_wall_suspects) ? r.thin_wall_suspects : [];
    return {
      name: String(r.name ?? 'part'),
      size: Array.isArray(r.size) ? r.size.map(Number).filter(Number.isFinite) : [],
      fitsBed: r.fits_bed !== false,
      closedSolid: r.closed_solid !== false,
      thinWalls: thin.map(w => Number(w && typeof w === 'object' ? (w as Record<string, unknown>).distance : w))
        .filter(Number.isFinite),
    };
  });
  return { ok: v?.ok === true, objects };
}

export interface Api {
  fence(): number;
  redeem(token: string, name: string): Promise<void>;
  submitBrief(text: string, board: string): string;
  lock(names: string[]): Promise<void>;
  release(names: string[]): Promise<void>;
  setParam(alias: string, value: string): Promise<void>;
  /** The printer bed the team builds for, in millimetres. */
  setBed(xMm: number, yMm: number, zMm: number): Promise<void>;
  /** Stop a task (a person may stop any; a helper only one it asked for or claimed). */
  cancelTask(id: string, reason: string): Promise<void>;
  /** Remove a leftover open task; a claimed one must be cancelled instead. */
  deleteTask(id: string): Promise<void>;
  /** Stop a whole request; its unstarted tasks are cancelled with it. */
  cancelBrief(id: string): Promise<void>;
  /** Ask the host whether the parts fit the bed, are closed solids and have printable walls. */
  checkPrintability(): Promise<Printability | null>;
  movePointer(object: string, point: number[], mode: string, label: string): void;
  logActivity(kind: string, summary: string, target: string[]): void;
  requestHandoff(note: string, trigger: 'now' | 'on_leave'): string;
  cancelHandoff(id: string): void;
  /** An instruction for an AI helper (by name, or "all"): it does it first and its receipt says how far it got. */
  sendCommand(to: string, body: string, taskId?: string): string;
  downloadArtifact(hash: string, filename: string): Promise<void>;
  /** Place a library part in the model; resolves true once the host has built it. */
  placeComponent(req: PlaceRequest): Promise<boolean>;
  /** Upload a part file (STEP, FCStd, STL) to the team library. */
  addComponent(filename: string, bytes: Uint8Array, meta: PartMeta): Promise<boolean>;
  importComponentUrl(url: string, meta: PartMeta): Promise<boolean>;
  setSetting(setting: string, num: number, text: string): Promise<void>;
  resetSetting(setting: string): Promise<void>;
  resolveSuggestion(id: string, accept: boolean): Promise<void>;
  /** A library part's preview STL (null until FreeCAD has measured it). */
  componentPreview(id: string): Promise<Uint8Array | null>;
  /** The team's project folder: files and folders under files/ on the host. */
  listFiles(folder?: string): Promise<ProjectListing | null>;
  /** Every kept version of one project file (null when the host can't answer). */
  fileHistory(path: string): Promise<FileHistory | null>;
}

// ---- Row mapping (pure; u64 → number is safe for ids, seqs and epoch millis) ----

const num = (v: bigint | number) => Number(v);

export const toMember = (r: Infer<typeof VMemberRow>): Member => ({
  id: num(r.id), name: r.name, kind: r.kind, client: r.client, role: r.role, status: r.status,
  statusText: r.statusText, fence: r.fence, online: r.online,
});
export const toObj = (r: Infer<typeof VObjectRow>): Obj => ({
  name: r.name, label: r.label || r.name, typeId: r.typeId, rev: r.rev, bbox: [...r.bbox], volume: r.volume,
  visible: r.visible, broken: r.broken, usedBy: [...r.usedBy], lastActor: num(r.lastActor),
});
export const toTask = (r: Infer<typeof VTaskRow>): Task => ({
  id: r.id, title: r.title, status: r.status, owner: num(r.owner), assignee: r.assignee, brief: r.brief, objects: [...r.objects],
});
export const toActivity = (r: Infer<typeof VActivityRow>): Activity => ({
  id: num(r.id), member: num(r.member), kind: r.kind, summary: r.summary, atMs: num(r.atMs),
});
export const toPointer = (r: Infer<typeof VPointerRow>): Pointer => ({
  member: num(r.member), object: r.object, point: [...r.point], mode: r.mode, label: r.label, op: r.op, seq: r.seq, atMs: num(r.atMs),
});
export const toBrief = (r: Infer<typeof VBriefRow>): Brief => ({
  id: r.id, by: num(r.by), text: r.text, board: r.board, status: r.status, planJson: r.planJson, stlHash: r.stlHash, updatedMs: num(r.updatedMs),
});
export const toReview = (r: Infer<typeof VReviewRow>): Review => ({
  id: r.id, brief: r.brief, round: r.round, passed: r.passed, findingsJson: r.findingsJson, atMs: num(r.atMs),
});
export const toArtifact = (r: Infer<typeof VArtifactRow>): Artifact => ({ hash: r.hash, name: r.name, mime: r.mime, size: num(r.size) });
export const toHandoff = (r: Infer<typeof VHandoffRow>): Handoff => ({
  id: r.id, from: num(r.from), note: r.note, trigger: r.trigger, status: r.status, taker: num(r.taker), createdMs: num(r.createdMs),
});
export const toMessage = (r: Infer<typeof VMessageRow>): Message => ({
  id: r.id, from: num(r.from), to: r.to, kind: r.kind, body: r.body, taskId: r.taskId, createdMs: num(r.createdMs),
});
export const toOp = (r: Infer<typeof VOpRow>): Op => ({
  id: r.id, by: num(r.by), kind: r.kind, status: r.status, errorCode: r.errorCode, error: r.error,
  argsJson: r.argsJson, resultJson: r.resultJson,
});

export const toComponent = (r: Infer<typeof VComponentRow>): Component => ({
  id: r.id, name: r.name, category: r.category, tags: [...r.tags], description: r.description, source: r.source,
  sourceUrl: r.sourceUrl, license: r.license, author: r.author, format: r.format, size: num(r.size), bbox: [...r.bbox],
  volume: r.volume, featuresJson: r.featuresJson, suggestJson: r.suggestJson, uses: r.uses, updatedMs: num(r.updatedMs),
});
export const toSetting = (r: Infer<typeof VSettingRow>): Setting => ({
  setting: r.setting, num: r.num, text: r.text, setBy: num(r.setBy), updatedMs: num(r.updatedMs),
});
export const toSuggestion = (r: Infer<typeof VSuggestionRow>): Suggestion => ({
  id: r.id, setting: r.setting, num: r.num, text: r.text, reason: r.reason, component: r.component, by: num(r.by),
  status: r.status, createdMs: num(r.createdMs),
});

/** Reducer errors are "<code>: <message>" (plan 00); split on the first ": ". */
export function splitError(e: unknown): { code: string; message: string } {
  const s = e instanceof Error ? e.message : String(e);
  const i = s.indexOf(': ');
  if (i > 0 && /^[a-z_]+$/.test(s.slice(0, i))) return { code: s.slice(0, i), message: s.slice(i + 2) };
  return { code: '', message: s };
}

// Whether the server accepts `WHERE` on a view subscription. Unknown until the first download tries it.
let filteredViewOk = true;

export function connect(store: Store, uri: string, db: string, token: string | null): Promise<Api> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const conn = DbConnection.builder()
      .withUri(uri)
      .withDatabaseName(db)
      .withToken(token ?? undefined)
      .withAutomaticReconnect()
      .onConnect((c, _identity, tok) => {
        saveSession({ uri, db, token: tok });
        store.set({ connected: true });
        c.subscriptionBuilder()
          .onApplied(() => { pull(); void ready(); })
          .onError((_ctx, e?: unknown) => fail(e ?? new Error('Could not subscribe to the build.')))
          .subscribe(VIEWS.map(v => `SELECT * FROM ${v}`));
      })
      .onAutomaticReconnect(() => store.set({ connected: true }))
      .onDisconnect(() => store.set({ connected: false }))
      .onConnectError((_ctx, e) => { if (!settled) fail(e); else store.set({ connected: false }); })
      .build();

    function fail(e: unknown) {
      if (settled) { report(e); return; }
      settled = true;
      reject(e instanceof Error ? e : new Error(String(e)));
    }

    /** First subscription applied: a returning member rejoins (plan 00: the web app calls rejoin on connect). */
    async function ready() {
      if (settled) return;
      const me = meRow();
      if (me && (me.status === 'active' || me.status === 'stale')) {
        try { await conn.reducers.rejoin({ client: 'phone' }); } catch (e) { report(e); }
        pull();
      }
      settled = true;
      resolve(api);
    }

    const meRow = () => {
      const rows = [...conn.db.vMe.iter()];
      return rows.sort((a, b) => Number(b.id - a.id))[0];
    };

    let scheduled = false;
    let prevPreviews = new Map<string, Preview>();
    function pull() {
      const me = meRow();
      const live = me && (me.status === 'active' || me.status === 'stale');
      const ws = [...conn.db.vWorkspace.iter()][0];
      const previews = new Map<string, Preview>();
      for (const p of conn.db.vPreview.iter()) {
        const old = prevPreviews.get(p.object);
        previews.set(p.object, old && old.rev === p.rev ? old : { object: p.object, rev: p.rev, mesh: p.mesh });
      }
      prevPreviews = previews;
      store.set({
        meId: live ? num(me.id) : null,
        workspace: ws ? { bedXMm: ws.bedXMm, bedYMm: ws.bedYMm, bedZMm: ws.bedZMm } : null,
        members: [...conn.db.vMember.iter()].map(toMember),
        locks: [...conn.db.vLock.iter()].map(l => ({ object: l.object, holder: num(l.holder) })),
        objects: [...conn.db.vObject.iter()].map(toObj),
        params: [...conn.db.vParam.iter()].map(p => ({ alias: p.alias, value: p.value, num: p.num })),
        ops: [...conn.db.vOp.iter()].map(toOp),
        tasks: [...conn.db.vTask.iter()].map(toTask),
        activity: [...conn.db.vActivity.iter()].map(toActivity).sort((a, b) => a.id - b.id),
        pointers: [...conn.db.vPointer.iter()].map(toPointer),
        briefs: [...conn.db.vBrief.iter()].map(toBrief),
        reviews: [...conn.db.vReview.iter()].map(toReview),
        previews,
        artifacts: [...conn.db.vArtifact.iter()].map(toArtifact),
        handoffs: [...conn.db.vHandoff.iter()].map(toHandoff),
        messages: [...conn.db.vMessage.iter()].map(toMessage),
        receipts: [...conn.db.vReceipt.iter()].map(r => ({ messageId: r.messageId, member: num(r.member), state: r.state })),
        components: [...conn.db.vComponent.iter()].map(toComponent),
        settings: [...conn.db.vSetting.iter()].map(toSetting),
        suggestions: [...conn.db.vSuggestion.iter()].map(toSuggestion),
      } satisfies Partial<State>);
    }
    function schedule() {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => { scheduled = false; pull(); });
    }
    const handles = [conn.db.vMe, conn.db.vWorkspace, conn.db.vMember, conn.db.vLock, conn.db.vObject, conn.db.vParam,
      conn.db.vOp, conn.db.vTask, conn.db.vActivity, conn.db.vPointer, conn.db.vBrief, conn.db.vReview, conn.db.vPreview,
      conn.db.vArtifact, conn.db.vHandoff, conn.db.vMessage, conn.db.vReceipt, conn.db.vComponent, conn.db.vSetting,
      conn.db.vSuggestion];
    for (const h of handles) {
      h.onInsert(schedule); h.onDelete(schedule);
      // A view without a primary key reports a change as delete + insert; one with a key reports an update.
      const withUpdate = h as unknown as { onUpdate?: (cb: () => void) => void };
      withUpdate.onUpdate?.(schedule);
    }

    function report(e: unknown) {
      const { code, message } = splitError(e);
      console.warn('[cadswarm]', code || 'error', message);
      store.set({ notice: { text: message, at: Date.now() } });
    }
    const quiet = (p: Promise<void>) => p.catch(report);
    /** The fence comes straight from the live cache so a rejoin is picked up before the next pull. */
    const fence = () => meRow()?.fence ?? 0;
    const r = conn.reducers;

    function waitFor(test: () => boolean, ms: number): Promise<boolean> {
      return new Promise(done => {
        const t0 = Date.now();
        const tick = () => { if (test()) done(true); else if (Date.now() - t0 > ms) done(false); else setTimeout(tick, 150); };
        tick();
      });
    }

    /** Queue a host op and wait for it; reports a failure as a notice and resolves how it ended. */
    async function runOpOutcome(kind: string, args: Record<string, unknown>, ms = 60000): Promise<{ ok: boolean; resultJson: string }> {
      const opId = newId('o_');
      try {
        await r.submitOp({ fence: fence(), opId, kind, argsJson: JSON.stringify(args), expectedRevsJson: '' });
      } catch (e) { report(e); return { ok: false, resultJson: '' }; }
      await waitFor(() => ['done', 'failed', 'rejected'].includes(store.state.ops.find(o => o.id === opId)?.status ?? ''), ms);
      const op = store.state.ops.find(o => o.id === opId);
      if (!op || op.status === 'queued' || op.status === 'running') {
        report(new Error('The host is still working on it; check back in a moment.')); return { ok: false, resultJson: '' };
      }
      if (op.status !== 'done') {
        report(new Error(op.error || `${kind.replace(/_/g, ' ')} failed (${op.status})`)); return { ok: false, resultJson: '' };
      }
      return { ok: true, resultJson: op.resultJson };
    }

    /** Queue a host op and wait for it; reports a failure as a notice and resolves its parsed result (null on failure). */
    async function runOpResult(kind: string, args: Record<string, unknown>, ms = 60000): Promise<Record<string, unknown> | null> {
      const { ok, resultJson } = await runOpOutcome(kind, args, ms);
      if (!ok) return null;
      try {
        const v = JSON.parse(resultJson || '{}');
        return v && typeof v === 'object' ? v as Record<string, unknown> : {};
      } catch { return {}; }
    }

    /** Queue a host op and wait for it; reports a failure as a notice and resolves whether it worked. */
    const runOp = async (kind: string, args: Record<string, unknown>, ms = 60000): Promise<boolean> =>
      (await runOpOutcome(kind, args, ms)).ok;

    /** A read-only host query whose result carries the answer (list_files, file_history, …). */
    async function queryOp<T>(kind: string, args: Record<string, unknown>): Promise<T | null> {
      const { ok, resultJson } = await runOpOutcome(kind, args);
      if (!ok) return null;
      try { return JSON.parse(resultJson) as T; } catch { return null; }
    }

    const api: Api = {
      fence,
      redeem: async (tok, name) => {
        try { await r.redeemInvite({ token: tok, name, client: 'phone', endpointId: '' }); }
        catch (e) { throw new Error(splitError(e).message); }
        if (!(await waitFor(() => { pull(); return store.state.meId != null; }, 10000))) {
          throw new Error('Joined, but the build did not show up. Reload to try again.');
        }
      },
      submitBrief: (text, board) => {
        const id = newId('b_');
        void quiet(r.submitBrief({ fence: fence(), briefId: id, text, board }));
        return id;
      },
      lock: names => quiet(r.lockObjects({ fence: fence(), names })),
      release: names => quiet(r.releaseObjects({ fence: fence(), names, force: false })),
      setBed: (xMm, yMm, zMm) => quiet(r.setBed({ fence: fence(), xMm, yMm, zMm })),
      cancelTask: (id, reason) => quiet(r.cancelTask({ fence: fence(), taskId: id, reason })),
      deleteTask: id => quiet(r.deleteTask({ fence: fence(), taskId: id })),
      cancelBrief: id => quiet(r.cancelBrief({ fence: fence(), briefId: id })),
      checkPrintability: async () => {
        const v = await runOpResult('printability', {}, 120000);
        return v === null ? null : toPrintability(v);
      },
      setParam: async (alias, value) => {
        // set_param needs the `Params` lock (rules::needed_locks). Hold it only until the host finishes the op.
        let locked = false;
        try {
          await r.lockObjects({ fence: fence(), names: ['Params'] });
          locked = true;
          const opId = newId('o_');
          await r.submitOp({ fence: fence(), opId, kind: 'set_param', argsJson: JSON.stringify({ alias, value }), expectedRevsJson: '' });
          await waitFor(() => ['done', 'failed', 'rejected'].includes(store.state.ops.find(o => o.id === opId)?.status ?? ''), 20000);
          const op = store.state.ops.find(o => o.id === opId);
          if (op && op.status !== 'done') report(new Error(op.error || `${alias} could not be changed (${op.status})`));
        } catch (e) { report(e); }
        finally { if (locked) await r.releaseObjects({ fence: fence(), names: ['Params'], force: false }).catch(() => {}); }
      },
      movePointer: (object, point, mode, label) =>
        void r.movePointer({ fence: fence(), object, point, mode, label }).catch(e => console.warn('[cadswarm] pointer', e)),
      logActivity: (kind, summary, target) => void quiet(r.logActivity({ fence: fence(), kind, summary, target })),
      requestHandoff: (note, trigger) => {
        const id = newId('h_');
        void quiet(r.requestHandoff({ fence: fence(), handoffId: id, note, trigger }));
        return id;
      },
      cancelHandoff: id => void quiet(r.cancelHandoff({ fence: fence(), handoffId: id })),
      sendCommand: (to, body, taskId = '') => {
        const id = newId('m_');
        void quiet(r.sendMessage({ fence: fence(), messageId: id, to, body, kind: 'command', taskId }));
        return id;
      },
      placeComponent: req => req.family
        ? runOp('insert_standard_part', { family: req.family, size: req.size, name: req.name, label: req.label,
          ...(req.length ? { length: req.length } : {}) })
        : runOp('insert_component', { component: req.component, name: req.name, label: req.label }),
      addComponent: async (filename, bytes, meta) => {
        if (bytes.length > 20 * 1024 * 1024) { report(new Error('Parts uploaded from here can be at most 20 MB; add bigger ones from FreeCAD.')); return false; }
        const { toBase64 } = await import('./ui/library-data');
        return runOp('library_add', { base64: toBase64(bytes), filename, ...meta }, 120000);
      },
      importComponentUrl: (url, meta) => runOp('library_import_url', { url, ...meta }, 120000),
      setSetting: (setting, n, text) => quiet(r.setSetting({ fence: fence(), setting, num: n, text })),
      resetSetting: setting => quiet(r.resetSetting({ fence: fence(), setting })),
      resolveSuggestion: (id, accept) => quiet(r.resolveSuggestion({ fence: fence(), suggestionId: id, accept })),
      listFiles: folder => queryOp<ProjectListing>('list_files', folder ? { folder } : {}),
      fileHistory: path => queryOp<FileHistory>('file_history', { path }),
      // Preview meshes are never kept subscribed: subscribe to the one row, read it, unsubscribe.
      componentPreview: id => new Promise<Uint8Array | null>(done => {
        if (!/^c_[a-z0-9_]+$/.test(id)) { done(null); return; }
        const timer = setTimeout(() => done(null), 15000);
        const take = () => [...conn.db.vComponentPreview.iter()].find(p => p.id === id)?.mesh ?? null;
        const attempt = (sql: string, onRefused: () => void) => {
          const handle = conn.subscriptionBuilder()
            .onApplied(() => {
              const mesh = take();
              try { handle.unsubscribe(); } catch { /* already ended */ }
              clearTimeout(timer);
              done(mesh ? new Uint8Array(mesh) : null);
            })
            .onError(() => onRefused())
            .subscribe(sql);
        };
        const whole = () => attempt('SELECT * FROM v_component_preview', () => { clearTimeout(timer); done(null); });
        if (!filteredViewOk) { whole(); return; }
        attempt(`SELECT * FROM v_component_preview WHERE id = '${id}'`, () => { filteredViewOk = false; whole(); });
      }),
      // Export bytes are never kept subscribed (plan 00): subscribe to the one blob, save it, unsubscribe.
      // If the server refuses WHERE on a view, fall back to the whole view for the length of the download.
      downloadArtifact: (hash, filename) => new Promise<void>((ok, bad) => {
        if (!/^[0-9a-fA-F]{64}$/.test(hash)) { bad(new Error('Unknown export.')); return; }
        const art = store.state.artifacts.find(a => a.hash === hash);
        if (art && art.size === 0) { bad(new Error('This export is too large to keep online; get it from the host.')); return; }
        let finished = false;
        const finish = (err?: Error) => {
          if (finished) return;
          finished = true; clearTimeout(timer);
          if (err) bad(err); else ok();
        };
        const timer = setTimeout(() => finish(new Error('The download timed out.')), 30000);
        const attempt = (sql: string, onRefused: (e: unknown) => void) => {
          const handle = conn.subscriptionBuilder()
            .onApplied(() => {
              const row = [...conn.db.vArtifactBlob.iter()].find(b => b.hash === hash);
              if (row) saveBytes(row.bytes, art?.mime || 'model/stl', filename);
              try { handle.unsubscribe(); } catch { /* already ended */ }
              finish(row ? undefined : new Error('This export is not stored online; get it from the host.'));
            })
            .onError((_ctx, e?: unknown) => onRefused(e))
            .subscribe(sql);
        };
        const whole = () => attempt('SELECT * FROM v_artifact_blob', e => finish(new Error(`Download failed: ${splitError(e).message}`)));
        if (!filteredViewOk) { whole(); return; }
        attempt(`SELECT * FROM v_artifact_blob WHERE hash = '${hash}'`, e => {
          console.warn('[cadswarm] filtered view subscription refused; using the whole view for this download', e);
          filteredViewOk = false;
          whole();
        });
      }),
    };
  });
}
