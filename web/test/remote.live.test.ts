// The web app's remote view and remote control against a LIVE team, through the app's own api.ts (the same code
// the phone runs). Skipped unless CADSWARM_E2E_INVITE holds a ticket (a `#t=` phone-link body or a `cadswarm-2:`
// invite); tests/e2e/team_e2e.py --scenario remote mints one and runs this file with it.
//
//   CADSWARM_E2E_INVITE=<ticket> CADSWARM_E2E_OUT=<dir> npx vitest run test/remote.live.test.ts
import { describe, expect, it } from 'vitest';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { Store } from '../src/store';
import { readInvite } from '../src/ticket';
import type { Api } from '../src/api';

const INVITE = process.env.CADSWARM_E2E_INVITE ?? '';
const OUT = process.env.CADSWARM_E2E_OUT ?? '';
const PART = process.env.CADSWARM_E2E_PART ?? 'PhoneBox';

// Browser bits api.ts touches, for node.
const g = globalThis as Record<string, unknown>;
g.requestAnimationFrame ??= (cb: () => void) => setTimeout(cb, 16);
if (!g.localStorage) {
  const m = new Map<string, string>();
  g.localStorage = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); },
    removeItem: (k: string) => { m.delete(k); } };
}

const until = async (test: () => boolean, ms: number) => {
  const t0 = Date.now();
  while (!test()) {
    if (Date.now() - t0 > ms) return false;
    await new Promise(r => setTimeout(r, 200));
  }
  return true;
};

describe.skipIf(!INVITE)('web remote control on a live team', () => {
  it('joins with the invite, sees the team, chats, builds a part on the host, locks it and points at it', async () => {
    const report: Record<string, unknown> = {};
    const t = readInvite(INVITE);
    const store = new Store();
    const { connect } = await import('../src/api');
    const api: Api = await connect(store, t.uri, t.db, null);
    // A fresh device: the name form redeems the single-use invite.
    await api.redeem(t.token, 'phone');
    expect(await until(() => store.state.meId != null, 15000)).toBe(true);
    report.me = store.me()?.name;
    report.members = store.state.members.map(m => `${m.name}:${m.status}`);
    expect(store.state.members.some(m => m.name === 'host' && m.status === 'active')).toBe(true);

    // Remote view: the team's chat and parts arrive by subscription.
    api.sendChat('hello from the phone');
    expect(await until(() => store.state.messages.some(m => m.body === 'hello from the phone'), 10000)).toBe(true);

    // Remote control: an op the host runs on the live FreeCAD model, under this member's lock.
    const created = await api.runOp('create_primitive', { kind: 'cylinder', name: PART, props: { Radius: 10, Height: 30 } }, [PART]);
    report.created = created;
    expect(created).not.toBeNull();
    expect(await until(() => store.state.objects.some(o => o.name === PART), 20000)).toBe(true);
    report.object = store.state.objects.find(o => o.name === PART);

    api.lock([PART]);
    expect(await until(() => store.holderOf(PART)?.id === store.state.meId, 10000)).toBe(true);
    await api.movePointer(PART, [], 'inspecting', `looking at ${PART}`);
    expect(await until(() => store.state.pointers.some(p => p.member === store.state.meId && p.object === PART), 10000)).toBe(true);
    // A live 3D preview mesh for the remote viewer (the host publishes one per part).
    report.preview = await until(() => store.state.previews.has(PART), 30000);

    // Steer: a command to everyone (`@all ...` from the phone); a person's command is stored as `steer`.
    api.sendCommand('all', 'e2e: phone says hold');
    expect(await until(() => store.state.messages.some(m => m.body === 'e2e: phone says hold' && m.kind === 'steer'), 10000)).toBe(true);
    report.ok = true;
    if (OUT) writeFileSync(join(OUT, 'web-remote.json'), JSON.stringify(report, (_k, v) => typeof v === 'bigint' ? Number(v) : v, 2));
  }, 120000);
});
