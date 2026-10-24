// Heading and pace maths for the agent arrows. Kept free of three.js so the behaviour is
// unit-tested without a WebGL context.
export type V3 = [number, number, number];

/** Below this speed (model units per second) an arrow counts as resting. */
export const REST_SPEED = 2;
/** How much longer a fast arrow grows, so a dash reads as motion instead of a jump. */
export const MAX_STRETCH = 1.45;

export function magnitude(v: number[]): number {
  return Math.hypot(v[0] ?? 0, v[1] ?? 0, v[2] ?? 0);
}

/** The unit vector of `v`, or null when it has no useful direction. */
export function unit(v: number[]): V3 | null {
  const n = magnitude(v);
  return n > 1e-6 ? [v[0] / n, v[1] / n, v[2] / n] : null;
}

/**
 * Where the arrow should point next. While it travels it flies along its speed; once it
 * rests it aims at what it is working on; with neither, it keeps the heading it had.
 */
export function headingFor(vel: number[], pos: number[], aim: number[] | null, previous: V3): V3 {
  if (magnitude(vel) > REST_SPEED) return unit(vel) ?? previous;
  if (aim) return unit([aim[0] - pos[0], aim[1] - pos[1], aim[2] - pos[2]]) ?? previous;
  return previous;
}

/** Length multiplier from speed: 1 at rest, MAX_STRETCH at 90 units/s and above. */
export function speedStretch(speed: number): number {
  const t = Math.min(Math.max(speed / 90, 0), 1);
  return 1 + t * (MAX_STRETCH - 1);
}

/**
 * Bead `i`'s place on the beam: 0 at the arrow, 1 at the part. `phase` loops every second,
 * and the beads are spread evenly so a line of them reads as flow.
 */
export function beadAt(phase: number, i: number, count = 2): number {
  const p = phase - Math.floor(phase);
  return (p + i / count) % 1;
}

export interface Rest { hover: V3; surface: V3 }

/**
 * Where an arrow rests: hovering over the spot the member works on. That is the exact point
 * they gave (agents rest on a vertex the worker picked) or else the top centre of the part's
 * box; the hover height grows with the part, between 8 and 18 units.
 */
export function restFor(box: { min: number[]; max: number[] } | null, point: number[]): Rest | null {
  let surface: V3;
  if (point.length === 3) surface = [point[0], point[1], point[2]];
  else if (box) surface = [(box.min[0] + box.max[0]) / 2, (box.min[1] + box.max[1]) / 2, box.max[2]];
  else return null;
  const clearance = Math.min(18, Math.max(8, box ? box.max[2] - box.min[2] : 0));
  return { hover: [surface[0], surface[1], surface[2] + clearance], surface };
}
