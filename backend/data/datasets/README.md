# Datasets

Each folder is one ingestible dataset in the starter-kit format (`building.json`, `equipment.csv`, floor plan images).
Ingest one with `POST /api/ingestions {"dataset": "<folder name>"}`.

| Dataset | Source | License |
|---|---|---|
| `proc-factory-dev-003` | Procedural factory from the Industrial Agents Hackathon 2026 student kit (`data/dev`) | CC0-1.0 |

To ingest any other kit building without copying it, point `DATASETS_DIR` at the kit's `data/dev`, `data/train` or `data/test` folder.

## Datasets without building.json

A dataset folder may contain only floor plans plus `equipment.csv`:

- `floor_<level>.svg` (best), or `floor_<level>.png` / `.jpg` (also work)
- `equipment.csv`

The ingestion pipeline's first stage, `reconstruct`, rebuilds `building.json` from them:

- SVG floor plans (the kit's Matplotlib export) are parsed exactly: room polygons, room types from fill colour, door/opening/entrance markers, labels.
- `equipment.csv` gives the pixel-to-metre scale and room names/types.
- An LLM agent fills what isn't drawn: names of unlabeled rooms, floor heights, door widths.
- Image-only floors are traced by a vision agent (approximate, needs an API key).

The result is written to `building.json` in the dataset folder, plus a provenance file `reconstruction.json`. The location id is the dataset name. Re-running with `force` reconstructs again.

Example: `test_WO` is `resplan-102` without its building.json.
