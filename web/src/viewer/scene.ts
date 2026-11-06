import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import type { Store, State } from '../store';
import { memberColor } from '../color';
import { Pointers } from './pointers';
import { insetShift } from './inset';
import { fitDistance } from './fit';
import { isDark as dark } from '../theme';
import { view, type ViewName } from '../viewstate';
import { parsePlan } from '../steps';

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

const SELECT = 0x1f7ae0, SELECT_DARK = 0x4c9aff, HOVER = 0xf2a93b;
const AXIS = { x: '#E5484D', y: '#2FA567', z: '#3D7BF7' };
/** Camera directions (from the target towards the camera) for the standard views; Z is up. */
const DIRS: Record<string, [number, number, number]> = {
  front: [0, -1, 0.0001], back: [0, 1, 0.0001], right: [1, 0, 0.0001], left: [-1, 0, 0.0001],
  top: [0, -0.0001, 1], bottom: [0, 0.0001, -1], iso: [1, -1, 0.82],
};

interface Part {
  mesh: THREE.Mesh; edges: THREE.LineSegments; outline: THREE.LineSegments; rev: number;
  fadeIn: number; flash: number; shake: number; center: THREE.Vector3;
}

export function mountViewer(el: HTMLElement, store: Store, onPick: (object: string, point: [number, number, number] | null, add: boolean) => void) {
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch {
    // No WebGL (old browser, blocked GPU, private window that withholds it): say so instead of throwing.
    el.innerHTML = '<p class="viewer-error">This browser can’t show 3D. Install or update it, then reload.</p>';
    return;
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0); // the viewport's CSS gradient shows through
  renderer.localClippingEnabled = true;
  el.appendChild(renderer.domElement);
  const labels = new CSS2DRenderer();
  labels.domElement.className = 'labels';
  el.appendChild(labels.domElement);

  const scene = new THREE.Scene();
  const FOV = 35;
  const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 20000);
  camera.position.set(120, -160, 140);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.addEventListener('start', () => { goal = null; });
  // A tap picks a part; only an actual drag (or a zoom) means "this is my view now".
  let moved = false;
  let down: { x: number; y: number } | null = null;
  renderer.domElement.addEventListener('pointerdown', e => { down = { x: e.clientX, y: e.clientY }; });
  renderer.domElement.addEventListener('pointermove', e => {
    if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) { moved = true; down = null; }
    if (!e.buttons) hoverAt = { x: e.clientX, y: e.clientY };
  });
  renderer.domElement.addEventListener('pointerleave', () => { hoverAt = null; hovered = ''; });
  renderer.domElement.addEventListener('pointercancel', () => { down = null; });
  renderer.domElement.addEventListener('wheel', () => { moved = true; }, { passive: true });
  controls.enableDamping = !reduced();
  // A soft studio environment lights every side and gives library steel a real reflection.
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.32;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x58636f, 0.75));
  const sun = new THREE.DirectionalLight(0xffffff, 1.25);
  sun.position.set(80, -60, 160);
  scene.add(sun);
  const fill = new THREE.DirectionalLight(0xffffff, 0.3);
  fill.position.set(-120, 90, 40);
  scene.add(fill);
  // A floor that reaches the horizon: 10 mm cells near the model, 100 mm cells further out, fading with distance.
  function floor(size: number, cells: number, opacity: number) {
    const g = new THREE.GridHelper(size, cells, 0x7d8794, 0x7d8794);
    g.rotation.x = Math.PI / 2;
    for (const m of [g.material].flat()) Object.assign(m, { transparent: true, opacity, depthWrite: false });
    scene.add(g);
    return g;
  }
  const grids = [floor(1000, 100, 0.16), floor(4000, 40, 0.28)];
  scene.fog = new THREE.Fog(0xf3f5f7, 600, 2600);
  function applyTheme() {
    (scene.fog as THREE.Fog).color.set(dark() ? 0x474f59 : 0xf3f5f7);
  }
  applyTheme();
  addEventListener('cadswarm:theme', applyTheme);

  const loader = new STLLoader();
  const parts = new Map<string, Part>();
  const pointers = new Pointers(scene, store);
  let framed = false;
  let inset = 0; // how much of the screen the chat sheet covers, in CSS pixels
  let hovered = '';
  let hoverAt: { x: number; y: number } | null = null;

  // Library parts read as bought steel; modelled parts as a light grey.
  let planKey = '', libraryObjects = new Set<string>();
  function libraryOf(state: State) {
    const b = [...state.briefs].sort((x, y) => y.updatedMs - x.updatedMs)[0];
    const key = b ? b.id + b.planJson.length : '';
    if (key !== planKey) {
      planKey = key;
      libraryObjects = new Set(b ? parsePlan(b.planJson, state.tasks, b.id).steps.filter(s => s.kind === 'library').flatMap(s => s.objects) : []);
    }
    return libraryObjects;
  }
  function baseColor(state: State, name: string) {
    const holder = state.locks.find(l => l.object === name);
    const steel = libraryOf(state).has(name);
    const base = new THREE.Color(steel ? (dark() ? 0x8e99a6 : 0x98a2ad) : dark() ? 0xb4bdc7 : 0xc4ccd5);
    if (holder) base.lerp(new THREE.Color(memberColor(holder.holder, dark())), 0.3); // lock tint
    return base;
  }

  function drop(p: Part) {
    scene.remove(p.mesh);
    p.mesh.geometry.dispose();
    p.edges.geometry.dispose();
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
        (existing.mesh.material as THREE.MeshStandardMaterial).color.copy(baseColor(state, o.name));
        continue;
      }
      const geom = loader.parse(pv.mesh.slice().buffer);
      geom.computeVertexNormals();
      geom.computeBoundingBox();
      const steel = libraryOf(state).has(o.name);
      const mat = new THREE.MeshStandardMaterial({ color: baseColor(state, o.name), roughness: steel ? 0.35 : 0.55,
        metalness: steel ? 0.55 : 0.02, transparent: true, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
      const mesh = new THREE.Mesh(geom, mat);
      mesh.userData.object = o.name;
      const edgeGeom = new THREE.EdgesGeometry(geom, 28);
      const edges = new THREE.LineSegments(edgeGeom, new THREE.LineBasicMaterial({ color: 0x20242a, transparent: true, opacity: 0.6 }));
      mesh.add(edges);
      // Static outline, shown instead of the pulse when the OS asks for reduced motion.
      const outline = new THREE.LineSegments(edgeGeom.clone(), new THREE.LineBasicMaterial({ color: 0xffffff }));
      outline.visible = false;
      mesh.add(outline);
      if (existing) drop(existing);
      const center = geom.boundingBox!.getCenter(new THREE.Vector3());
      parts.set(o.name, { mesh, edges, outline, rev: pv.rev, center,
        fadeIn: reduced() ? 1 : 0, flash: existing && !reduced() ? 1 : 0, shake: 0 });
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
  function aim(box: THREE.Box3, dir?: THREE.Vector3) {
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3());
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 10);
    // Fit the whole model in what the screen actually shows: a portrait phone has a narrow
    // horizontal field of view, and the chat sheet covers the bottom, so both shrink the angle.
    const distance = fitDistance(radius, FOV, el.clientWidth, el.clientHeight, inset) * 1.04;
    const d = (dir ?? camera.position.clone().sub(controls.target)).clone().normalize();
    if (!dir && d.lengthSq() < 0.5) d.set(...DIRS.iso).normalize();
    goal = { target: c, position: c.clone().add(d.multiplyScalar(distance)) };
    if (reduced()) { controls.target.copy(goal.target); camera.position.copy(goal.position); goal = null; }
  }
  function modelBox(): THREE.Box3 {
    const box = new THREE.Box3();
    for (const [name, p] of parts) if (!view.get().hidden.has(name)) box.expandByObject(p.mesh);
    return box;
  }
  function frame(dir?: THREE.Vector3) {
    const box = modelBox();
    if (!box.isEmpty()) {  // centre the floor under the model, on its lowest face
      const c = box.getCenter(new THREE.Vector3());
      for (const g of grids) g.position.set(Math.round(c.x / 100) * 100, Math.round(c.y / 100) * 100, box.min.z);
    }
    aim(box, dir ?? (framed ? undefined : new THREE.Vector3(...DIRS.iso).normalize()));
  }
  function focus(name: string) {
    const p = parts.get(name);
    if (!p) return;
    aim(new THREE.Box3().setFromObject(p.mesh));
    if (!reduced()) p.flash = 1; // show which one it is
  }
  addEventListener('cadswarm:focus', e => focus((e as CustomEvent<string>).detail));
  addEventListener('cadswarm:view', e => {
    const v = (e as CustomEvent<ViewName>).detail;
    moved = true;
    if (v === 'fit') frame();
    else if (v === 'selection') {
      const box = new THREE.Box3();
      for (const n of view.get().selection) { const p = parts.get(n); if (p) box.expandByObject(p.mesh); }
      if (box.isEmpty()) frame(); else aim(box);
    } else if (DIRS[v]) frame(new THREE.Vector3(...DIRS[v]).normalize());
  });
  // Phones keep a round Fit button (desktop has the heads-up toolbar).
  const fit = document.createElement('button');
  fit.type = 'button';
  fit.className = 'fit';
  fit.textContent = 'Fit';
  fit.title = 'Show the whole model';
  fit.addEventListener('click', () => frame());
  el.appendChild(fit);

  // The view triad: X/Y/Z as the camera sees them. Click an axis to look along it.
  const triad = document.createElement('div');
  triad.className = 'triad';
  triad.innerHTML = `<svg viewBox="-40 -40 80 80" aria-hidden="true"></svg>`;
  el.appendChild(triad);
  const triadSvg = triad.querySelector('svg')!;
  triad.addEventListener('click', e => {
    const a = (e.target as Element).closest<SVGElement>('[data-axis]')?.dataset.axis;
    if (a) view.look(a === 'x' ? 'right' : a === 'y' ? 'back' : 'top');
  });
  const inv = new THREE.Quaternion();
  function drawTriad() {
    inv.copy(camera.quaternion).invert();
    const axes = (['x', 'y', 'z'] as const).map(k => {
      const v = new THREE.Vector3(k === 'x' ? 1 : 0, k === 'y' ? 1 : 0, k === 'z' ? 1 : 0).applyQuaternion(inv);
      return { k, x: v.x * 26, y: -v.y * 26, z: v.z };
    }).sort((a, b) => a.z - b.z);
    triadSvg.innerHTML = '<circle r="3" class="triad-o"/>' + axes.map(a =>
      `<g data-axis="${a.k}" style="--ax:${AXIS[a.k]}" opacity="${a.z < -0.2 ? 0.55 : 1}">
        <line x1="0" y1="0" x2="${a.x.toFixed(1)}" y2="${a.y.toFixed(1)}"/>
        <circle cx="${(a.x * 1.2).toFixed(1)}" cy="${(a.y * 1.2).toFixed(1)}" r="7"/>
        <text x="${(a.x * 1.2).toFixed(1)}" y="${(a.y * 1.2 + 3.2).toFixed(1)}">${a.k.toUpperCase()}</text></g>`).join('');
  }

  store.subscribe(sync);

  // The phone's chat sheet covers the bottom of the screen; frame the model in what is left of it.
  // Shifting the projection up by ~half the covered height keeps the model centred above the sheet.
  addEventListener('cadswarm:inset', e => {
    inset = Math.max(0, Number((e as CustomEvent<number>).detail) || 0);
    resize();
  });
  const ray = new THREE.Raycaster();
  function hit(x: number, y: number) {
    const r = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1), camera);
    const vis = [...parts.entries()].filter(([n]) => !view.get().hidden.has(n)).map(([, p]) => p.mesh);
    const hits = ray.intersectObjects(vis, false);
    const plane = section.on ? clip : null;
    return hits.find(h => !plane || plane.distanceToPoint(h.point) >= 0);
  }
  renderer.domElement.addEventListener('pointerup', ev => {
    if (!down) return; // the end of a drag, not a click
    down = null;
    const h = hit(ev.clientX, ev.clientY);
    const object = h ? h.object.userData.object as string : '';
    const point: [number, number, number] | null = h ? [h.point.x, h.point.y, h.point.z] : null;
    onPick(object, point, ev.shiftKey || ev.ctrlKey || ev.metaKey);
    dispatchEvent(new CustomEvent('cadswarm:pick', { detail: { object, point } }));
  });
  renderer.domElement.addEventListener('dblclick', () => { if (view.get().selection.length) view.look('selection'); });

  function resize() {
    const w = el.clientWidth, h = el.clientHeight;
    renderer.setSize(w, h); labels.setSize(w, h);
    camera.aspect = w / Math.max(h, 1);
    if (inset > 0) camera.setViewOffset(w, h, 0, insetShift(inset), w, h);
    else camera.clearViewOffset();
    camera.updateProjectionMatrix();
    // A turn of the phone, or the sheet opening, changes how much room the model has: re-fit it
    // until the person takes over the view themselves.
    if (framed && !moved) frame();
  }
  new ResizeObserver(resize).observe(el);
  resize();

  // Section plane, from the model's box along the chosen axis.
  const clip = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);
  let section = view.get().section;
  let followed = { member: 0, object: '' };

  let last = performance.now();
  let hoverFrame = 0;
  renderer.setAnimationLoop(now => {
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    const state = store.state;
    const vs = view.get();
    const still = reduced();
    controls.enableDamping = !still;
    section = vs.section;

    // Hover pre-selection, a few times a second.
    if (hoverAt && ++hoverFrame % 3 === 0) { const h = hit(hoverAt.x, hoverAt.y); hovered = h ? h.object.userData.object : ''; }
    renderer.domElement.style.cursor = hovered ? 'pointer' : '';

    // The model box (without explode) drives the section plane and the explode directions.
    const box = new THREE.Box3();
    for (const p of parts.values()) if (p.mesh.geometry.boundingBox) box.union(p.mesh.geometry.boundingBox);
    const mid = box.isEmpty() ? new THREE.Vector3() : box.getCenter(new THREE.Vector3());
    if (section.on && !box.isEmpty()) {
      const k = section.axis === 'x' ? 0 : section.axis === 'y' ? 1 : 2;
      const lo = box.min.getComponent(k), hi = box.max.getComponent(k);
      const at = lo + (hi - lo) * section.at;
      const n = new THREE.Vector3().setComponent(k, section.flip ? 1 : -1);
      clip.set(n, section.flip ? -at : at);
    }
    const planes = section.on ? [clip] : [];
    const accent = new THREE.Color(dark() ? SELECT_DARK : SELECT);
    const edgeColor = new THREE.Color(dark() ? 0x0e1013 : 0x20242a);

    for (const [name, p] of parts) {
      const mat = p.mesh.material as THREE.MeshStandardMaterial;
      const edgeMat = p.edges.material as THREE.LineBasicMaterial;
      p.mesh.visible = !vs.hidden.has(name);
      p.fadeIn = still ? 1 : Math.min(1, p.fadeIn + dt * 2.5);        // cross-fade a new revision in
      const wire = vs.display === 'wireframe';
      mat.opacity = p.fadeIn * (wire ? 0.07 : 1);
      mat.depthWrite = !wire;
      if (mat.clippingPlanes?.length !== planes.length) {
        mat.clippingPlanes = planes; edgeMat.clippingPlanes = planes;
        mat.side = section.on ? THREE.DoubleSide : THREE.FrontSide; mat.needsUpdate = true; edgeMat.needsUpdate = true;
      }
      const selected = vs.selection.includes(name);
      p.edges.visible = vs.display !== 'shaded' || selected;
      edgeMat.color.copy(selected ? accent : edgeColor);
      edgeMat.opacity = selected ? 1 : wire ? 0.85 : 0.5;
      const editing = state.pointers.find(x => x.object === name && x.mode === 'editing');
      const pulse = editing && !still ? 0.18 + 0.18 * Math.sin(now / 180) : 0;
      p.flash = still ? 0 : Math.max(0, p.flash - dt * 1.5);          // one flash when an edit lands
      const glow = editing ? new THREE.Color(memberColor(editing.member, dark())) : new THREE.Color(0xffffff);
      mat.emissive.copy(glow).multiplyScalar(Math.max(pulse, p.flash * 0.5));
      if (selected) mat.emissive.copy(accent).multiplyScalar(0.42);
      else if (hovered === name) mat.emissive.set(HOVER).multiplyScalar(0.32);
      p.outline.visible = !!editing && still;                         // reduced motion: static outline instead of a pulse
      if (p.outline.visible) (p.outline.material as THREE.LineBasicMaterial).color.copy(glow);
      p.shake = still ? 0 : Math.max(0, p.shake - dt * 2.5);          // rejected edit
      const off = p.center.clone().sub(mid).multiplyScalar(vs.explode * 1.1);
      p.mesh.position.set(off.x + (p.shake ? Math.sin(now / 25) * 1.2 * p.shake : 0), off.y, off.z);
    }

    // Follow a teammate: fly to whatever their pointer works on.
    if (vs.follow) {
      const ptr = store.activePointers().find(x => x.member === vs.follow);
      const obj = ptr?.object ?? '';
      if (vs.follow !== followed.member || obj !== followed.object) {
        followed = { member: vs.follow, object: obj };
        if (obj && parts.has(obj)) focus(obj);
      }
    } else followed = { member: 0, object: '' };

    pointers.update(dt, name => parts.get(name)?.mesh ?? null);
    if (goal) { // ease toward the requested view; any drag by the person cancels it
      const k = Math.min(1, dt * 5);
      controls.target.lerp(goal.target, k);
      camera.position.lerp(goal.position, k);
      if (camera.position.distanceTo(goal.position) < 0.5) { controls.target.copy(goal.target); camera.position.copy(goal.position); goal = null; }
    }
    controls.update();
    const fog = scene.fog as THREE.Fog, d = camera.position.distanceTo(controls.target);
    fog.near = d * 1.6; fog.far = d * 6;  // the floor fades out softly wherever you zoom
    renderer.render(scene, camera);
    labels.render(scene, camera);
    drawTriad();
  });
}
