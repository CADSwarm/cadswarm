// The project folder, as a pane: the notes, plans and scripts the team's helpers saved, the
// version and author of each, and every kept version when you pick one. Reading uses the
// host's read-only list_files/file_history ops, so it works from a phone without a new table.
import type { Api, FileHistory, ProjectFile, ProjectListing } from '../api';
import type { Store } from '../store';
import { esc } from './esc';

const REFRESH_MS = 20000;

const ICONS: Record<string, string> = {
  md: '📝', txt: '📝', csv: '📊', json: '🧾', step: '📦', stp: '📦', stl: '📦', fcstd: '📐',
  py: '🐍', png: '🖼', jpg: '🖼', pdf: '📄',
};

const icon = (path: string) => ICONS[path.split('.').pop()?.toLowerCase() ?? ''] ?? '📄';
const base = (path: string) => path.split('/').pop() ?? path;
const folderOf = (path: string) => path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';

function sizeText(n: number): string {
  for (const [unit, size] of [['GB', 1024 ** 3], ['MB', 1024 ** 2], ['KB', 1024]] as const) {
    if (n >= size) return `${(n / size).toFixed(n / size < 10 ? 1 : 0)} ${unit}`;
  }
  return `${n} B`;
}

/** Epoch seconds to "12 min ago", "3 h ago" or a date. */
function ago(sec: number): string {
  if (!sec) return '';
  const mins = Math.max(0, Math.round((Date.now() / 1000 - sec) / 60));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(sec * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function mountFiles(el: HTMLElement, store: Store, api: Api | null) {
  let listing: ProjectListing | null = null;
  let loadedAt = 0;
  let loading = false;
  let selected = '';
  let history: FileHistory | null = null;
  let historyLoading = false;
  let error = '';

  el.innerHTML = `
    <div class="files-bar">
      <span class="muted small" data-files-count></span>
      <button type="button" class="text-button" data-files-refresh${api ? '' : ' disabled'}>Refresh</button>
    </div>
    <p class="muted small files-status" data-files-status></p>
    <div data-files-list></div>
    <div data-files-history></div>`;

  const $ = <T extends Element = HTMLElement>(sel: string) => el.querySelector<T>(sel)!;

  function files(): ProjectFile[] {
    return [...(listing?.files ?? [])].sort((a, b) => a.path.localeCompare(b.path));
  }

  function renderList() {
    const list = $('[data-files-list]');
    // Rows are rebuilt as whole markup; keep keyboard focus on the row the person was on.
    const keep = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>('[data-file]')?.dataset.file ?? '';
    const all = files();
    $('[data-files-count]').textContent = listing
      ? `${all.length} file${all.length === 1 ? '' : 's'}${listing.folders.length ? ` · ${listing.folders.length} folders` : ''}`
      : '';
    if (!listing) { list.innerHTML = ''; return; }
    if (!all.length) {
      list.innerHTML = '<p class="empty-note">Nothing saved yet. Notes, plans and scripts your helpers save show up here.</p>';
      return;
    }
    const groups = new Map<string, ProjectFile[]>();
    for (const f of all) {
      const dir = folderOf(f.path);
      groups.set(dir, [...(groups.get(dir) ?? []), f]);
    }
    list.innerHTML = [...groups.entries()].map(([dir, rows]) => `
      <h2 class="group-title">${dir ? esc(dir) : 'Project folder'}</h2>
      <ul class="group files-list">
        ${rows.map(f => `<li class="row-item${f.path === selected ? ' selected' : ''}" role="button" tabindex="0"
          data-file="${esc(f.path)}"${f.path === selected ? ' aria-current="true"' : ''}>
          <span class="file-icon" aria-hidden="true">${icon(f.path)}</span>
          <span class="row-main"><span class="row-title">${esc(base(f.path))}</span>
            <span class="row-detail">${esc(f.author || 'someone')} · ${esc(ago(f.updated_at))} · ${sizeText(f.size)}</span></span>
          <span class="file-version">v${f.version}</span>
        </li>`).join('')}
      </ul>`).join('');
    if (keep) list.querySelector<HTMLElement>(`[data-file="${CSS.escape(keep)}"]`)?.focus();
  }

  function renderHistory() {
    const box = $('[data-files-history]');
    if (!selected) {
      box.innerHTML = '<p class="empty-note">Pick a file to see its saved versions.</p>';
      return;
    }
    if (historyLoading) {
      box.innerHTML = `<p class="empty-note">Reading the history of ${esc(selected)}…</p>`;
      return;
    }
    if (!history || history.path !== selected) {
      box.innerHTML = '<p class="empty-note">No history to show yet.</p>';
      return;
    }
    const current = history.current;
    const older = [...history.older_versions].sort((a, b) => b.version - a.version);
    box.innerHTML = `
      <h2 class="group-title">History · ${esc(history.path)}</h2>
      <ul class="group history-list">
        ${current ? `<li class="row-item current">
          <span class="file-version">v${current.version}</span>
          <span class="row-main"><span class="row-title">Current version</span>
            <span class="row-detail">${esc(current.author || 'someone')} · ${esc(ago(current.updated_at))} · ${sizeText(current.size)}</span></span>
        </li>` : ''}
        ${older.map(v => `<li class="row-item">
          <span class="file-version">v${v.version}</span>
          <span class="row-main"><span class="row-title">${esc(v.reason || 'replaced')}</span>
            <span class="row-detail">${esc(v.author || 'someone')} · ${esc(ago(v.saved_at))} · ${sizeText(v.size)}</span></span>
        </li>`).join('')}
      </ul>
      <p class="muted small">Every replaced version is kept on the host, so nothing is lost.</p>`;
  }

  function renderStatus() {
    const status = $('[data-files-status]');
    const connected = store.state.connected;
    status.textContent = error ? error
      : !api ? 'Join a team to see its shared project files.'
      : !connected ? 'Reconnecting to the team…'
      : listing ? `Files live on the computer hosting the team${loadedAt ? ` · read ${ago(loadedAt / 1000)}` : ''}.`
      : 'Reading the project folder…';
    $<HTMLButtonElement>('[data-files-refresh]').disabled = !api || loading;
  }

  function render() {
    renderStatus();
    renderList();
    renderHistory();
  }

  async function loadHistory(path: string) {
    if (!api || !path) return;
    historyLoading = true;
    if (path === selected) renderHistory();
    const found = await api.fileHistory(path);
    historyLoading = false;
    if (path !== selected) return; // the person moved on while it loaded
    history = found;
    if (!found) error = `Couldn't read the history of ${path}. Check that the host computer is online.`;
    render();
  }

  async function load(force = false) {
    if (!api) { render(); return; }
    if (loading) return;
    if (!force && listing && Date.now() - loadedAt < REFRESH_MS) return;
    loading = true;
    error = '';
    renderStatus();
    const found = await api.listFiles();
    loading = false;
    if (!found) {
      error = 'Couldn\u2019t read the project folder. Check that the host computer is online, then refresh.';
      render();
      return;
    }
    listing = found;
    loadedAt = Date.now();
    render();
    if (selected && !files().some(f => f.path === selected)) {
      selected = '';
      history = null;
      render();
    } else if (selected) {
      void loadHistory(selected);
    }
  }

  function choose(path: string) {
    selected = path;
    history = history?.path === selected ? history : null;
    error = '';
    render();
    void loadHistory(selected);
  }

  el.addEventListener('click', e => {
    const t = e.target as HTMLElement;
    if (t.closest('[data-files-refresh]')) { void load(true); return; }
    const row = t.closest<HTMLElement>('[data-file]');
    if (row?.dataset.file) choose(row.dataset.file);
  });

  el.addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const row = (e.target as HTMLElement).closest<HTMLElement>('[data-file]');
    if (!row?.dataset.file) return;
    e.preventDefault(); // Space would scroll the pane
    choose(row.dataset.file);
  });

  store.subscribe(() => renderStatus());
  render();

  return { load, reload: () => load(true) };
}
