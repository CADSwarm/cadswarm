type V3 = [number, number, number];

/** Critically damped spring (exact solution per step, so any dt is stable and it never overshoots). */
export class Spring3 {
  pos: V3; vel: V3 = [0, 0, 0]; target: V3;
  private omega: number;
  constructor(start: V3, omega = 9) { this.omega = omega; this.pos = [...start] as V3; this.target = [...start] as V3; }
  step(dt: number) {
    const w = this.omega, e = Math.exp(-w * dt);
    for (let i = 0; i < 3; i++) {
      const x = this.pos[i] - this.target[i];
      const v = this.vel[i];
      const temp = (v + w * x) * dt;
      this.vel[i] = (v - w * temp) * e;
      this.pos[i] = this.target[i] + (x + temp) * e;
    }
  }
  snap() { this.pos = [...this.target] as V3; this.vel = [0, 0, 0]; }
  settled(eps = 0.01) {
    return this.pos.every((p, i) => Math.abs(p - this.target[i]) < eps) && this.vel.every(v => Math.abs(v) < eps);
  }
}

export function acceptSeq(lastSeen: Map<number, number>, member: number, seq: number): boolean {
  const prev = lastSeen.get(member) ?? -1;
  if (seq <= prev) return false;
  lastSeen.set(member, seq);
  return true;
}
