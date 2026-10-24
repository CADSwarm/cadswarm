import { describe, expect, it } from 'vitest';
import { detents, nearestDetent, nextStop } from '../src/ui/sheet';

describe('sheet detents', () => {
  it('peeks, sits at half the screen and opens under the top bar', () => {
    expect(detents(844)).toEqual({ peek: 190, half: 456, full: 788 });
    expect(detents(667)).toEqual({ peek: 190, half: 360, full: 611 });
  });

  it('keeps the stops ordered on a short screen', () => {
    const d = detents(400);
    expect(d).toEqual({ peek: 190, half: 216, full: 344 });
    expect(d.peek).toBeLessThanOrEqual(d.half);
    expect(d.half).toBeLessThanOrEqual(d.full);
    expect(d.full).toBeLessThanOrEqual(400);
  });

  it('never lets a stop fall below the peek or past the viewport', () => {
    expect(detents(120)).toEqual({ peek: 120, half: 120, full: 120 });
    for (const v of [120, 300, 480, 844, 1400]) {
      const d = detents(v);
      expect(d.peek).toBeGreaterThanOrEqual(Math.min(190, v));
      expect(d.full).toBeLessThanOrEqual(v);
    }
  });
});

describe('nearest detent', () => {
  it('snaps to the closest stop', () => {
    expect(nearestDetent(120, 844)).toBe('peek');
    expect(nearestDetent(300, 844)).toBe('peek');
    expect(nearestDetent(400, 844)).toBe('half');
    expect(nearestDetent(700, 844)).toBe('full');
  });

  it('prefers the lower stop on a tie', () => {
    const { peek, half } = detents(844);
    expect(nearestDetent((peek + half) / 2, 844)).toBe('peek');
  });

  it('cycles peek → half → full → peek', () => {
    expect(nextStop('peek')).toBe('half');
    expect(nextStop('half')).toBe('full');
    expect(nextStop('full')).toBe('peek');
  });
});
