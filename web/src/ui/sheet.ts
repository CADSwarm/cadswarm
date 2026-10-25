// The mobile chat sheet's three stops, as pure math: the peeking bar, half the screen, and the
// whole screen minus the glass top bar. Drag and keyboard both snap through nearestDetent.
export type SheetStop = 'peek' | 'half' | 'full';

export interface Detents {
  peek: number;
  half: number;
  full: number;
}

const PEEK = 190;
const TOP_BAR = 56;

/** Heights in CSS pixels for a viewport, clamped to what actually fits. */
export function detents(viewport: number): Detents {
  const clamp = (v: number) => Math.max(Math.min(PEEK, viewport), Math.min(Math.round(v), viewport));
  return { peek: clamp(PEEK), half: clamp(viewport * 0.54), full: clamp(viewport - TOP_BAR) };
}

const STOPS: SheetStop[] = ['peek', 'half', 'full'];

/** The stop closest to where a drag was released; a tie keeps the lower stop. */
export function nearestDetent(height: number, viewport: number): SheetStop {
  const d = detents(viewport);
  let best: SheetStop = 'peek';
  for (const stop of STOPS) {
    if (Math.abs(d[stop] - height) < Math.abs(d[best] - height)) best = stop;
  }
  return best;
}

/** The next stop for a tap on the header: peek → half → full → peek. */
export function nextStop(stop: SheetStop): SheetStop {
  return STOPS[(STOPS.indexOf(stop) + 1) % STOPS.length];
}
