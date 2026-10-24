import { useEffect, useRef, useState } from 'react';
import type { RoomGraphData } from '../../lib/api';
import type { BuildingModel, Selection } from '../../lib/building';
import { BuildingScene } from './scene';
import type { CameraAction, ObjectInfo, ViewSettings } from './view';

export interface CameraCommand {
  action: CameraAction;
  nonce: number;
}

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
  /** Width (px) of a panel docked over the left edge; the view centres beside it. */
  insetLeft: number;
  onSelect: (selection: Selection | null, info?: ObjectInfo) => void;
}

export default function Viewer3D({
  model,
  hqUrl,
  glbUrl,
  graph,
  settings,
  selection,
  camera,
  insetLeft,
  onSelect,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const scene = useRef<BuildingScene | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!container.current) return;
    try {
      scene.current = new BuildingScene(container.current, settings, (s, i) =>
        onSelectRef.current(s, i),
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
  useEffect(() => scene.current?.setGraph(graph), [graph]);
  useEffect(() => scene.current?.setSelection(selection), [selection]);
  useEffect(() => {
    if (camera) scene.current?.camera3d(camera.action);
  }, [camera]);

  return (
    <div className="viewer" ref={container} aria-label="3D building view" role="img">
      {error && <p className="viewer__error">{error}</p>}
    </div>
  );
}
