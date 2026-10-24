import { describe, expect, it } from 'vitest';
import { detents } from '../src/ui/sheet';
import { insetShift } from '../src/viewer/inset';

describe('viewer inset', () => {
  it('lifts the view by about half of what the sheet covers', () => {
    expect(insetShift(190)).toBe(86);
    expect(insetShift(456)).toBe(205);
    expect(insetShift(788)).toBe(355);
  });

  it('keeps the model above the sheet, not at the edge, at every stop', () => {
    const d = detents(844);
    for (const covered of [d.peek, d.half, d.full]) {
      const visibleCentre = (844 - covered) / 2;
      const modelCentre = 844 / 2 - insetShift(covered);
      expect(modelCentre).toBeGreaterThanOrEqual(visibleCentre);
      expect(modelCentre).toBeLessThanOrEqual(visibleCentre + covered * 0.1);
    }
  });

  it('does nothing when nothing is covered, and never shifts down', () => {
    expect(insetShift(0)).toBe(0);
    expect(insetShift(-40)).toBe(0);
    expect(insetShift(Number.NaN)).toBe(0);
  });
});
