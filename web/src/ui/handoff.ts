import type { Store } from '../store';
import type { Api } from '../api';

/**
 * "If I leave, let an AI continue my work": arms a handoff the host acts on when this person leaves
 * (plan 02 Task 8). Handing work over right now is just telling an agent in Ask.
 */
export function mountHandoff(el: HTMLElement, store: Store, api: Api | null) {
  el.innerHTML = `
    <label class="switch-row">
      <span><span class="row-title">Continue my work if I leave</span>
        <span class="row-detail" data-state>An AI picks up where you left off.</span></span>
      <input type="checkbox" role="switch" data-arm${api ? '' : ' disabled'}>
    </label>`;
  const box = el.querySelector<HTMLInputElement>('[data-arm]')!;
  const state = el.querySelector('[data-state]')!;
  let armed: string | null = null;
  box.addEventListener('change', () => {
    if (!api) return;
    if (box.checked) armed = api.requestHandoff('', 'on_leave');
    else if (armed) { api.cancelHandoff(armed); armed = null; }
  });
  store.subscribe(s => {
    const mine = s.handoffs.filter(h => h.from === s.meId).sort((a, b) => b.createdMs - a.createdMs)[0];
    const waiting = s.handoffs.find(h => h.from === s.meId && h.trigger === 'on_leave' && h.status === 'waiting');
    if (waiting && !armed) { armed = waiting.id; box.checked = true; }   // a reload keeps the switch honest
    if (mine?.status === 'cancelled' && armed === mine.id) { armed = null; box.checked = false; }
    const taker = mine?.taker ? store.member(mine.taker)?.name : null;
    state.textContent = mine?.status === 'accepted' ? `${taker ?? 'An AI'} picked up where you left off.`
      : mine?.status === 'open' ? 'Finding an AI to continue…' : 'An AI picks up where you left off.';
  });
}
