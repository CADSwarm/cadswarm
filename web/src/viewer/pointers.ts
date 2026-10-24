import * as THREE from 'three';
import { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import type { Store } from '../store';
import { memberColor } from '../color';
import { Spring3, acceptSeq } from './spring';
import { beadAt, headingFor, speedStretch } from './arrow';

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;
const GLYPH: Record<string, string> = { human: '➤', agent: '◆', reviewer: '⌕', host: '■' };
const MODE_WORD: Record<string, string> = { pointing: 'looking', inspecting: 'inspecting', editing: 'editing' };
const UP = new THREE.Vector3(0, 1, 0);
const BEADS = 2;
const TRAIL = 14;

/** Where a pointer rests on a part: an arrow hovering over it and the surface it works on. */
interface Anchor { hover: THREE.Vector3; surface: THREE.Vector3 }

interface Stroke { mat: THREE.Material; base: number }

interface Glyph {
  group: THREE.Group;
  spring: Spring3;
  tag: HTMLDivElement;
  arrow: THREE.Group;
  strokes: Stroke[];
  beam: THREE.Line;
  beads: THREE.Mesh[];
  trail: THREE.Line;
  trailPoints: THREE.Vector3[];
  heading: THREE.Vector3;
  phase: number;
  fade: number;
}

export class Pointers {
  private glyphs = new Map<number, Glyph>();
  private seen = new Map<number, number>();
  private scene: THREE.Scene;
  private store: Store;
  constructor(scene: THREE.Scene, store: Store) { this.scene = scene; this.store = store; }

  private make(member: number): Glyph {
    const m = this.store.member(member);
    const color = memberColor(member, dark());
    const tint = new THREE.Color(color);

    const tag = document.createElement('div');
    tag.className = 'pointer-tag';
    tag.style.setProperty('--c', color);
    tag.dataset.glyph = GLYPH[m?.kind ?? 'agent'] ?? '◆';
    const label = new CSS2DObject(tag);

    // An arrow whose tip sits on the pointer and whose body trails behind it, so it points
    // at whatever the member is working on. The head and shaft share one rotation.
    const headMat = new THREE.MeshBasicMaterial({ color: tint, transparent: true });
    const head = new THREE.Mesh(new THREE.ConeGeometry(2.2, 5.5, 14), headMat);
    head.position.y = -2.75;
    const shaftMat = new THREE.MeshBasicMaterial({ color: tint, transparent: true });
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.65, 0.65, 6, 10), shaftMat);
    shaft.position.y = -8.75;
    const arrow = new THREE.Group();
    arrow.add(head, shaft);

    const group = new THREE.Group();
    group.add(arrow, label);

    // The beam joins the arrow to the part; two beads run along it while the member works.
    const beamMat = new THREE.LineBasicMaterial({ color: tint, transparent: true, opacity: 0.3 });
    const beam = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]), beamMat);
    const beadMat = new THREE.MeshBasicMaterial({ color: tint, transparent: true });
    const beads = Array.from({ length: BEADS }, () => {
      const bead = new THREE.Mesh(new THREE.SphereGeometry(0.85, 10, 8), beadMat);
      bead.visible = false;
      return bead;
    });

    // A short trail of where the arrow has been; it retracts as the arrow comes to rest.
    const trailGeom = new THREE.BufferGeometry();
    trailGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(TRAIL * 3), 3));
    trailGeom.setDrawRange(0, 0);
    const trailMat = new THREE.LineBasicMaterial({ color: tint, transparent: true, opacity: 0.45 });
    const trail = new THREE.Line(trailGeom, trailMat);

    this.scene.add(group, beam, trail, ...beads);
    return {
      group, spring: new Spring3([0, 0, 40]), tag, arrow,
      strokes: [{ mat: headMat, base: 1 }, { mat: shaftMat, base: 1 }, { mat: beadMat, base: 1 }, { mat: trailMat, base: 0.45 }],
      beam, beads, trail, trailPoints: [], heading: new THREE.Vector3(0, 0, 1),
      phase: (member * 0.37) % 1, fade: 0,
    };
  }

  update(dt: number, meshOf: (name: string) => THREE.Mesh | null) {
    const live = new Set<number>();
    const still = reduced();
    for (const p of this.store.activePointers()) {
      if (p.member === this.store.state.meId) continue;
      live.add(p.member);
      const g = this.glyphs.get(p.member) ?? this.make(p.member);
      this.glyphs.set(p.member, g);

      const anchor = this.anchor(meshOf(p.object));
      if (acceptSeq(this.seen, p.member, p.seq) || g.fade === 0) {
        const rest = p.point.length === 3 ? new THREE.Vector3().fromArray(p.point) : anchor?.hover;
        if (rest) g.spring.target = [rest.x, rest.y, rest.z];
      }
      if (still) g.spring.snap(); else g.spring.step(dt);               // reduced motion: pointers jump
      g.group.position.set(...g.spring.pos);

      // Flying arrows follow their speed; a resting arrow aims down at the part it works on.
      const aim = anchor ? [anchor.surface.x, anchor.surface.y, anchor.surface.z] : null;
      const heading = headingFor(g.spring.vel, g.spring.pos, aim, [g.heading.x, g.heading.y, g.heading.z]);
      g.heading.set(heading[0], heading[1], heading[2]);
      const q = new THREE.Quaternion().setFromUnitVectors(UP, g.heading);
      if (still) g.arrow.quaternion.copy(q);
      else g.arrow.quaternion.slerp(q, 1 - Math.exp(-dt * 10));
      g.arrow.scale.set(1, still ? 1 : speedStretch(Math.hypot(...g.spring.vel)), 1);

      const m = this.store.member(p.member);
      const what = p.label || MODE_WORD[p.mode] || '';
      g.tag.textContent = `${g.tag.dataset.glyph} ${m?.name ?? p.member}${what ? ' · ' + what : ''}`;
      g.tag.classList.toggle('editing', p.mode === 'editing');

      // Beam and beads: only when the arrow hovers off the part it is working on.
      const beamOn = !!anchor && g.group.position.distanceTo(anchor.surface) > 1;
      g.beam.visible = beamOn;
      for (const bead of g.beads) bead.visible = beamOn && !still;
      if (anchor && beamOn) {
        (g.beam.geometry as THREE.BufferGeometry).setFromPoints([g.group.position.clone(), anchor.surface]);
        (g.beam.material as THREE.LineBasicMaterial).opacity = (p.mode === 'editing' ? 0.6 : 0.3) * g.fade;
        g.phase = (g.phase + dt * 0.8) % 1;
        for (let i = 0; i < g.beads.length; i++) {
          g.beads[i].position.lerpVectors(g.group.position, anchor.surface, beadAt(g.phase, i, g.beads.length));
        }
      }

      this.trail(g, still);

      g.fade = Math.min(1, g.fade + dt * 4);
      g.tag.style.opacity = String(g.fade);
      for (const s of g.strokes) s.mat.opacity = s.base * g.fade;
    }
    for (const [id, g] of this.glyphs) {
      if (live.has(id)) continue;
      g.fade = Math.max(0, g.fade - dt);                                // idle pointers fade out over a second
      g.tag.style.opacity = String(g.fade);
      for (const s of g.strokes) s.mat.opacity = s.base * g.fade;
      if (g.beam.visible) (g.beam.material as THREE.LineBasicMaterial).opacity = 0.3 * g.fade;
      if (g.fade === 0) { this.drop(g); this.glyphs.delete(id); }
    }
  }

  /** A short line through the arrow's recent positions; resting pulls the tail in. */
  private trail(g: Glyph, still: boolean) {
    if (still) {
      g.trailPoints.length = 0;
      g.trail.geometry.setDrawRange(0, 0);
      g.trail.visible = false;
      return;
    }
    g.trail.visible = true;
    const pos = g.group.position;
    const last = g.trailPoints[g.trailPoints.length - 1];
    if (!last || last.distanceToSquared(pos) > 0.09) g.trailPoints.push(pos.clone());
    else if (g.trailPoints.length) g.trailPoints.shift();
    if (g.trailPoints.length > TRAIL) g.trailPoints.shift();
    const attr = g.trail.geometry.getAttribute('position') as THREE.BufferAttribute;
    for (let i = 0; i < g.trailPoints.length; i++) {
      const p = g.trailPoints[i];
      attr.setXYZ(i, p.x, p.y, p.z);
    }
    attr.needsUpdate = true;
    g.trail.geometry.setDrawRange(0, g.trailPoints.length);
  }

  /** Where a pointer rests on a part: just above its bounding box, and the surface below. */
  private anchor(mesh: THREE.Mesh | null): Anchor | null {
    if (!mesh) return null;
    const b = new THREE.Box3().setFromObject(mesh);
    const c = b.getCenter(new THREE.Vector3());
    const size = b.getSize(new THREE.Vector3());
    const surface = c.clone().setZ(b.max.z);
    const clearance = Math.min(18, Math.max(8, size.z));
    return { hover: surface.clone().setZ(surface.z + clearance), surface };
  }

  private drop(g: Glyph) {
    this.scene.remove(g.group, g.beam, g.trail, ...g.beads);
    g.tag.remove();
    g.group.traverse(o => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.geometry.dispose();
      (mesh.material as THREE.Material).dispose();
    });
    g.beam.geometry.dispose();
    (g.beam.material as THREE.Material).dispose();
    g.trail.geometry.dispose();
    (g.trail.material as THREE.Material).dispose();
    for (const bead of g.beads) bead.geometry.dispose();
    (g.beads[0]?.material as THREE.Material | undefined)?.dispose();
  }
}
