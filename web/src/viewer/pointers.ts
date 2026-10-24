import * as THREE from 'three';
import { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import type { Store } from '../store';
import { memberColor } from '../color';
import { Spring3, acceptSeq } from './spring';

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;
const GLYPH: Record<string, string> = { human: '➤', agent: '◆', reviewer: '⌕', host: '■' };

interface Glyph { group: THREE.Group; spring: Spring3; tag: HTMLDivElement; line: THREE.Line; fade: number }

export class Pointers {
  private glyphs = new Map<number, Glyph>();
  private seen = new Map<number, number>();
  private scene: THREE.Scene;
  private store: Store;
  constructor(scene: THREE.Scene, store: Store) { this.scene = scene; this.store = store; }

  private make(member: number): Glyph {
    const m = this.store.member(member);
    const color = memberColor(member, dark());
    const tag = document.createElement('div');
    tag.className = 'pointer-tag';
    tag.style.setProperty('--c', color);
    const label = new CSS2DObject(tag);
    const group = new THREE.Group();
    const dot = new THREE.Mesh(new THREE.SphereGeometry(1.6, 16, 12), new THREE.MeshBasicMaterial({ color }));
    group.add(dot, label);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.6 }));
    this.scene.add(group, line);
    tag.dataset.glyph = GLYPH[m?.kind ?? 'agent'] ?? '◆';
    return { group, spring: new Spring3([0, 0, 40]), tag, line, fade: 0 };
  }

  update(dt: number, meshOf: (name: string) => THREE.Mesh | null) {
    const live = new Set<number>();
    const still = reduced();
    for (const p of this.store.activePointers()) {
      if (p.member === this.store.state.meId) continue;
      live.add(p.member);
      const g = this.glyphs.get(p.member) ?? this.make(p.member);
      this.glyphs.set(p.member, g);
      if (acceptSeq(this.seen, p.member, p.seq) || g.fade === 0) {
        const target = p.point.length === 3 ? new THREE.Vector3().fromArray(p.point) : this.anchor(meshOf(p.object));
        if (target) g.spring.target = [target.x, target.y, target.z];
      }
      if (still) g.spring.snap(); else g.spring.step(dt);               // reduced motion: pointers jump
      g.group.position.set(...g.spring.pos);
      const m = this.store.member(p.member);
      g.tag.textContent = `${g.tag.dataset.glyph} ${m?.name ?? p.member}${p.label ? ' · ' + p.label : ''}`;
      g.tag.classList.toggle('editing', p.mode === 'editing');
      const mesh = meshOf(p.object);
      g.line.visible = p.mode === 'editing' && !!mesh;
      if (mesh) {
        const c = new THREE.Box3().setFromObject(mesh).getCenter(new THREE.Vector3());
        (g.line.geometry as THREE.BufferGeometry).setFromPoints([g.group.position.clone(), c]);
      }
      g.fade = Math.min(1, g.fade + dt * 4);
      g.tag.style.opacity = String(g.fade);
    }
    for (const [id, g] of this.glyphs) {
      if (live.has(id)) continue;
      g.fade = Math.max(0, g.fade - dt);                                // idle pointers fade out over a second
      g.tag.style.opacity = String(g.fade);
      if (g.fade === 0) { this.scene.remove(g.group, g.line); g.tag.remove(); this.glyphs.delete(id); }
    }
  }

  /** The point a pointer rests on: just above the object's bounding box centre. */
  private anchor(mesh: THREE.Mesh | null): THREE.Vector3 | null {
    if (!mesh) return null;
    const b = new THREE.Box3().setFromObject(mesh);
    const c = b.getCenter(new THREE.Vector3());
    return c.setZ(b.max.z + 3);
  }
}
