# Datasets

Each folder is one ingestible dataset in the starter-kit format (`building.json`, `equipment.csv`, floor plan images).
Ingest one with `POST /api/ingestions {"dataset": "<folder name>"}`.

| Dataset | Source | License |
|---|---|---|
| `proc-factory-dev-003` | Procedural factory from the Industrial Agents Hackathon 2026 student kit (`data/dev`) | CC0-1.0 |

To ingest any other kit building without copying it, point `DATASETS_DIR` at the kit's `data/dev`, `data/train` or `data/test` folder.
