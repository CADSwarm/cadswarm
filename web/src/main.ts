import './style.css';
import { Store } from './store';
import { startMockSwarm } from './mock';
import { mountViewer } from './viewer/scene';
import { decodeTicket, type Ticket } from './ticket';
import type { Api } from './api';
import { loadSession, lastDb, forgetSession } from './session';
import { PointerGate } from './pointer-gate';
import { mountPair } from './ui/pair';
import { mountPanels } from './ui/panels';
import { esc } from './ui/esc';

const store = new Store();
const app = document.querySelector<HTMLDivElement>('#app')!;
app.innerHTML = `<main class="layout"><section id="viewer" class="viewer" aria-label="3D view"></section><aside id="panels" class="panels"></aside></main>`;
const panels = document.getElementById('panels')!;

// Human pointers: at most 5 move_pointer calls a second, only when the pick changed.
let apiRef: Api | null = null;
type Pick = { object: string; point: [number, number, number] | null };
const gate = new PointerGate<Pick>(({ object, point }) =>
  apiRef?.movePointer(object, point ?? [], object ? 'inspecting' : 'pointing', object ? `looking at ${object}` : ''));
mountViewer(document.getElementById('viewer')!, store, (object, point) => {
  if (!apiRef) return;
  gate.push(object + '|' + (point ?? []).map(v => v.toFixed(1)).join(','), { object, point });
});

function message(html: string) { panels.innerHTML = `<div class="welcome">${html}</div>`; }

async function boot() {
  if (new URLSearchParams(location.search).has('mock')) {
    const { api } = startMockSwarm(store); // exercises every panel without a server
    apiRef = api;
    mountPanels(panels, store, api);
    return;
  }
  const frag = new URLSearchParams(location.hash.slice(1)).get('t');
  if (location.hash) history.replaceState(null, '', location.pathname + location.search); // never leave the ticket in the address bar
  let t: Ticket | null = null;
  if (frag) t = decodeTicket(frag);
  const target = t ? { uri: t.uri, db: t.db } : lastDb();
  if (!target) {
    message(`<h1>CadSwarm</h1><p>Scan the invite QR from the host to join a build.</p>
      <p class="muted"><a href="?mock">See a demo build</a></p>`);
    return;
  }
  message('<p class="muted">Connecting…</p>');
  const { connect } = await import('./api');
  const saved = loadSession(target.uri, target.db);
  let api: Api;
  try { api = await connect(store, target.uri, target.db, saved); }
  catch (e) {
    if (!saved || !t) throw e;
    forgetSession(target.uri, target.db); // stored token refused (database reset?); join fresh with the ticket
    api = await connect(store, target.uri, target.db, null);
  }
  if (store.state.meId == null) {
    if (!t) {
      message(`<h1>Not in this build</h1><p>You're no longer a member of <b>${esc(target.db)}</b>. Scan a new invite QR to join.</p>`);
      return;
    }
    const ticket = t;
    await new Promise<void>(done => mountPair(panels, ticket, async name => { await api.redeem(ticket.token, name); done(); }));
  }
  apiRef = api;
  mountPanels(panels, store, api);
}

boot().catch(e => message(`<p class="err">${esc((e as Error).message ?? String(e))}</p>`));
