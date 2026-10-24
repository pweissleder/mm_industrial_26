---
name: building-to-blender
description: Turn a hackathon building (a folder with building.json, equipment.csv and floor_<level>.png) into a to-scale 3D Blender model with every room, door, stair/lift and asset placed at its exact dataset coordinates, then validate it and render plan/3D views. Use this whenever the user wants a 3D model, Blender scene, .blend/.glb, 3D view, digital twin or "to scale" visualisation of a building or floor from the industrial-agents-hackathon data (ids like osm-way…, proc-campus-…, proc-factory-…, resplan-…, sd-…), or asks to place equipment/machines/furniture in 3D, even if they don't say "Blender".
---

# Building → to-scale Blender model

The repo already contains a deterministic, headless pipeline. Don't rebuild geometry by hand or in the Blender GUI. Run the scripts, check the numbers, look at the renders and report. This keeps every result reproducible and cheap: one build takes about 5 s, and renders take under a minute.

- `blender/build_building.py`: builds `model.blend`, with optional `model.glb` and renders.
- `blender/validate_model.py`: re-reads the `.blend` and compares every asset, space and opening against `building.json` (1 mm / 0.1° tolerance).
- `blender/README.md`: modelling conventions (wall inset, door gaps, placeholder heights, colour key, scene tree).

Paths below are relative to the repo root. `DATA=problem_statement/industrial-agents-hackathon-2026/data`, and `manifest.csv` in there lists every building with its split (`dev`/`train`/`test`), floors, levels and whether it has a lift.

## Workflow

### 1. Locate and sanity-check the building

Find the folder (`$DATA/<split>/<building_id>`), via `manifest.csv` if you only have a name. `building.json` has top-level keys `building`, `floors`, `spaces`, `openings`, `vertical_connectors` and `assets`. The schema is in section 3 of the kit's `README.md`. Print a quick summary with plain Python:
- floors (level, elevation_m, height_m),
- counts per key,
- space `type`/`room_type` counts,
- asset `type`/`category` counts.

Things to notice, and why each matters:
- **Missing levels** (e.g. −2, 0, 1) become a real empty storey in the model. That is correct, so mention it.
- **Overlapping storeys:** a floor's `elevation_m + height_m` can exceed the next floor's elevation, as in a double-height factory hall with an office level over part of it. The build script handles this by stopping each wall stretch under any higher slab that covers it, so just report it.
- **Asset heights:** the data only has footprints. Heights come from `ASSET_HEIGHT` (by type), then `CATEGORY_HEIGHT` (by category), then 1.0 m. List types missing from `ASSET_HEIGHT`. If one is common or obviously wrong at its category default (e.g. a 4 m pallet rack), add a realistic height to `ASSET_HEIGHT` before building.

### 2. Check Blender

`/Applications/Blender.app/Contents/MacOS/Blender --version` (macOS default; on Linux try `blender`). The scripts were written against 4.2 LTS and use only `bpy`/`mathutils` plus the stdlib.

### 3. Build

```bash
mkdir -p blender/out/<id>
/Applications/Blender.app/Contents/MacOS/Blender -b --python-exit-code 1 --python blender/build_building.py -- --building $DATA/<split>/<id> --out blender/out/<id>/model.blend --glb blender/out/<id>/model.glb --render blender/out/<id>/renders > blender/out/<id>/build.log 2>&1
```

Blender is chatty (glTF export logs every primitive), so redirect to a log file and print the exit code plus `grep "^built\|Error\|Traceback" build.log`. Success prints `built <id>: N spaces, N openings, N assets -> …`, with `Blender quit` after it. Big buildings (thousands of assets) take longer to render. Drop `--render` if the user only wants the model.

### 4. Validate

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b blender/out/<id>/model.blend --python-exit-code 1 --python blender/validate_model.py -- --building $DATA/<split>/<id> --report blender/out/<id>/validation.json > blender/out/<id>/validate.log 2>&1
```

Then `grep -A4 "^category" validate.log; grep RESULT validate.log`. Expect `RESULT: PASS` with max errors of a few µm (float32 mesh precision), far inside the 1 mm tolerance. Read the log directly rather than piping Blender into `tail`, because a pipe hides Blender's exit code.

If it fails, read the first failures before touching anything. A model failure means the build script is wrong. A dataset warning (asset outside its space polygon) is a property of the data, so report it but don't fix it. The validator matches objects by custom property, so if you add new object kinds that carry `space_id`, exclude them the way assets are excluded.

### 5. Look at the renders

Open `renders/plan_floor_<level>.png` next to the dataset's `floor_<level>.png` (same orientation, y up) and compare the room layout, labels, door positions and furniture clusters. Glance at `view_building.png` for floor stacking. Hall zones (`type: hall`, e.g. production_zone or warehouse_zone) are deliberately open: they have no walls between them, only yellow floor lines, plus wide threshold strips where the data has an `opening`. This visual check catches things the numeric check can't, such as wrong wall heights or labels in odd places. Floors with `Swiss Dwellings` data may be centred per floor rather than aligned (see the dataset README), and that will show up here.

### 6. Report

Give the user:
- a small table of validation results (checked counts and max errors),
- what is modelled vs. assumed: walls come from room outlines because the source has no walls, and hall zones are left open, asset heights are assumed per type, and stairs are shown as stairwell space plus a connector line,
- links to `model.blend`, `model.glb` and the renders.

## Placeholder equipment

Every asset is a linked duplicate of one shared mesh, `PH_Equipment_Unit`: a unit box with its origin at the bottom centre. Each copy is scaled to `width_m × depth_m × height`, rotated `rotation_deg` counter-clockwise about Z (width runs along the rotation direction, which was verified against the dataset plans), and placed at `(x, y, elevation_m)`. To swap in a nicer model, replace that one mesh's geometry in the `.blend` and keep it inside the unit box with the same origin. Every asset updates and stays to scale.

## Extending

- New asset type heights go in `ASSET_HEIGHT` in `build_building.py`.
- Keep the conventions in the build script's docstring. The validator depends on them: object origin = footprint centre on the floor surface, custom props `asset_id` / `space_id` / `opening_id`, and space meshes carrying the polygon verbatim at `z = elevation_m`.
- After any change, re-run build + validate on `osm-way27326387` (the reference building, which must stay PASS) and on the target building.

## Delegation

Building and validating are mechanical, so a cheaper subagent (Haiku/Sonnet) can run steps 1–4 for a batch of buildings and return the summary tables. Keep the visual comparison (step 5) and any changes to the scripts with the main model.
