# Blender building model

Builds a reproducible, to-scale 3D model of a hackathon building from its `building.json`, headless via Blender's Python API. Scale is 1 Blender unit = 1 m; dataset x/y map 1:1 to Blender X/Y; Z is up.

Example building: `problem_statement/industrial-agents-hackathon-2026/data/dev/osm-way27326387` (TUM 4107 Institutsgebäude 3). Three floors at levels -2, 0, 1 with elevations -6, 0, 3 m. Level -1 is absent from the data, so the model has an empty 3 m storey between the basement and the ground floor. Contents: 61 spaces, 68 openings, 1 stair connector, 184 assets.

## Build

Run from the repository root. Writes the `.blend`, the `.glb` and the renders.

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b --python-exit-code 1 --python blender/build_building.py -- --building problem_statement/industrial-agents-hackathon-2026/data/dev/osm-way27326387 --out blender/out/osm-way27326387/model.blend --glb blender/out/osm-way27326387/model.glb --render blender/out/osm-way27326387/renders
```

Options: `--building` (directory containing `building.json`, required), `--out` (`.blend` path, required), `--glb` (optional GLB export), `--render` (optional render directory).

## Validate

Run after the build. Compares the `.blend` against the dataset and exits with status 1 on any failure.

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b blender/out/osm-way27326387/model.blend --python-exit-code 1 --python blender/validate_model.py -- --building problem_statement/industrial-agents-hackathon-2026/data/dev/osm-way27326387 --report blender/out/osm-way27326387/validation.json
```

Options: `--building` (required), `--report` (optional JSON output).

## Modelling conventions

- **Floors:** walking surface at `elevation_m`; each slab is 0.15 m thick below it. Walls run up to `height_m - 0.15` (underside of the next slab).
- **Spaces:** the dataset polygon is used verbatim for the floor mesh.
- **Walls:** derived from room outlines and inset 0.08 m into each room, so a shared wall is 2 x 0.08 m thick and centred on the shared edge. The source has no wall data.
- **Openings:** a gap of `width_m` is cut into the walls of the spaces it joins, with a lintel above 2.1 m (`DOOR_H`) for doors and entrances. Kind `opening` (no door leaf) gets no lintel. A thin threshold strip marks each opening.
- **Halls:** no wall between two `hall` spaces or between a hall and a corridor. These are zones of one open hall, so the edge is drawn as a yellow floor line.
- **Overlapping storeys:** walls stop under the slab of any higher floor that starts inside the storey and covers that stretch of wall (e.g. an 8 m factory hall with an office level at 3.5 m).
- **Assets:** one shared placeholder mesh `PH_Equipment_Unit` (unit box, origin at bottom centre) is linked into every asset. Each instance is scaled to `width_m` x `depth_m` x height, rotated by `rotation_deg` CCW about Z (width runs along the rotation direction), and placed at `(x, y, elevation_m)`.
- **Asset heights (assumed):** by type from `ASSET_HEIGHT` (about 40 types, e.g. desk 0.75, chair 0.9, shelf 2.0, pallet_rack 4.5, press_brake 3.0 m); otherwise by category from `CATEGORY_HEIGHT` (furniture 0.8, fixture 1.0, machine 1.8, equipment 1.2 m); fallback 1.0 m.
- **Colour key:** spaces by type, assets by category (furniture brown, fixture teal, machine blue, equipment red), doors brown, building entrance green, vertical connectors orange.
- **Vertical connectors:** a polyline through the stop centroids, 1.2 m above each floor's elevation.

## Scene structure

```
Building_<id>
├── Floor_<level>           custom props: floor_id, elevation_m
│   ├── F<level>_Spaces     space meshes
│   ├── F<level>_Walls      mesh "Walls F<level>"
│   ├── F<level>_Openings   openings
│   ├── F<level>_Assets     one object per asset
│   └── F<level>_Labels     room labels
└── Vertical_Connectors     polylines
```

Custom properties on objects (exported as glTF extras in `model.glb`):

- Spaces: `space_id`, `space_name`, `space_type`, `room_type`, `floor_level`, `area_m2`
- Openings: `opening_id`, `kind`, `width_m`, `space_ids`, `step_free`
- Assets: `asset_id`, `asset_type`, `category`, `space_id`, `floor_level`, `width_m`, `depth_m`, `height_m`, `rotation_deg`
- Connectors: `connector_id`, `kind`, `step_free`

## Outputs

Written to `blender/out/<building-id>/`:

- `model.blend`: Blender scene
- `model.glb`: glTF binary with custom properties as extras
- `renders/plan_floor_<level>.png`: top-down orthographic plan, same orientation as the dataset's `floor_<level>.png`
- `renders/view_floor_<level>.png`: perspective view of each floor alone
- `renders/view_building.png`: perspective view of the whole building
- `validation.json`: validation report

## Validation result

**PASS** on both test buildings, with every position and dimension within 1 mm and every rotation within 0.1°:

| Building | Assets | Spaces | Openings | Max position error |
|---|---|---|---|---|
| osm-way27326387 | 184 | 61 | 68 | 0.6 µm |
| proc-factory-dev-004 | 280 | 50 | 57 | 2.5 µm |

The validator checks existence and uniqueness (by custom property), position, height above the floor, rotation and footprint dimensions for assets, plus space polygon vertices. Asset positions outside their space polygon are reported as dataset warnings, not failures.

## Limitations

- Asset heights are assumed per type or category; the dataset provides footprints only.
- Walls are derived from room outlines; the source has no wall data.
- Stairs are not modelled. Only the stairwell space and the connector polyline are present.
