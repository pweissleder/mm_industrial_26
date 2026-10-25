// Imperative three.js scene for a building. Geometry is generated from building.json
// (the low-quality preview), loaded from the pipeline's model.glb (high quality, still
// selectable through its node extras), or an uploaded GLB is shown as is. React drives
// it through the public methods.
//
// Axes: dataset (x, y, elevation) -> three (x, elevation, -y), as in blender/.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
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
  type SelectionKind,
  type Space,
} from '../../lib/building';
import type { RoomGraphData, RouteResponse } from '../../lib/api';
import { constrainItemMove, startItemDrag, type ItemDrag } from '../../lib/edit';
import { routeDrawing } from './routeGeometry';
import { EYE_HEIGHT_M, angleDiff, wrap360 } from '../../lib/spatial';
import type { CameraAction, NavMode, ObjectInfo, ViewSettings } from './view';
import { MAX_VISIBLE_ASSETS, buildAssetIndex, visibleAssetIds, type AssetIndex } from './assetCulling';

// Vertical view angle of the first-person camera (wider on portrait phones, see renderPov).
const POV_FOV = 72;
const SLAB = 0.15;
/** Minimap: hidden at phone widths (CSS px), else a rounded card with a thin gray border. */
const MINIMAP_MIN_WIDTH = 640;
const MINIMAP_RADIUS = 14;
const MINIMAP_BORDER = 0xdddddd;
const WALL_T = 0.1;
const WALL_INSET = 0.06;
const EXPLODE_GAP = 12;
const GHOST_OPACITY = 0.12;
const ACCENT = new THREE.Color('#fb7928');
/** Selection colour: the brand orange (--orange). */
const SELECT = new THREE.Color('#fb7928');
/** Rooms, doors, stairs and lifts the agent has closed. */
const CLOSED_COLOR = '#e5484d';
const OPEN_TYPES = new Set(['hall', 'corridor']);
/** Height of the route line above the floor slab. */
const ROUTE_LIFT = 0.35;
// Route fly-through: a chase drone just above walls (2.4 m in overview) and most furniture (the
// official route ignores furniture), looking ahead and down onto the path.
const DRONE_HEIGHT_M = 3.4;
const DRONE_LOOK_DOWN_M = 2.4;
const DRONE_MPS = 9;
const ROUTE_COLORS = { official: '#7c3aed', precision: '#004fab', vertical: '#5d6070' };

/** Where a click hit the model, in building coordinates. */
export interface PickHit {
  position: Point;
}

/** Where a dragged asset ended up: its room and the rotation along that room's walls. */
export interface MovedItem {
  spaceId: string;
  rotation?: number;
}

/** Element kinds that can be dragged in edit mode; stairs and lifts stay put. */
const DRAGGABLE = new Set(['asset', 'opening']);

/** An asset or door being dragged in edit mode, across the horizontal plane of its base. */
interface Drag {
  obj: THREE.Object3D;
  plane: THREE.Plane;
  /** Object position minus the grabbed point, so it doesn't jump to the cursor. */
  offset: THREE.Vector3;
  start: [number, number];
  moved: boolean;
  /** Assets only: the room the item is kept in while dragging (walls stop it). */
  item: ItemDrag | null;
  /** Room at the start, to show the new one when the item changes room. */
  startSpace: string | null;
  /** Assets only: the rotation lining it up with the walls of the room it is in. */
  rotation?: number;
}

const OPENING_COLORS: Record<string, string> = {
  door: '#55648a',
  entrance: '#12a594',
  opening: '#b9c4de',
};

interface FloorView {
  floor: Floor;
  group: THREE.Group;
  spaces: THREE.Group;
  /** A unit-height mesh scaled to the wall height, or (HQ) the model's walls at full height. */
  walls: THREE.Object3D;
  assets: THREE.Group;
  openings: THREE.Group;
  /** HQ only: stair/lift markers and hall lines; shown with the stairs & lifts layer. */
  marks: THREE.Group;
  labels: THREE.Group;
  materials: THREE.Material[];
  hq: boolean;
}

/** Layer of each node kind the pipeline writes into model.glb extras. */
const HQ_LAYERS: Record<string, 'spaces' | 'walls' | 'assets' | 'openings'> = {
  space: 'spaces',
  walls: 'walls',
  asset: 'assets',
  opening: 'openings',
};

/** The simulated person (Phase B guidance): floor, XY in the building frame, heading (deg). */
export interface WalkerPose {
  floorId: string;
  position: Point;
  headingDeg: number;
  /** Distance along the route (m): the part behind is drawn as done. */
  distanceM?: number;
}

/** Route fly-through ("drone video"): circle the destination, pull out to the start, fly the
 * path at drone height looking ahead, then settle on an overview of the whole route. Camera only:
 * the person never moves. Times in ms from `start`. */
interface Preview {
  curve: THREE.CatmullRomCurve3;
  length: number;
  destination: THREE.Vector3;
  overviewPos: THREE.Vector3;
  overviewTarget: THREE.Vector3;
  fromPos: THREE.Vector3;
  fromTarget: THREE.Vector3;
  orbitAngle: number;
  /** Floor level at each curve point and the point's share of the path (0..1), so floors above
   * the one being flown over can be hidden (their slab sits just above drone height). */
  levels: number[];
  shares: number[];
  phases: { orbit: number; pull: number; fly: number; settle: number };
  start: number;
  onDone?: () => void;
}

interface Fly {
  fromPos: THREE.Vector3;
  toPos: THREE.Vector3;
  fromTarget: THREE.Vector3;
  toTarget: THREE.Vector3;
  start: number;
  duration: number;
}

const GRAPH_LIFT = 0.6;
/** Walking speed (m/s) for turning a hop's routing length into a time. */
const WALK_SPEED = 1.4;
const GRAPH_COLORS: Record<string, string> = {
  door: '#002478',
  opening: '#5a6b99',
  entrance: '#12a594',
  stairs: '#fb7928',
  lift: '#fb7928',
  closed: '#e5484d',
  cut: '#e5484d',
};

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

/** A room's name pill, remembering the room's size so it only shows once the room is big enough on screen. */
function makeRoomLabel(space: Space, elevation: number): CSS2DObject {
  const text = space.ref || space.name;
  const label = makeLabel(text, 'viewer__label');
  label.position.copy(toVec(centroid(space.polygon), elevation + 0.3));
  const xs = space.polygon.map((p) => p[0]);
  const ys = space.polygon.map((p) => p[1]);
  label.userData.roomSize = Math.sqrt(
    (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys)),
  );
  // Roughly the pill's width in px (10.5px semibold text plus padding).
  label.userData.pillPx = text.length * 6.5 + 16;
  return label;
}

export class BuildingScene {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly labelRenderer: CSS2DRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(40, 1, 0.1, 4000);
  // POV (night task §5): an eye-height camera at the person; the avatar lives on layer 1, which
  // only the overview cameras see, so it never blocks the lens.
  private readonly povCamera = new THREE.PerspectiveCamera(POV_FOV, 1, 0.05, 600);
  private readonly miniCamera = new THREE.OrthographicCamera(-15, 15, 12, -12, 0.1, 400);
  // The minimap is rendered to a texture and drawn as a rounded card with a thin gray border.
  private readonly miniTarget = new THREE.WebGLRenderTarget(1, 1, { samples: 4 });
  private readonly miniFrame = new THREE.ShaderMaterial({
    uniforms: {
      map: { value: this.miniTarget.texture },
      size: { value: new THREE.Vector2(1, 1) },
      radius: { value: MINIMAP_RADIUS },
      border: { value: 1 },
      borderColor: { value: new THREE.Color(MINIMAP_BORDER) },
    },
    vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
    fragmentShader: `
      uniform sampler2D map;
      uniform vec2 size;
      uniform float radius;
      uniform float border;
      uniform vec3 borderColor;
      varying vec2 vUv;
      void main() {
        // Signed distance (CSS px) to the rounded rectangle: negative inside.
        vec2 q = abs((vUv - 0.5) * size) - size * 0.5 + radius;
        float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
        float alpha = 1.0 - smoothstep(-0.75, 0.25, d);
        if (alpha <= 0.0) discard;
        float edge = smoothstep(-border - 0.75, -border + 0.25, d);
        gl_FragColor = vec4(mix(texture2D(map, vUv).rgb, borderColor, edge), alpha);
        #include <colorspace_fragment>
      }`,
    transparent: true,
    depthTest: false,
    depthWrite: false,
  });
  private readonly miniFrameScene = new THREE.Scene().add(
    new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.miniFrame),
  );
  private look: { view: 'overview' | 'pov'; yaw: number | null; yawOffset: number; pitch: number; roll?: number } = {
    view: 'overview',
    yaw: null,
    yawOffset: 0,
    pitch: -8,
  };
  private povYaw: number | null = null;
  private split = false;
  /** Developer split view: the first-person view as a phone over the overview (CSS frame). */
  private readonly phoneFrame = Object.assign(document.createElement('div'), { className: 'phone-mock' });
  /** Where the first-person view was last drawn (CSS px in the canvas), for picking in it. */
  private povRect: { x: number; y: number; w: number; h: number } | null = null;
  private readonly controls: OrbitControls;
  private readonly sun = new THREE.DirectionalLight('#ffffff', 1.5);
  private readonly root = new THREE.Group();
  private readonly connectors = new THREE.Group();
  private readonly highlight = new THREE.Group();
  /** Nav-graph overlay, drawn over everything else. */
  private readonly graph = new THREE.Group();
  private graphData: RoomGraphData | null = null;
  /** Edge index of each drawn graph line segment, and room id of each graph dot, for picking. */
  private graphSegments: number[] = [];
  private graphDotIds: string[] = [];
  private readonly edgeTip: CSS2DObject;
  /** Assets of the selected room, painted along with it. */
  private litAssets: THREE.Object3D[] = [];
  /** Asset culling for huge buildings: null = off, else the ids currently shown. */
  private assetIndex: AssetIndex | null = null;
  private shownAssets: Set<string> | null = null;
  // A selected room's equipment, in the selection orange like a selected asset.
  private readonly roomAssetMat = new THREE.MeshStandardMaterial({
    color: SELECT,
    emissive: SELECT,
    emissiveIntensity: 0.2,
    roughness: 0.55,
    flatShading: true,
  });
  private readonly pin: CSS2DObject;
  private readonly hoverTip: CSS2DObject;
  private readonly raycaster = new THREE.Raycaster();
  private readonly resizeObserver: ResizeObserver;
  private readonly unitBox = new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0);
  // flatShading: the pipeline's glTF ships no normals, so smooth shading would light it black.
  private readonly selectMat = new THREE.MeshStandardMaterial({
    color: SELECT,
    emissive: SELECT,
    emissiveIntensity: 0.35,
    roughness: 0.5,
    flatShading: true,
  });
  private readonly hoverMat = new THREE.MeshStandardMaterial({
    color: '#fdba8c',
    roughness: 0.7,
    flatShading: true,
  });
  // Overlays painted over an element's own material: closed > diagnosis > highlight.
  private readonly closedMat = new THREE.MeshStandardMaterial({
    color: '#d93b3b',
    roughness: 0.8,
    flatShading: true,
    transparent: true,
    opacity: 0.55,
  });
  private readonly diagnosisMat = new THREE.MeshStandardMaterial({ color: '#e0115f', roughness: 0.6, flatShading: true });
  private readonly highlightMat = new THREE.MeshStandardMaterial({
    color: SELECT,
    emissive: SELECT,
    emissiveIntensity: 0.2,
    roughness: 0.55,
    flatShading: true,
  });
  // Route: shared materials and marker geometry, so a redraw allocates only line buffers.
  private readonly routeGroup = new THREE.Group();
  // Guidance: the walker figure, camera follow and a skippable route preview.
  private readonly walkerGroup = new THREE.Group();
  private readonly walkerHalo = new THREE.Group();
  private readonly walkerLegs: THREE.Group[] = [];
  private readonly walkerArms: THREE.Group[] = [];
  private walkerPhase = 0;
  private walkerPose: WalkerPose | null = null;
  private readonly doneGroup = new THREE.Group();
  private doneDrawnAt = -1;
  private readonly doneMat = new LineMaterial({
    color: '#b8bcc8',
    linewidth: 6,
    depthTest: false,
    transparent: true,
    opacity: 0.95,
  });
  private follow = false;
  private preview: Preview | null = null;
  private readonly routeMats = {
    official: new LineMaterial({
      color: ROUTE_COLORS.official,
      linewidth: 5,
      dashed: true,
      dashSize: 0.9,
      gapSize: 0.55,
      depthTest: false,
      transparent: true,
    }),
    precision: new LineMaterial({
      color: ROUTE_COLORS.precision,
      linewidth: 5,
      depthTest: false,
      transparent: true,
    }),
    vertical: new LineMaterial({
      color: ROUTE_COLORS.vertical,
      linewidth: 3,
      dashed: true,
      dashSize: 0.4,
      gapSize: 0.3,
      depthTest: false,
      transparent: true,
    }),
  };
  private readonly markerGeo = new THREE.SphereGeometry(0.45, 20, 14);
  private readonly doorGeo = new THREE.CylinderGeometry(0.32, 0.32, 0.12, 20);
  private readonly markerMats = {
    start: new THREE.MeshBasicMaterial({ color: '#12a594', depthTest: false }),
    target: new THREE.MeshBasicMaterial({ color: '#fb7928', depthTest: false }),
    door: new THREE.MeshBasicMaterial({ color: '#002478', depthTest: false }),
    exit: new THREE.MeshBasicMaterial({ color: '#12a594', depthTest: false }),
  };

  private grid: THREE.GridHelper | null = null;
  private floors: FloorView[] = [];
  private objects = new Map<string, THREE.Object3D>();
  /** The same objects by element id alone (ids are unique across kinds in building.json). */
  private byId = new Map<string, THREE.Object3D>();
  private overlaid = new Set<THREE.Object3D>();
  private closedIds: string[] = [];
  private diagnosisIds: string[] = [];
  private highlightIds: string[] = [];
  private route: RouteResponse | null = null;
  private model: BuildingModel | null = null;
  private glb: THREE.Object3D | null = null;
  /** Fly-through: floors above this level are hidden (null: as the view settings say). */
  private floorCap: number | null = null;
  private hq = false;
  /** Building shown last; switching quality of the same building keeps the camera. */
  private buildingId: string | null = null;
  /** Bumped per content change so a slow GLB load can't replace newer content. */
  private loadToken = 0;
  private settings: ViewSettings;
  private selection: Selection | null = null;
  private hovered: THREE.Object3D | null = null;
  private fly: Fly | null = null;
  private needsRender = true;
  private insetLeft = 0;
  private freeAspect = 1;
  private downAt: [number, number] | null = null;
  private disposed = false;
  /** Edit mode: assets and doors of the generated view can be dragged. */
  private editable = false;
  private drag: Drag | null = null;
  private noFly: string | null = null;

  constructor(
    private readonly container: HTMLElement,
    settings: ViewSettings,
    private readonly onSelect: (sel: Selection | null, info?: ObjectInfo, hit?: PickHit) => void,
    private readonly onMove: (sel: Selection, position: Point, item?: MovedItem) => void = () => {},
  ) {
    this.settings = settings;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(this.renderer.domElement);

    this.camera.layers.enable(1);
    this.miniCamera.layers.enable(1);
    this.labelRenderer = new CSS2DRenderer();
    this.labelRenderer.domElement.className = 'viewer__labels';
    container.appendChild(this.labelRenderer.domElement);
    this.phoneFrame.hidden = true;
    this.phoneFrame.setAttribute('aria-hidden', 'true');
    container.appendChild(this.phoneFrame);

    this.scene.add(new THREE.HemisphereLight('#ffffff', '#d6dbea', 1.7));
    this.sun.position.set(40, 90, 30);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.0005;
    this.scene.add(this.sun, this.sun.target, this.root, this.connectors, this.highlight, this.graph);
    this.scene.add(this.routeGroup);
    this.buildWalker();
    this.scene.add(this.walkerGroup);
    this.scene.add(this.doneGroup);

    this.pin = makeLabel('', 'viewer__pin');
    this.pin.center.set(0.5, 1);
    this.pin.visible = false;
    this.hoverTip = makeLabel('', 'viewer__tip');
    this.hoverTip.center.set(0.5, 1.4);
    this.hoverTip.visible = false;
    this.edgeTip = makeLabel('', 'viewer__pin');
    this.edgeTip.center.set(0.5, 1);
    this.edgeTip.visible = false;
    this.scene.add(this.pin, this.hoverTip, this.edgeTip);

    this.camera.position.set(60, 60, 60);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    // Pan along the ground, as map and BIM viewers do, so dragging never lifts the view.
    this.controls.screenSpacePanning = false;
    this.setNavMode('orbit');
    this.controls.addEventListener('start', () => (this.fly = null));

    const el = this.renderer.domElement;
    // Capture phase: runs before OrbitControls' own listener, so a drag can switch it off.
    el.addEventListener('pointerdown', this.handlePointerDown, { capture: true });
    el.addEventListener('pointerup', this.handlePointerUp);
    el.addEventListener('pointercancel', this.handlePointerCancel);
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
    this.phoneFrame.remove();
    this.controls.dispose();
    this.clear();
    this.clearRouteObjects();
    for (const m of Object.values(this.routeMats)) m.dispose();
    for (const m of Object.values(this.markerMats)) m.dispose();
    this.clearDone();
    this.doneMat.dispose();
    for (const m of [this.closedMat, this.diagnosisMat, this.highlightMat]) m.dispose();
    this.miniTarget.dispose();
    this.miniFrame.dispose();
    this.markerGeo.dispose();
    this.doorGeo.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.labelRenderer.domElement.remove();
  }

  // ---------------------------------------------------------------- public API

  setModel(model: BuildingModel | null) {
    this.loadToken++;
    const keepCamera = this.sameBuilding(model);
    if (!keepCamera) this.route = null;
    const previous = this.selection;
    this.clear();
    this.model = model;
    if (model) this.build(model);
    // An edit of the same building rebuilds the scene; keep what was selected.
    if (keepCamera && previous && this.lookup(previous)) this.selection = previous;
    this.initAssetCulling();
    this.markClosed();
    this.applySettings();
    this.applyOverlays();
    this.buildRoute();
    if (!keepCamera) this.frame('iso', false);
  }

  /** The pipeline's model.glb for `model` (its cleaned building.json). */
  async setHq(url: string, model: BuildingModel): Promise<void> {
    const token = ++this.loadToken;
    const gltf = await new GLTFLoader().loadAsync(url);
    if (this.disposed || token !== this.loadToken) return;
    const keepCamera = this.sameBuilding(model);
    if (!keepCamera) this.route = null;
    this.clear();
    this.model = model;
    this.hq = true;
    this.buildHq(gltf.scene, model);
    this.initAssetCulling();
    this.markClosed();
    this.applySettings();
    this.applyOverlays();
    this.buildRoute();
    if (!keepCamera) this.frame('iso', false);
  }

  async setGlb(url: string): Promise<void> {
    const token = ++this.loadToken;
    const gltf = await new GLTFLoader().loadAsync(url);
    if (this.disposed || token !== this.loadToken) return;
    this.buildingId = null;
    this.route = null;
    this.clear();
    this.clearRouteObjects();
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
    const redrawRoute = rebuildConnectors || settings.floor !== this.settings.floor;
    this.settings = settings;
    if (rebuildConnectors && this.model && !this.hq) {
      this.buildConnectors(this.model);
      this.markClosed(['connector']);
      this.applyOverlays();
    }
    this.applySettings();
    if (redrawRoute) this.buildRoute();
  }

  /** Draw a route (or clear it with null); `fit` flies the camera to it. */
  setRoute(route: RouteResponse | null, fit = false) {
    this.clearDone();
    this.route = route;
    this.buildRoute();
    if (fit && route && this.routeGroup.children.length) {
      const box = new THREE.Box3();
      for (const o of this.routeGroup.children) {
        if (o instanceof THREE.Mesh || o instanceof Line2) box.expandByObject(o);
      }
      if (!box.isEmpty()) this.flyTo(box);
    }
  }

  /** POV camera state (per frame from useImmersive); overview keeps the orbit camera. */
  setLook(look: { view: 'overview' | 'pov'; yaw: number | null; yawOffset: number; pitch: number; roll?: number }) {
    const entering = look.view === 'pov' && this.look.view !== 'pov';
    this.look = look;
    if (entering) this.povYaw = null;
    // Developer split: the map around the embedded phone stays orbitable.
    this.controls.enabled = look.view !== 'pov' || this.split;
    this.labelRenderer.domElement.style.display = look.view === 'pov' ? 'none' : '';
    this.needsRender = true;
  }

  /** Developer split view: POV left, whole building right (same pose). */
  setSplit(on: boolean) {
    this.split = on;
    this.phoneFrame.hidden = !on;
    if (!this.preview) this.controls.enabled = this.look.view !== 'pov' || on;
    this.needsRender = true;
  }

  /** Place the simulated person; null hides it. Follow mode keeps it in view (smoothed). */
  setWalker(pose: WalkerPose | null) {
    const before = this.walkerGroup.visible ? this.walkerGroup.position.clone() : null;
    this.walkerPose = pose;
    this.walkerGroup.visible = !!pose && !!this.floors.length;
    if (pose) {
      this.walkerGroup.position.copy(this.walkerPoint(pose));
      const moved = before ? Math.hypot(before.x - this.walkerGroup.position.x, before.z - this.walkerGroup.position.z) : 0;
      this.swingWalker(moved < 2 ? moved : 0); // a jump (ride, reroute) is not a step
      // Building frame is counter-clockwise from +x; toVec maps building +y to scene -z.
      this.walkerGroup.rotation.y = THREE.MathUtils.degToRad(pose.headingDeg);
    }
    this.drawDone(pose?.distanceM ?? null);
    this.needsRender = true;
  }

  /** Grey out the part of the route already walked (rebuilt every half metre). */
  private drawDone(distance: number | null) {
    if (distance === null || !this.route?.waypoints?.length) {
      if (this.doneDrawnAt !== -1) this.clearDone();
      return;
    }
    if (this.doneDrawnAt >= 0 && Math.abs(distance - this.doneDrawnAt) < 0.5) return;
    this.clearDone();
    this.doneDrawnAt = distance;
    const floorView = new Map(this.floors.map((fv) => [fv.floor.id, fv]));
    const focus = this.settings.floor;
    const y = (floorId: string) => {
      const fv = floorView.get(floorId);
      return (fv?.floor.elevation_m ?? 0) + this.floorOffset(fv) + ROUTE_LIFT + 0.02;
    };
    const wps = this.route.waypoints;
    let run: THREE.Vector3[] = [];
    let floor = wps[0].floor_id;
    const flush = () => {
      const fv = floorView.get(floor);
      if (run.length >= 2 && fv && (focus === 'all' || fv.floor.level <= focus)) {
        const geo = new LineGeometry();
        geo.setPositions(run.flatMap((v) => [v.x, v.y, v.z]));
        const line = new Line2(geo, this.doneMat);
        line.renderOrder = 12;
        line.raycast = () => {};
        this.doneGroup.add(line);
      }
      run = [];
    };
    for (let i = 0; i < wps.length; i++) {
      const w = wps[i];
      if (w.floor_id !== floor) {
        flush();
        floor = w.floor_id;
      }
      if (w.distance_m <= distance) {
        run.push(toVec(w.position, y(w.floor_id)));
        continue;
      }
      const prev = wps[i - 1];
      if (prev && prev.floor_id === w.floor_id && w.distance_m > prev.distance_m) {
        const t = (distance - prev.distance_m) / (w.distance_m - prev.distance_m);
        const a = toVec(prev.position, y(w.floor_id));
        run.push(a.lerp(toVec(w.position, y(w.floor_id)), Math.max(0, Math.min(1, t))));
      }
      break;
    }
    flush();
  }

  private clearDone() {
    for (const child of [...this.doneGroup.children]) {
      (child as Line2).geometry.dispose();
      this.doneGroup.remove(child);
    }
    this.doneDrawnAt = -1;
  }

  setFollow(on: boolean) {
    this.follow = on;
    if (on && this.walkerPose) {
      const p = this.walkerPoint(this.walkerPose);
      const dir = this.camera.position.clone().sub(this.controls.target).normalize();
      if (dir.y < 0.5) dir.setY(0.5).normalize();
      this.startFly(p.clone().add(dir.multiplyScalar(26)), p);
    }
  }

  /** The whole building, follow off. */
  overview() {
    this.follow = false;
    this.cancelPreview();
    this.frame('iso', true);
  }

  /** Fly-through of the route (camera only, the walker does not move); `onDone` when finished.
   * `maxFlyMs` caps the drone part; short routes fly faster than walking, long ones up to the cap. */
  startPreview(route: RouteResponse, maxFlyMs: number, onDone?: () => void) {
    this.cancelPreview();
    const floorView = new Map(this.floors.map((fv) => [fv.floor.id, fv]));
    const raw = (route.waypoints ?? []).map((w) => {
      const fv = floorView.get(w.floor_id);
      return {
        point: toVec(w.position, (fv?.floor.elevation_m ?? 0) + this.floorOffset(fv) + DRONE_HEIGHT_M),
        level: fv?.floor.level ?? 0,
      };
    });
    // Drop near-duplicate points (they make the spline loop); keep the last one.
    const kept = raw.filter((p, i) => i === raw.length - 1 || p.point.distanceTo(raw[i + 1].point) > 0.3);
    const points = kept.map((p) => p.point);
    if (points.length < 2) {
      onDone?.();
      return;
    }
    const curve = new THREE.CatmullRomCurve3(points, false, 'centripetal', 0.5);
    const length = curve.getLength();
    const cumulative = [0];
    for (let i = 1; i < points.length; i++) cumulative.push(cumulative[i - 1] + points[i].distanceTo(points[i - 1]));
    const total = cumulative[cumulative.length - 1] || 1;
    const destination = points[points.length - 1].clone().setY(points[points.length - 1].y - DRONE_HEIGHT_M + 1.2);
    const box = new THREE.Box3().setFromPoints(points);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length();
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    if (dir.y < 0.6) dir.setY(0.6).normalize();
    const toCam = this.camera.position.clone().sub(destination);
    this.fly = null;
    this.follow = false;
    this.controls.enabled = false;
    this.labelRenderer.domElement.style.display = 'none';
    this.preview = {
      curve,
      length,
      destination,
      overviewPos: center.clone().add(dir.multiplyScalar(THREE.MathUtils.clamp(size * 1.6, 30, 160))),
      overviewTarget: center,
      fromPos: this.camera.position.clone(),
      fromTarget: this.controls.target.clone(),
      orbitAngle: Math.atan2(toCam.z, toCam.x),
      levels: kept.map((p) => p.level),
      shares: cumulative.map((c) => c / total),
      phases: {
        orbit: 1800,
        pull: 1300,
        fly: THREE.MathUtils.clamp((length / DRONE_MPS) * 1000, 3500, Math.max(3500, maxFlyMs)),
        settle: 1400,
      },
      start: performance.now(),
      onDone,
    };
    this.needsRender = true;
  }

  cancelPreview() {
    const done = this.preview?.onDone;
    if (this.preview) {
      this.controls.enabled = this.look.view !== 'pov' || this.split;
      this.labelRenderer.domElement.style.display = this.look.view === 'pov' ? 'none' : '';
      this.capFloors(null);
    }
    this.preview = null;
    done?.();
  }

  /** The floor level flown over at path share `u`: on a stair/lift leg the lower of its two ends,
   * so the floor above stays hidden until the drone is up there. */
  private previewLevel(pv: Preview, u: number): number {
    let i = 0;
    while (i < pv.shares.length - 2 && pv.shares[i + 1] <= u) i++;
    return Math.min(pv.levels[i], pv.levels[i + 1] ?? pv.levels[i]);
  }

  /** Hide floors above `level` (null: back to the view settings), within the floor focus. */
  private capFloors(level: number | null) {
    if (level === this.floorCap) return;
    this.floorCap = level;
    const focus = this.settings.floor;
    const shown = (lvl: number) => (focus === 'all' || lvl <= focus) && (level === null || lvl <= level);
    for (const fv of this.floors) fv.group.visible = shown(fv.floor.level);
    this.glb?.traverse((o) => {
      const lvl = o.userData.floor_level;
      if (typeof lvl === 'number') o.visible = shown(lvl);
    });
    this.needsRender = true;
  }

  /** Camera pose of the fly-through at `elapsed` ms (with the floor level it shows); null when it
   * is over. */
  private previewPose(
    pv: Preview,
    elapsed: number,
  ): { pos: THREE.Vector3; target: THREE.Vector3; level: number } | null {
    const { orbit, pull, fly, settle } = pv.phases;
    const orbitPos = (t: number) => {
      const a = pv.orbitAngle + t * 1.1; // a slow quarter turn around the destination
      return pv.destination.clone().add(new THREE.Vector3(Math.cos(a) * 7, 4.5, Math.sin(a) * 7));
    };
    const ahead = (u: number) =>
      pv.curve.getPointAt(Math.min(1, u + Math.min(0.3, 9 / pv.length))).add(new THREE.Vector3(0, -DRONE_LOOK_DOWN_M, 0));
    let t = elapsed;
    if (t < orbit) {
      const k = t / orbit;
      const blend = easeInOut(Math.min(1, t / 700)); // from wherever the camera was
      return {
        pos: pv.fromPos.clone().lerp(orbitPos(k), blend),
        target: pv.fromTarget.clone().lerp(pv.destination, blend),
        level: pv.levels[pv.levels.length - 1],
      };
    }
    t -= orbit;
    const startPos = pv.curve.getPointAt(0);
    const behind = startPos.clone().sub(ahead(0)).setY(0).normalize();
    const high = startPos.clone().add(behind.multiplyScalar(9)).add(new THREE.Vector3(0, 9, 0));
    if (t < pull) {
      const k = easeInOut(t / pull);
      return {
        pos: orbitPos(1).lerp(high, k),
        target: pv.destination.clone().lerp(ahead(0), k),
        level: k < 0.5 ? pv.levels[pv.levels.length - 1] : pv.levels[0],
      };
    }
    t -= pull;
    // Stop a few metres short of the destination (it is often a machine the drone would enter).
    const end = Math.max(0.5, 1 - 6 / pv.length);
    if (t < fly) {
      const u = easeInOut(t / fly) * end;
      // Dive from the high start point onto the path during the first fifth, then follow it.
      const dive = Math.min(1, t / (fly * 0.2));
      return {
        pos: high.clone().lerp(pv.curve.getPointAt(u), easeInOut(dive)),
        target: ahead(u),
        level: this.previewLevel(pv, u),
      };
    }
    t -= fly;
    if (t < settle) {
      const k = easeInOut(t / settle);
      return {
        pos: pv.curve.getPointAt(end).lerp(pv.overviewPos, k),
        target: ahead(end).lerp(pv.overviewTarget, k),
        level: this.previewLevel(pv, end),
      };
    }
    return null;
  }

  get previewing() {
    return this.preview !== null;
  }

  private walkerPoint(pose: WalkerPose): THREE.Vector3 {
    const fv = this.floors.find((f) => f.floor.id === pose.floorId);
    return toVec(pose.position, (fv?.floor.elevation_m ?? 0) + this.floorOffset(fv) + 0.02);
  }

  private buildWalker() {
    // A person at real scale (about 1.75 m, facing +x): hard hat, high-visibility vest with
    // reflective bands, dark trousers; lit like the building. Legs and arms swing with the
    // distance walked (setWalker). Hidden parts show as a soft navy silhouette through walls.
    // Transparent pass (opacity 1) so the silhouette below is drawn first, against the building only.
    const lit = (color: string) => new THREE.MeshLambertMaterial({ color, transparent: true });
    const capsule = (r: number, len: number) => new THREE.CapsuleGeometry(r, len, 6, 14);
    const figure = new THREE.Group();
    const add = (parent: THREE.Object3D, mesh: THREE.Mesh, x: number, y: number, z: number) => {
      mesh.position.set(x, y, z);
      parent.add(mesh);
      return mesh;
    };
    const limb = (y: number, z: number, r: number, len: number, color: string, pivot: THREE.Group[]) => {
      const joint = new THREE.Group();
      joint.position.set(0, y, z);
      add(joint, new THREE.Mesh(capsule(r, len), lit(color)), 0, -(len / 2 + r), 0);
      figure.add(joint);
      pivot.push(joint);
    };
    limb(0.9, 0.095, 0.075, 0.72, '#26324d', this.walkerLegs); // trousers
    limb(0.9, -0.095, 0.075, 0.72, '#26324d', this.walkerLegs);
    const torso = add(figure, new THREE.Mesh(capsule(0.17, 0.36), lit('#fb7928')), 0, 1.2, 0);
    torso.scale.set(0.72, 1, 1);
    for (const y of [1.1, 1.3]) {
      const band = add(figure, new THREE.Mesh(new THREE.CylinderGeometry(0.173, 0.173, 0.035, 20), lit('#e9edf5')), 0, y, 0);
      band.scale.set(0.72, 1, 1);
    }
    limb(1.47, 0.235, 0.055, 0.46, '#fb7928', this.walkerArms); // vest sleeves
    limb(1.47, -0.235, 0.055, 0.46, '#fb7928', this.walkerArms);
    add(figure, new THREE.Mesh(new THREE.SphereGeometry(0.105, 18, 14), lit('#d6a888')), 0, 1.64, 0);
    const hat = add(
      figure,
      new THREE.Mesh(new THREE.SphereGeometry(0.122, 18, 10, 0, Math.PI * 2, 0, Math.PI / 2), lit('#f7f8fb')),
      0.01,
      1.675,
      0,
    );
    hat.scale.set(1.05, 0.9, 1);
    add(figure, new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.15, 0.014, 22), lit('#f7f8fb')), 0.035, 1.675, 0);
    // Through walls: the same shapes, drawn only where the building is in front of them (before
    // the figure itself, so the figure never shows its own silhouette).
    const ghost = new THREE.MeshBasicMaterial({
      color: '#002478',
      transparent: true,
      opacity: 0.32,
      depthWrite: false,
      depthFunc: THREE.GreaterDepth,
    });
    const meshes: THREE.Mesh[] = [];
    figure.traverse((o) => o instanceof THREE.Mesh && meshes.push(o));
    for (const m of meshes) {
      const copy = new THREE.Mesh(m.geometry, ghost);
      copy.renderOrder = 11;
      m.add(copy);
    }
    // On the floor: position disc and a view beam, always visible and grown with distance.
    const floor = (geometry: THREE.BufferGeometry, color: string, opacity: number) => {
      const m = new THREE.Mesh(
        geometry,
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthTest: false, depthWrite: false }),
      );
      m.rotation.x = -Math.PI / 2;
      m.renderOrder = 12;
      this.walkerHalo.add(m);
      return m;
    };
    floor(new THREE.CircleGeometry(2.4, 40, -Math.PI / 6, Math.PI / 3), '#2f6bff', 0.18).position.y = 0.03;
    floor(new THREE.CircleGeometry(0.5, 40), '#2f6bff', 0.28).position.y = 0.035;
    floor(new THREE.RingGeometry(0.5, 0.58, 48), '#ffffff', 0.95).position.y = 0.04;
    floor(new THREE.RingGeometry(0.58, 0.64, 48), '#2f6bff', 0.9).position.y = 0.04;
    this.walkerGroup.add(this.walkerHalo, figure);
    this.walkerGroup.traverse((o) => {
      o.layers.set(1);
      o.raycast = () => {};
      if (o instanceof THREE.Mesh && o.renderOrder === 0) o.renderOrder = 13;
    });
    this.walkerGroup.visible = false;
  }

  /** Legs and arms swing with the distance walked; standing still straightens them. */
  private swingWalker(moved: number) {
    this.walkerPhase = moved > 1e-4 ? this.walkerPhase + (moved / 0.75) * Math.PI : 0;
    const swing = moved > 1e-4 ? Math.sin(this.walkerPhase) * 0.45 : 0;
    this.walkerLegs.forEach((leg, i) => (leg.rotation.z = i ? -swing : swing));
    this.walkerArms.forEach((arm, i) => (arm.rotation.z = (i ? swing : -swing) * 0.8));
  }

  private stepGuidanceCamera(now: number) {
    const pv = this.preview;
    if (pv) {
      const pose = this.previewPose(pv, now - pv.start);
      if (!pose) {
        this.camera.position.copy(pv.overviewPos);
        this.controls.target.copy(pv.overviewTarget);
        this.needsRender = true;
        this.cancelPreview();
        return;
      }
      this.capFloors(pose.level);
      // Light smoothing keeps the drone calm without lagging behind the path.
      this.camera.position.lerp(pose.pos, 0.35);
      this.controls.target.lerp(pose.target, 0.35);
      this.needsRender = true;
      return;
    }
    if (this.walkerGroup.visible) {
      // The floor disc stays readable from far away; the figure itself keeps its real size.
      const grow = THREE.MathUtils.clamp(this.camera.position.distanceTo(this.walkerGroup.position) / 30, 1, 4);
      if (Math.abs(this.walkerHalo.scale.x - grow) > 0.05) {
        this.walkerHalo.scale.setScalar(grow);
        this.needsRender = true;
      }
    }
    if (this.follow && this.walkerPose && !this.fly) {
      const p = this.walkerPoint(this.walkerPose);
      const delta = p.sub(this.controls.target).multiplyScalar(0.08);
      if (delta.lengthSq() > 1e-6) {
        this.controls.target.add(delta);
        this.camera.position.add(delta);
        this.needsRender = true;
      }
    }
  }

  setHighlight(ids: readonly string[]) {
    this.highlightIds = [...ids];
    this.updateAssetCulling();
    this.applyOverlays();
  }

  /** Elements a failed route blames (red-pink). */
  setDiagnosis(ids: readonly string[]) {
    this.diagnosisIds = [...ids];
    this.applyOverlays();
  }

  /** Elements closed in the navigation scenario (translucent red). */
  setClosed(ids: readonly string[]) {
    this.closedIds = [...ids];
    this.applyOverlays();
  }

  setGraph(data: RoomGraphData | null) {
    this.graphData = data;
    this.buildGraph();
    this.needsRender = true;
  }

  setEditable(on: boolean) {
    this.editable = on;
    if (!on) this.endDrag(false);
  }

  setSelection(selection: Selection | null, fly = true) {
    // The asset just dragged gets selected by the app too; the camera stays where it is.
    if (fly && selection && selKey(selection) === this.noFly) fly = false;
    if (fly) this.noFly = null;
    if (this.selection) this.restoreMaterial(this.lookup(this.selection));
    for (const a of this.litAssets) this.restoreMaterial(a);
    this.litAssets = [];
    this.selection = selection;
    this.updateAssetCulling();
    this.clearHighlight();
    this.pin.visible = false;
    // The pin takes over from the hover pill once the hovered thing is selected.
    if (this.hovered && selection && this.lookup(selection) === this.hovered) this.hoverTip.visible = false;
    const obj = selection && this.lookup(selection);
    if (obj) {
      this.paint(obj, this.selectMat);
      const box = new THREE.Box3().setFromObject(obj);
      let top = box.max.y;
      if (selection.kind === 'space') top = this.addRoomVolume(selection.id, box);
      // Doors are thin and hide in walls; equipment is painted, which is enough.
      else if (selection.kind === 'opening') this.addBoxOutline(box);
      const anchor = box.getCenter(new THREE.Vector3());
      anchor.y = top + 0.3;
      this.pin.position.copy(anchor);
      this.pin.element.textContent = obj.userData.title ?? '';
      this.pin.visible = true;
      if (selection.kind === 'space') {
        for (const a of this.model?.assets ?? []) {
          const mesh = a.space_id === selection.id && this.objects.get(`asset:${a.id}`);
          if (mesh) this.litAssets.push(mesh);
        }
        for (const a of this.litAssets) this.paint(a, this.roomAssetMat);
      }
      if (fly) this.flyTo(box);
    }
    this.needsRender = true;
  }

  /**
   * Orbit: left drag / one finger rotates; right or middle drag, shift + drag and two
   * fingers pan. Pan swaps the primary drag, the usual hand tool of 3D viewers.
   */
  setNavMode(mode: NavMode) {
    const { MOUSE, TOUCH } = THREE;
    const pan = mode === 'pan';
    this.controls.mouseButtons = {
      LEFT: pan ? MOUSE.PAN : MOUSE.ROTATE,
      MIDDLE: MOUSE.PAN,
      RIGHT: pan ? MOUSE.ROTATE : MOUSE.PAN,
    };
    this.controls.touches = {
      ONE: pan ? TOUCH.PAN : TOUCH.ROTATE,
      TWO: pan ? TOUCH.DOLLY_ROTATE : TOUCH.DOLLY_PAN,
    };
    this.renderer.domElement.dataset.nav = mode;
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

  private sameBuilding(model: BuildingModel | null): boolean {
    const id = model?.building.id ?? null;
    const same = id !== null && id === this.buildingId && this.floors.length > 0;
    this.buildingId = id;
    return same;
  }

  private lookup(sel: Selection): THREE.Object3D | undefined {
    return this.objects.get(selKey(sel)) ?? this.byId.get(sel.id);
  }

  private clear() {
    this.setHovered(null);
    this.selection = null;
    this.overlaid.clear();
    this.byId.clear();
    this.litAssets = [];
    this.assetIndex = null;
    this.shownAssets = null;
    this.clearHighlight();
    this.pin.visible = false;
    for (const f of this.floors) for (const m of f.materials) m.dispose();
    this.root.traverse((o) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
        if (o.geometry !== this.unitBox) o.geometry.dispose();
      }
      if (o instanceof CSS2DObject) o.element.remove();
    });
    this.root.clear();
    this.connectors.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        o.geometry.dispose();
        if (this.hq) (o.material as THREE.Material).dispose();
      }
    });
    this.connectors.clear();
    this.floors = [];
    this.objects.clear();
    this.glb = null;
    this.hq = false;
  }

  private register(obj: THREE.Object3D, sel: Selection, title: string) {
    obj.userData.sel = sel;
    obj.userData.title = title;
    this.objects.set(selKey(sel), obj);
    this.byId.set(sel.id, obj);
  }

  // ---------------------------------------------------------------- asset culling

  /** Index assets and hide them all; only buildings above the cap are culled. */
  private initAssetCulling() {
    this.assetIndex = null;
    this.shownAssets = null;
    if (!this.model) return;
    const index = buildAssetIndex(this.model.assets);
    if (visibleAssetIds(index, { selection: null, focusFloorId: null, cap: MAX_VISIBLE_ASSETS }) === null) {
      // Under the cap: everything shows.
      for (const a of this.model.assets) {
        const obj = this.objects.get(`asset:${a.id}`);
        if (obj) obj.visible = true;
      }
      return;
    }
    this.assetIndex = index;
    this.shownAssets = new Set();
    for (const a of this.model.assets) {
      const obj = this.objects.get(`asset:${a.id}`);
      if (obj) obj.visible = false;
    }
  }

  /** Show the assets of the selection/route/focused floor; touches only what changed. */
  private updateAssetCulling() {
    const index = this.assetIndex;
    const prev = this.shownAssets;
    if (!index || !prev) return;
    const focus = this.settings.floor;
    const floorId = focus === 'all' ? null : (this.floors.find((f) => f.floor.level === focus)?.floor.id ?? null);
    const next = visibleAssetIds(index, {
      selection: this.selection,
      focusFloorId: floorId,
      highlightIds: this.highlightIds,
      cap: MAX_VISIBLE_ASSETS,
    });
    if (!next) return;
    const set = (id: string, visible: boolean) => {
      const obj = this.objects.get(`asset:${id}`);
      if (obj) obj.visible = visible;
    };
    for (const id of prev) if (!next.has(id)) set(id, false);
    for (const id of next) if (!prev.has(id)) set(id, true);
    this.shownAssets = next;
    this.needsRender = true;
  }

  // ---------------------------------------------------------------- overlays + route

  /** Swap an element's resting material (null = its own), keeping hover/selection logic intact. */
  private setBase(obj: THREE.Object3D, material: THREE.Material | null) {
    obj.traverse((o) => {
      if (!(o instanceof THREE.Mesh) || !o.userData.baseMaterial) return;
      o.userData.origMaterial ??= o.userData.baseMaterial;
      o.userData.baseMaterial = material ?? o.userData.origMaterial;
      o.material = o.userData.baseMaterial;
    });
  }

  private applyOverlays() {
    for (const obj of this.overlaid) this.setBase(obj, null);
    this.overlaid.clear();
    if (this.glb) return;
    const paint = (ids: string[], material: THREE.Material) => {
      for (const id of ids) {
        const obj = this.byId.get(id);
        if (!obj || this.overlaid.has(obj)) continue;
        this.setBase(obj, material);
        this.overlaid.add(obj);
      }
    };
    paint(this.closedIds, this.closedMat);
    paint(this.diagnosisIds, this.diagnosisMat);
    // When the assistant marks objects, mark only those, not the rooms they stand in.
    const assetRooms = new Set<string>();
    const assetById = new Map(this.model?.assets.map((a) => [a.id, a]) ?? []);
    for (const id of this.highlightIds) {
      const room = assetById.get(id)?.space_id;
      if (room) assetRooms.add(room);
    }
    paint(
      this.highlightIds.filter((id) => !assetRooms.has(id)),
      this.highlightMat,
    );
    const selected = this.selection && this.lookup(this.selection);
    if (selected) this.paint(selected, this.selectMat);
    this.needsRender = true;
  }

  private clearRouteObjects() {
    for (const o of this.routeGroup.children) {
      if (o instanceof Line2) o.geometry.dispose();
      if (o instanceof CSS2DObject) o.element.remove();
    }
    this.routeGroup.clear();
  }

  /** Rebuild the route for the current floors, floor focus and explode setting. */
  private buildRoute() {
    this.clearRouteObjects();
    this.needsRender = true;
    const route = this.route;
    if (!route || !this.floors.length) return;
    const drawing = routeDrawing(route);
    const floorView = new Map(this.floors.map((fv) => [fv.floor.id, fv]));
    const focus = this.settings.floor;
    const shown = (floorId: string) => {
      const fv = floorView.get(floorId);
      return !!fv && (focus === 'all' || fv.floor.level <= focus);
    };
    const yOf = (floorId: string) => {
      const fv = floorView.get(floorId);
      return (fv?.floor.elevation_m ?? 0) + this.floorOffset(fv) + ROUTE_LIFT;
    };
    const line = (points: THREE.Vector3[], material: LineMaterial) => {
      const geo = new LineGeometry();
      geo.setPositions(points.flatMap((p) => [p.x, p.y, p.z]));
      const l = new Line2(geo, material);
      l.computeLineDistances();
      l.renderOrder = 10;
      l.raycast = () => {};
      this.routeGroup.add(l);
    };
    const marker = (geo: THREE.BufferGeometry, material: THREE.Material, pos: THREE.Vector3) => {
      const m = new THREE.Mesh(geo, material);
      m.position.copy(pos);
      m.renderOrder = 11;
      m.raycast = () => {};
      this.routeGroup.add(m);
    };
    const tag = (text: string, pos: THREE.Vector3) => {
      const label = makeLabel(text, 'viewer__route-tag');
      label.position.copy(pos);
      label.center.set(0.5, 1.6);
      this.routeGroup.add(label);
    };

    const material = this.routeMats[drawing.style];
    for (const seg of drawing.lines) {
      if (!shown(seg.floorId)) continue;
      const y = yOf(seg.floorId);
      line(
        seg.points.map((p) => toVec(p, y)),
        material,
      );
    }
    const exits = new Set(
      this.model?.openings.filter((o) => o.space_ids.includes('outside')).map((o) => o.id) ?? [],
    );
    for (const d of drawing.doors) {
      if (!shown(d.floorId)) continue;
      const mat = exits.has(d.elementId) ? this.markerMats.exit : this.markerMats.door;
      marker(this.doorGeo, mat, toVec(d.position, yOf(d.floorId)));
    }
    for (const v of drawing.vertical) {
      if (!shown(v.fromFloorId) || !shown(v.toFloorId)) continue;
      const a = toVec(v.position, yOf(v.fromFloorId));
      const b = toVec(v.position, yOf(v.toFloorId));
      line([a, b], this.routeMats.vertical);
      const name = v.kind === 'lift' ? 'Lift' : 'Stairs';
      tag(v.schematic ? `${name} · schematic` : name, a.clone().lerp(b, 0.5));
    }
    if (drawing.start && shown(drawing.start.floorId)) {
      const p = toVec(drawing.start.position, yOf(drawing.start.floorId));
      marker(this.markerGeo, this.markerMats.start, p);
      tag('Start', p);
    }
    if (drawing.target && shown(drawing.target.floorId)) {
      const p = toVec(drawing.target.position, yOf(drawing.target.floorId));
      marker(this.markerGeo, this.markerMats.target, p);
      tag('Destination', p);
    }
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

      labels.add(makeRoomLabel(s, floor.elevation_m));
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

    const marks = new THREE.Group();
    group.add(spaceGroup, walls, assetGroup, openings, labels);
    return {
      floor,
      group,
      spaces: spaceGroup,
      walls,
      assets: assetGroup,
      openings,
      marks,
      labels,
      materials,
      hq: false,
    };
  }

  /**
   * Split the pipeline's GLB (building > floor > space/walls/asset/opening/marker nodes,
   * connectors under the building) into the same per-floor layers as the generated view,
   * and register its nodes under their building.json ids so selection works the same.
   */
  private buildHq(gltfRoot: THREE.Object3D, model: BuildingModel) {
    const floorById = new Map(model.floors.map((f) => [f.id, f]));
    const spaceById = new Map(model.spaces.map((s) => [s.id, s]));
    const assetById = new Map(model.assets.map((a) => [a.id, a]));
    const connectorById = new Map(model.vertical_connectors.map((c) => [c.id, c]));
    const floorNodes: THREE.Object3D[] = [];
    const connectorNodes: THREE.Object3D[] = [];
    gltfRoot.traverse((o) => {
      if (o.userData.kind === 'floor') floorNodes.push(o);
      if (o.userData.kind === 'connector') connectorNodes.push(o);
    });

    // Floors get their own copies of materials so ghosting one floor leaves the others.
    const adopt = (obj: THREE.Object3D, owned: Map<string, THREE.Material>, kind: string) => {
      obj.traverse((o) => {
        if (!(o instanceof THREE.Mesh) || Array.isArray(o.material)) return;
        const key = `${kind}:${o.material.uuid}`;
        const material = owned.get(key) ?? o.material.clone();
        owned.set(key, material);
        o.material = material;
        o.userData.baseMaterial = material;
        o.castShadow = kind !== 'space' && kind !== 'opening';
        o.receiveShadow = true;
      });
    };

    for (const node of floorNodes) {
      const ex = node.userData;
      const floor: Floor = floorById.get(ex.id) ?? {
        id: String(ex.id),
        level: Number(ex.level ?? 0),
        name: String(ex.id),
        elevation_m: Number(ex.elevation_m ?? 0),
        height_m: Number(ex.height_m ?? 3),
      };
      const layers = {
        spaces: new THREE.Group(),
        walls: new THREE.Group(),
        assets: new THREE.Group(),
        openings: new THREE.Group(),
      };
      const marks = new THREE.Group();
      const labels = new THREE.Group();
      const owned = new Map<string, THREE.Material>();

      for (const child of [...node.children]) {
        const kind = String(child.userData.kind ?? '');
        const layer = HQ_LAYERS[kind];
        (layer ? layers[layer] : marks).add(child);
        adopt(child, owned, kind);
        const id = String(child.userData.id ?? '');
        if (kind === 'space') {
          const space = spaceById.get(id);
          this.register(child, { kind: 'space', id }, space?.name ?? String(child.userData.name ?? id));
          if (child instanceof THREE.Mesh) {
            child.userData.roomColor = `#${(child.material as THREE.MeshStandardMaterial).color.getHexString()}`;
          }
          if (space && space.polygon.length >= 3) {
            labels.add(makeRoomLabel(space, floor.elevation_m));
          }
        } else if (kind === 'asset') {
          this.register(child, { kind: 'asset', id }, assetById.get(id)?.name ?? id);
        } else if (kind === 'opening') {
          const title = `${child.userData.opening_kind ?? 'opening'} ${id.split('-').pop()}`;
          this.register(child, { kind: 'opening', id }, title);
        } else {
          child.traverse((o) => (o.raycast = () => {}));
        }
      }

      node.add(layers.spaces, layers.walls, layers.assets, layers.openings, marks, labels);
      const group = new THREE.Group();
      group.name = floor.name;
      group.add(node);
      this.floors.push({
        floor,
        group,
        ...layers,
        marks,
        labels,
        materials: [...owned.values()],
        hq: true,
      });
    }
    this.floors.sort((a, b) => a.floor.level - b.floor.level);
    for (const fv of this.floors) this.root.add(fv.group);

    const owned = new Map<string, THREE.Material>();
    gltfRoot.updateMatrixWorld(true); // attach() keeps world transforms
    for (const node of connectorNodes) {
      const id = String(node.userData.id ?? '');
      this.connectors.attach(node);
      adopt(node, owned, 'connector');
      this.register(node, { kind: 'connector', id }, connectorById.get(id)?.name ?? id);
    }
  }

  /** Paint closed elements red with their own material copy, so ghosting and selection still work. */
  private markClosed(kinds: SelectionKind[] = ['space', 'opening', 'connector']) {
    for (const id of this.model?.closed ?? []) {
      for (const kind of kinds) {
        const obj = this.objects.get(`${kind}:${id}`);
        if (!obj) continue;
        const fv = this.floors.find((f) => f.group.getObjectById(obj.id));
        obj.userData.closed = true;
        obj.traverse((o) => {
          const base = o.userData.baseMaterial;
          if (!(o instanceof THREE.Mesh) || !(base instanceof THREE.MeshStandardMaterial)) return;
          const red = base.clone();
          red.color.set(CLOSED_COLOR);
          o.material = o.userData.baseMaterial = red;
          o.userData.closed = true;
          fv?.materials.push(red);
        });
        obj.userData.title = `${String(obj.userData.title ?? id).replace(/ \(closed\)$/, '')} (closed)`;
      }
    }
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

  /** Rooms as dots at their anchor, doors as lines through the door, stairs/lifts as risers. */
  private buildGraph() {
    for (const o of this.graph.children) {
      if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
        o.geometry.dispose();
        (o.material as THREE.Material).dispose();
      }
    }
    this.graph.clear();
    this.graphSegments = [];
    this.graphDotIds = [];
    this.edgeTip.visible = false;
    const data = this.graphData;
    if (!data || !this.settings.layers.graph || !this.floors.length) return;

    const focus = this.settings.floor;
    const yOf = (floorId: string | undefined) => {
      const fv = this.floors.find((f) => f.floor.id === floorId);
      if (!fv || (focus !== 'all' && fv.floor.level > focus)) return null;
      return fv.floor.elevation_m + this.floorOffset(fv) + GRAPH_LIFT;
    };
    const nodes = new Map<string, { at: THREE.Vector3; floor?: string }>();
    for (const n of data.nodes) {
      const y = yOf(n.floor_id);
      if (n.anchor && y !== null) nodes.set(n.id, { at: toVec(n.anchor, y), floor: n.floor_id });
    }

    const points: number[] = [];
    const colors: number[] = [];
    const color = new THREE.Color();
    const segment = (a: THREE.Vector3, b: THREE.Vector3, hex: string) => {
      color.set(hex);
      points.push(a.x, a.y, a.z, b.x, b.y, b.z);
      colors.push(color.r, color.g, color.b, color.r, color.g, color.b);
    };
    for (const [i, e] of data.edges.entries()) {
      const before = points.length;
      const hex = GRAPH_COLORS[e.closed ? 'closed' : e.kind] ?? GRAPH_COLORS.door;
      const [a, b] = [nodes.get(e.source), nodes.get(e.target)];
      if (e.position) {
        // Through the door itself; an entrance has only its inside end.
        const y = yOf(e.floor_id);
        if (y === null) continue;
        const door = toVec(e.position, y);
        if (a) segment(a.at, door, hex);
        if (b) segment(door, b.at, hex);
      } else if (a && b) {
        segment(a.at, b.at, hex);
      }
      for (let k = before; k < points.length; k += 6) this.graphSegments.push(i);
    }
    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
    lineGeo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    const lines = new THREE.LineSegments(
      lineGeo,
      new THREE.LineBasicMaterial({ vertexColors: true, depthTest: false, transparent: true }),
    );
    lines.renderOrder = 1000;

    const dots = new THREE.InstancedMesh(
      new THREE.SphereGeometry(0.35, 12, 8),
      new THREE.MeshBasicMaterial({ depthTest: false, transparent: true }),
      nodes.size,
    );
    dots.renderOrder = 1001;
    const byId = new Map(data.nodes.map((n) => [n.id, n]));
    let i = 0;
    for (const [id, { at }] of nodes) {
      const n = byId.get(id)!;
      dots.setMatrixAt(i, new THREE.Matrix4().setPosition(at));
      color.set(
        !n.connected_to_outside
          ? GRAPH_COLORS.cut
          : n.closed
            ? '#9a9db0'
            : spaceColor(n as unknown as Space),
      );
      // Room fills are pastel; the dots need a stronger version of the same hue to stand out.
      if (n.connected_to_outside && !n.closed) color.offsetHSL(0, 0.45, -0.3);
      dots.setColorAt(i++, color);
      this.graphDotIds.push(id);
    }
    this.graph.add(lines, dots);
  }

  private floorOffset(fv: FloorView | undefined): number {
    return fv ? this.floors.indexOf(fv) * this.settings.explode * EXPLODE_GAP : 0;
  }

  // ---------------------------------------------------------------- settings

  private applySettings() {
    const s = this.settings;
    const focus = s.floor;
    this.floorCap = null; // a running fly-through hides its floors again on the next frame
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
      fv.marks.visible = s.layers.connectors && !ghost;
      // Labels of stacked floors would show through each other, so only label one floor.
      const labelled = focus === 'all' ? this.floors.length === 1 : level === focus;
      fv.labels.visible = s.layers.labels && labelled;
      if (fv.hq) {
        // Model walls are full storey height: squash them towards the floor's elevation.
        const k = Math.max(0.01, Math.min(1, s.wallHeight / fv.floor.height_m));
        fv.walls.scale.y = k;
        fv.walls.position.y = fv.floor.elevation_m * (1 - k);
      } else {
        fv.walls.scale.y = Math.max(0.01, Math.min(s.wallHeight, fv.floor.height_m - SLAB));
      }
      for (const mesh of fv.spaces.children) {
        // The own material, also while an overlay (closed, highlight) is painted over it.
        const material = (mesh.userData.origMaterial ?? mesh.userData.baseMaterial) as
          | THREE.MeshStandardMaterial
          | undefined;
        if (!material || !mesh.userData.roomColor) continue;
        material.color.set(
          mesh.userData.closed ? CLOSED_COLOR : s.colorBy === 'room' ? mesh.userData.roomColor : '#eef0f4',
        );
      }
    }
    // The model's stair/lift shafts span storeys, so they can't follow exploded floors.
    this.connectors.visible = s.layers.connectors && !(this.hq && s.explode > 0);
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
    this.updateAssetCulling();
    this.buildGraph();
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
    for (const m of Object.values(this.routeMats)) m.resolution.set(w, h);
    this.doneMat.resolution.set(w, h);
    this.needsRender = true;
  }

  /** POV frame: eye-height view of the person, plus the overview as a minimap or split half. */
  private renderPov() {
    const pose = this.walkerPose;
    if (!pose) return;
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    const target = wrap360(this.look.yaw ?? pose.headingDeg + this.look.yawOffset);
    // Calm camera: turn smoothly towards the wanted heading (no head-bob).
    this.povYaw = this.povYaw === null ? target : wrap360(this.povYaw + angleDiff(this.povYaw, target) * 0.18);
    const base = this.walkerPoint(pose);
    const eye = base.clone().setY(base.y - 0.02 + EYE_HEIGHT_M);
    const yaw = THREE.MathUtils.degToRad(this.povYaw);
    const pitch = THREE.MathUtils.degToRad(this.look.pitch);
    const dir = new THREE.Vector3(Math.cos(yaw) * Math.cos(pitch), Math.sin(pitch), -Math.sin(yaw) * Math.cos(pitch));
    this.povCamera.position.copy(eye);
    this.povCamera.lookAt(eye.clone().add(dir));
    // Phone tilt (device orientation): roll about the view axis after aiming.
    if (this.look.roll) this.povCamera.rotateZ(-THREE.MathUtils.degToRad(this.look.roll));
    const r = this.renderer;
    // Split (developer, desktop): the whole building from the orbit camera fills the view right of
    // the docked panel; the first-person view is an embedded phone (same pose, route and heading).
    const x0 = this.split ? Math.min(this.insetLeft, w / 2) : 0;
    const phone = this.split ? this.phoneRect(x0, w, h) : null;
    const povW = phone ? phone.w : w;
    const povH = phone ? phone.h : h;
    this.povRect = phone ?? { x: 0, y: 0, w, h };
    this.povCamera.aspect = povW / povH;
    // A tall portrait phone would see a narrow slice at a fixed vertical angle (looks zoomed in):
    // keep about 70 deg across, within a 100 deg vertical angle.
    const across = 2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(70) / 2) / this.povCamera.aspect);
    this.povCamera.fov = Math.min(100, Math.max(POV_FOV, THREE.MathUtils.radToDeg(across)));
    this.povCamera.updateProjectionMatrix();
    r.setScissorTest(true);
    if (phone) {
      this.resizeCameraOnly();
      r.setViewport(0, 0, w, h);
      r.setScissor(0, 0, w, h);
      r.render(this.scene, this.camera);
      // GL counts from the bottom; the frame (CSS) from the top.
      r.setViewport(phone.x, h - phone.y - phone.h, phone.w, phone.h);
      r.setScissor(phone.x, h - phone.y - phone.h, phone.w, phone.h);
      r.clear();
    } else {
      r.setViewport(0, 0, w, h);
      r.setScissor(0, 0, w, h);
    }
    // HQ walls are squashed to the overview wall height (about 1 m in an 8 m hall storey);
    // at eye height they must be full storey height, or one looks over every wall.
    const squashed = this.floors
      .filter((fv) => fv.hq)
      .map((fv) => [fv, fv.walls.scale.y, fv.walls.position.y] as const);
    for (const [fv] of squashed) {
      fv.walls.scale.y = 1;
      fv.walls.position.y = 0;
    }
    r.render(this.scene, this.povCamera);
    for (const [fv, scale, y] of squashed) {
      fv.walls.scale.y = scale;
      fv.walls.position.y = y;
    }
    if (phone) {
      // the overview was drawn first, under the phone
    } else if (w > MINIMAP_MIN_WIDTH) {
      // Minimap (top-down, north up, centred on the person) in the free area's top-left corner;
      // phones skip it, the first-person view needs the whole screen there.
      const size = Math.round(Math.min(w, h) * (w < 600 ? 0.34 : 0.26));
      const mh = Math.round(size * 0.8);
      const x = Math.min(this.insetLeft, w / 2) + 16;
      const y = Math.max(8, h - mh - 160); // below the top bar and the immersive mode bar
      this.miniCamera.left = -15;
      this.miniCamera.right = 15;
      this.miniCamera.top = 12;
      this.miniCamera.bottom = -12;
      this.miniCamera.position.set(base.x, base.y + 60, base.z);
      this.miniCamera.up.set(0, 0, -1);
      this.miniCamera.lookAt(base.x, base.y, base.z);
      this.miniCamera.updateProjectionMatrix();
      const dpr = r.getPixelRatio();
      this.miniTarget.setSize(Math.round(size * dpr), Math.round(mh * dpr));
      r.setRenderTarget(this.miniTarget);
      r.setClearColor(0xf4f6fb, 1);
      r.clear();
      r.render(this.scene, this.miniCamera);
      r.setRenderTarget(null);
      r.setClearColor(0x000000, 0);
      this.miniFrame.uniforms.size.value.set(size, mh);
      r.setViewport(x, y, size, mh);
      r.setScissor(x, y, size, mh);
      r.render(this.miniFrameScene, this.camera);
    }
    r.setScissorTest(false);
    r.setViewport(0, 0, w, h);
  }

  /** The embedded phone (CSS px from the container's top-left) and its frame and HUD position. */
  private phoneRect(x0: number, w: number, h: number) {
    const width = Math.round(Math.min(300, Math.max(200, (w - x0) * 0.24)));
    const height = Math.round(Math.min(width * 2.05, h - 210));
    const rect = { x: Math.round(x0 + 28), y: Math.round(Math.max(96, (h - height) / 2 - 20)), w: width, h: height };
    const style = this.phoneFrame.style;
    const key = `${rect.x},${rect.y},${rect.w},${rect.h}`;
    if (this.phoneFrame.dataset.rect !== key) {
      this.phoneFrame.dataset.rect = key;
      Object.assign(style, { left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.w}px`, height: `${rect.h}px` });
      const app = this.container.closest<HTMLElement>('.app') ?? this.container;
      app.style.setProperty('--phone-cx', `${rect.x + rect.w / 2}px`);
      app.style.setProperty('--phone-bottom', `${h - rect.y - rect.h}px`);
      app.style.setProperty('--phone-w', `${rect.w}px`);
      app.style.setProperty('--phone-left', `${rect.x}px`);
      app.style.setProperty('--phone-top', `${rect.y}px`);
      app.style.setProperty('--phone-h', `${rect.h}px`);
    }
    return rect;
  }

  private resizeCameraOnly() {
    const { clientWidth: w, clientHeight: h } = this.container;
    const inset = Math.min(this.insetLeft, w / 2);
    this.camera.aspect = (w + inset) / h;
    if (inset) this.camera.setViewOffset(w + inset, h, 0, 0, w, h);
    else this.camera.clearViewOffset();
    this.camera.updateProjectionMatrix();
  }

  private tick = (now: number) => {
    this.stepGuidanceCamera(now);
    if (this.fly) {
      const t = Math.min(1, (now - this.fly.start) / this.fly.duration);
      const k = easeInOut(t);
      this.camera.position.lerpVectors(this.fly.fromPos, this.fly.toPos, k);
      this.controls.target.lerpVectors(this.fly.fromTarget, this.fly.toTarget, k);
      if (t >= 1) this.fly = null;
      this.needsRender = true;
    }
    const pov = this.look.view === 'pov' && this.walkerPose !== null;
    if (this.controls.update() || this.needsRender || pov) {
      this.needsRender = false;
      if (pov) this.renderPov();
      else {
        this.renderer.render(this.scene, this.camera);
        this.cullRoomLabels();
        this.labelRenderer.render(this.scene, this.camera);
      }
    }
  };

  /**
   * Show a room's pill only when the room looks wider on screen than the pill itself, so a
   * distant overview (and small phone screens) isn't buried under overlapping labels.
   * Every room is measured at the orbit target's distance, so the choice follows room size
   * (not which rooms happen to be nearer the camera); bigger rooms claim their spot first,
   * overlapping pills are dropped, and a small hysteresis stops pills flickering while orbiting.
   */
  private cullRoomLabels() {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (!h) return;
    const pxPerUnit = h / 2 / Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2);
    const margin = w < 640 ? 2.6 : 2;
    const dist = Math.max(1e-3, this.camera.position.distanceTo(this.controls.target));
    const labels: THREE.Object3D[] = [];
    for (const fv of this.floors) if (fv.labels.visible) labels.push(...fv.labels.children);
    labels.sort((a, b) => (b.userData.roomSize ?? 0) - (a.userData.roomSize ?? 0));
    const placed: number[][] = [];
    const at = new THREE.Vector3();
    for (const label of labels) {
      const { roomSize, pillPx } = label.userData as { roomSize?: number; pillPx?: number };
      if (!roomSize || !pillPx) continue;
      const needed = pillPx * margin * (label.visible ? 0.85 : 1);
      let show = (roomSize / dist) * pxPerUnit >= needed;
      if (show) {
        label.getWorldPosition(at).project(this.camera);
        const x = ((at.x + 1) / 2) * w;
        const y = ((1 - at.y) / 2) * h;
        const box = [x - pillPx / 2 - 2, y - 12, x + pillPx / 2 + 2, y + 12];
        show =
          at.z < 1 &&
          !placed.some((p) => box[0] < p[2] && box[2] > p[0] && box[1] < p[3] && box[3] > p[1]);
        if (show) placed.push(box);
      }
      label.visible = show;
    }
  }

  // ---------------------------------------------------------------- picking

  private pick(e: PointerEvent): THREE.Object3D | null {
    return this.pickHit(e)?.obj ?? null;
  }

  private pickHit(e: PointerEvent): { obj: THREE.Object3D; point: THREE.Vector3 } | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const [px, py] = [e.clientX - rect.left, e.clientY - rect.top];
    const pov = this.look.view === 'pov' && this.walkerPose ? this.povRect : null;
    const inPov = !!pov && px >= pov.x && px <= pov.x + pov.w && py >= pov.y && py <= pov.y + pov.h;
    if (inPov && pov) {
      // First person: through the eye camera, and nothing behind a wall (walls are not pickable
      // themselves, so the nearest wall hit limits the pick distance).
      const ndc = new THREE.Vector2(((px - pov.x) / pov.w) * 2 - 1, -((py - pov.y) / pov.h) * 2 + 1);
      this.raycaster.setFromCamera(ndc, this.povCamera);
    } else {
      const ndc = new THREE.Vector2((px / rect.width) * 2 - 1, -(py / rect.height) * 2 + 1);
      this.raycaster.setFromCamera(ndc, this.camera);
    }
    const wallAt = inPov ? this.nearestWallHit() : Infinity;
    const hits = this.raycaster.intersectObjects([this.root, this.connectors], true);
    for (const hit of hits) {
      if (hit.distance > wallAt + 0.05) break;
      let visible = true;
      for (let o: THREE.Object3D | null = hit.object; o; o = o.parent) visible &&= o.visible;
      if (!visible || isGhost(hit.object)) continue;
      if (this.glb) return { obj: hit.object, point: hit.point };
      for (let o: THREE.Object3D | null = hit.object; o && o !== this.root; o = o.parent) {
        if (o.userData.sel) return { obj: o, point: hit.point };
      }
    }
    return null;
  }

  /** Distance along the current ray to the first wall at full storey height (first person). */
  private nearestWallHit(): number {
    const found: THREE.Intersection[] = [];
    for (const fv of this.floors) {
      if (!fv.walls.visible) continue;
      const [scale, y] = [fv.walls.scale.y, fv.walls.position.y];
      if (fv.hq) {
        fv.walls.scale.y = 1;
        fv.walls.position.y = 0;
      }
      fv.walls.updateMatrixWorld(true);
      fv.walls.traverse((o) => {
        if (o instanceof THREE.Mesh) THREE.Mesh.prototype.raycast.call(o, this.raycaster, found);
      });
      if (fv.hq) {
        fv.walls.scale.y = scale;
        fv.walls.position.y = y;
        fv.walls.updateMatrixWorld(true);
      }
    }
    return found.reduce((best, h) => Math.min(best, h.distance), Infinity);
  }

  private handlePointerDown = (e: PointerEvent) => {
    this.downAt = [e.clientX, e.clientY];
    if (!this.editable || this.hq || this.glb || e.button !== 0) return;
    const obj = this.pick(e);
    if (!obj || !DRAGGABLE.has((obj.userData.sel as Selection | undefined)?.kind ?? '')) return;
    const base = obj.getWorldPosition(new THREE.Vector3());
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -base.y);
    const grabbed = this.raycaster.ray.intersectPlane(plane, new THREE.Vector3());
    if (!grabbed) return;
    const sel = obj.userData.sel as Selection;
    const item = sel.kind === 'asset' && this.model ? startItemDrag(this.model, sel.id) : null;
    this.drag = {
      obj,
      plane,
      offset: base.sub(grabbed),
      start: [e.clientX, e.clientY],
      moved: false,
      item,
      startSpace: item?.spaceId ?? null,
    };
    this.controls.enabled = false;
    this.renderer.domElement.setPointerCapture(e.pointerId);
  };

  private moveDrag(e: PointerEvent) {
    const d = this.drag!;
    if (!d.moved && Math.hypot(e.clientX - d.start[0], e.clientY - d.start[1]) < 4) return;
    if (!d.moved) {
      d.moved = true;
      this.pin.visible = this.hoverTip.visible = false;
      this.clearHighlight();
      this.renderer.domElement.style.cursor = 'grabbing';
    }
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.raycaster.setFromCamera(
      new THREE.Vector2(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1,
      ),
      this.camera,
    );
    const hit = this.raycaster.ray.intersectPlane(d.plane, new THREE.Vector3());
    if (!hit) return;
    const local = d.obj.parent!.worldToLocal(hit.add(d.offset));
    let [x, y] = [local.x, -local.z];
    if (d.item && this.model) {
      const sel = d.obj.userData.sel as Selection;
      const from: Point = [d.obj.position.x, -d.obj.position.z];
      const result = constrainItemMove(this.model, sel.id, d.item, from, [x, y]);
      [x, y] = result.position;
      d.item = result.state;
      d.rotation = result.rotation;
      d.obj.rotation.y = THREE.MathUtils.degToRad(result.rotation);
    }
    d.obj.position.x = x;
    d.obj.position.z = -y;
    if (d.item) this.showDragRoom(d);
    this.needsRender = true;
  }

  /** While an item is dragged into another room, name that room above it. */
  private showDragRoom(d: Drag) {
    const changed = d.item && d.item.spaceId !== d.startSpace;
    const name = changed ? this.model?.spaces.find((s) => s.id === d.item!.spaceId)?.name : null;
    this.hoverTip.visible = !!name;
    if (!name) return;
    const box = new THREE.Box3().setFromObject(d.obj);
    this.hoverTip.position.set((box.min.x + box.max.x) / 2, box.max.y, (box.min.z + box.max.z) / 2);
    this.hoverTip.element.textContent = `→ ${name}`;
  }

  /** Finish a drag; `commit` reports the new position (dataset coordinates). */
  private endDrag(commit: boolean): boolean {
    const d = this.drag;
    if (!d) return false;
    this.drag = null;
    this.controls.enabled = true;
    this.renderer.domElement.style.cursor = '';
    if (!d.moved) return false;
    this.downAt = null;
    const sel = d.obj.userData.sel as Selection;
    if (commit) {
      // Keep it selected through the rebuild the move triggers, without flying to it.
      if (this.selection) this.restoreMaterial(this.lookup(this.selection));
      this.selection = sel;
      this.noFly = selKey(sel);
      this.onMove(
        sel,
        [d.obj.position.x, -d.obj.position.z],
        d.item ? { spaceId: d.item.spaceId, rotation: d.rotation } : undefined,
      );
      this.onSelect(sel);
    }
    this.needsRender = true;
    return true;
  }

  private handlePointerCancel = () => {
    const d = this.drag;
    this.endDrag(false);
    // Put it back: the model was not changed.
    if (d?.moved && this.model) this.setModel(this.model);
  };

  /** The graph overlay is drawn on top, so it wins over the building underneath. */
  private pickGraph(e: PointerEvent): THREE.Intersection | null {
    if (!this.graph.children.length) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(ndc, this.camera);
    this.raycaster.params.Line.threshold = 0.3;
    const hits = this.raycaster.intersectObjects(this.graph.children, false);
    // Prefer a dot over the lines that meet at it.
    return hits.find((h) => h.instanceId !== undefined) ?? hits[0] ?? null;
  }

  private showEdge(hit: THREE.Intersection) {
    const e = this.graphData?.edges[this.graphSegments[(hit.index ?? 0) / 2]];
    if (!e) return;
    const name = e.name ?? `${e.kind} ${e.element.split('-').pop()}`;
    const time = e.cost_m == null ? 'time unknown' : `~${Math.round(e.cost_m / WALK_SPEED)} s · ${e.cost_m.toFixed(1)} m`;
    this.edgeTip.element.textContent = `${name}${e.closed ? ' (closed)' : ''}: ${time}`;
    this.edgeTip.position.copy(hit.point);
    this.edgeTip.visible = true;
    this.needsRender = true;
  }

  private handlePointerUp = (e: PointerEvent) => {
    if (this.endDrag(true)) return;
    if (!this.downAt || Math.hypot(e.clientX - this.downAt[0], e.clientY - this.downAt[1]) > 6) return;
    this.downAt = null;
    this.edgeTip.visible = false;
    this.needsRender = true;
    const graphHit = this.pickGraph(e);
    if (graphHit?.instanceId !== undefined) {
      const id = this.graphDotIds[graphHit.instanceId];
      if (this.objects.has(`space:${id}`)) this.onSelect({ kind: 'space', id });
      return;
    }
    if (graphHit) return this.showEdge(graphHit);
    const hit = this.pickHit(e);
    const obj = hit?.obj ?? null;
    if (obj && this.glb) {
      this.registerGlbObject(obj);
      this.onSelect(obj.userData.sel, this.glbInfo(obj));
      return;
    }
    this.onSelect(
      obj ? (obj.userData.sel as Selection) : null,
      undefined,
      hit ? { position: [hit.point.x, -hit.point.z] } : undefined,
    );
  };

  private handlePointerMove = (e: PointerEvent) => {
    if (this.drag) return this.moveDrag(e);
    if (e.pointerType !== 'mouse' || e.buttons) return;
    this.setHovered(this.pick(e));
  };

  private setHovered(obj: THREE.Object3D | null) {
    if (obj === this.hovered) return;
    const isSelected = (o: THREE.Object3D) =>
      (this.selection && this.objects.get(selKey(this.selection)) === o) || this.litAssets.includes(o);
    if (this.hovered && !isSelected(this.hovered)) this.restoreMaterial(this.hovered);
    this.hovered = obj;
    const draggable = this.editable && !this.hq && !this.glb && DRAGGABLE.has(obj?.userData.sel?.kind);
    this.renderer.domElement.style.cursor = draggable ? 'grab' : obj ? 'pointer' : '';
    if (obj && !isSelected(obj) && !this.glb) this.paint(obj, this.hoverMat);
    // Selected or highlighted things already carry the pin/colour, so no second pill over them.
    const marked = !!obj && (isSelected(obj) || this.highlightIds.includes(obj.userData.sel?.id));
    const title = obj && !marked && (obj.userData.title ?? obj.name);
    this.hoverTip.visible = !!title;
    if (obj && title) {
      const box = new THREE.Box3().setFromObject(obj);
      this.hoverTip.position.set((box.min.x + box.max.x) / 2, box.max.y, (box.min.z + box.max.z) / 2);
      this.hoverTip.element.textContent = title;
    }
    this.needsRender = true;
  }

  private paint(obj: THREE.Object3D, material: THREE.Material) {
    obj.traverse((o) => {
      if (o instanceof THREE.Mesh) o.material = material;
    });
  }

  private restoreMaterial(obj: THREE.Object3D | undefined) {
    obj?.traverse((o) => {
      if (o instanceof THREE.Mesh && o.userData.baseMaterial) o.material = o.userData.baseMaterial;
    });
  }

  private clearHighlight() {
    this.highlight.traverse((o) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
        o.geometry.dispose();
        (o.material as THREE.Material).dispose();
      }
    });
    this.highlight.clear();
  }

  /**
   * A selected room: a translucent column over its footprint up to wall height, outlined
   * on top of everything, so it stands out even under furniture. Returns the column's top.
   */
  private addRoomVolume(spaceId: string, box: THREE.Box3): number {
    const space = this.model?.spaces.find((s) => s.id === spaceId);
    const fv = this.floors.find((f) => f.floor.id === space?.floor_id);
    if (!space || !fv || space.polygon.length < 3) {
      this.addBoxOutline(box);
      return box.max.y;
    }
    const height = Math.max(0.6, Math.min(this.settings.wallHeight, fv.floor.height_m - SLAB));
    const shape = new THREE.Shape(space.polygon.map(([x, y]) => new THREE.Vector2(x, y)));
    const geo = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false });
    geo.rotateX(-Math.PI / 2);
    const volume = new THREE.Mesh(
      geo,
      new THREE.MeshBasicMaterial({
        color: SELECT,
        transparent: true,
        opacity: 0.16,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    );
    volume.raycast = () => {};
    const outline = new THREE.LineSegments(
      new THREE.EdgesGeometry(geo, 30),
      new THREE.LineBasicMaterial({ color: SELECT, depthTest: false, transparent: true, opacity: 0.9 }),
    );
    outline.renderOrder = 5;
    // The slab's top is the floor surface, whichever floor offset (explode) applies.
    volume.position.y = outline.position.y = box.max.y;
    this.highlight.add(volume, outline);
    return box.max.y + height;
  }

  /** A box around a selected door, drawn through walls so it can't hide. */
  private addBoxOutline(box: THREE.Box3) {
    const padded = box.clone().expandByScalar(0.08);
    const size = padded.getSize(new THREE.Vector3());
    const cube = new THREE.BoxGeometry(size.x, size.y, size.z);
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(cube),
      new THREE.LineBasicMaterial({ color: SELECT, depthTest: false, transparent: true, opacity: 0.9 }),
    );
    cube.dispose();
    edges.renderOrder = 5;
    padded.getCenter(edges.position);
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
