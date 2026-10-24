"""scripts/nav_benchmark.py: the harness logic offline (synthetic questions on the in-repo building)
and, with the kit sources (`kit` marker), regression samples against the kit 1.1.0 answers."""

import copy
from collections.abc import Iterator
from typing import Any

import pytest

from app.domain.building import BuildingFile
from app.navigation import NavigationService, kit
from app.navigation.official import building_map as official_map
from scripts import nav_benchmark as harness
from tests.navigation.conftest import BID, P

# One building per source family plus a campus (prototype's OfficialWarmupRegressionTests).
SAMPLE_BUILDINGS = (
    "proc-factory-dev-003",
    "proc-campus-dev-009",
    "osm-way22744230",
    "osm-way27326387",
    "resplan-14509",
    "resplan-1677",
    "swiss-1785",
    "swiss-2211",
)


class LocalBuildings(harness.KitBuildings):
    """KitBuildings stand-in serving the in-repo building through an existing service."""

    def __init__(self, nav: NavigationService) -> None:
        self.service = nav
        self.repository = nav.repository  # type: ignore[assignment]

    def ensure(self, building_id: str) -> str:
        return building_id

    def source(self, building_id: str) -> str | None:
        return "procedural"


def official_answer(building: dict[str, Any], stops: list[str], **kw: Any) -> dict[str, Any]:
    bmap = official_map.BuildingMap(copy.deepcopy(building))
    for element in kw.get("closed", []):
        bmap.set_closed(element, True)
    try:
        result = bmap.route(stops[0], stops[-1], kw.get("step_free", False), via=stops[1:-1])
    except official_map.MapError:
        return {"reachable": False}
    return {"space_path": result.space_path, "optimal_length_m": result.length_m}


def question(kind: str, answer: dict[str, Any], qid: str = "q1") -> dict[str, Any]:
    return {"id": qid, "building_id": BID, "kind": kind, "question": "", "answer": answer}


def test_route_request_shapes() -> None:
    q = question(
        "route_with_closure",
        {"from_space_id": "a", "to_space_id": "b", "closed_element_id": "d"},
    )
    assert harness.route_request(q) == {
        "mode": "official",
        "start": {"kind": "space", "id": "a"},
        "target": {"kind": "space", "id": "b"},
        "step_free": False,
        "assume_closed": ["d"],
    }
    tour = harness.route_request(question("tour", {"stops": ["a", "b", "c"]}))
    assert tour["via"] == [{"kind": "space", "id": "b"}] and tour["target"]["id"] == "c"
    sf = harness.route_request(
        question("route_step_free", {"from_space_id": "a", "to_space_id": "b"})
    )
    assert sf["step_free"] is True and sf["assume_closed"] == []


def test_run_route_checks_paths_lengths_and_reachability(
    nav: NavigationService, building: dict[str, Any]
) -> None:
    kb = LocalBuildings(nav)
    a, b = P + "F0-S021", P + "F1-S010"
    expected = official_answer(building, [a, b])
    routed = nav.route(
        BID, {"start": {"kind": "space", "id": a}, "target": {"kind": "space", "id": b}}
    )
    answer = {
        "from_space_id": a,
        "to_space_id": b,
        **expected,
        "element_path": routed["element_path"],
    }
    good = harness.run_route(kb, question("route", answer), "dev")
    assert good["ok"] and good["checks"]["length_equal_2dp"] and good["cause"] is None
    off = harness.run_route(kb, question("route", {**answer, "optimal_length_m": 57.2}), "dev")
    assert (off["ok"], off["cause"]) == (False, "length_off_under_1pct")
    wrong = harness.run_route(kb, question("route", {**answer, "element_path": []}), "dev")
    assert wrong["cause"] == "different_element_path"
    unreachable = {"from_space_id": a, "to_space_id": b, "reachable": False}
    got_route = harness.run_route(kb, question("route_step_free", unreachable), "dev")
    assert got_route["cause"] == "expected_unreachable_got_route"
    closed = harness.run_route(
        kb,
        question("route_step_free", {**unreachable, "closed_element_id": P + "V02"}),
        "dev",
    )
    assert closed["ok"] and closed["checks"]["reachability_agrees"]


def test_run_locate_and_count(building: dict[str, Any]) -> None:
    oven = next(a for a in building["assets"] if a["type"] == "curing_oven")
    floors = {f["id"]: f["level"] for f in building["floors"]}
    located = harness.run_locate(
        building,
        {
            "id": "l1",
            "building_id": BID,
            "question": "Where is the curing oven?",
            "answer": {
                "asset_id": oven["id"],
                "space_id": oven["space_id"],
                "floor_level": floors[oven["floor_id"]],
            },
        },
        "dev",
        "procedural",
    )
    assert located["ok"], located
    ambiguous = harness.run_locate(
        building,
        {"id": "l2", "building_id": BID, "question": "Where is the chair?", "answer": {}},
        "dev",
        "procedural",
    )
    assert ambiguous["cause"] == "ambiguous_phrase"
    chairs = sum(a["type"] == "chair" and a["floor_id"] == P + "F0" for a in building["assets"])
    counted = harness.run_count(
        building,
        {
            "id": "c1",
            "building_id": BID,
            "question": "How many chair items are on the Ground floor?",
            "answer": {"count": chairs, "asset_type": "chair", "floor_level": 0},
        },
        "dev",
        "procedural",
    )
    assert counted["ok"] and counted["actual"]["count"] == 65


def test_summary_and_markdown_render() -> None:
    records = [
        {
            "section": "benchmark",
            "id": "b1",
            "split": "dev",
            "kind": "errand",
            "source": "x",
            "ok": True,
            "cause": None,
        },
        {
            "section": "warmup",
            "id": "w1",
            "split": "dev",
            "kind": "route",
            "source": "x",
            "ok": False,
            "cause": "different_space_path",
            "checks": {
                "expected_reachable": True,
                "reachability_agrees": True,
                "space_path": False,
                "element_path": True,
                "length_within_1pct": True,
                "length_within_0_01m": True,
                "length_equal_2dp": True,
                "length_error_m": 0.0,
            },
        },
    ]
    summary = harness.summarise(records)
    assert harness.headline(summary)["benchmark"] == {"ok": 1, "total": 1}
    assert summary["failures_by_cause"] == {"warmup:route:different_space_path": 1}
    report = {
        "splits": ["dev"],
        "sections": list(harness.SECTIONS),
        "kit": {"version": "1.1.0", "commit": "c"},
        "runtime_s": {"total": 1.0, "routes": 1.0},
        "headline": harness.headline(summary),
        "reference": harness.REFERENCE,
        "summary": summary,
        "failures": [r for r in records if not r["ok"]],
    }
    text = harness.markdown(report)
    assert "`w1` (route, x): different_space_path" in text
    assert "| benchmark | **1/1** | 655/655 |" in text


# ---------------------------------------------------------------- kit 1.1.0 data


@pytest.fixture(scope="module")
def kit_buildings() -> Iterator[harness.KitBuildings]:
    yield harness.KitBuildings()


@pytest.mark.kit
def test_kit_sources_are_complete_and_verified() -> None:
    assert len(kit.building_index()) == 240
    assert (len(kit.examples("dev")), len(kit.examples("train"))) == (138, 517)
    assert (len(kit.basic_examples("dev")), len(kit.basic_examples("train"))) == (369, 1761)
    for bid in SAMPLE_BUILDINGS:  # building.json blob verified + validates like ingestion
        BuildingFile.model_validate_json(kit.load_building_bytes(bid))


@pytest.mark.kit
def test_kit_sample_warmup_routes_exact(kit_buildings: harness.KitBuildings) -> None:
    rows = [
        q
        for q in kit.basic_examples("dev")
        if q["building_id"] in SAMPLE_BUILDINGS and q["kind"] in harness.ROUTE_KINDS
    ]
    assert len(rows) >= 40
    records = [harness.run_route(kit_buildings, q, "dev") for q in rows]
    failures = [(r["id"], r["cause"]) for r in records if not r["ok"]]
    assert failures == []
    checks = [r["checks"] for r in records]
    assert sum("space_path" in c for c in checks) > 30
    assert any(c["expected_reachable"] is False for c in checks)
    assert all(c.get("legs_length_equal_2dp", True) for c in checks)


@pytest.mark.kit
def test_kit_sample_benchmark_questions(kit_buildings: harness.KitBuildings) -> None:
    rows = [q for q in kit.examples("dev") if q["building_id"] in SAMPLE_BUILDINGS]
    assert len(rows) >= 15
    records = [harness.run_benchmark(kit_buildings, q, "dev") for q in rows]
    assert [(r["id"], r["cause"]) for r in records if not r["ok"]] == []


@pytest.mark.kit
def test_kit_dev_locate_and_count(kit_buildings: harness.KitBuildings) -> None:
    report, _ = harness.run(["dev"], ["locate", "count"], kb=kit_buildings)
    assert report["headline"]["locate"] == {"ok": 100, "total": 100}
    assert report["headline"]["count"] == {"ok": 40, "total": 40}
    assert report["failures"] == []
