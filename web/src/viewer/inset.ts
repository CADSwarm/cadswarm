// How far to lift the 3D view when the phone's chat sheet covers part of it.
//
// The camera is framed as if the screen were taller by the covered height, so the model's centre
// sits in the middle of what is still visible above the sheet. That is roughly half the covered
// height; 0.45 leaves a little breathing room under the model rather than pushing it to the edge.
export const INSET_RATIO = 0.45;

/** Pixels to shift the projection up for a sheet covering `coveredPx` of the screen. Zero when nothing is covered. */
export function insetShift(coveredPx: number): number {
  const covered = Number.isFinite(coveredPx) ? Math.max(0, coveredPx) : 0;
  return Math.round(covered * INSET_RATIO);
}
