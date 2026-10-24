"""Fixtures for the navigation core tests.

Default tests use the in-repo dataset (backend/data/datasets/proc-factory-dev-003), ingested through
the real ingestion path into an InMemoryBuildingRepository. Tests marked `kit` need the official
kit 1.1.0 sources (SYNERA_REFERENCE_DIR checkout + SYNERA_KIT_ZIP) and skip cleanly without them.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.config import BACKEND_DIR
from app.ingestion.service import ingest_dataset
from app.navigation import NavigationService, kit
from app.storage import InMemoryBuildingRepository

DATASETS_DIR = BACKEND_DIR / "data" / "datasets"
DATASET = "proc-factory-dev-003"
BID = DATASET
P = BID + "-"


def space(suffix: str) -> dict[str, Any]:
    """Endpoint of a space of the sample building by id suffix (e.g. 'F0-S021')."""
    return {"kind": "space", "id": P + suffix}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "kit: needs the official kit 1.1.0 sources (SYNERA_REFERENCE_DIR, SYNERA_KIT_ZIP); "
        "skipped when they are missing",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    kit_items = [item for item in items if item.get_closest_marker("kit")]
    if not kit_items:
        return
    checks = (kit.questions_available(), kit.buildings_available())
    if all(ok for ok, _ in checks):
        return
    reasons = [reason for ok, reason in checks if not ok]
    skip = pytest.mark.skip(reason="kit 1.1.0 sources missing: " + "; ".join(reasons))
    for item in kit_items:
        item.add_marker(skip)


def make_service(datasets_dir: Path | None = DATASETS_DIR) -> NavigationService:
    repository = InMemoryBuildingRepository()
    ingest_dataset(repository, DATASETS_DIR, DATASET)
    return NavigationService(repository, datasets_dir)


@pytest.fixture(scope="session")
def nav() -> NavigationService:
    """Shared service for read-only tests (never change its scenario)."""
    return make_service()


@pytest.fixture
def fresh_nav() -> NavigationService:
    """A private service for tests that change the scenario."""
    return make_service()


@pytest.fixture(scope="session")
def building(nav: NavigationService) -> dict[str, Any]:
    return nav.building(BID)


@pytest.fixture(scope="session")
def route(nav: NavigationService) -> Callable[..., dict[str, Any]]:
    """`route(start, target, **request)` on the shared service, official mode by default."""

    def call(start: dict[str, Any], target: dict[str, Any], **request: Any) -> dict[str, Any]:
        return nav.route(BID, {"mode": "official", "start": start, "target": target, **request})

    return call
