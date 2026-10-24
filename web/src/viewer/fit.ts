// How far the camera must sit from the model so all of it stays on screen.
//
// The vertical field of view is fixed, so a portrait phone sees much less horizontally than a
// desktop: fitting by height alone crops the model on a phone. The chat sheet also covers the
// bottom of the screen, and the model is kept above it, which shrinks the usable height further.
// Fit both angles and use whichever needs the camera farther away.

/** Distance from the model's centre for a sphere of `radius` to fit the visible part of the view. */
export function fitDistance(radius: number, fovDeg: number, width: number, height: number, coveredPx = 0): number {
  const w = Math.max(width, 1), h = Math.max(height, 1);
  // Past half the screen the sheet is the point of the screen: stop shrinking the model for it.
  const covered = Math.min(Math.max(coveredPx, 0), h * 0.5);
  const halfFov = (fovDeg / 2) * (Math.PI / 180);
  const vertical = halfFov * ((h - covered) / h);
  const horizontal = Math.atan(Math.tan(halfFov) * (w / h));
  const half = Math.max(Math.min(vertical, horizontal), 0.05);
  return radius / Math.sin(half);
}
