import { describe, expect, it } from 'vitest';
import { fitDistance } from '../src/viewer/fit';

describe('camera fit', () => {
  it('fits by height on a desktop screen', () => {
    const d = fitDistance(100, 40, 1440, 900);
    expect(d / 100).toBeCloseTo(1 / Math.sin((20 * Math.PI) / 180), 4);
  });

  it('fits by width on a portrait phone, where the horizontal view is narrow', () => {
    const d = fitDistance(100, 40, 390, 844, 190);
    const hHalf = Math.atan(Math.tan((20 * Math.PI) / 180) * (390 / 844));
    expect(d / 100).toBeCloseTo(1 / Math.sin(hHalf), 4);
    // Far enough back that the model's full width is on screen, unlike the old height-only fit.
    expect(d).toBeGreaterThan(fitDistance(100, 40, 1440, 900));
  });

  it('keeps the model above the sheet without shrinking it forever', () => {
    const peek = fitDistance(100, 40, 390, 844, 190);
    const half = fitDistance(100, 40, 390, 844, 456);
    const full = fitDistance(100, 40, 390, 844, 788);
    expect(half).toBeGreaterThanOrEqual(peek);
    expect(full).toBe(fitDistance(100, 40, 390, 844, 422)); // clamped at half the screen
  });

  it('never divides by zero on a collapsed viewport', () => {
    expect(fitDistance(10, 40, 0, 0, 0)).toBeGreaterThan(0);
  });
});
