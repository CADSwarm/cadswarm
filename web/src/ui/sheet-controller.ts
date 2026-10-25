// The phone's draggable chat sheet: pointer drag with a live height, a snap to the nearest detent on
// release, a header tap for keyboard/AT, and a callback so the viewer can frame the model above it.
// On desktop the sheet is the fixed rail and this does nothing.
//
// Listeners are delegated from the sheet element, so re-rendering the panel markup keeps working.
import { detents, nearestDetent, type SheetStop } from './sheet';

export interface SheetControl {
  /** Move to a stop (the composer and tab switches use this). */
  setStop(stop: SheetStop): void;
  /** The stop it is at now. */
  stop(): SheetStop;
}

const PHONE = '(max-width: 760px)';

export function mountSheet(sheet: HTMLElement, onChange: (coveredPx: number) => void): SheetControl {
  const isPhone = () => matchMedia(PHONE).matches;
  let stop: SheetStop = (sheet.dataset.stop as SheetStop) || 'peek';
  let drag: { id: number; y: number; height: number; moved: boolean } | null = null;
  let suppressClickUntil = 0;

  function apply(next: SheetStop) {
    stop = next;
    sheet.dataset.stop = next;
    sheet.querySelectorAll<HTMLButtonElement>('[data-sheet-toggle]')
      .forEach(b => b.setAttribute('aria-expanded', String(next !== 'peek')));
    if (!isPhone()) {
      sheet.style.height = '';
      onChange(0);
      return;
    }
    const height = Math.round(detents(window.innerHeight)[next]);
    sheet.style.height = `${height}px`;
    onChange(height);
  }

  // Half ↔ full (from a peek, the first tap opens to half).
  function toggle() {
    if (!isPhone()) return;
    apply(stop === 'full' ? 'half' : stop === 'peek' ? 'half' : 'full');
  }

  sheet.addEventListener('click', e => {
    if (!(e.target as HTMLElement).closest('[data-sheet-toggle]')) return;
    e.preventDefault();
    if (performance.now() < suppressClickUntil) return; // the click that ended a drag
    toggle();
  });

  function beginDrag(e: PointerEvent) {
    if (!isPhone() || e.button > 0) return;
    if (!(e.target as HTMLElement).closest('[data-grabber], .sheet-head')) return;
    drag = { id: e.pointerId, y: e.clientY, height: sheet.getBoundingClientRect().height, moved: false };
    sheet.classList.add('dragging');
    try { sheet.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
    e.preventDefault();
  }
  sheet.addEventListener('pointerdown', beginDrag);

  sheet.addEventListener('pointermove', e => {
    if (!drag || e.pointerId !== drag.id) return;
    const dy = drag.y - e.clientY;
    if (Math.abs(dy) > 3) drag.moved = true;
    const d = detents(window.innerHeight);
    const height = Math.max(d.peek, Math.min(d.full, drag.height + dy));
    sheet.style.height = `${Math.round(height)}px`;
    onChange(Math.round(height));
  });

  function endDrag(e: PointerEvent) {
    if (!drag || e.pointerId !== drag.id) return;
    const { moved } = drag;
    drag = null;
    sheet.classList.remove('dragging');
    try { sheet.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    suppressClickUntil = performance.now() + 300;
    if (!moved) { toggle(); return; } // a tap on the grabber or header
    apply(nearestDetent(sheet.getBoundingClientRect().height, window.innerHeight));
  }
  sheet.addEventListener('pointerup', endDrag);
  sheet.addEventListener('pointercancel', endDrag);

  // Tapping a tab from the peek opens the sheet; focusing the composer does too.
  addEventListener('cadswarm:tab', () => { if (isPhone() && stop === 'peek') apply('half'); });
  sheet.addEventListener('focusin', e => {
    if (isPhone() && stop === 'peek' && (e.target as HTMLElement).matches('[data-text]')) apply('half');
  });

  addEventListener('resize', () => apply(stop));
  apply(stop);

  return { setStop: apply, stop: () => stop };
}
