# Prompts and tool log: building.json → to-scale Blender model

This is how the Blender model of `osm-way27326387` was produced: the user prompts, each step the main agent took (with tool calls), and the full prompts given to the cheaper subagents. The repeatable part of the process is packaged as the project skill [`.claude/skills/building-to-blender/SKILL.md`](../.claude/skills/building-to-blender/SKILL.md).

> **About system prompts.** The main agent and the subagents run on system prompts built into Claude Code. These are not stored in the repo and cannot be exported verbatim. What *is* under our control, and fully reproduced below, is:
> - the **user prompts**,
> - each subagent's **agent type, model and tool set**,
> - the **task prompt** passed to each subagent. For a `general-purpose` subagent, this prompt is the whole task-specific instruction; the built-in system prompt only adds generic coding-agent behaviour.

## Agents used

| Role | Agent type | Model | Tools available | Why this model |
|---|---|---|---|---|
| Orchestrator: data analysis, build script, visual QA, fixes | main session | Opus 5.5 | Bash, Read, Write, Edit, Agent, … | Geometry decisions and visual checks need judgement |
| Validator author | `general-purpose` | **Sonnet** | all (`*`) | Well-specified coding task against a fixed contract |
| README author | `general-purpose` | **Haiku** | all (`*`) | Summarising two scripts into docs |
| Skill tester | `general-purpose` | **Haiku** | all (`*`) | Mechanical run of the skill's steps on a new building |

All subagents ran in the background (`run_in_background: true`) while the orchestrator kept working.

---

## User prompt 1

> OK difficult task. I have the following dataset. try to create a to scale 3d model in blender (you can also us epython scripts rto make it cheaper/reproducable)and locate the Objects most acurate in the space. add Add one spaceholder equipment 3d object . to kkep it simple. us the problem_statement/industrial-agents-hackathon-2026/data/dev/osm-way27326387 asset.
>
> Use subagents with cheaper models for the simpler task.

### Step 1: Inspect dataset and asset (orchestrator)

**Bash:** list the kit and the building folder, and check for Blender.
```bash
cd mm_industrial_26; D=problem_statement/industrial-agents-hackathon-2026
ls $D; ls $D/data; find $D/data/dev/osm-way27326387 -type f
which blender; ls /Applications | grep -i blender
```
Result: `building.json`, `equipment.csv`, `floor_{-2,0,1}.{png,svg}`, `*_plain.png` and `questions*.jsonl`. Blender lives at `/Applications/Blender.app` (not on PATH).

**Bash:** read the data-format section of the kit README and summarise `building.json`.
```bash
sed -n 160,230p README.md          # schema, units (metres, x right, y up), route model
python3 -c "import json; b=json.load(open('building.json')); ..."   # keys, floors, counts, samples
```
Findings:
- 3 floors: levels −2/0/1 at −6/0/3 m, each 3 m high. Level −1 is missing.
- 61 spaces, 68 openings (66 doors + 2 entrances), 1 stair connector, 184 assets (116 chairs, 41 desks, 23 shelves, 4 fire extinguishers).

**Bash:** count asset types and look at the SVG structure.
```bash
cut -d, -f3,4 equipment.csv | sort | uniq -c; grep -o '<[a-z]*' floor_-2.svg | sort | uniq -c
```

**Read:** `floor_-2.png`, to confirm the **rotation convention**. Desk `K10-A` at `rotation_deg=101.7` runs along the K10 west wall, whose edge angle is ≈101.7°. So `width_m` lies along the rotation direction, counter-clockwise from +x.

**Bash:** check Blender's version, stair alignment and whether openings sit on room edges.
```bash
/Applications/Blender.app/Contents/MacOS/Blender --version     # 4.2.2 LTS
python3 -c "..."   # stair stop polygons per floor; space type counts; opening kinds;
                   # distance of every opening to nearest edge of its spaces (max 8 mm)
```
Findings:
- Stairwells line up across floors.
- Every opening lies on a room edge, so door gaps can be cut directly into walls derived from room outlines.

### Step 2: Delegate the validator (Sonnet subagent, background)

**Agent call:** `subagent_type: general-purpose`, `model: sonnet`, `run_in_background: true`, description *"Write Blender validation script"*. Full prompt:

````text
Write ONE Python file: /Users/pascal/Documents/Repositories/Privat/Hackathons/mm_industrial_26/blender/validate_model.py. Do NOT write any other files and do not run Blender yourself (another script that builds the .blend is being written in parallel; it doesn't exist yet).

Purpose: run inside Blender 4.2 in background mode to check that a generated .blend reproduces the dataset geometry exactly. Invocation:
  /Applications/Blender.app/Contents/MacOS/Blender -b <model.blend> --python validate_model.py -- --building <building_dir> [--report <out.json>]
Parse args after "--" with argparse. Use only bpy, mathutils, json, math, argparse, sys (no shapely/numpy).

Dataset: <building_dir>/building.json (e.g. /Users/pascal/Documents/Repositories/Privat/Hackathons/mm_industrial_26/problem_statement/industrial-agents-hackathon-2026/data/dev/osm-way27326387/building.json — read it to see the shape). Units are metres, x right, y up. Keys: floors[{id, level, elevation_m, height_m}], spaces[{id, floor_id, type, name, polygon:[[x,y],...] (not closed)}], openings[{id, floor_id, space_ids, kind, position:[x,y], width_m}], assets[{id, name, category, type, space_id, floor_id, position:[x,y], footprint:{width_m, depth_m, rotation_deg}}].

Conventions the .blend follows (rely on these):
- Every asset is an object with custom property "asset_id" (== asset id). Its object origin is the footprint centre ON the floor surface: obj.matrix_world.translation == (x, y, floor.elevation_m). rotation_euler.z == radians(rotation_deg) (compare modulo 360°). obj.dimensions.x == width_m (along the rotated local x), obj.dimensions.y == depth_m. Also has custom props "floor_level", "space_id".
- Every space is a mesh object with custom property "space_id". Its mesh has a polygon whose world-space vertex XY coordinates equal the dataset polygon (same vertex count; order may start at a different index and may be reversed), all at z == floor elevation_m (the floor surface). The object may contain extra faces (e.g. a slab extruded downward), so match: for each dataset vertex, the nearest world vertex of the object at z≈elevation within tolerance.
- Every opening is an object with custom property "opening_id"; its world translation XY == position, z == floor elevation.

Checks to implement, with tolerance 1 mm for positions, 0.1° for rotation, 1 mm for dimensions:
1. Each dataset asset/space/opening has exactly one matching object (report missing and extra/duplicate ids).
2. Asset: position error, z vs floor elevation, rotation error, width/depth error; plus point-in-polygon check that the dataset position lies inside its space polygon (report as a dataset-level warning, not a model failure, if outside).
3. Space: max vertex XY error, z error.
4. Opening: position error, z error.
Print a compact summary table (counts checked, failures, max error per category) and the first 10 failures per category. Write the full results to --report JSON if given. Exit code: call sys.exit(1) on any failure (Blender background mode respects sys.exit from scripts when you also pass --python-exit-code 1 on the command line; mention that in the module docstring usage line).

Keep the code compact and readable (~150-250 lines), a short module docstring, no over-engineering. Syntax-check it with `python3 -m py_compile` (bpy import will fail at runtime outside Blender; that's fine, only compile). Reply with just the file path and a 3-line summary.
````

Design note: the prompt fixes a **contract**: object origin, custom property names and the vertex layout. That let the build script and the validator be written in parallel without seeing each other.

### Step 3: Write the build script (orchestrator)

**Write:** [`blender/build_building.py`](build_building.py). Key decisions:

| Element | How it's built |
|---|---|
| Floors | One collection per level; walking surface at `elevation_m`, 0.15 m slab below |
| Spaces | Dataset polygon used verbatim, normalised to counter-clockwise order, then extruded down; coloured by `type` |
| Walls | Each room edge becomes a box inset 0.08 m into the room, so adjacent rooms form a 0.16 m wall on the shared edge with no overlapping faces. Height is `height_m − slab` |
| Doors | For every opening within 5 cm of an edge, cut a gap of `width_m` centred on the projected position, add a lintel above 2.1 m, and mark it with a threshold strip (entrances in green) |
| Assets | **One** placeholder mesh `PH_Equipment_Unit` (unit box, origin at bottom centre), linked into every asset. Scale = `width × depth × type height`, rotation Z = `rotation_deg`, location = `(x, y, elevation)` |
| Connectors | Polyline through the stairwell centroids |
| Labels | Flat text with the room ref at the polygon centroid |
| Renders | Workbench engine: an orthographic top-down plan per floor (same framing as the dataset PNGs), a perspective view per floor, and one of the whole building |
| Export | `.blend`, plus `.glb` with custom properties as glTF extras |

### Step 4: Build + validate

**Bash:**
```bash
B=problem_statement/industrial-agents-hackathon-2026/data/dev/osm-way27326387; O=blender/out/osm-way27326387
/Applications/Blender.app/Contents/MacOS/Blender -b --python-exit-code 1 --python blender/build_building.py -- \
  --building $B --out $O/model.blend --glb $O/model.glb --render $O/renders
/Applications/Blender.app/Contents/MacOS/Blender -b $O/model.blend --python-exit-code 1 --python blender/validate_model.py -- \
  --building $B --report $O/validation.json
```
First result:
- Assets and openings **PASS**.
- Spaces reported 55 "duplicates". Cause: the validator indexed every object carrying `space_id`, and assets carry it too. This was a bug in the validator, not in the model.

**Bash (patch):** `index_objects(prop, exclude=())`, with spaces indexed via `exclude=("asset_id",)`. Re-run: **PASS**, 184 assets / 61 spaces / 68 openings, max error < 1 µm.

### Step 5: Visual QA and render fixes (orchestrator)

**Read:** `renders/plan_floor_-2.png`, `view_floor_0.png` and `view_building.png`, compared with the dataset's `floor_-2.png`.
- The layout matches.
- The plan was dark, with shadow streaks, and the walls were too heavy.

**Bash (patch):** lighter wall colour; plans use `FLAT` light with no shadows; perspective views use `STUDIO`. Rebuild, then **Read** `plan_floor_0.png` against `floor_0.png`: a room-for-room match.

**Bash (patch):** the stair polyline poked through single-floor views, so the `Vertical_Connectors` collection now only renders in the full-building view. Rebuild + validate: **PASS**.

### Step 6: Delegate the README (Haiku subagent, background)

**Agent call:** `subagent_type: general-purpose`, `model: haiku`, `run_in_background: true`, description *"Write README for Blender pipeline"*. Full prompt:

````text
Write a concise README at /Users/pascal/Documents/Repositories/Privat/Hackathons/mm_industrial_26/blender/README.md (max ~70 lines, Markdown). Only write that one file; do not run Blender or modify other files.

First read the module docstrings and argparse options of these two scripts to get details right:
- /Users/pascal/Documents/Repositories/Privat/Hackathons/mm_industrial_26/blender/build_building.py
- /Users/pascal/Documents/Repositories/Privat/Hackathons/mm_industrial_26/blender/validate_model.py

Content to cover:
1. Purpose: reproducible, to-scale (1 Blender unit = 1 m) 3D model of a hackathon building from its building.json, built headless via Python. Example building: data/dev/osm-way27326387 (TUM 4107 Institutsgebäude 3; 3 floors at levels -2, 0, 1 with elevations -6, 0, 3 m; level -1 is absent from the data so there is a gap; 61 spaces, 68 openings, 1 stair connector, 184 assets).
2. Commands (run from repo root), each in its own ```bash block:
   /Applications/Blender.app/Contents/MacOS/Blender -b --python-exit-code 1 --python blender/build_building.py -- --building problem_statement/industrial-agents-hackathon-2026/data/dev/osm-way27326387 --out blender/out/osm-way27326387/model.blend --glb blender/out/osm-way27326387/model.glb --render blender/out/osm-way27326387/renders
   /Applications/Blender.app/Contents/MacOS/Blender -b blender/out/osm-way27326387/model.blend --python-exit-code 1 --python blender/validate_model.py -- --building problem_statement/industrial-agents-hackathon-2026/data/dev/osm-way27326387 --report blender/out/osm-way27326387/validation.json
3. Modelling conventions (from the build script docstring and constants): floor surface at elevation_m, slab below; walls inset into each room so shared walls are 2×WALL_T; door gaps of width_m cut at each opening position with lintel above DOOR_H; ONE shared placeholder equipment mesh "PH_Equipment_Unit" (unit box, origin bottom-centre) instanced for every asset, scaled to width_m × depth_m × per-type height, rotated by rotation_deg CCW about Z (width runs along the rotation direction); per-type heights table; colour key by category; vertical connector drawn as a polyline through stop centroids.
4. Scene structure: Building_<id> > Floor_<level> > F<level>_Spaces/Walls/Openings/Assets/Labels, plus Vertical_Connectors. Custom properties on objects (asset_id, space_id, opening_id, etc.) — these are exported as glTF extras in model.glb, useful for a web viewer.
5. Outputs: model.blend, model.glb, renders/plan_floor_<level>.png (top-down ortho, same orientation as the dataset floor_<level>.png), renders/view_floor_<level>.png, renders/view_building.png, validation.json.
6. Validation result: PASS — 184 assets, 61 spaces, 68 openings, all within 1 mm / 0.1° (actual max error < 1 µm).
7. Limitations: asset heights are assumed per type (dataset only has footprints); walls are derived from room outlines (no wall data in source); stair geometry not modelled (only the stairwell space + connector line).

Plain, factual tone; no emojis. Reply with just "done" and the path.
````

**Bash (review fix):** two corrections to the Haiku output.
- Walls stop at `height_m − 0.15`, not at `height_m`.
- The missing level −1 is a physical 3 m gap in the model, not only a numbering gap.

---

## User prompt 2

> can you give me the systems prompts for teh subagents and each of the steps? Put them oin am prompt md file. also include tool usages etc. In addition create a skill for this process

### Step 7: Create the skill (orchestrator, following the `skill-creator` skill)

- **Skill:** `anthropic-skills:skill-creator`, loaded for the skill-authoring guidance.
- **Bash:** `manifest.csv` was used to pick a test building different from the reference. The choice was `proc-factory-dev-004`: procedural, 2 floors, with a lift and machines.
- **Write:** [`.claude/skills/building-to-blender/SKILL.md`](../.claude/skills/building-to-blender/SKILL.md).
  - It is a project skill that calls the scripts in `blender/` and doesn't copy them, so there is one source of truth.
  - Its six steps are: locate & summarise, check Blender, build, validate, visual comparison, report.
  - It also covers placeholder-swap instructions, rules for extending the scripts, and delegation guidance.

### Step 8: Test the skill on a new building (Haiku subagent, background)

**Agent call:** `subagent_type: general-purpose`, `model: haiku`, `run_in_background: true`, description *"Test skill on factory building"*. Full prompt:

````text
Follow the skill at /Users/pascal/Documents/Repositories/Privat/Hackathons/mm_industrial_26/.claude/skills/building-to-blender/SKILL.md (read it first) to build and validate a to-scale Blender model of building `proc-factory-dev-004` (dev split). Repo root: /Users/pascal/Documents/Repositories/Privat/Hackathons/mm_industrial_26.

Do steps 1–4 of the skill exactly (summary, Blender check, build with --glb and --render, validate). Do NOT modify any script or the skill. Write outputs only under blender/out/proc-factory-dev-004/.

Reply with (max 25 lines):
- the step-1 summary (floors with level/elevation/height, counts, asset type counts, and which asset types are NOT in ASSET_HEIGHT),
- build exit code + last log line,
- the validator summary table lines and RESULT line verbatim,
- the list of render files produced,
- any place where the skill's instructions were unclear, wrong, or made you guess (be specific — this is a test of the skill).
````

## Skill test result (Haiku, `proc-factory-dev-004`)

**Model check:** **PASS** on the first try, with no script changes: 280 assets, 50 spaces and 57 openings. Max position error was 2.5 µm.

**Problems the tester reported, and how each was handled:**

| # | Reported by the Haiku tester | Fix (orchestrator) |
|---|---|---|
| 1 | The skill never says to `mkdir` the output dir before redirecting the log into it | Skill step 3 now starts with `mkdir -p` |
| 2 | "Last log line is `built …`" is wrong; Blender prints `Blender quit` after it | Skill now greps `^built\|Error\|Traceback` |
| 3 | No schema hint for step 1; the manifest column `connectors` ≠ JSON key `vertical_connectors` | Skill lists the top-level keys and points to the kit README §3 |
| 4 | Height fallback order not named | Skill names `ASSET_HEIGHT` → `CATEGORY_HEIGHT` → 1.0 m |
| 5 | 37 asset types (pallet_rack, press_brake, office_chair, …) used category defaults | `ASSET_HEIGHT` extended to about 40 types with realistic heights |
| 6 | **F0 is 8 m high but F1 starts at 3.5 m, so F0 walls ran through the F1 floor** | Build script: walls are sampled every 0.25 m and stop under any higher slab that covers that stretch. The hall keeps its 8 m walls; walls under the office strip stop at 3.35 m |
| 7 | "Max error < 1 µm" is too tight (factory: 2.5 µm) | Skill/README now say "a few µm (float32)" |

**Found by the orchestrator** during the visual check of the factory renders (step 5 of the skill, which stays with the main model):

| # | Problem | Fix |
|---|---|---|
| 8 | Hall zones (production/assembly/warehouse…) were boxed in by 8 m walls. The data joins them with 12.8–15.9 m `opening`s, and the lintel rule had filled the space above those gaps | No lintel over kind `opening`; no wall between hall↔hall or hall↔corridor, only a yellow floor marking. The render now matches the plan's H-A/B/C grid |

Both buildings were rebuilt and re-validated after the fixes, and both PASS. The tools used were **Bash** (Python patches, Blender runs) and **Read** (renders against the dataset PNGs).

---

## Reproduce everything

```bash
B=problem_statement/industrial-agents-hackathon-2026/data/dev/osm-way27326387; O=blender/out/osm-way27326387
/Applications/Blender.app/Contents/MacOS/Blender -b --python-exit-code 1 --python blender/build_building.py -- --building $B --out $O/model.blend --glb $O/model.glb --render $O/renders
/Applications/Blender.app/Contents/MacOS/Blender -b $O/model.blend --python-exit-code 1 --python blender/validate_model.py -- --building $B --report $O/validation.json
```

Or ask Claude Code: *"build a 3D Blender model of proc-campus-dev-001"*. The `building-to-blender` skill takes it from there.
