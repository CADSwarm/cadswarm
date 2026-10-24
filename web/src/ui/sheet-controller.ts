// The phone's draggable chat sheet: pointer drag with a live height, a snap to the nearest detent on
// release, a header tap for keyboard/AT, and a `cadswarm:inset`-style callback so the viewer can frame
// the model above it. On desktop the sheet is the fixed rail and this does nothing.
import { detents, nearestDetent, type SheetStop } from './sheet';

export interface SheetControl {
  /** Move to a stop (the composer and tab switches use this). */
  setStop(stop: SheetStop): void;
  /** The stop it is at now. */
  stop(): SheetStop;
}

export function mountSheet(sheet: HTMLElement, onChange: (coveredPx: number) => void): SheetControl {
  const grabber = sheet.querySelector<HTMLElement>('[data-grabber]');
  const head = sheet.querySelector<HTMLElement>('.sheet-head');
  const toggles = [...sheet.querySelectorAll<HTMLButtonElement>('[data-sheet-toggle]')];
  const isPhone = () => matchMedia('(max-width: 760px)').matches;
  let stop: SheetStop = (sheet.dataset.stop as SheetStop) || 'peek';
  let drag: { id: number; y: number; height: number; moved: boolean } | null = null;
  let suppressClickUntil = 0;

  function apply(next: SheetStop) {
    stop = next;
    if (!isPhone()) {
      sheet.style.height = '';
      sheet.dataset.stop = next;
      onChange(0);
      return;
    }
    const height = Math.round(detents(window.innerHeight)[next]);
    sheet.dataset.stop = next;
    sheet.style.height = `${height}px`;
    toggles.forEach(b => b.setAttribute('aria-expanded', String(next !== 'peek')));
    onChange(height);
  }

  // Half ↔ full (from a peek, the first tap opens to half).
  function toggle() {
    if (!isPhone()) return;
    apply(stop === 'full' ? 'half' : stop === 'peek' ? 'half' : 'full');
  }

  toggles.forEach(b => b.addEventListener('click', e => {
    e.preventDefault();
    if (performance.now() < suppressClickUntil) return; // the click that ended a drag
    toggle();
  }));

  function beginDrag(e: PointerEvent) {
    if (!isPhone() || e.button > 0) return;
    drag = { id: e.pointerId, y: e.clientY, height: sheet.getBoundingClientRect().height, moved: false };
    sheet.classList.add('dragging');
    try { sheet.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
    e.preventDefault();
  }
  for (const surface of [grabber, head]) surface?.addEventListener('pointerdown', beginDrag);

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
    const { moved, height } = drag;
    drag = null;
    sheet.classList.remove('dragging');
    try { sheet.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    if (!moved) { // a tap on the grabber or header
      suppressClickUntil = performance.now() + 300;
      toggle();
      return;
    }
    suppressClickUntil = performance.now() + 300;
    apply(nearestDetent(sheet.getBoundingClientRect().height || height, window.innerHeight));
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
