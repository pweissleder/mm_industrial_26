"""Coordinate frames: the dataset is z-up metres, glTF is y-up metres.

There is exactly one conversion, used everywhere: dataset (x, y, z) -> glTF (x, z, -y).
Geometry is built in the dataset frame and converted once with `to_gltf_matrix()`;
asset/opening node matrices are built with `asset_matrix` and read back with
`decompose_asset_matrix`.

Asset convention (from the old Blender pipeline): a unit-box mesh with its origin at the
bottom centre of the footprint is scaled to width x depth x height, rotated
`rotation_deg` counter-clockwise about +z (so the asset's local +x, its width, points at
(cos t, sin t) in dataset XY), and translated to (x, y, floor elevation).
"""

import math

import numpy as np

# Dataset -> glTF basis change (rotation of -90 deg about +x).
_C = np.array(
    [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, -1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
)
_C_INV = _C.T.copy()


def to_gltf_matrix() -> np.ndarray:
    """4x4 matrix taking dataset-frame points (z up) to glTF-frame points (y up)."""
    return _C.copy()


def from_gltf_matrix() -> np.ndarray:
    """4x4 matrix taking glTF-frame points back to the dataset frame."""
    return _C_INV.copy()


def to_gltf_points(points: np.ndarray) -> np.ndarray:
    """(n, 3) dataset points -> (n, 3) glTF points."""
    p = np.asarray(points, dtype=np.float64)
    return np.column_stack([p[:, 0], p[:, 2], -p[:, 1]])


def from_gltf_points(points: np.ndarray) -> np.ndarray:
    """(n, 3) glTF points -> (n, 3) dataset points."""
    p = np.asarray(points, dtype=np.float64)
    return np.column_stack([p[:, 0], -p[:, 2], p[:, 1]])


def dataset_asset_matrix(
    x: float, y: float, z: float, width: float, depth: float, height: float, rotation_deg: float
) -> np.ndarray:
    """4x4 dataset-frame transform T * Rz * S for a unit-box mesh in the dataset frame."""
    t = math.radians(rotation_deg)
    c, s = math.cos(t), math.sin(t)
    return np.array(
        [
            [c * width, -s * depth, 0.0, x],
            [s * width, c * depth, 0.0, y],
            [0.0, 0.0, height, z],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def asset_matrix(
    x: float, y: float, z: float, width: float, depth: float, height: float, rotation_deg: float
) -> np.ndarray:
    """4x4 glTF-frame node transform for a unit-box mesh already converted to the glTF frame.

    Equals C @ T_dataset @ C^-1, so applying it to (C @ unit_mesh) gives C @ T_dataset @ unit_mesh.
    """
    return _C @ dataset_asset_matrix(x, y, z, width, depth, height, rotation_deg) @ _C_INV


def decompose_asset_matrix(
    matrix: np.ndarray,
) -> tuple[float, float, float, float, float, float, float]:
    """Inverse of `asset_matrix`: (x, y, z, width, depth, height, rotation_deg in [0, 360))."""
    m = _C_INV @ np.asarray(matrix, dtype=np.float64) @ _C
    x, y, z = (float(v) for v in m[:3, 3])
    col_w, col_d, col_h = m[:3, 0], m[:3, 1], m[:3, 2]
    width = float(np.linalg.norm(col_w))
    depth = float(np.linalg.norm(col_d))
    height = float(np.linalg.norm(col_h))
    rotation = math.degrees(math.atan2(col_w[1], col_w[0])) % 360.0
    return x, y, z, width, depth, height, rotation


def angle_diff_deg(a: float, b: float) -> float:
    """Smallest absolute difference between two angles in degrees."""
    d = (a - b) % 360.0
    return min(d, 360.0 - d)
