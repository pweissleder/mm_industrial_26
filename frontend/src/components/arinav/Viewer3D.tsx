import { useEffect, useRef, useState } from 'react';
import type { RoomGraphData, RouteResponse } from '../../lib/api';
import type { BuildingModel, Selection } from '../../lib/building';
import { BuildingScene, type PersonMarker, type PickHit } from './scene';
import type { PoseStore } from './useGuidance';
import type { LookStore } from './useImmersive';
import type { CameraAction, NavMode, ObjectInfo, ViewSettings } from './view';

export interface CameraCommand {
  action: CameraAction;
  nonce: number;
}

/** A route to draw; a new `nonce` flies the camera to it. */
export interface RouteView {
  route: RouteResponse;
  fit: boolean;
  nonce: number;
}

/** Guidance camera: follow the walker, building overview, or a one-off route preview. */
export interface GuidanceCameraCommand {
  action: 'follow' | 'free' | 'overview' | 'preview' | 'cancel_preview';
  nonce: number;
  route?: RouteResponse;
  durationMs?: number;
}

const NONE: readonly string[] = [];
const NO_PEOPLE: readonly PersonMarker[] = [];

interface Props {
  model: BuildingModel | null;
  /** The pipeline's model.glb for `model`; shown instead of the generated preview. */
  hqUrl: string | null;
  /** An uploaded GLB, shown as is (wins over both). */
  glbUrl: string | null;
  /** Nav-graph overlay, drawn when the graph layer is on. */
  graph: RoomGraphData | null;
  settings: ViewSettings;
  selection: Selection | null;
  camera: CameraCommand | null;
  navMode: NavMode;
  /** Width (px) of a panel docked over the left edge; the view centres beside it. */
  insetLeft: number;
  onSelect: (selection: Selection | null, info?: ObjectInfo, hit?: PickHit) => void;
  route?: RouteView | null;
  /** Element ids to highlight (assistant), blame (route diagnosis) and show as closed. */
  highlight?: readonly string[];
  diagnosis?: readonly string[];
  closed?: readonly string[];
  /** The simulated person (Phase B guidance), updated per frame outside React rendering. */
  walker?: PoseStore | null;
  guidanceCamera?: GuidanceCameraCommand | null;
  /** The route preview finished or was skipped. */
  onPreviewDone?: () => void;
  /** POV camera state (per frame, outside React rendering). */
  look?: LookStore | null;
  /** Developer split view: POV left, building right. */
  split?: boolean;
  /** People in this building (multi-user presence), you included. */
  people?: readonly PersonMarker[];
  /** A person chosen in the list: selected and zoomed to (`nonce` repeats the zoom). */
  focusPerson?: { id: string; nonce: number } | null;
}

export default function Viewer3D({
  model,
  hqUrl,
  glbUrl,
  graph,
  settings,
  selection,
  camera,
  navMode,
  insetLeft,
  onSelect,
  route = null,
  highlight = NONE,
  diagnosis = NONE,
  closed = NONE,
  walker = null,
  guidanceCamera = null,
  onPreviewDone,
  look = null,
  split = false,
  people = NO_PEOPLE,
  focusPerson = null,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const scene = useRef<BuildingScene | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const onPreviewDoneRef = useRef(onPreviewDone);
  onPreviewDoneRef.current = onPreviewDone;
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!container.current) return;
    try {
      scene.current = new BuildingScene(container.current, settings, (s, i, h) =>
        onSelectRef.current(s, i, h),
      );
    } catch {
      setError('3D view needs WebGL, which this browser does not provide.');
    }
    if (import.meta.env.DEV) (window as unknown as { __arinavScene?: unknown }).__arinavScene = scene.current;
    return () => {
      scene.current?.dispose();
      scene.current = null;
    };
    // The scene is created once; later prop changes go through the effects below.
  }, []);

  useEffect(() => {
    if (!scene.current) return;
    const current = scene.current;
    setError(null);
    if (glbUrl) {
      current.setGlb(glbUrl).catch(() => setError('Could not load this 3D model.'));
    } else if (hqUrl && model) {
      current.setHq(hqUrl, model).catch(() => {
        setError('Could not load the high-quality model; showing the preview.');
        current.setModel(model);
      });
    } else {
      current.setModel(model);
    }
  }, [model, hqUrl, glbUrl]);

  useEffect(() => scene.current?.setInsetLeft(insetLeft), [insetLeft]);
  useEffect(() => scene.current?.setSettings(settings), [settings]);
  useEffect(() => scene.current?.setGraph(graph), [graph]);
  useEffect(() => scene.current?.setSelection(selection), [selection]);
  useEffect(() => scene.current?.setNavMode(navMode), [navMode]);
  useEffect(() => {
    if (camera) scene.current?.camera3d(camera.action);
  }, [camera]);
  // After the model effect above, so a new building is in place before its route is drawn.
  useEffect(() => scene.current?.setRoute(route?.route ?? null, route?.fit ?? false), [route]);
  useEffect(() => scene.current?.setHighlight(highlight), [highlight]);
  useEffect(() => scene.current?.setDiagnosis(diagnosis), [diagnosis]);
  useEffect(() => scene.current?.setClosed(closed), [closed]);
  useEffect(() => {
    const current = scene.current;
    if (!current || !walker) return;
    current.setWalker(walker.get());
    return walker.subscribe(() => current.setWalker(walker.get()));
  }, [walker, model]);
  useEffect(() => {
    const current = scene.current;
    if (!current || !look) return;
    current.setLook(look.get());
    return look.subscribe(() => current.setLook(look.get()));
  }, [look]);
  useEffect(() => scene.current?.setSplit(split), [split]);
  useEffect(() => scene.current?.setPeople([...people]), [people, model]);
  useEffect(() => scene.current?.focusPerson(focusPerson?.id ?? null, !!focusPerson), [focusPerson]);
  useEffect(() => {
    const current = scene.current;
    if (!current || !guidanceCamera) return;
    const c = guidanceCamera;
    if (c.action === 'follow') current.setFollow(true);
    else if (c.action === 'free') current.setFollow(false);
    else if (c.action === 'overview') current.overview();
    else if (c.action === 'cancel_preview') current.cancelPreview();
    else if (c.action === 'preview' && c.route) {
      current.startPreview(c.route, c.durationMs ?? 6000, () => onPreviewDoneRef.current?.());
    }
  }, [guidanceCamera]);

  return (
    <div className="viewer" ref={container} aria-label="3D building view" role="img">
      {error && <p className="viewer__error">{error}</p>}
    </div>
  );
}
