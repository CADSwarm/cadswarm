// What the person is looking at, shared by the viewport, the feature tree, the ribbon and the status bar:
// the selection, hidden bodies, display style, section plane, explode factor and who they follow.
// Local to this browser: none of it changes the team's model.
export type DisplayStyle = 'shaded' | 'edges' | 'wireframe';
export type Axis = 'x' | 'y' | 'z';
export type ViewName = 'fit' | 'front' | 'back' | 'top' | 'bottom' | 'right' | 'left' | 'iso' | 'selection';

export interface ViewState {
  selection: string[];
  hidden: Set<string>;
  display: DisplayStyle;
  section: { on: boolean; axis: Axis; at: number; flip: boolean };
  /** 0 = assembled, 1 = fully exploded. */
  explode: number;
  /** A teammate whose pointer the camera follows (0 = nobody). */
  follow: number;
  /** Measure mode: the next picks are collected as points. */
  measuring: boolean;
}

const state: ViewState = {
  selection: [], hidden: new Set(), display: 'edges', section: { on: false, axis: 'z', at: 0.5, flip: false },
  explode: 0, follow: 0, measuring: false,
};
const fns = new Set<(s: ViewState) => void>();

export const view = {
  get: (): ViewState => state,
  set(partial: Partial<ViewState>) {
    Object.assign(state, partial);
    for (const f of fns) f(state);
  },
  on(fn: (s: ViewState) => void): () => void { fns.add(fn); return () => fns.delete(fn); },
  select(names: string[]) { view.set({ selection: [...new Set(names.filter(Boolean))] }); },
  toggleHidden(names: string[]) {
    const hidden = new Set(state.hidden);
    const allHidden = names.every(n => hidden.has(n));
    for (const n of names) if (allHidden) hidden.delete(n); else hidden.add(n);
    view.set({ hidden });
  },
  /** Ask the viewport to move the camera (one-shot). */
  look(name: ViewName) { dispatchEvent(new CustomEvent('cadswarm:view', { detail: name })); },
};

/** A pick in the viewport: the body and the surface point (null when the click missed). */
export interface Pick { object: string; point: [number, number, number] | null }
export function onPick(fn: (p: Pick) => void): () => void {
  const h = (e: Event) => fn((e as CustomEvent<Pick>).detail);
  addEventListener('cadswarm:pick', h);
  return () => removeEventListener('cadswarm:pick', h);
}
