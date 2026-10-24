import type { Store } from '../store';
import type { Api } from '../api';

// Stub until Task 6.
export function mountPanels(el: HTMLElement, store: Store, _api: Api | null) {
  store.subscribe(s => { el.textContent = s.connected ? `connected · ${s.objects.length} parts` : 'connecting…'; });
}
