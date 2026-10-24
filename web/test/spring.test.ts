import { describe, expect, it } from 'vitest';
import { Spring3, acceptSeq } from '../src/viewer/spring';

describe('Spring3', () => {
  it('glides to the target without overshooting (critically damped)', () => {
    const s = new Spring3([0, 0, 0]);
    s.target = [10, 0, 0];
    let max = 0;
    for (let i = 0; i < 120; i++) { s.step(1 / 60); max = Math.max(max, s.pos[0]); }
    expect(s.pos[0]).toBeCloseTo(10, 1);
    expect(max).toBeLessThanOrEqual(10.0001);
  });
  it('is stable for large frame gaps', () => {
    const s = new Spring3([0, 0, 0]);
    s.target = [5, 5, 5];
    s.step(0.5);
    expect(Number.isFinite(s.pos[0])).toBe(true);
    expect(Math.abs(s.pos[0])).toBeLessThan(6);
  });
  it('snap jumps straight to the target (reduced motion)', () => {
    const s = new Spring3([0, 0, 0]);
    s.target = [1, 2, 3];
    s.snap();
    expect(s.pos).toEqual([1, 2, 3]);
    expect(s.settled()).toBe(true);
  });
});

describe('acceptSeq', () => {
  it('drops pointer updates that arrive out of order', () => {
    const seen = new Map<number, number>();
    expect(acceptSeq(seen, 1, 5)).toBe(true);
    expect(acceptSeq(seen, 1, 4)).toBe(false);
    expect(acceptSeq(seen, 1, 6)).toBe(true);
    expect(acceptSeq(seen, 2, 1)).toBe(true);
  });
});
