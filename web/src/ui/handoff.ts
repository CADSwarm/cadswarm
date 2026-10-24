import type { Store } from '../store';
import type { Api } from '../api';

/**
 * "Hand your work to an AI": ask an agent to continue from here now, or arm it for when you leave.
 * The host spawns one agent per open handoff (plan 02 Task 8); this only asks.
 */
export function mountHandoff(el: HTMLElement, store: Store, api: Api | null) {
  el.innerHTML = `
    <details class="card handoff">
      <summary>Hand your work to an AI</summary>
      <label>Where you left off (optional)
        <textarea name="note" rows="2" placeholder="Making the lid lip 1 mm thinner so it snaps in"></textarea></label>
      <button type="button" class="button" data-now${api ? '' : ' disabled'}>Hand off now</button>
      <label class="row"><input type="checkbox" data-arm${api ? '' : ' disabled'}> If I leave, let an AI continue my work</label>
      <p class="muted small" data-state></p>
    </details>`;
  const note = el.querySelector<HTMLTextAreaElement>('textarea')!;
  const armed = { id: null as string | null };
  el.querySelector('[data-now]')!.addEventListener('click', () => {
    api?.requestHandoff(note.value, 'now');
    note.value = '';
  });
  el.querySelector<HTMLInputElement>('[data-arm]')!.addEventListener('change', e => {
    if (!api) return;
    if ((e.target as HTMLInputElement).checked) armed.id = api.requestHandoff(note.value, 'on_leave');
    else if (armed.id) { api.cancelHandoff(armed.id); armed.id = null; }
  });
  store.subscribe(s => {
    const mine = s.handoffs.filter(h => h.from === s.meId).sort((a, b) => b.createdMs - a.createdMs)[0];
    const taker = mine?.taker ? store.member(mine.taker)?.name : null;
    el.querySelector('[data-state]')!.textContent = !mine ? '' :
      mine.status === 'waiting' ? 'Armed: an AI will continue if you leave.' :
      mine.status === 'open' ? 'Finding an AI to continue…' :
      mine.status === 'accepted' ? `${taker ?? 'An AI'} picked up where you left off.` :
      mine.status === 'cancelled' ? '' : '';
    // Keep the checkbox honest when someone else (or a reload) resolved it.
    const box = el.querySelector<HTMLInputElement>('[data-arm]')!;
    if (mine?.status === 'cancelled' && armed.id === mine.id) { armed.id = null; box.checked = false; }
  });
}
