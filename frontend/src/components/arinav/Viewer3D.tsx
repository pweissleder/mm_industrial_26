import { useEffect, useRef, useState } from 'react';
import type { RouteResponse } from '../../lib/api';
import type { BuildingModel, Selection } from '../../lib/building';
import { BuildingScene, type PickHit } from './scene';
import type { CameraAction, ObjectInfo, ViewSettings } from './view';

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

const NONE: readonly string[] = [];

interface Props {
  model: BuildingModel | null;
  /** The pipeline's model.glb for `model`; shown instead of the generated preview. */
  hqUrl: string | null;
  /** An uploaded GLB, shown as is (wins over both). */
  glbUrl: string | null;
  settings: ViewSettings;
  selection: Selection | null;
  camera: CameraCommand | null;
  /** Width (px) of a panel docked over the left edge; the view centres beside it. */
  insetLeft: number;
  onSelect: (selection: Selection | null, info?: ObjectInfo, hit?: PickHit) => void;
  route?: RouteView | null;
  /** Element ids to highlight (assistant), blame (route diagnosis) and show as closed. */
  highlight?: readonly string[];
  diagnosis?: readonly string[];
  closed?: readonly string[];
}

export default function Viewer3D({
  model,
  hqUrl,
  glbUrl,
  settings,
  selection,
  camera,
  insetLeft,
  onSelect,
  route = null,
  highlight = NONE,
  diagnosis = NONE,
  closed = NONE,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const scene = useRef<BuildingScene | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
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
  useEffect(() => scene.current?.setSelection(selection), [selection]);
  useEffect(() => {
    if (camera) scene.current?.camera3d(camera.action);
  }, [camera]);
  // After the model effect above, so a new building is in place before its route is drawn.
  useEffect(() => scene.current?.setRoute(route?.route ?? null, route?.fit ?? false), [route]);
  useEffect(() => scene.current?.setHighlight(highlight), [highlight]);
  useEffect(() => scene.current?.setDiagnosis(diagnosis), [diagnosis]);
  useEffect(() => scene.current?.setClosed(closed), [closed]);

  return (
    <div className="viewer" ref={container} aria-label="3D building view" role="img">
      {error && <p className="viewer__error">{error}</p>}
    </div>
  );
}
