import './style.css';
import { Store } from './store';
import { mountViewer } from './viewer/scene';
import { decodeTicket, reachableUri, readInvite, type Invite, type Ticket } from './ticket';
import type { Api } from './api';
import { loadSession, lastDb, forgetLast, forgetSession } from './session';
import { PointerGate } from './pointer-gate';
import { mountPair } from './ui/pair';
import { mountPanels } from './ui/panels';
import { mountSheet, type SheetControl } from './ui/sheet-controller';
import { mountJoin, type JoinScreen } from './ui/join';
import { esc } from './ui/esc';

const store = new Store();
const app = document.querySelector<HTMLDivElement>('#app')!;

// ---- The live shell: mounted once a build is open ----

let apiRef: Api | null = null;
let sheet: SheetControl | null = null;
let shellMounted = false;
let picked = ''; // the part last tapped in 3D: quick instructions name it

// Human pointers: at most 5 move_pointer calls a second, only when the pick changed.
type Pick = { object: string; point: [number, number, number] | null };
const gate = new PointerGate<Pick>(({ object, point }) =>
  apiRef?.movePointer(object, point ?? [], object ? 'inspecting' : 'pointing', object ? `looking at ${object}` : ''));

/** The model, the top bar and the sheet. The app only shows this once a build is joined. */
function showBuild(api: Api, project: string) {
  apiRef = api;
  if (!shellMounted) {
    app.innerHTML = `<main class="layout">
      <section id="viewer" class="viewer" aria-label="3D view"></section>
      <header id="topbar" class="topbar"></header>
      <aside id="panels" class="panels sheet" data-stop="peek"></aside>
    </main>`;
    mountViewer(document.getElementById('viewer')!, store, (object, point) => {
      picked = object;
      if (!apiRef) return;
      gate.push(object + '|' + (point ?? []).map(v => v.toFixed(1)).join(','), { object, point });
    });
    sheet = mountSheet(document.getElementById('panels')!, px =>
      dispatchEvent(new CustomEvent('cadswarm:inset', { detail: px })));
    shellMounted = true;
  }
  const panels = document.getElementById('panels')!;
  const topbar = document.getElementById('topbar');
  mountPanels(panels, store, api, () => (picked ? [picked] : []), project, topbar);
  sheet?.setStop(sheet.stop()); // the sheet's height, expanded state and inset follow the new markup
}

// ---- Screens before a build is open ----

let joinScreen: JoinScreen | null = null;

function connecting(db?: string) {
  joinScreen = null;
  app.innerHTML = `<div class="signin"><p class="muted">Connecting${db ? ` to <b>${esc(db)}</b>` : ''}…</p></div>`;
}

function showJoin(error?: string, code?: string) {
  const remembered = lastDb();
  joinScreen = mountJoin(app, {
    last: remembered ? { db: remembered.db } : null,
    code,
    error,
    onCode: text => {
      let ticket: Invite;
      try { ticket = readInvite(text); } catch (e) { return Promise.reject(e); }
      return start(ticket);
    },
    onScan: () => {
      // The camera and its decoder are only fetched when someone actually scans.
      void import('./ui/scan').then(({ openScanner }) => openScanner(
        text => joinWithScanned(text),
        message => joinScreen?.setError(message),
        () => { /* closed: the paste box is still there */ }));
    },
    onSwitch: () => {
      if (remembered) forgetSession(remembered.uri, remembered.db);
      forgetLast();
      showJoin();
    },
  });
}

function joinWithScanned(text: string) {
  let ticket: Invite;
  try { ticket = readInvite(text); } catch (e) {
    joinScreen?.setError((e as Error).message);
    return;
  }
  joinScreen?.setBusy('Joining…');
  start(ticket).catch((e: Error) => {
    joinScreen?.setBusy();
    joinScreen?.setError(e?.message ?? String(e));
  });
}

/** Show the name form for an invite that has not been redeemed on this device yet. */
function askName(ticket: Ticket, api: Api) {
  return new Promise<void>(done => {
    app.innerHTML = '<div class="signin"></div>';
    mountPair(app.querySelector<HTMLDivElement>('.signin')!, ticket, async name => {
      await api.redeem(ticket.token, name);
      done();
    });
  });
}

/** What to say when the database cannot be reached: the same words whether from a code or a remembered build. */
function unreachable(uri: string): Error {
  const local = /^(127\.\d+\.\d+\.\d+|localhost|\[::1\])$/i.test(new URL(uri, location.href).hostname);
  return new Error(`Couldn’t reach the team at ${uri}. Check that the host computer is on and CADSwarm is running there` +
    (local ? ', then open this page on that computer: its invite code points at the computer itself.' : '.') +
    ' Paste a new invite code if the build has moved.');
}

/** Join `ticket`, or rejoin the remembered build when there is none. Throws a plain-language Error. */
async function start(ticket: Ticket | null): Promise<void> {
  const t = ticket ? { ...ticket, uri: reachableUri(ticket.uri, location.hostname) } : null;
  const target = t ? { uri: t.uri, db: t.db } : lastDb();
  if (!target) { showJoin(); return; }
  const { connect } = await import('./api');
  const saved = loadSession(target.uri, target.db);
  let api: Api;
  try {
    api = await connect(store, target.uri, target.db, saved);
  } catch (e) {
    if (!saved || !t) throw unreachable(target.uri);
    forgetSession(target.uri, target.db); // the stored token was refused (database reset?): join fresh with the ticket
    api = await connect(store, target.uri, target.db, null).catch(() => { throw unreachable(target.uri); });
  }
  if (store.state.meId == null) {
    if (!t) {
      forgetSession(target.uri, target.db);
      throw new Error(`You’re no longer a member of ${target.db}. Paste a new invite code, or scan a new QR.`);
    }
    await askName(t, api);
  }
  showBuild(api, target.db);
}

async function boot() {
  // A phone link carries `#t=`; read it once, then take it out of the address bar.
  const frag = new URLSearchParams(location.hash.slice(1)).get('t');
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  if (frag) {
    let ticket: Ticket;
    try {
      ticket = decodeTicket(frag);
    } catch (e) {
      showJoin((e as Error).message);
      return;
    }
    connecting(ticket.db);
    try { await start(ticket); }
    catch (e) { showJoin((e as Error).message, frag); }
    return;
  }
  if (lastDb()) {
    connecting(lastDb()?.db);
    try { await start(null); }
    catch (e) { showJoin((e as Error).message); }
    return;
  }
  showJoin();
}

void boot();
