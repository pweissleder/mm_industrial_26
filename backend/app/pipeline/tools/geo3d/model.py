"""Export a built scene as GLB plus a node index (node name -> extras) next to it."""

import json
import struct
from pathlib import Path
from typing import Any

import trimesh


def export_model(
    scene: trimesh.Scene, index: dict[str, dict[str, Any]], out_dir: Path, name: str = "model"
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    glb_path = out_dir / f"{name}.glb"
    index_path = out_dir / f"{name}_index.json"
    data = scene.export(file_type="glb")
    glb_path.write_bytes(data)
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True))
    return glb_path, index_path


def read_gltf_json(glb_path: Path) -> dict[str, Any]:
    """The JSON chunk of a GLB (node names, matrices and extras exactly as written)."""
    data = glb_path.read_bytes()
    magic, _version, _length = struct.unpack("<4sII", data[:12])
    if magic != b"glTF":
        raise ValueError(f"{glb_path} is not a GLB file")
    chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
    if chunk_type != b"JSON":
        raise ValueError(f"{glb_path}: first chunk is not JSON")
    parsed: dict[str, Any] = json.loads(data[20 : 20 + chunk_len])
    return parsed


def load_glb(glb_path: Path) -> trimesh.Scene:
    scene = trimesh.load(str(glb_path), file_type="glb", process=False)
    if not isinstance(scene, trimesh.Scene):
        scene = trimesh.Scene(scene)
    return scene
