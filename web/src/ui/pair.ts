import type { Ticket } from '../ticket';
import { esc } from './esc';

export function mountPair(el: HTMLElement, t: Ticket, join: (name: string) => Promise<void>) {
  const who = t.kind === 'human' || !t.kind ? '' : ` as ${esc(t.kind)}`;
  el.innerHTML = `
    <form class="pair">
      <h1>Join the build</h1>
      <p class="muted">You're joining <b>${esc(t.db)}</b>${who}. Pick a name your teammates will see.</p>
      <label>Your name
        <input name="name" required maxlength="40" pattern="[A-Za-z0-9_\\-]+" autocomplete="nickname"
               title="Letters, numbers, - and _" value="guest-${Math.floor(Math.random() * 900 + 100)}">
      </label>
      <button type="submit">Join</button>
      <p class="err" role="alert"></p>
    </form>`;
  const form = el.querySelector('form')!;
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const btn = form.querySelector('button')!;
    btn.disabled = true;
    btn.textContent = 'Joining…';
    try { await join((form.elements.namedItem('name') as HTMLInputElement).value.trim()); }
    catch (err) {
      form.querySelector('.err')!.textContent = (err as Error).message;
      btn.disabled = false;
      btn.textContent = 'Join';
    }
  });
}
