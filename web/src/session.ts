// The SpacetimeDB token per database, so a reload reconnects as the same member. Every access is guarded:
// storage throws in private mode, with blocked site data, and in some embedded previews.
const key = (uri: string, db: string) => `cadswarm:${uri}:${db}`;

export interface Saved { uri: string; db: string; token: string }

export function loadSession(uri: string, db: string): string | null {
  try { return localStorage.getItem(key(uri, db)); } catch { return null; }
}

export function saveSession(s: Saved) {
  try {
    localStorage.setItem(key(s.uri, s.db), s.token);
    localStorage.setItem('cadswarm:last', JSON.stringify({ uri: s.uri, db: s.db }));
  } catch { /* private mode: the session just won't survive a reload */ }
}

export function forgetSession(uri: string, db: string) {
  try { localStorage.removeItem(key(uri, db)); } catch { /* ignore */ }
}

/** Stop offering the remembered build on the join screen. */
export function forgetLast() {
  try { localStorage.removeItem('cadswarm:last'); } catch { /* ignore */ }
}

export function lastDb(): { uri: string; db: string } | null {
  try {
    const v = JSON.parse(localStorage.getItem('cadswarm:last') ?? 'null');
    return v && typeof v.uri === 'string' && typeof v.db === 'string' ? { uri: v.uri, db: v.db } : null;
  } catch { return null; }
}
