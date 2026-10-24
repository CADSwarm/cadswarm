import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import type { Store, State } from '../store';
import { memberColor } from '../color';
import { Pointers } from './pointers';

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const dark = () => matchMedia('(prefers-color-scheme: dark)').matches;

interface Part { mesh: THREE.Mesh; outline: THREE.LineSegments; rev: number; fadeIn: number; flash: number; shake: number }

export function mountViewer(el: HTMLElement, store: Store, onPick: (object: string, point: [number, number, number] | null) => void) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  el.appendChild(renderer.domElement);
  const labels = new CSS2DRenderer();
  labels.domElement.className = 'labels';
  el.appendChild(labels.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 20000);
  camera.position.set(120, -160, 140);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.addEventListener('start', () => { goal = null; });
  controls.enableDamping = !reduced();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(80, -60, 160);
  scene.add(sun);
  // A floor that reaches the horizon: 10 mm cells near the model, 100 mm cells out to 4 m, fading with distance.
  function floor(size: number, cells: number, opacity: number) {
    const g = new THREE.GridHelper(size, cells, 0x8e8e93, 0x8e8e93);
    g.rotation.x = Math.PI / 2;
    for (const m of [g.material].flat()) Object.assign(m, { transparent: true, opacity, depthWrite: false });
    scene.add(g);
    return g;
  }
  const grids = [floor(1000, 100, 0.18), floor(4000, 40, 0.32)];
  scene.fog = new THREE.Fog(dark() ? 0x000000 : 0xf2f2f7, 600, 2600);

  const loader = new STLLoader();
  const parts = new Map<string, Part>();
  const pointers = new Pointers(scene, store);
  let framed = false;

  function material(state: State, name: string) {
    const holder = state.locks.find(l => l.object === name);
    const base = new THREE.Color(dark() ? 0x9aa7b4 : 0xb8c2cc);
    if (holder) base.lerp(new THREE.Color(memberColor(holder.holder, dark())), 0.35); // lock tint
    return new THREE.MeshStandardMaterial({ color: base, roughness: 0.55, metalness: 0.05, transparent: true });
  }

  function drop(p: Part) {
    scene.remove(p.mesh);
    p.mesh.geometry.dispose();
    p.outline.geometry.dispose();
  }

  function sync(state: State) {
    for (const [name, p] of parts) {
      if (!state.objects.some(o => o.name === name && o.visible)) { drop(p); parts.delete(name); }
    }
    for (const o of state.objects) {
      const pv = state.previews.get(o.name);
      if (!o.visible || !pv || pv.mesh.length < 84) continue;
      const existing = parts.get(o.name);
      if (existing && existing.rev === pv.rev) {
        (existing.mesh.material as THREE.MeshStandardMaterial).color.copy(material(state, o.name).color);
        continue;
      }
      const geom = loader.parse(pv.mesh.slice().buffer);
      geom.computeVertexNormals();
      const mesh = new THREE.Mesh(geom, material(state, o.name));
      mesh.userData.object = o.name;
      // Static outline, shown instead of the pulse when the OS asks for reduced motion.
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(geom, 30), new THREE.LineBasicMaterial({ color: 0xffffff }));
      outline.visible = false;
      mesh.add(outline);
      if (existing) drop(existing);
      parts.set(o.name, { mesh, outline, rev: pv.rev, fadeIn: reduced() ? 1 : 0, flash: existing && !reduced() ? 1 : 0, shake: 0 });
      scene.add(mesh);
    }
    for (const op of state.ops) { // a rejected op shakes the object it targeted
      if (op.status === 'rejected' && !shaken.has(op.id)) {
        shaken.add(op.id);
        const name = (() => { try { return JSON.parse(op.argsJson).name as string; } catch { return ''; } })();
        const p = parts.get(name);
        if (p && !reduced()) p.shake = 1;
      }
    }
    if (!framed && parts.size) { frame(); framed = true; }
  }
  const shaken = new Set<string>();

  // Camera moves glide (or jump, with reduced motion) to `goal`: framing everything, or one part.
  let goal: { target: THREE.Vector3; position: THREE.Vector3 } | null = null;
  function aim(box: THREE.Box3) {
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3()), size = Math.max(box.getSize(new THREE.Vector3()).length(), 20);
    goal = { target: c, position: c.clone().add(new THREE.Vector3(0.7, -0.9, 0.8).multiplyScalar(size * 1.2)) };
    if (reduced()) { controls.target.copy(goal.target); camera.position.copy(goal.position); goal = null; }
  }
  function frame() {
    const box = new THREE.Box3();
    for (const p of parts.values()) box.expandByObject(p.mesh);
    if (!box.isEmpty()) {  // centre the floor under the model, on its lowest face
      const c = box.getCenter(new THREE.Vector3());
      for (const g of grids) g.position.set(Math.round(c.x / 100) * 100, Math.round(c.y / 100) * 100, box.min.z);
    }
    aim(box);
  }
  function focus(name: string) {
    const p = parts.get(name);
    if (!p) return;
    aim(new THREE.Box3().setFromObject(p.mesh));
    if (!reduced()) p.flash = 1; // show which one it is
  }
  addEventListener('cadswarm:focus', e => focus((e as CustomEvent<string>).detail));
  const fit = document.createElement('button');
  fit.type = 'button';
  fit.className = 'fit';
  fit.textContent = 'Fit';
  fit.title = 'Show the whole model';
  fit.addEventListener('click', frame);
  el.appendChild(fit);

  store.subscribe(sync);

  const ray = new THREE.Raycaster();
  renderer.domElement.addEventListener('pointerup', ev => {
    const r = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1), camera);
    const hit = ray.intersectObjects([...parts.values()].map(p => p.mesh), false)[0];
    onPick(hit ? hit.object.userData.object : '', hit ? [hit.point.x, hit.point.y, hit.point.z] : null);
  });

  function resize() {
    const w = el.clientWidth, h = el.clientHeight;
    renderer.setSize(w, h); labels.setSize(w, h);
    camera.aspect = w / Math.max(h, 1); camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(el);
  resize();

  let last = performance.now();
  renderer.setAnimationLoop(now => {
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    const state = store.state;
    const still = reduced();
    controls.enableDamping = !still;
    for (const [name, p] of parts) {
      const mat = p.mesh.material as THREE.MeshStandardMaterial;
      p.fadeIn = still ? 1 : Math.min(1, p.fadeIn + dt * 2.5);        // cross-fade a new revision in
      mat.opacity = p.fadeIn;
      const editing = state.pointers.find(x => x.object === name && x.mode === 'editing');
      const pulse = editing && !still ? 0.25 + 0.25 * Math.sin(now / 180) : 0;
      p.flash = still ? 0 : Math.max(0, p.flash - dt * 1.5);          // one flash when an edit lands
      const glow = editing ? new THREE.Color(memberColor(editing.member, dark())) : new THREE.Color(0xffffff);
      mat.emissive.copy(glow).multiplyScalar(Math.max(pulse, p.flash * 0.6));
      p.outline.visible = !!editing && still;                         // reduced motion: static outline instead of a pulse
      if (p.outline.visible) (p.outline.material as THREE.LineBasicMaterial).color.copy(glow);
      p.shake = still ? 0 : Math.max(0, p.shake - dt * 2.5);          // rejected edit
      p.mesh.position.x = p.shake ? Math.sin(now / 25) * 1.2 * p.shake : 0;
    }
    pointers.update(dt, name => parts.get(name)?.mesh ?? null);
    if (goal) { // ease toward the requested view; any drag by the person cancels it
      const k = Math.min(1, dt * 4);
      controls.target.lerp(goal.target, k);
      camera.position.lerp(goal.position, k);
      if (camera.position.distanceTo(goal.position) < 0.5) goal = null;
    }
    controls.update();
    const fog = scene.fog as THREE.Fog, d = camera.position.distanceTo(controls.target);
    fog.near = d * 1.5; fog.far = d * 6;  // the floor fades out softly wherever you zoom
    renderer.render(scene, camera);
    labels.render(scene, camera);
  });
}
