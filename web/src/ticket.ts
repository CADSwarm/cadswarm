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

// ---- Invite codes: the same text people paste into FreeCAD (mirrors cadswarm/invite.py) ----

export const INVITE_PREFIX = 'cadswarm-2:';
/** A decoded ticket plus the project name a `cadswarm-2:` code carries. */
export interface Invite extends Ticket { project: string }

const INVISIBLE = /[​-‏⁠﻿­]/g;  // zero-width and soft hyphens that chat apps add
const QUOTES = /["'<>`‘’‚‛“”„«»]/g;
const B64_RUN = /^[A-Za-z0-9_=-]*/;
const B32_RUN = /^[A-Za-z2-7]*/;
const B32_MIN = 40;  // a real ticket is well over 100 characters; this keeps ordinary words out

function base64UrlJson(body: string): unknown {
  const b = body.replace(/=+$/, '').replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(b + '='.repeat((4 - (b.length % 4)) % 4));
  return JSON.parse(new TextDecoder().decode(Uint8Array.from(bin, c => c.charCodeAt(0))));
}

/** Code-looking chunks from the start of `text`: it may be line-wrapped or followed by chat ("… thanks!"). */
function chunks(text: string, run: RegExp): string[] {
  const out: string[] = [];
  for (const chunk of text.split(/\s+/).filter(Boolean)) {
    const r = run.exec(chunk)![0];
    if (r) out.push(r);
    if (r !== chunk || !r) break;
  }
  return out;
}

/** The longest run of chunks that decodes, so trailing words that merely look like code are dropped. */
function longest(parts: string[], decode: (s: string) => unknown): Record<string, unknown> | null {
  for (let n = parts.length; n > 0; n--) {
    try {
      const v = decode(parts.slice(0, n).join('').replace(/=+$/, ''));
      if (v && typeof v === 'object') return v as Record<string, unknown>;
    } catch { /* try a shorter run */ }
  }
  return null;
}

const base32Json = (s: string) => JSON.parse(new TextDecoder().decode(base32Decode(s)));

/**
 * Read whatever the person pasted: a `cadswarm-2:` invite code (FreeCAD's Invite button), the bare ticket
 * `cadswarm-host invite` prints, or a phone link with `#t=`. Throws an Error with a plain-language message.
 */
export function readInvite(text: string): Invite {
  const s = (text ?? '').replace(INVISIBLE, '').replace(QUOTES, '');
  if (!s.trim()) throw new Error('Paste the invite code your teammate sent you.');
  let data: Record<string, unknown> | null = null;
  const i = s.indexOf(INVITE_PREFIX);
  if (i >= 0) {
    data = longest(chunks(s.slice(i + INVITE_PREFIX.length), B64_RUN), base64UrlJson);
  } else if (s.includes('cadswarm-1:')) {
    throw new Error('This invite is from an older CadSwarm. Ask your teammate for a new one.');
  } else {
    const at = s.search(/[#?&]t=/);
    const words = s.split(/\s+/);
    const start = at >= 0 ? s.slice(at + 3) : (() => {
      const k = words.findIndex(w => B32_RUN.exec(w)![0].length >= B32_MIN);
      return k >= 0 ? words.slice(k).join(' ') : null;
    })();
    if (start == null) {
      throw new Error(`That doesn't look like a CadSwarm invite code. It should start with “${INVITE_PREFIX}”. ` +
        'Ask your teammate to copy it again with the Copy button.');
    }
    data = longest(chunks(start, B32_RUN), base32Json);
  }
  if (!data || !data.uri || !data.db || !data.token) {
    throw new Error('That invite code is incomplete — part of it may have been cut off. Ask your teammate to send it again.');
  }
  const { p, ...t } = data as Record<string, unknown> & { p?: string };
  const defaults = { v: 1, kind: 'human', role: 'member', host_addr: '' };
  return Object.assign(defaults, t as Partial<Ticket>, { project: String(p ?? '') }) as Invite;
}

const LOOPBACK = /^(127\.\d+\.\d+\.\d+|localhost|\[::1\])$/i;

/**
 * A code made on a computer that hosts its database itself points at 127.0.0.1, which only that computer can
 * reach. When this page was served by that same computer over the network, use its address instead.
 */
export function reachableUri(uri: string, pageHost: string): string {
  try {
    const u = new URL(uri);
    if (LOOPBACK.test(u.hostname) && pageHost && !LOOPBACK.test(pageHost)) {
      u.hostname = pageHost;
      return u.toString().replace(/\/$/, '');
    }
  } catch { /* not a URL: leave it */ }
  return uri;
}
