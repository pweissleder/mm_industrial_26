from pathlib import Path

import numpy as np
import pytest

from app.pipeline.tools.geo3d.spec import LibraryEntry, spec_to_mesh, validate_spec

SEED_DIR = Path(__file__).resolve().parents[2] / "data/asset_library/seed"
FILES = sorted(SEED_DIR.glob("*.json"))


def test_seed_count() -> None:
    assert len(FILES) >= 30


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_seed_entry(path: Path) -> None:
    entry = LibraryEntry.model_validate_json(path.read_text())
    assert path.stem == entry.spec.type
    assert entry.provenance == "seed"
    assert validate_spec(entry.spec) == []
    mesh = spec_to_mesh(entry.spec)
    assert len(mesh.vertices) > 0 and len(mesh.faces) > 0
    lo, hi = mesh.bounds
    assert (lo >= np.array([-0.5, -0.5, 0.0]) - 1e-6).all()
    assert (hi <= np.array([0.5, 0.5, 1.0]) + 1e-6).all()
