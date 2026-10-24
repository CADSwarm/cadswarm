import { describe, expect, it } from 'vitest';
import { cameraMessage, frameSize } from '../src/ui/scan';

describe('camera errors', () => {
  it('says what to do when access is refused', () => {
    expect(cameraMessage({ name: 'NotAllowedError' })).toBe(
      'Camera access is off for this page. Allow it in your browser, or type the invite code instead.');
    expect(cameraMessage({ name: 'SecurityError' })).toContain('Camera access is off');
  });

  it('names a missing or busy camera, and never blocks the paste box', () => {
    expect(cameraMessage({ name: 'NotFoundError' })).toContain('no camera');
    expect(cameraMessage({ name: 'OverconstrainedError' })).toContain('no camera');
    expect(cameraMessage({ name: 'NotReadableError' })).toContain('Another app is using the camera');
    for (const e of [{ name: 'NotFoundError' }, { name: 'NotReadableError' }, {}, null]) {
      expect(cameraMessage(e)).toContain('instead');
    }
  });

  it('falls back to a plain message for anything else', () => {
    expect(cameraMessage(new Error('boom'))).toBe(
      'The camera could not start. Type or paste the invite code instead.');
  });
});

describe('frame size', () => {
  it('shrinks a phone frame to a square-ish decode size', () => {
    expect(frameSize(1920, 1080)).toEqual({ width: 640, height: 360 });
    expect(frameSize(1080, 1920)).toEqual({ width: 360, height: 640 });
    expect(frameSize(640, 480)).toEqual({ width: 640, height: 480 });
  });

  it('never scales a small frame up, and never asks for zero pixels', () => {
    expect(frameSize(320, 240)).toEqual({ width: 320, height: 240 });
    expect(frameSize(0, 0)).toEqual({ width: 1, height: 1 });
  });
});
