// Light (default) or dark workspace theme: one attribute on <html>, remembered per browser.
export type Theme = 'light' | 'dark';

export function theme(): Theme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}
export const isDark = () => typeof document !== 'undefined' && theme() === 'dark';

export function setTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('cadswarm:theme', t); } catch { /* no storage */ }
  dispatchEvent(new CustomEvent('cadswarm:theme', { detail: t }));
}

/** Read the remembered theme (or the system's) before the first paint. */
export function initTheme() {
  let t: string | null = null;
  try { t = localStorage.getItem('cadswarm:theme'); } catch { /* no storage */ }
  document.documentElement.dataset.theme = t === 'dark' || t === 'light' ? t : 'light';
}
