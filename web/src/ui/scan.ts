// Reading the invite QR with the phone's camera.
//
// Browsers that ship `BarcodeDetector` decode the video element directly; everywhere else (notably
// Safari and older Chrome) we downscale a frame and hand the pixels to jsQR. Both run in one throttled
// loop, and the camera is released on every exit: success, Cancel, Escape or navigating away.
// jsQR is loaded on demand, so phones with a built-in decoder never download it.

/** How many pixels jsQR gets per frame: enough for a QR held at arm's length, cheap on a phone. */
const MAX_SIDE = 640;
/** Decode at most this often: 6 frames a second is plenty for a code held still. */
const INTERVAL_MS = 160;

interface DetectedCode { rawValue: string }
interface Detector { detect(source: CanvasImageSource): Promise<DetectedCode[]> }
type Decoder = (data: Uint8ClampedArray, width: number, height: number, options?: { inversionAttempts?: string }) =>
  { data: string } | null;

let decoder: Promise<Decoder> | null = null;
function loadJsqr(): Promise<Decoder> {
  decoder ??= import('jsqr').then(m => m.default as unknown as Decoder);
  return decoder;
}

/** The camera can be refused, missing or busy: say which, in words a person can act on. */
export function cameraMessage(e: unknown): string {
  const name = (e as { name?: string } | null)?.name ?? '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Camera access is off for this page. Allow it in your browser, or type the invite code instead.';
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return 'This device has no camera to scan with. Type or paste the invite code instead.';
  }
  if (name === 'NotReadableError') {
    return 'Another app is using the camera. Close it and try again, or type the invite code instead.';
  }
  return 'The camera could not start. Type or paste the invite code instead.';
}

/** The size jsQR works on: the frame shrunk to fit `max`, never scaled up. */
export function frameSize(width: number, height: number, max = MAX_SIDE): { width: number; height: number } {
  const scale = Math.min(1, max / Math.max(width, height, 1));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

function barcodeDetector(): Detector | null {
  const ctor = (globalThis as unknown as { BarcodeDetector?: new (o: { formats: string[] }) => Detector }).BarcodeDetector;
  if (typeof ctor !== 'function') return null;
  try { return new ctor({ formats: ['qr_code'] }); } catch { return null; }
}

function abortError(): Error {
  const e = new Error('cancelled');
  e.name = 'AbortError';
  return e;
}

/**
 * Start the camera in `video` and resolve with the first QR text it reads.
 * Rejects with a plain-language Error when the camera cannot start; an `AbortError` when `signal` aborts.
 */
export async function scanQR(video: HTMLVideoElement, signal?: AbortSignal): Promise<string> {
  if (signal?.aborted) throw abortError();
  const media = navigator.mediaDevices;
  if (!media?.getUserMedia) {
    throw new Error('This browser can’t open the camera. Type or paste the invite code instead.');
  }
  let stream: MediaStream;
  try {
    stream = await media.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
    });
  } catch (e) {
    throw new Error(cameraMessage(e));
  }
  video.srcObject = stream;
  video.muted = true;
  video.setAttribute('playsinline', '');
  try { await video.play(); } catch { /* autoplay can be refused; the frames still arrive */ }

  const detector = barcodeDetector();
  const jsqr = detector ? null : await loadJsqr();
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true });

  async function readFrame(): Promise<string | null> {
    if (detector) {
      try {
        const found = (await detector.detect(video)).find(c => c.rawValue);
        if (found) return found.rawValue;
      } catch { /* a frame it cannot decode: try the next one */ }
      return null;
    }
    if (!jsqr || !ctx || !video.videoWidth || !video.videoHeight) return null;
    const { width, height } = frameSize(video.videoWidth, video.videoHeight);
    canvas.width = width;
    canvas.height = height;
    ctx.drawImage(video, 0, 0, width, height);
    const pixels = ctx.getImageData(0, 0, width, height);
    return jsqr(pixels.data, width, height, { inversionAttempts: 'dontInvert' })?.data ?? null;
  }

  return new Promise<string>((resolve, reject) => {
    let done = false;
    let last = 0;
    const finish = () => { done = true; };
    signal?.addEventListener('abort', () => { finish(); reject(abortError()); }, { once: true });
    const tick = (now: number) => {
      if (done) return;
      if (now - last >= INTERVAL_MS && video.readyState >= 2) {
        last = now;
        void readFrame().then(text => {
          if (done) return;
          if (text) { finish(); resolve(text); }
          else requestAnimationFrame(tick);
        }, () => { if (!done) requestAnimationFrame(tick); });
        return;
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

function fullScreen(el: HTMLElement) {
  el.classList.add('scan');
  el.innerHTML = `
    <video class="scan-video" playsinline muted autoplay aria-hidden="true"></video>
    <div class="scan-target" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
    <div class="scan-panel">
      <p class="scan-hint">Point the camera at the invite QR shown by the host computer.</p>
      <p class="scan-error" role="alert"></p>
      <div class="scan-actions">
        <button type="button" class="scan-cancel" data-cancel>Cancel</button>
        <button type="button" class="scan-manual" data-manual>Type the code instead</button>
      </div>
    </div>`;
  return {
    video: el.querySelector<HTMLVideoElement>('video')!,
    hint: el.querySelector<HTMLParagraphElement>('.scan-hint')!,
    error: el.querySelector<HTMLParagraphElement>('.scan-error')!,
    cancel: el.querySelector<HTMLButtonElement>('[data-cancel]')!,
    manual: el.querySelector<HTMLButtonElement>('[data-manual]')!,
  };
}

/**
 * The camera overlay: owns the stream and the decode loop, and hands the first QR text to `onText`.
 * Closing — by success, Cancel, Escape, or a manual-entry choice — always stops the camera.
 */
export function openScanner(onText: (text: string) => void, onError: (text: string) => void, onClose: () => void): () => void {
  const host = document.createElement('div');
  host.setAttribute('role', 'dialog');
  host.setAttribute('aria-modal', 'true');
  host.setAttribute('aria-label', 'Scan the invite QR');
  document.body.appendChild(host);
  const ui = fullScreen(host);
  const abort = new AbortController();
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    abort.abort(); // stops the decode loop
    const stream = ui.video.srcObject as MediaStream | null;
    stream?.getTracks().forEach(track => track.stop());
    ui.video.srcObject = null;
    host.remove();
    removeEventListener('keydown', onKey);
    onClose();
  };

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
  }

  ui.cancel.addEventListener('click', close);
  ui.manual.addEventListener('click', close);
  // Tapping the dark surrounds closes it; tapping the panel or the target does not.
  host.addEventListener('click', e => { if (e.target === host) close(); });
  addEventListener('keydown', onKey);
  ui.cancel.focus();

  void scanQR(ui.video, abort.signal).then(text => {
    if (closed) return;
    close();
    onText(text);
  }, (e: unknown) => {
    if (closed || (e as Error)?.name === 'AbortError') return;
    // The camera never started: release whatever it may have opened, show why, keep manual entry.
    const stream = ui.video.srcObject as MediaStream | null;
    stream?.getTracks().forEach(track => track.stop());
    ui.video.srcObject = null;
    const message = (e as Error)?.message || cameraMessage(e);
    ui.error.textContent = message;
    ui.hint.textContent = 'You can still type the invite code instead.';
    onError(message);
  });

  return close;
}
