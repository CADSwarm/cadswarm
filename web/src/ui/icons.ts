// CADSwarm's own line icons: 24px grid, 1.5px stroke, round caps, drawn for this app (no icon font, no
// vendor artwork). `icon(name)` returns an inline <svg>; colour follows `currentColor`, and an accent
// part (class "ac") takes `--icon-accent` so ribbon icons carry one blue detail like a pro toolset.
const P: Record<string, string> = {
  // features
  extrude: '<path d="M5 15.5 12 19l7-3.5M5 15.5V9l7-3.5L19 9v6.5M12 19v-6.5M5 9l7 3.5L19 9"/><path class="ac" d="M12 3v5M10 5l2-2 2 2"/>',
  revolve: '<path d="M8 4v16M8 4h3.5a2.5 2.5 0 0 1 0 5H8"/><path class="ac" d="M15 12.5a6 2.6 0 1 1-8 2.3M7 14.8 5.4 13.6M7 14.8l-.4 1.9"/>',
  cut: '<path d="M4 8.5 12 4.5l8 4v7l-8 4-8-4z"/><path class="ac" d="M9 10.2 12 8.7l3 1.5v3.6l-3 1.5-3-1.5z"/>',
  fillet: '<path d="M4 20V9M4 20h11"/><path class="ac" d="M4 9a11 11 0 0 0 11 11"/><path d="M15 20V8.5M4 9h11"/>',
  chamfer: '<path d="M4 20V10M4 20h11M15 20V9.5M10 4.5h5v5"/><path class="ac" d="M4 10l6-5.5"/>',
  shell: '<path d="M4 8.5 12 4.5l8 4v7l-8 4-8-4z"/><path class="ac" d="M7 10v4.5l5 2.5 5-2.5V10"/>',
  hole: '<path d="M3.5 9 12 5l8.5 4v6.5L12 19.5 3.5 15.5z"/><ellipse class="ac" cx="12" cy="11" rx="3.4" ry="1.8"/><path class="ac" d="M8.6 11v4.2M15.4 11v4.2"/>',
  pattern: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect class="ac" x="14" y="4" width="6" height="6" rx="1"/><rect class="ac" x="4" y="14" width="6" height="6" rx="1"/><rect class="ac" x="14" y="14" width="6" height="6" rx="1"/>',
  mirror: '<path d="M10 6 4 18h6z"/><path class="ac" d="M14 6l6 12h-6z"/><path d="M12 3v18" stroke-dasharray="2 2"/>',
  boolean: '<circle cx="9.5" cy="12" r="5.5"/><circle class="ac" cx="14.5" cy="12" r="5.5"/>',
  box: '<path d="M4 8 12 4l8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8"/>',
  cylinder: '<ellipse cx="12" cy="6" rx="6.5" ry="2.5"/><path d="M5.5 6v12c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5V6"/><path class="ac" d="M12 6v5"/>',
  // sketch
  sketch: '<path d="M3 17.5 9 6l12 2-6 11.5z"/><path class="ac" d="M8.5 13.5 11 10l2.5 3.5"/>',
  line: '<path d="M5 19 19 5"/><circle class="ac" cx="5" cy="19" r="1.6"/><circle class="ac" cx="19" cy="5" r="1.6"/>',
  rect: '<rect x="4" y="6" width="16" height="12" rx=".5"/><circle class="ac" cx="4" cy="6" r="1.4"/><circle class="ac" cx="20" cy="18" r="1.4"/>',
  circle: '<circle cx="12" cy="12" r="7.5"/><path class="ac" d="M12 12h7.5"/><circle class="ac" cx="12" cy="12" r="1.2"/>',
  arc: '<path d="M4.5 17a8 8 0 0 1 15 0"/><circle class="ac" cx="4.5" cy="17" r="1.4"/><circle class="ac" cx="19.5" cy="17" r="1.4"/>',
  slot: '<path d="M8 8h8a4 4 0 0 1 0 8H8a4 4 0 0 1 0-8z"/><path class="ac" d="M8 12h8"/>',
  polygon: '<path d="M12 3.5 19.5 9l-2.8 9h-9.4L4.5 9z"/><circle class="ac" cx="12" cy="12" r="1.2"/>',
  dimension: '<path d="M4 7v10M20 7v10"/><path class="ac" d="M4 12h16M7 10l-3 2 3 2M17 10l3 2-3 2"/>',
  constraint: '<path d="M7 17 17 7"/><path d="M5 9h6M5 12h6"/><path class="ac" d="M14 16.5h5.5M14 19.5h5.5"/>',
  trim: '<path d="M4 12h6"/><path d="M14 12h6" stroke-dasharray="2 2"/><path class="ac" d="M12 4v16"/>',
  offset: '<path d="M5 18V9a4 4 0 0 1 4-4h10"/><path class="ac" d="M9 18v-8.5a.5.5 0 0 1 .5-.5H19"/>',
  finish: '<path d="M3 17.5 9 6l12 2-6 11.5z"/><path class="ac" d="m9.5 12.5 2 2 4-4.5"/>',
  // evaluate
  measure: '<path d="m3.5 15.5 12-12 5 5-12 12z"/><path class="ac" d="m7 12 2 2M10 9l1.5 1.5M13 6l2 2"/>',
  mass: '<path d="M7 9h10l2.5 11h-15z"/><circle class="ac" cx="12" cy="6" r="2.5"/>',
  interference: '<rect x="3.5" y="5" width="10" height="10" rx="1"/><rect x="10.5" y="9" width="10" height="10" rx="1"/><path class="ac" d="M10.5 9h3v6h-3z"/>',
  section: '<path d="M4 8.5 12 4.5l8 4v7l-8 4-8-4z"/><path class="ac" d="M2.5 13 12 9l9.5 4"/>',
  fea: '<path d="M4 18h16M6 18c0-6 3-9 6-9s6 3 6 9"/><path class="ac" d="M12 3v4M10 5.5l2 2 2-2"/>',
  print: '<path d="M5 20h14M7 20v-3h10v3"/><path d="M8 17 10 11h4l2 6"/><path class="ac" d="M12 3v5M9.5 6 12 8.5 14.5 6"/>',
  // assembly
  insert: '<path d="M4 9 11 5.5l7 3.5v6.5L11 19l-7-3.5zM4 9l7 3.5L18 9M11 12.5V19"/><path class="ac" d="M18.5 3v5M16 5.5h5"/>',
  mate: '<circle cx="8" cy="12" r="4.5"/><circle cx="16" cy="12" r="4.5"/><path class="ac" d="M12 6v12"/>',
  move: '<path d="M12 3v18M3 12h18"/><path class="ac" d="m9.5 5.5 2.5-2.5 2.5 2.5M9.5 18.5l2.5 2.5 2.5-2.5M5.5 9.5 3 12l2.5 2.5M18.5 9.5 21 12l-2.5 2.5"/>',
  explode: '<rect x="9" y="9" width="6" height="6" rx="1"/><path class="ac" d="M4 4l3 3M20 4l-3 3M4 20l3-3M20 20l-3-3"/>',
  // library
  search: '<circle cx="10.5" cy="10.5" r="6"/><path class="ac" d="m15 15 5 5"/>',
  screw: '<path d="M8 4h8v4H8z"/><path d="M10 8v12h4V8"/><path class="ac" d="m10 11 4 1.5M10 14l4 1.5M10 17l4 1.5"/>',
  recommend: '<path d="M12 3.5 14.4 9l5.6.4-4.3 3.6 1.4 5.5L12 15.6 6.9 18.5l1.4-5.5L4 9.4 9.6 9z"/><path class="ac" d="M12 8.5v4"/>',
  library: '<path d="M4 4h4v16H4zM10 4h4v16h-4z"/><path class="ac" d="m15.5 5.2 3.9-1 3.1 14.9-3.9 1z"/>',
  // AI build
  spark: '<path d="M12 3c.6 4.5 2.5 6.4 7 7-4.5.6-6.4 2.5-7 7-.6-4.5-2.5-6.4-7-7 4.5-.6 6.4-2.5 7-7z"/><path class="ac" d="M19 16.5c.25 1.6.9 2.25 2.5 2.5-1.6.25-2.25.9-2.5 2.5-.25-1.6-.9-2.25-2.5-2.5 1.6-.25 2.25-.9 2.5-2.5z"/>',
  plan: '<path d="M9 6h11M9 12h11M9 18h11"/><path class="ac" d="M4 5h1.5v3M4 11.2a1 1 0 0 1 2 .2c0 .8-2 1.6-2 2.6h2M4 17h2l-1 1.2a1 1 0 1 1-1 1.3"/>',
  next: '<path d="M6 5v14l9-7z"/><path class="ac" d="M18 5v14"/>',
  runall: '<path d="M4 5v14l7-7z"/><path class="ac" d="M12 5v14l7-7z"/>',
  pause: '<rect x="6.5" y="5" width="3.5" height="14" rx="1"/><rect class="ac" x="14" y="5" width="3.5" height="14" rx="1"/>',
  stop: '<rect x="5.5" y="5.5" width="13" height="13" rx="1.5"/><path class="ac" d="M9.5 9.5h5v5h-5z"/>',
  replan: '<path d="M4.5 12a7.5 7.5 0 0 1 13-5.1M19.5 12a7.5 7.5 0 0 1-13 5.1"/><path class="ac" d="M17.5 3v4h-4M6.5 21v-4h4"/>',
  assign: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path class="ac" d="M16.5 11l2 2 3.5-4"/>',
  review: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle class="ac" cx="12" cy="12" r="3"/>',
  // view
  fit: '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/><rect class="ac" x="8.5" y="8.5" width="7" height="7" rx="1"/>',
  front: '<path d="M7 7h12v12H7z"/><path d="M4 4l3 3M4 4h12l3 3M4 4v12l3 3"/><path class="ac" d="M7 7h12v12H7z"/>',
  top: '<path d="M4 9 12 5l8 4-8 4z" class="ac"/><path d="M4 9v7l8 4 8-4V9M12 13v7"/>',
  right: '<path d="M4 9 12 5l8 4v7l-8 4-8-4zM4 9l8 4 8-4M12 13v7"/><path class="ac" d="M12 13l8-4v7l-8 4z"/>',
  iso: '<path d="M4 8 12 4l8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8"/><circle class="ac" cx="12" cy="12" r="1.3"/>',
  shaded: '<path d="M4 8 12 4l8 4v8l-8 4-8-4z" fill="currentColor" fill-opacity=".22"/><path d="M4 8l8 4 8-4M12 12v8" stroke-opacity=".0"/>',
  edges: '<path d="M4 8 12 4l8 4v8l-8 4-8-4z" fill="currentColor" fill-opacity=".22"/><path d="M4 8l8 4 8-4M12 12v8"/>',
  wireframe: '<path d="M4 8 12 4l8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8"/><path d="M4 16l8-4 8 4M12 4v8" stroke-dasharray="1.6 1.6"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
  eyeoff: '<path d="M4 4l16 16M9.6 5.9A9.6 9.6 0 0 1 12 5.5C18 5.5 21.5 12 21.5 12a17 17 0 0 1-3 3.6M6.2 7.4A17 17 0 0 0 2.5 12S6 18.5 12 18.5c1.3 0 2.5-.3 3.6-.8"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>',
  follow: '<circle cx="12" cy="12" r="8.5"/><circle class="ac" cx="12" cy="12" r="3"/><path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3"/>',
  panel: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path class="ac" d="M15 4.5v15"/>',
  // quick access + misc
  new: '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/><path class="ac" d="M12 11v6M9 14h6"/>',
  save: '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4"/><rect class="ac" x="8" y="13" width="8" height="5" rx=".5"/>',
  undo: '<path d="M9 7 4.5 11.5 9 16"/><path class="ac" d="M4.5 11.5H15a4.5 4.5 0 0 1 0 9h-3"/>',
  redo: '<path d="m15 7 4.5 4.5L15 16"/><path class="ac" d="M19.5 11.5H9a4.5 4.5 0 0 0 0 9h3"/>',
  rebuild: '<path d="M19 12a7 7 0 1 1-2.05-4.95"/><path class="ac" d="M19.5 3.5V8H15"/>',
  tree: '<path d="M5 4v15M5 8h5M5 14h5M5 19h5"/><rect class="ac" x="11" y="6" width="9" height="4" rx="1"/><rect x="11" y="12" width="9" height="4" rx="1"/>',
  props: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle class="ac" cx="15" cy="6" r="2"/><circle class="ac" cx="9" cy="12" r="2"/><circle class="ac" cx="17" cy="18" r="2"/>',
  params: '<path d="M4 4h16v16H4zM4 10h16M10 10v10"/><path class="ac" d="M6.5 7h5"/>',
  team: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path class="ac" d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18.5 20a6.5 6.5 0 0 0-3-5.5"/>',
  chat: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4A2.5 2.5 0 0 1 3 13.5z"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.5a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .8-1 1.5v.7"/><circle cx="12" cy="16.8" r=".4" fill="currentColor"/>',
  pin: '<path d="M9 4h6l-1 5 3 3H7l3-3zM12 12v8"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  lock: '<rect x="5.5" y="10.5" width="13" height="9.5" rx="1.5"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
  body: '<path d="M4 8 12 4l8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8"/>',
  plane: '<path d="M3 16.5 8 7.5h13l-5 9z"/>',
  origin: '<path d="M12 12V4M12 12l7 4M12 12l-7 4"/><circle cx="12" cy="12" r="1.5"/>',
  doc: '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/><path d="M9 13h6M9 16.5h4"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  warn: '<path d="M12 4 21 19.5H3z"/><path d="M12 10v4.5"/><circle cx="12" cy="17" r=".4" fill="currentColor"/>',
  error: '<circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/>',
  activity: '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>',
  more: '<circle cx="5.5" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="18.5" cy="12" r="1.3" fill="currentColor"/>',
  send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>',
  assembly: '<rect x="3.5" y="12" width="8" height="8" rx="1"/><rect x="12.5" y="12" width="8" height="8" rx="1"/><rect class="ac" x="8" y="3.5" width="8" height="8" rx="1"/>',
  part: '<path d="M4 8 12 4l8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8"/>',
  ruler: '<path d="M3 17 17 3l4 4L7 21z"/><path d="m7 13 1.5 1.5M10 10l2 2M13 7l1.5 1.5"/>',
};

export type IconName = keyof typeof P;

export function icon(name: string, cls = 'ico'): string {
  return `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[name] ?? P.part}</svg>`;
}

export const hasIcon = (name: string) => name in P;

/** The CADSwarm mark: a hex cell with three linked nodes (a part being built by a small swarm). */
export const MARK = `<svg class="mark" viewBox="0 0 24 24" aria-hidden="true">
  <path d="M12 2.2 20.5 7v10L12 21.8 3.5 17V7z" fill="var(--accent)" stroke="none"/>
  <path d="M8.2 9.4 12 15.6l3.8-6.2z" fill="none" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/>
  <circle cx="8.2" cy="9.4" r="1.7" fill="#fff"/><circle cx="15.8" cy="9.4" r="1.7" fill="#fff"/><circle cx="12" cy="15.6" r="1.7" fill="#fff"/>
</svg>`;
