import { describe, expect, it } from 'vitest';
import { MAX_STRETCH, beadAt, headingFor, magnitude, speedStretch, unit } from '../src/viewer/arrow';

const PREVIOUS: [number, number, number] = [0, 0, 1];

describe('headingFor', () => {
  it('flies along its speed while it is moving', () => {
    const h = headingFor([0, 30, 0], [0, 0, 0], [100, 0, 0], PREVIOUS);
    expect(h).toEqual([0, 1, 0]);
  });
  it('aims at the part once it rests', () => {
    const h = headingFor([0.2, 0, 0], [0, 0, 20], [10, 0, 10], PREVIOUS);
    expect(h[0]).toBeCloseTo(Math.SQRT1_2, 5);
    expect(h[2]).toBeCloseTo(-Math.SQRT1_2, 5);
  });
  it('keeps its heading when it rests with nothing to look at', () => {
    expect(headingFor([0, 0, 0], [5, 5, 5], null, PREVIOUS)).toEqual(PREVIOUS);
  });
  it('does not turn towards a point it already sits on', () => {
    expect(headingFor([0, 0, 0], [5, 5, 5], [5, 5, 5], PREVIOUS)).toEqual(PREVIOUS);
  });
});

describe('speedStretch', () => {
  it('is plain at rest and longest at full speed', () => {
    expect(speedStretch(0)).toBe(1);
    expect(speedStretch(90)).toBeCloseTo(MAX_STRETCH, 5);
    expect(speedStretch(500)).toBeCloseTo(MAX_STRETCH, 5);
  });
  it('grows with speed', () => {
    expect(speedStretch(45)).toBeGreaterThan(speedStretch(10));
  });
});

describe('beadAt', () => {
  it('spreads the beads evenly along the beam', () => {
    expect(beadAt(0, 0, 2)).toBe(0);
    expect(beadAt(0, 1, 2)).toBe(0.5);
  });
  it('loops back to the arrow', () => {
    expect(beadAt(1, 0, 2)).toBe(0);
    expect(beadAt(2.25, 0, 2)).toBeCloseTo(0.25, 5);
  });
});

describe('unit', () => {
  it('normalises and rejects a zero vector', () => {
    expect(unit([0, 3, 4])).toEqual([0, 0.6, 0.8]);
    expect(unit([0, 0, 0])).toBeNull();
    expect(magnitude([3, 4, 0])).toBe(5);
  });
});
