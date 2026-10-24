import { describe, expect, it } from 'vitest';
import { Color, SRGBColorSpace } from 'three';
import { memberColor } from '../src/color';

describe('member colours in three.js', () => {
  it('parse as real colours (three.js only reads comma-separated hsl)', () => {
    const c = new Color(memberColor(1, false));
    expect(c.getHexString()).not.toBe('ffffff');
    const hsl = { h: 0, s: 0, l: 0 };
    c.getHSL(hsl, SRGBColorSpace);
    expect(hsl.h * 360).toBeCloseTo(137.5, 0);
  });
});
