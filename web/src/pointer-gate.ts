// Human pointer updates (plan 04): at most 5 per second, only when the pointer moved.
// Leading edge sends at once; a burst inside the window collapses into one trailing send of the latest value.
export class PointerGate<T> {
  private lastKey = '';
  private lastSent = -Infinity;
  private pending: { key: string; value: T } | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private send: (value: T) => void;
  private minMs: number;
  private now: () => number;

  constructor(send: (value: T) => void, minMs = 200, now: () => number = () => performance.now()) {
    this.send = send;
    this.minMs = minMs;
    this.now = now;
  }

  push(key: string, value: T) {
    if (key === (this.pending?.key ?? this.lastKey)) return;
    const wait = this.lastSent + this.minMs - this.now();
    if (wait <= 0 && !this.timer) { this.fire(key, value); return; }
    this.pending = { key, value };
    if (!this.timer) this.timer = setTimeout(() => this.flush(), Math.max(0, wait));
  }

  private flush() {
    this.timer = null;
    const p = this.pending;
    this.pending = null;
    if (p && p.key !== this.lastKey) this.fire(p.key, p.value);
  }

  private fire(key: string, value: T) {
    this.lastKey = key;
    this.lastSent = this.now();
    this.send(value);
  }

  dispose() { if (this.timer) clearTimeout(this.timer); this.timer = null; this.pending = null; }
}
