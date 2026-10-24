import './style.css';
import { Store } from './store';
import { startMockSwarm } from './mock';
import { mountViewer } from './viewer/scene';
import { decodeTicket, reachableUri, readInvite, type Ticket } from './ticket';
import type { Api } from './api';
import { loadSession, lastDb, forgetSession } from './session';
import { PointerGate } from './pointer-gate';
import { mountPair } from './ui/pair';
import { mountPanels } from './ui/panels';
import { esc } from './ui/esc';

const store = new Store();
const app = document.querySelector<HTMLDivElement>('#app')!;
app.innerHTML = `<main class="layout">
  <section id="viewer" class="viewer" aria-label="3D view"></section>
  <header id="topbar" class="topbar"></header>
  <aside id="panels" class="panels sheet" data-stop="peek"></aside>
</main>`;
const panels = document.getElementById('panels')!;
const topbar = document.getElementById('topbar');

// Human pointers: at most 5 move_pointer calls a second, only when the pick changed.
let apiRef: Api | null = null;
type Pick = { object: string; point: [number, number, number] | null };
const gate = new PointerGate<Pick>(({ object, point }) =>
  apiRef?.movePointer(object, point ?? [], object ? 'inspecting' : 'pointing', object ? `looking at ${object}` : ''));
let picked = '';  // the part last tapped in 3D: quick instructions name it
mountViewer(document.getElementById('viewer')!, store, (object, point) => {
  picked = object;
  if (!apiRef) return;
  gate.push(object + '|' + (point ?? []).map(v => v.toFixed(1)).join(','), { object, point });
});

function message(html: string) { panels.innerHTML = `<div class="welcome">${html}</div>`; }

/** "Paste the invite code": the same `cadswarm-2:` code people paste into FreeCAD, when there's no QR to scan. */
function askForCode(intro: string) {
  panels.innerHTML = `<div class="welcome">
    ${intro}
    <form class="pair" data-code>
      <label>Invite code
        <textarea name="code" rows="4" required placeholder="cadswarm-2:…" autocomplete="off" spellcheck="false"></textarea></label>
      <p class="muted small">In FreeCAD: CadSwarm team panel ▸ <b>Invite</b> makes a code and copies it. A code from
        <code>cadswarm-host invite</code> or a phone link works too.</p>
      <button type="submit">Continue</button>
      <p class="err" role="alert"></p>
      <p class="muted small"><a href="?mock">See a demo build</a></p>
    </form></div>`;
  const form = panels.querySelector<HTMLFormElement>('[data-code]')!;
  form.addEventListener('submit', e => {
    e.preventDefault();
    const err = form.querySelector('.err')!;
    try {
      const t = readInvite((form.elements.namedItem('code') as HTMLTextAreaElement).value);
      err.textContent = '';
      start(t).catch(x => message(`<p class="err">${esc((x as Error).message ?? String(x))}</p>`));
    } catch (x) { err.textContent = (x as Error).message; }
  });
}

async function boot() {
  if (new URLSearchParams(location.search).has('mock')) {
    const { api } = startMockSwarm(store); // exercises every panel without a server
    apiRef = api;
    mountPanels(panels, store, api, () => (picked ? [picked] : []), 'Enclosure', topbar);
    return;
  }
  const frag = new URLSearchParams(location.hash.slice(1)).get('t');
  if (location.hash) history.replaceState(null, '', location.pathname + location.search); // never leave the ticket in the address bar
  let t: Ticket | null = null;
  if (frag) t = decodeTicket(frag);
  if (!t && !lastDb()) {
    askForCode('<h1>CadSwarm</h1><p>Scan the invite QR from the host, or paste the invite code your teammate sent you.</p>');
    return;
  }
  await start(t);
}

/** Join with `t` (from a QR link or a pasted code), or rejoin the last build this browser was in. */
async function start(t: Ticket | null) {
  // A local team's code points at 127.0.0.1; from another device, reach that computer at the address that served this page.
  if (t) t = { ...t, uri: reachableUri(t.uri, location.hostname) };
  const target = t ? { uri: t.uri, db: t.db } : lastDb();
  if (!target) { askForCode('<h1>CadSwarm</h1><p>Paste the invite code your teammate sent you.</p>'); return; }
  message('<p class="muted">Connecting…</p>');
  const { connect } = await import('./api');
  const saved = loadSession(target.uri, target.db);
  let api: Api;
  try { api = await connect(store, target.uri, target.db, saved); }
  catch (e) {
    if (!saved || !t) {
      throw new Error(`Couldn't reach the team at ${target.uri}. Check that the host computer is on and CadSwarm is ` +
        `running there${/127\.0\.0\.1|localhost/.test(target.uri) ? ', and open this page on that computer (its code points at itself)' : ''}.`);
    }
    forgetSession(target.uri, target.db); // stored token refused (database reset?); join fresh with the ticket
    api = await connect(store, target.uri, target.db, null);
  }
  if (store.state.meId == null) {
    if (!t) {
      askForCode(`<h1>Not in this build</h1><p>You're no longer a member of <b>${esc(target.db)}</b>. Paste a new invite code or scan a new QR.</p>`);
      return;
    }
    const ticket = t;
    await new Promise<void>(done => mountPair(panels, ticket, async name => { await api.redeem(ticket.token, name); done(); }));
  }
  apiRef = api;
  mountPanels(panels, store, api, () => (picked ? [picked] : []), target.db, topbar);
}

boot().catch(e => message(`<p class="err">${esc((e as Error).message ?? String(e))}</p>`));
