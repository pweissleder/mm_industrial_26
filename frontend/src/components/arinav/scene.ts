// Imperative three.js scene for a building. Geometry is generated from building.json
// (or an uploaded GLB is shown instead); React drives it through the public methods.
//
// Axes: dataset (x, y, elevation) -> three (x, elevation, -y), as in blender/.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { CSS2DObject, CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import {
  assetColor,
  assetHeight,
  centroid,
  polygonArea,
  spaceColor,
  type BuildingModel,
  type Floor,
  type Point,
  type Selection,
  type Space,
} from '../../lib/building';
import type { CameraAction, ObjectInfo, ViewSettings } from './view';

const SLAB = 0.15;
const WALL_T = 0.1;
const WALL_INSET = 0.06;
const EXPLODE_GAP = 12;
const GHOST_OPACITY = 0.12;
const ACCENT = new THREE.Color('#fb7928');
const OPEN_TYPES = new Set(['hall', 'corridor']);
const OPENING_COLORS: Record<string, string> = {
  door: '#55648a',
  entrance: '#12a594',
  opening: '#b9c4de',
};

interface FloorView {
  floor: Floor;
  group: THREE.Group;
  spaces: THREE.Group;
  walls: THREE.Mesh;
  assets: THREE.Group;
  openings: THREE.Group;
  labels: THREE.Group;
  materials: THREE.Material[];
}

interface Fly {
  fromPos: THREE.Vector3;
  toPos: THREE.Vector3;
  fromTarget: THREE.Vector3;
  toTarget: THREE.Vector3;
  start: number;
  duration: number;
}

const toVec = ([x, y]: Point, z = 0) => new THREE.Vector3(x, z, -y);
const selKey = (s: Selection) => `${s.kind}:${s.id}`;
const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

function pointInPolygon([x, y]: Point, polygon: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function isGhost(obj: THREE.Object3D): boolean {
  for (let o: THREE.Object3D | null = obj; o; o = o.parent) if (o.userData.ghost) return true;
  return false;
}

function makeLabel(text: string, className: string): CSS2DObject {
  const el = document.createElement('div');
  el.className = className;
  el.textContent = text;
  return new CSS2DObject(el);
}

export class BuildingScene {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly labelRenderer: CSS2DRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(40, 1, 0.1, 4000);
  private readonly controls: OrbitControls;
  private readonly sun = new THREE.DirectionalLight('#ffffff', 1.5);
  private readonly root = new THREE.Group();
  private readonly connectors = new THREE.Group();
  private readonly highlight = new THREE.Group();
  private readonly pin: CSS2DObject;
  private readonly hoverTip: CSS2DObject;
  private readonly raycaster = new THREE.Raycaster();
  private readonly resizeObserver: ResizeObserver;
  private readonly unitBox = new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0);
  private readonly selectMat = new THREE.MeshStandardMaterial({ color: ACCENT, roughness: 0.6 });
  private readonly hoverMat = new THREE.MeshStandardMaterial({ color: '#ffb27f', roughness: 0.7 });

  private grid: THREE.GridHelper | null = null;
  private floors: FloorView[] = [];
  private objects = new Map<string, THREE.Object3D>();
  private model: BuildingModel | null = null;
  private glb: THREE.Object3D | null = null;
  private settings: ViewSettings;
  private selection: Selection | null = null;
  private hovered: THREE.Object3D | null = null;
  private fly: Fly | null = null;
  private needsRender = true;
  private insetLeft = 0;
  private freeAspect = 1;
  private downAt: [number, number] | null = null;
  private disposed = false;

  constructor(
    private readonly container: HTMLElement,
    settings: ViewSettings,
    private readonly onSelect: (sel: Selection | null, info?: ObjectInfo) => void,
  ) {
    this.settings = settings;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(this.renderer.domElement);

    this.labelRenderer = new CSS2DRenderer();
    this.labelRenderer.domElement.className = 'viewer__labels';
    container.appendChild(this.labelRenderer.domElement);

    this.scene.add(new THREE.HemisphereLight('#ffffff', '#d6dbea', 1.7));
    this.sun.position.set(40, 90, 30);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.0005;
    this.scene.add(this.sun, this.sun.target, this.root, this.connectors, this.highlight);

    this.pin = makeLabel('', 'viewer__pin');
    this.pin.center.set(0.5, 1);
    this.pin.visible = false;
    this.hoverTip = makeLabel('', 'viewer__tip');
    this.hoverTip.center.set(0.5, 1.4);
    this.hoverTip.visible = false;
    this.scene.add(this.pin, this.hoverTip);

    this.camera.position.set(60, 60, 60);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.controls.screenSpacePanning = true;
    this.controls.addEventListener('start', () => (this.fly = null));

    const el = this.renderer.domElement;
    el.addEventListener('pointerdown', this.handlePointerDown);
    el.addEventListener('pointerup', this.handlePointerUp);
    el.addEventListener('pointermove', this.handlePointerMove);
    el.addEventListener('pointerleave', () => this.setHovered(null));

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();
    this.renderer.setAnimationLoop(this.tick);
  }

  dispose() {
    this.disposed = true;
    this.renderer.setAnimationLoop(null);
    this.resizeObserver.disconnect();
    this.controls.dispose();
    this.clear();
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.labelRenderer.domElement.remove();
  }

  // ---------------------------------------------------------------- public API

  setModel(model: BuildingModel | null) {
    this.clear();
    this.model = model;
    if (model) this.build(model);
    this.applySettings();
    this.frame('iso', false);
  }

  async setGlb(url: string): Promise<void> {
    const gltf = await new GLTFLoader().loadAsync(url);
    if (this.disposed) return;
    this.clear();
    this.model = null;
    this.glb = gltf.scene;
    this.glb.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        o.castShadow = o.receiveShadow = true;
        o.userData.baseMaterial = o.material;
      }
    });
    this.root.add(this.glb);
    this.applySettings();
    this.frame('iso', false);
  }

  setSettings(settings: ViewSettings) {
    const rebuildConnectors = settings.explode !== this.settings.explode;
    this.settings = settings;
    if (rebuildConnectors && this.model) this.buildConnectors(this.model);
    this.applySettings();
  }

  setSelection(selection: Selection | null, fly = true) {
    if (this.selection) this.restoreMaterial(this.objects.get(selKey(this.selection)));
    this.selection = selection;
    this.highlight.clear();
    this.pin.visible = false;
    const obj = selection && this.objects.get(selKey(selection));
    if (obj) {
      this.paint(obj, this.selectMat);
      const box = new THREE.Box3().setFromObject(obj);
      const top = box.getCenter(new THREE.Vector3());
      top.y = box.max.y + 0.3;
      this.pin.position.copy(top);
      this.pin.element.textContent = obj.userData.title ?? '';
      this.pin.visible = true;
      if (selection.kind === 'space') this.addOutline(obj);
      if (fly) this.flyTo(box);
    }
    this.needsRender = true;
  }

  camera3d(action: CameraAction) {
    if (action === 'zoomIn' || action === 'zoomOut') {
      const k = action === 'zoomIn' ? 0.7 : 1.4;
      const offset = this.camera.position.clone().sub(this.controls.target).multiplyScalar(k);
      this.startFly(this.controls.target.clone().add(offset), this.controls.target.clone());
      return;
    }
    this.frame(action === 'fit' ? 'keep' : action, true);
  }

  // ---------------------------------------------------------------- building

  private clear() {
    this.setHovered(null);
    this.selection = null;
    this.highlight.clear();
    this.pin.visible = false;
    for (const f of this.floors) for (const m of f.materials) m.dispose();
    this.root.traverse((o) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
        if (o.geometry !== this.unitBox) o.geometry.dispose();
      }
      if (o instanceof CSS2DObject) o.element.remove();
    });
    this.root.clear();
    this.connectors.clear();
    this.floors = [];
    this.objects.clear();
    this.glb = null;
  }

  private register(obj: THREE.Object3D, sel: Selection, title: string) {
    obj.userData.sel = sel;
    obj.userData.title = title;
    this.objects.set(selKey(sel), obj);
  }

  private build(model: BuildingModel) {
    const floors = [...model.floors].sort((a, b) => a.level - b.level);
    const spacesByFloor = new Map<string, Space[]>();
    for (const s of model.spaces) {
      spacesByFloor.set(s.floor_id, [...(spacesByFloor.get(s.floor_id) ?? []), s]);
    }

    for (const floor of floors) {
      const spaces = spacesByFloor.get(floor.id) ?? [];
      const view = this.buildFloor(model, floor, spaces);
      this.floors.push(view);
      this.root.add(view.group);
    }
    this.buildConnectors(model);
  }

  private buildFloor(model: BuildingModel, floor: Floor, spaces: Space[]): FloorView {
    const group = new THREE.Group();
    group.name = floor.name;
    const materials: THREE.Material[] = [];
    const mat = <M extends THREE.Material>(m: M) => (materials.push(m), m);
    const edgeMat = mat(new THREE.LineBasicMaterial({ color: '#b8bfd3', transparent: true }));

    // Room slabs: the polygon extruded downwards so the top sits at the floor elevation.
    const spaceGroup = new THREE.Group();
    const labels = new THREE.Group();
    for (const s of spaces) {
      if (s.polygon.length < 3) continue;
      const shape = new THREE.Shape(s.polygon.map(([x, y]) => new THREE.Vector2(x, y)));
      const geo = new THREE.ExtrudeGeometry(shape, { depth: SLAB, bevelEnabled: false });
      geo.translate(0, 0, -SLAB).rotateX(-Math.PI / 2);
      const material = mat(new THREE.MeshStandardMaterial({ color: spaceColor(s), roughness: 0.95 }));
      const mesh = new THREE.Mesh(geo, material);
      mesh.position.y = floor.elevation_m;
      mesh.receiveShadow = true;
      mesh.userData.baseMaterial = material;
      mesh.userData.roomColor = spaceColor(s);
      this.register(mesh, { kind: 'space', id: s.id }, s.name);
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 30), edgeMat);
      edges.raycast = () => {};
      mesh.add(edges);
      spaceGroup.add(mesh);

      const label = makeLabel(s.ref || s.name, 'viewer__label');
      label.position.copy(toVec(centroid(s.polygon), floor.elevation_m + 0.3));
      labels.add(label);
    }

    // Walls: one unit-height mesh per floor, scaled to the chosen wall height.
    const wallMat = mat(
      new THREE.MeshStandardMaterial({ color: '#f7f8fb', roughness: 0.8, transparent: true }),
    );
    const walls = new THREE.Mesh(this.wallGeometry(model, floor, spaces), wallMat);
    walls.position.y = floor.elevation_m;
    walls.castShadow = walls.receiveShadow = true;
    walls.raycast = () => {};

    // Assets: a shared unit box scaled to each footprint and assumed height.
    const assetGroup = new THREE.Group();
    const assetMats = new Map<string, THREE.MeshStandardMaterial>();
    for (const a of model.assets) {
      if (a.floor_id !== floor.id) continue;
      const color = assetColor(a);
      let material = assetMats.get(color);
      if (!material) {
        material = mat(new THREE.MeshStandardMaterial({ color, roughness: 0.55 }));
        assetMats.set(color, material);
      }
      const mesh = new THREE.Mesh(this.unitBox, material);
      mesh.scale.set(a.footprint.width_m ?? 0.6, assetHeight(a), a.footprint.depth_m ?? 0.6);
      mesh.rotation.y = THREE.MathUtils.degToRad(a.footprint.rotation_deg ?? 0);
      mesh.position.copy(toVec(a.position, floor.elevation_m));
      mesh.castShadow = mesh.receiveShadow = true;
      mesh.userData.baseMaterial = material;
      this.register(mesh, { kind: 'asset', id: a.id }, a.name);
      assetGroup.add(mesh);
    }

    // Openings: a flat strip across the gap, aligned with the nearest room edge.
    const openings = new THREE.Group();
    const spaceById = new Map(spaces.map((s) => [s.id, s]));
    for (const o of model.openings) {
      if (o.floor_id !== floor.id) continue;
      const material = mat(
        new THREE.MeshStandardMaterial({ color: OPENING_COLORS[o.kind] ?? '#8792b0' }),
      );
      const mesh = new THREE.Mesh(this.unitBox, material);
      const polygon = o.space_ids.map((id) => spaceById.get(id)?.polygon).find(Boolean);
      mesh.rotation.y = polygon ? this.nearestEdgeAngle(o.position, polygon) : 0;
      mesh.scale.set(o.width_m, 0.04, 0.35);
      mesh.position.copy(toVec(o.position, floor.elevation_m + 0.005));
      mesh.userData.baseMaterial = material;
      this.register(mesh, { kind: 'opening', id: o.id }, `${o.kind} ${o.id.split('-').pop()}`);
      openings.add(mesh);
    }

    group.add(spaceGroup, walls, assetGroup, openings, labels);
    return { floor, group, spaces: spaceGroup, walls, assets: assetGroup, openings, labels, materials };
  }

  private nearestEdgeAngle([px, py]: Point, polygon: Point[]): number {
    let best = Infinity;
    let angle = 0;
    for (let i = 0; i < polygon.length; i++) {
      const [ax, ay] = polygon[i];
      const [bx, by] = polygon[(i + 1) % polygon.length];
      const dx = bx - ax;
      const dy = by - ay;
      const len2 = dx * dx + dy * dy || 1;
      const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
      const d = Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
      if (d < best) {
        best = d;
        angle = Math.atan2(dy, dx);
      }
    }
    return angle;
  }

  /** Walls along every room edge, inset into the room, with gaps at openings. */
  private wallGeometry(model: BuildingModel, floor: Floor, spaces: Space[]): THREE.BufferGeometry {
    const openings = model.openings.filter((o) => o.floor_id === floor.id);
    const pieces: THREE.BufferGeometry[] = [];
    const matrix = new THREE.Matrix4();
    const quat = new THREE.Quaternion();
    const up = new THREE.Vector3(0, 1, 0);

    for (const s of spaces) {
      const poly = s.polygon;
      if (poly.length < 3) continue;
      const inward = Math.sign(polygonArea(poly)) || 1; // CCW: interior on the left
      for (let i = 0; i < poly.length; i++) {
        const [ax, ay] = poly[i];
        const [bx, by] = poly[(i + 1) % poly.length];
        const len = Math.hypot(bx - ax, by - ay);
        if (len < 0.05) continue;
        const ux = (bx - ax) / len;
        const uy = (by - ay) / len;
        const nx = -uy * inward;
        const ny = ux * inward;

        // Open halls: no wall between two hall/corridor zones.
        if (OPEN_TYPES.has(s.type)) {
          const probe: Point = [(ax + bx) / 2 - nx * 0.3, (ay + by) / 2 - ny * 0.3];
          const neighbour = spaces.find(
            (o) => o !== s && o.floor_id === s.floor_id && pointInPolygon(probe, o.polygon),
          );
          if (neighbour && OPEN_TYPES.has(neighbour.type)) continue;
        }

        // Cut gaps where an opening of this room sits on the edge.
        const gaps: [number, number][] = [];
        for (const o of openings) {
          if (!o.space_ids.includes(s.id)) continue;
          const t = (o.position[0] - ax) * ux + (o.position[1] - ay) * uy;
          const d = Math.abs((o.position[0] - ax) * -uy + (o.position[1] - ay) * ux);
          if (d < 0.4 && t > -o.width_m / 2 && t < len + o.width_m / 2) {
            gaps.push([t - o.width_m / 2, t + o.width_m / 2]);
          }
        }
        gaps.sort((g1, g2) => g1[0] - g2[0]);
        let cursor = 0;
        const segments: [number, number][] = [];
        for (const [g0, g1] of gaps) {
          if (g0 > cursor) segments.push([cursor, Math.min(g0, len)]);
          cursor = Math.max(cursor, g1);
        }
        if (cursor < len) segments.push([cursor, len]);

        const angle = Math.atan2(uy, ux);
        for (const [t0, t1] of segments) {
          if (t1 - t0 < 0.03) continue;
          const mid = (t0 + t1) / 2;
          const cx = ax + ux * mid + nx * WALL_INSET;
          const cy = ay + uy * mid + ny * WALL_INSET;
          quat.setFromAxisAngle(up, angle);
          matrix.compose(new THREE.Vector3(cx, 0, -cy), quat, new THREE.Vector3(t1 - t0, 1, WALL_T));
          pieces.push(this.unitBox.clone().applyMatrix4(matrix));
        }
      }
    }
    if (!pieces.length) return new THREE.BufferGeometry();
    const merged = mergeGeometries(pieces) ?? new THREE.BufferGeometry();
    for (const p of pieces) p.dispose();
    return merged;
  }

  /** Stairs and lifts: a tube through the stop centroids, following the exploded floors. */
  private buildConnectors(model: BuildingModel) {
    for (const o of this.connectors.children) {
      if (o instanceof THREE.Mesh) o.geometry.dispose();
    }
    this.connectors.clear();
    const spaceById = new Map(model.spaces.map((s) => [s.id, s]));
    const material = new THREE.MeshStandardMaterial({ color: ACCENT, roughness: 0.5 });
    for (const vc of model.vertical_connectors) {
      const stops = vc.space_ids
        .map((id) => spaceById.get(id))
        .filter((s): s is Space => !!s)
        .map((s) => {
          const fv = this.floors.find((f) => f.floor.id === s.floor_id);
          const y = (fv?.floor.elevation_m ?? 0) + this.floorOffset(fv) + 1.2;
          return toVec(centroid(s.polygon), y);
        })
        .sort((a, b) => a.y - b.y);
      if (stops.length < 2) continue;
      const curve = new THREE.CatmullRomCurve3(stops, false, 'catmullrom', 0);
      const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 16 * stops.length, 0.18, 8), material);
      tube.userData.baseMaterial = material;
      this.register(tube, { kind: 'connector', id: vc.id }, vc.name);
      this.connectors.add(tube);
    }
  }

  private floorOffset(fv: FloorView | undefined): number {
    return fv ? this.floors.indexOf(fv) * this.settings.explode * EXPLODE_GAP : 0;
  }

  // ---------------------------------------------------------------- settings

  private applySettings() {
    const s = this.settings;
    const focus = s.floor;
    for (const fv of this.floors) {
      const level = fv.floor.level;
      const hidden = focus !== 'all' && level > focus;
      const ghost = focus !== 'all' && level < focus;
      fv.group.visible = !hidden;
      fv.group.position.y = this.floorOffset(fv);
      fv.group.userData.ghost = ghost;
      for (const m of fv.materials) {
        m.transparent = true;
        m.opacity = ghost ? GHOST_OPACITY : 1;
        m.depthWrite = !ghost;
      }
      fv.spaces.visible = s.layers.spaces;
      fv.walls.visible = s.layers.walls;
      fv.assets.visible = s.layers.assets && !ghost;
      fv.openings.visible = s.layers.openings && !ghost;
      // Labels of stacked floors would show through each other, so only label one floor.
      const labelled = focus === 'all' ? this.floors.length === 1 : level === focus;
      fv.labels.visible = s.layers.labels && labelled;
      fv.walls.scale.y = Math.max(0.01, Math.min(s.wallHeight, fv.floor.height_m - SLAB));
      for (const mesh of fv.spaces.children) {
        const material = mesh.userData.baseMaterial as THREE.MeshStandardMaterial;
        material.color.set(s.colorBy === 'room' ? mesh.userData.roomColor : '#eef0f4');
      }
    }
    this.connectors.visible = s.layers.connectors;
    if (this.glb) {
      this.glb.traverse((o) => {
        const lvl = o.userData.floor_level;
        if (typeof lvl === 'number') o.visible = focus === 'all' || lvl <= focus;
      });
    }
    this.renderer.shadowMap.enabled = s.shadows;
    this.root.traverse((o) => {
      if (o instanceof THREE.Mesh && o.material instanceof THREE.Material) o.material.needsUpdate = true;
    });
    this.updateGridAndShadows();
    if (this.selection) this.setSelection(this.selection, false);
    this.needsRender = true;
  }

  private updateGridAndShadows() {
    const box = this.visibleBounds();
    if (this.grid) {
      this.scene.remove(this.grid);
      this.grid.geometry.dispose();
      (this.grid.material as THREE.Material).dispose();
      this.grid = null;
    }
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    if (this.settings.grid) {
      const extent = Math.ceil((Math.max(size.x, size.z) * 1.6) / 10) * 10;
      this.grid = new THREE.GridHelper(extent, extent / 2, '#cfd5e4', '#e4e7f0');
      this.grid.position.set(center.x, box.min.y - 0.02, center.z);
      this.scene.add(this.grid);
    }
    const r = Math.max(size.x, size.y, size.z) * 0.75;
    const cam = this.sun.shadow.camera;
    cam.left = cam.bottom = -r;
    cam.right = cam.top = r;
    cam.near = 1;
    cam.far = r * 6;
    cam.updateProjectionMatrix();
    this.sun.target.position.copy(center);
    this.sun.position.copy(center).add(new THREE.Vector3(r * 0.8, r * 2, r * 0.6));
  }

  private visibleBounds(): THREE.Box3 {
    const box = new THREE.Box3();
    this.root.traverseVisible((o) => {
      if (o instanceof THREE.Mesh && !isGhost(o)) box.expandByObject(o, false);
    });
    return box;
  }

  // ---------------------------------------------------------------- camera

  private frame(view: 'iso' | 'top' | 'front' | 'keep', animate: boolean) {
    const box = this.visibleBounds();
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const radius = Math.max(size.x, size.z, size.y) * 0.62;
    const fov = THREE.MathUtils.degToRad(this.camera.fov);
    const dist = (radius / Math.sin(fov / 2)) * Math.max(1, 1 / this.freeAspect);
    const dir =
      view === 'top'
        ? new THREE.Vector3(0, 1, 0.0001)
        : view === 'front'
          ? new THREE.Vector3(0, 0.35, 1)
          : view === 'iso'
            ? new THREE.Vector3(0.75, 0.85, 1)
            : this.camera.position.clone().sub(this.controls.target);
    const pos = center.clone().add(dir.normalize().multiplyScalar(dist));
    if (animate) this.startFly(pos, center);
    else {
      this.camera.position.copy(pos);
      this.controls.target.copy(center);
      this.controls.update();
    }
    this.needsRender = true;
  }

  private flyTo(box: THREE.Box3) {
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length();
    // Stay far enough out to show the surrounding room, not just the object.
    const dist = THREE.MathUtils.clamp(size * 2.5, 28, 140);
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    if (dir.y < 0.45) dir.setY(0.45).normalize();
    this.startFly(center.clone().add(dir.multiplyScalar(dist)), center);
  }

  private startFly(toPos: THREE.Vector3, toTarget: THREE.Vector3) {
    this.fly = {
      fromPos: this.camera.position.clone(),
      toPos,
      fromTarget: this.controls.target.clone(),
      toTarget,
      start: performance.now(),
      duration: 650,
    };
  }

  /** Keep the view centred in the area right of a docked panel `px` wide. */
  setInsetLeft(px: number) {
    this.insetLeft = px;
    this.resize();
  }

  private resize() {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    const inset = Math.min(this.insetLeft, w / 2);
    // Render the right-hand part of a frustum widened by the inset: its centre lands mid-free-area.
    this.camera.aspect = (w + inset) / h;
    if (inset) this.camera.setViewOffset(w + inset, h, 0, 0, w, h);
    else this.camera.clearViewOffset();
    this.freeAspect = (w - inset) / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
    this.labelRenderer.setSize(w, h);
    this.needsRender = true;
  }

  private tick = (now: number) => {
    if (this.fly) {
      const t = Math.min(1, (now - this.fly.start) / this.fly.duration);
      const k = easeInOut(t);
      this.camera.position.lerpVectors(this.fly.fromPos, this.fly.toPos, k);
      this.controls.target.lerpVectors(this.fly.fromTarget, this.fly.toTarget, k);
      if (t >= 1) this.fly = null;
      this.needsRender = true;
    }
    if (this.controls.update() || this.needsRender) {
      this.needsRender = false;
      this.renderer.render(this.scene, this.camera);
      this.labelRenderer.render(this.scene, this.camera);
    }
  };

  // ---------------------------------------------------------------- picking

  private pick(e: PointerEvent): THREE.Object3D | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(ndc, this.camera);
    const hits = this.raycaster.intersectObjects([this.root, this.connectors], true);
    for (const hit of hits) {
      let visible = true;
      for (let o: THREE.Object3D | null = hit.object; o; o = o.parent) visible &&= o.visible;
      if (!visible || isGhost(hit.object)) continue;
      if (this.glb) return hit.object;
      if (hit.object.userData.sel) return hit.object;
    }
    return null;
  }

  private handlePointerDown = (e: PointerEvent) => {
    this.downAt = [e.clientX, e.clientY];
  };

  private handlePointerUp = (e: PointerEvent) => {
    if (!this.downAt || Math.hypot(e.clientX - this.downAt[0], e.clientY - this.downAt[1]) > 6) return;
    this.downAt = null;
    const obj = this.pick(e);
    if (obj && this.glb) {
      this.registerGlbObject(obj);
      this.onSelect(obj.userData.sel, this.glbInfo(obj));
      return;
    }
    this.onSelect(obj ? (obj.userData.sel as Selection) : null);
  };

  private handlePointerMove = (e: PointerEvent) => {
    if (e.pointerType !== 'mouse' || e.buttons) return;
    this.setHovered(this.pick(e));
  };

  private setHovered(obj: THREE.Object3D | null) {
    if (obj === this.hovered) return;
    const isSelected = (o: THREE.Object3D) =>
      this.selection && this.objects.get(selKey(this.selection)) === o;
    if (this.hovered && !isSelected(this.hovered)) this.restoreMaterial(this.hovered);
    this.hovered = obj;
    this.renderer.domElement.style.cursor = obj ? 'pointer' : '';
    if (obj && !isSelected(obj) && !this.glb) this.paint(obj, this.hoverMat);
    const title = obj && (obj.userData.title ?? obj.name);
    this.hoverTip.visible = !!title;
    if (obj && title) {
      const box = new THREE.Box3().setFromObject(obj);
      this.hoverTip.position.set((box.min.x + box.max.x) / 2, box.max.y, (box.min.z + box.max.z) / 2);
      this.hoverTip.element.textContent = title;
    }
    this.needsRender = true;
  }

  private paint(obj: THREE.Object3D, material: THREE.Material) {
    if (obj instanceof THREE.Mesh) obj.material = material;
  }

  private restoreMaterial(obj: THREE.Object3D | undefined) {
    if (obj instanceof THREE.Mesh && obj.userData.baseMaterial) obj.material = obj.userData.baseMaterial;
  }

  private addOutline(obj: THREE.Object3D) {
    if (!(obj instanceof THREE.Mesh)) return;
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(obj.geometry, 30),
      new THREE.LineBasicMaterial({ color: ACCENT, depthTest: false }),
    );
    edges.renderOrder = 5;
    obj.updateWorldMatrix(true, false);
    edges.applyMatrix4(obj.matrixWorld);
    edges.position.y += 0.02;
    this.highlight.add(edges);
  }

  private registerGlbObject(obj: THREE.Object3D) {
    if (!obj.userData.sel) this.register(obj, { kind: 'object', id: obj.uuid }, this.glbInfo(obj).title);
  }

  private glbInfo(obj: THREE.Object3D): ObjectInfo {
    // Blender puts custom properties (glTF extras) on the object, not the mesh primitive.
    let source: THREE.Object3D = obj;
    for (let o: THREE.Object3D | null = obj; o && o !== this.glb; o = o.parent) {
      if (Object.keys(o.userData).some((k) => !['baseMaterial', 'sel', 'title'].includes(k))) {
        source = o;
        break;
      }
    }
    const props = Object.entries(source.userData)
      .filter(([k]) => !['baseMaterial', 'sel', 'title', 'name'].includes(k))
      .map(([k, v]): [string, string] => [
        k,
        typeof v === 'number' ? String(+v.toFixed(3)) : Array.isArray(v) ? v.join(', ') : String(v),
      ]);
    return { title: String(source.userData.name ?? source.name ?? 'Object'), props };
  }
}
