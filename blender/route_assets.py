"""Shortest walking route between two assets, written as JSON for the web viewer.

Uses the hackathon route model (starter_agent/building_map.py): spaces are nodes at their
representative point, joined to their openings by straight lines; stairs/lifts cost per floor.
The two assets are added as extra nodes, joined to every opening of their own space, so the
route starts and ends at the assets instead of at the room anchors.

    uv run --with networkx --with shapely python blender/route_assets.py \
        --building problem_statement/industrial-agents-hackathon-2026/data/dev/osm-way27326387 \
        --from osm-way27326387-A00127 --to osm-way27326387-A00116 \
        --out blender/out/osm-way27326387/routes/A00127_A00116.json
"""

import argparse
import json
import math
import sys
from pathlib import Path

import networkx as nx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "problem_statement/industrial-agents-hackathon-2026"))
from starter_agent.building_map import DOOR_PREFIX, BuildingMap  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--building", type=Path, required=True)
    parser.add_argument("--from", dest="start", required=True, help="asset id")
    parser.add_argument("--to", dest="end", required=True, help="asset id")
    parser.add_argument("--step-free", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    building = json.loads((args.building / "building.json").read_text())
    bmap = BuildingMap(building)
    assets = {a["id"]: a for a in building["assets"]}
    floors = {f["id"]: f for f in building["floors"]}
    space_floor = {s["id"]: s["floor_id"] for s in building["spaces"]}

    graph = bmap.graph(args.step_free)
    for asset_id in (args.start, args.end):
        asset = assets[asset_id]
        for opening in building["openings"]:
            if asset["space_id"] in opening["space_ids"] and graph.has_node(DOOR_PREFIX + opening["id"]):
                distance = math.dist(asset["position"], opening["position"])
                graph.add_edge(asset_id, DOOR_PREFIX + opening["id"], weight=distance, via=opening["id"])

    length, nodes = nx.single_source_dijkstra(graph, args.start, args.end, weight="weight")

    # 3D polyline (dataset x, y, floor elevation). An opening sits on the floor of the space we came from.
    points, floor_id = [], assets[args.start]["floor_id"]
    for node in nodes:
        if node in assets:
            xy, floor_id = assets[node]["position"], assets[node]["floor_id"]
        elif node.startswith(DOOR_PREFIX):
            xy = bmap.openings[node.removeprefix(DOOR_PREFIX)]["position"]
        else:
            xy, floor_id = bmap.anchor(node), space_floor[node]
        points.append({"node": node, "x": xy[0], "y": xy[1], "z": floors[floor_id]["elevation_m"]})

    space_path = [n for n in nodes if n in bmap.spaces]
    room_to_room = bmap.route(assets[args.start]["space_id"], assets[args.end]["space_id"], args.step_free)
    result = {
        "from": {"asset_id": args.start, "name": assets[args.start]["name"], "space_id": assets[args.start]["space_id"]},
        "to": {"asset_id": args.end, "name": assets[args.end]["name"], "space_id": assets[args.end]["space_id"]},
        "step_free": args.step_free,
        "length_m": round(length, 2),
        "room_to_room_length_m": room_to_room.length_m,
        "element_path": [n.removeprefix(DOOR_PREFIX) for n in nodes if n.startswith(DOOR_PREFIX)],
        "space_path": [assets[args.start]["space_id"], *space_path, assets[args.end]["space_id"]],
        "spaces": {s: bmap.spaces[s]["name"] for s in {assets[args.start]["space_id"], *space_path, assets[args.end]["space_id"]}},
        "points": points,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print(f"{result['from']['name']} -> {result['to']['name']}: {result['length_m']} m "
          f"(room anchor to room anchor: {result['room_to_room_length_m']} m)")
    print(" -> ".join(result["spaces"][s] for s in result["space_path"]))
    print("doors:", ", ".join(result["element_path"]))


if __name__ == "__main__":
    main()
