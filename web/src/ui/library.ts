// The Library tab: the team's parts and the built-in standard parts, with thumbnails, search, placing a
// part in the model, uploading a part file and adding one from a link.
import type { Api, PartMeta } from '../api';
import type { Component, State, Store } from '../store';
import { esc } from './esc';
import {
  type Catalog, type StdFamily, effective, holeFor, libraryEntries, loadJson, parseCatalog, parseSpec, partName,
  partSuggestions, sizeLabel, type SettingSpec,
} from './library-data';
import { thumbnail } from './thumbs';

const CATEGORIES = ['fastener', 'bearing', 'insert', 'board', 'electronics', 'enclosure', 'motion', 'structural', 'hardware', 'other'];
const SOURCE: Record<string, string> = { upload: 'Team part', url: 'From a link', pack: 'From a pack' };
const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;

export function mountLibrary(el: HTMLElement, store: Store, api: Api | null) {
  let catalog: Catalog = parseCatalog(null);
  let spec: SettingSpec[] = [];
  let query = '', category = '', key = '';
  const thumbs = new Map<string, string | 'none'>();  // id@updatedMs -> data URL
  const fetching = new Set<string>();
  void Promise.all([loadJson('./standard_parts.json', parseCatalog), loadJson('./settings_spec.json', parseSpec)])
    .then(([c, s]) => { catalog = c; spec = s; key = ''; render(store.state); });

  el.innerHTML = `
    <div class="lib-tools">
      <input type="search" class="lib-q" placeholder="Search: bearing, M3 screw, motor mount…" aria-label="Search the library">
      <select class="lib-cat" aria-label="Kind of part"><option value="">All kinds</option>
        ${CATEGORIES.map(c => `<option value="${c}">${c[0].toUpperCase() + c.slice(1)}</option>`).join('')}</select>
    </div>
    <details class="lib-add"${api ? '' : ' hidden'}><summary>Add a part to the library</summary>
      <form class="lib-form">
        <label>File (STEP, FreeCAD or STL)<input type="file" name="file" accept=".step,.stp,.fcstd,.stl"></label>
        <label>…or a link to the file<input type="url" name="url" placeholder="https://…/part.step"></label>
        <label>Name<input name="name" maxlength="80"></label>
        <label>Kind<select name="category">${CATEGORIES.map(c => `<option>${c}</option>`).join('')}</select></label>
        <label>Tags<input name="tags" placeholder="words people would search for, comma separated"></label>
        <label>License<input name="license" placeholder="e.g. CC-BY-4.0, vendor terms, our own design"></label>
        <button type="submit">Add to library</button>
      </form>
    </details>
    <ul class="lib-list list"></ul>`;
  const list = el.querySelector<HTMLUListElement>('.lib-list')!;
  el.querySelector<HTMLInputElement>('.lib-q')!.addEventListener('input', e => { query = (e.target as HTMLInputElement).value; key = ''; render(store.state); });
  el.querySelector<HTMLSelectElement>('.lib-cat')!.addEventListener('change', e => { category = (e.target as HTMLSelectElement).value; key = ''; render(store.state); });

  el.querySelector<HTMLFormElement>('.lib-form')!.addEventListener('submit', async e => {
    e.preventDefault();
    if (!api) return;
    const form = e.target as HTMLFormElement;
    const data = new FormData(form);
    const file = data.get('file') as File | null;
    const url = String(data.get('url') || '').trim();
    const meta: PartMeta = {
      name: String(data.get('name') || '').trim(), category: String(data.get('category') || 'other'),
      tags: String(data.get('tags') || '').split(',').map(t => t.trim()).filter(Boolean), description: '',
      license: String(data.get('license') || '').trim(),
    };
    const button = form.querySelector('button')!;
    button.disabled = true;
    button.textContent = 'Adding…';
    let ok = false;
    if (file && file.size) ok = await api.addComponent(file.name, new Uint8Array(await file.arrayBuffer()), { ...meta, name: meta.name || file.name.replace(/\.[^.]+$/, '') });
    else if (url) ok = await api.importComponentUrl(url, meta);
    else store.set({ notice: { text: 'Choose a file or paste a link first.', at: Date.now() } });
    button.disabled = false;
    button.textContent = 'Add to library';
    if (ok) { form.reset(); store.set({ notice: { text: 'Added to the team library. FreeCAD is measuring it…', at: Date.now() } }); }
  });

  el.addEventListener('click', async e => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-place]');
    if (!b || !api) return;
    const card = b.closest<HTMLElement>('[data-entry]')!;
    const taken = store.state.objects.map(o => o.name);
    b.disabled = true;
    b.textContent = 'Placing…';
    let ok: boolean;
    if (card.dataset.family) {
      const fam = catalog.families.find(f => f.id === card.dataset.family)!;
      const size = card.querySelector<HTMLSelectElement>('[data-size]')!.value;
      const lengthSel = card.querySelector<HTMLSelectElement>('[data-length]');
      const length = lengthSel ? Number(lengthSel.value) : undefined;
      const label = `${size}${length ? `x${length}` : ''} ${fam.name.toLowerCase()}`;
      ok = await api.placeComponent({ family: fam.id, size, length, name: partName(label, taken), label });
    } else {
      const c = store.state.components.find(x => x.id === card.dataset.entry)!;
      ok = await api.placeComponent({ component: c.id, name: partName(c.name, taken), label: c.name });
    }
    b.disabled = false;
    b.textContent = 'Place';
    if (ok) store.set({ notice: { text: 'Placed in the model at the origin. Move it from FreeCAD or ask a helper.', at: Date.now() } });
  });
  el.addEventListener('change', e => {
    const t = e.target as HTMLElement;
    if (t.matches('[data-size]')) updateHole(t.closest<HTMLElement>('[data-entry]')!);
  });

  function updateHole(card: HTMLElement) {
    const fam = catalog.families.find(f => f.id === card.dataset.family);
    const out = card.querySelector('.hole');
    if (!fam || !out) return;
    const h = holeFor(catalog, fam, card.querySelector<HTMLSelectElement>('[data-size]')!.value,
      effective(spec, store.state.settings));
    out.textContent = h ? `Hole for it: Ø${h.diameter} mm (${h.why})` : '';
  }

  function thumb(c: Component): string {
    const k = `${c.id}@${c.updatedMs}`;
    const t = thumbs.get(k);
    if (t && t !== 'none') return `<img class="thumb" alt="" src="${t}">`;
    if (!t && api && !fetching.has(k)) {
      fetching.add(k);
      void api.componentPreview(c.id).then(mesh => {
        thumbs.set(k, mesh ? (thumbnail(k, mesh, dark()) ?? 'none') : 'none');
        if (!mesh) setTimeout(() => thumbs.delete(k), 10000);  // not measured yet: try again soon
        key = '';
        render(store.state);
      });
    }
    return `<span class="thumb empty">${esc(c.format.toUpperCase())}</span>`;
  }

  function teamCard(c: Component) {
    const sugg = partSuggestions(c);
    return `<li class="lib-card" data-entry="${esc(c.id)}">
      ${thumb(c)}
      <div class="lib-body">
        <b>${esc(c.name)}</b> <span class="muted small">${esc(SOURCE[c.source] ?? c.source)} · ${esc(c.category)}</span>
        <p class="small">${esc(sizeLabel(c.bbox) || 'Measuring…')}${c.uses ? ` · placed ${c.uses}×` : ''}${c.license ? ` · ${esc(c.license)}` : ''}</p>
        ${sugg.length ? `<p class="small muted">Suggests: ${sugg.slice(0, 2).map(s => `${esc(s.setting)} → ${esc(String(s.value))}`).join(', ')}</p>` : ''}
        ${api ? '<button type="button" class="mini" data-place>Place</button>' : ''}
      </div></li>`;
  }

  function stdCard(f: StdFamily) {
    const sizes = Object.keys(f.sizes);
    const pick = sizes.includes('M3') ? 'M3' : sizes.includes('608') ? '608' : sizes[0];
    return `<li class="lib-card" data-entry="c_std_${esc(f.id)}" data-family="${esc(f.id)}">
      <span class="thumb empty std">${esc(f.category === 'bearing' ? '◎' : f.category === 'insert' ? '▥' : '⚙')}</span>
      <div class="lib-body">
        <b>${esc(f.name)}</b> <span class="muted small">Standard part · ${esc(f.standard)}</span>
        <p class="small">
          <select data-size aria-label="Size">${sizes.map(s => `<option${s === pick ? ' selected' : ''}>${esc(s)}</option>`).join('')}</select>
          ${f.lengths.length ? `<select data-length aria-label="Length">${f.lengths.map(l => `<option value="${l}"${l === 10 ? ' selected' : ''}>${l} mm</option>`).join('')}</select>` : ''}
        </p>
        <p class="small muted hole"></p>
        ${api ? '<button type="button" class="mini" data-place>Place</button>' : ''}
      </div></li>`;
  }

  function render(s: State) {
    const k = `${query}|${category}|${s.components.map(c => `${c.id}:${c.updatedMs}:${c.uses}`).join(',')}|${s.settings.map(x => `${x.setting}=${x.num}${x.text}`).join(',')}|${catalog.families.length}`;
    if (k === key || el.contains(document.activeElement) && document.activeElement?.tagName === 'SELECT') return;
    key = k;
    const entries = libraryEntries(s.components, catalog, query, category);
    list.innerHTML = entries.map(e => e.kind === 'team' ? teamCard(e.c) : stdCard(e.f)).join('')
      || '<li class="empty muted">Nothing matches. Try another word, or add a part above.</li>';
    list.querySelectorAll<HTMLElement>('[data-family]').forEach(updateHole);
  }

  store.subscribe(render);
}
