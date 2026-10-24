// Library thumbnails: one shared offscreen three.js renderer turns a part's preview STL into a small PNG,
// cached by part id and revision, so a long library never holds dozens of WebGL contexts.
import * as THREE from 'three';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';

const SIZE = 160;
const cache = new Map<string, string>();
let renderer: THREE.WebGLRenderer | null = null;

function getRenderer(): THREE.WebGLRenderer | null {
  if (renderer) return renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setSize(SIZE, SIZE);
    return renderer;
  } catch { return null; } // no WebGL: cards show the size text instead
}

/** A PNG data URL of the mesh from an isometric angle, or null without WebGL. */
export function thumbnail(key: string, stl: Uint8Array, dark: boolean): string | null {
  const hit = cache.get(key);
  if (hit) return hit;
  const r = getRenderer();
  if (!r) return null;
  const geo = new STLLoader().parse(stl.slice().buffer);
  geo.computeVertexNormals();
  geo.computeBoundingSphere();
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xffffff, dark ? 0x202830 : 0xb0b8c0, 2.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(1, 2, 3);
  scene.add(sun);
  const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x8aa4c8, roughness: 0.5, metalness: 0.1 }));
  scene.add(mesh);
  const s = geo.boundingSphere!;
  const cam = new THREE.PerspectiveCamera(35, 1, 0.1, s.radius * 20 + 10);
  const dir = new THREE.Vector3(1, -1.2, 0.9).normalize();
  cam.up.set(0, 0, 1);
  cam.position.copy(s.center).addScaledVector(dir, s.radius * 3.2);
  cam.lookAt(s.center);
  r.render(scene, cam);
  const url = r.domElement.toDataURL('image/png');
  geo.dispose();
  (mesh.material as THREE.Material).dispose();
  cache.set(key, url);
  return url;
}
