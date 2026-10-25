from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.ingestion import DatasetNotFoundError
from app.pipeline.runner import PipelineRunner, RunNotFoundError, RunStateError
from app.pipeline.state import PipelineMode, ReviewDecision, RunRecord
from app.pipeline.store import LocationRecord

router = APIRouter(tags=["pipeline"])


def get_runner(request: Request) -> PipelineRunner:
    runner: PipelineRunner = request.app.state.pipeline_runner
    return runner


RunnerDep = Annotated[PipelineRunner, Depends(get_runner)]

# Location files a client may fetch: previews and plans shown with reviews, and models.
ARTIFACT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".glb": "model/gltf-binary",
}


class PipelineRunRequest(BaseModel):
    dataset: str = Field(min_length=1, max_length=200, examples=["proc-factory-dev-003"])
    force: bool = Field(default=False, description="Re-run even if the input is unchanged.")
    mode: PipelineMode = Field(
        default="full",
        description="'graph' = benchmark variant: deterministic cleanup and the relationship "
        "graph only, no 3D model or metadata.",
    )


class LocationSummary(BaseModel):
    id: str
    name: str
    dataset: str
    source: str
    pillars: dict[str, str]
    has_model: bool = Field(description="True once the pipeline has written a 3D model.")


def _run(runner: PipelineRunner, run_id: str) -> RunRecord:
    try:
        return runner.get(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run '{run_id}' not found") from None


def _location(runner: PipelineRunner, location_id: str) -> LocationRecord:
    try:
        record = runner.workspace.locations.get(location_id)
    except ValueError:
        record = None
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Location '{location_id}' not found")
    return record


@router.post("/pipeline-runs", response_model=RunRecord, status_code=status.HTTP_202_ACCEPTED)
async def start_run(body: PipelineRunRequest, runner: RunnerDep) -> RunRecord:
    try:
        return await runner.start(body.dataset, force=body.force, mode=body.mode)
    except DatasetNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Dataset '{body.dataset}' not found"
        ) from None
    except RunStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.get("/pipeline-runs", response_model=list[RunRecord])
def list_runs(runner: RunnerDep, dataset: str | None = None) -> list[RunRecord]:
    """All runs, newest first; `dataset` limits them to one dataset."""
    return [r for r in runner.list() if dataset is None or r.dataset == dataset]


@router.get("/pipeline-runs/{run_id}", response_model=RunRecord)
def get_run(run_id: str, runner: RunnerDep) -> RunRecord:
    return _run(runner, run_id)


@router.post("/pipeline-runs/{run_id}/resume", response_model=RunRecord)
async def resume_run(run_id: str, body: ReviewDecision, runner: RunnerDep) -> RunRecord:
    _run(runner, run_id)
    try:
        return await runner.resume(run_id, body)
    except RunStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.get("/locations", response_model=list[LocationSummary])
def list_locations(runner: RunnerDep) -> list[LocationSummary]:
    return [
        LocationSummary(
            id=r.id,
            name=r.name,
            dataset=r.dataset,
            source=r.source,
            pillars={k: v.status for k, v in r.pillars.items()},
            has_model=runner.workspace.locations.path(r.id, "3d/model.glb").is_file(),
        )
        for r in runner.workspace.locations.all()
    ]


@router.get("/locations/{location_id}", response_model=LocationRecord)
def get_location(location_id: str, runner: RunnerDep) -> LocationRecord:
    return _location(runner, location_id)


@router.get("/locations/{location_id}/model.glb", response_class=FileResponse)
def get_location_model(location_id: str, runner: RunnerDep) -> FileResponse:
    _location(runner, location_id)
    path = runner.workspace.locations.path(location_id, "3d/model.glb")
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No 3D model for this location yet")
    return FileResponse(path, media_type="model/gltf-binary", filename=f"{location_id}.glb")


@router.get("/locations/{location_id}/building")
def get_location_building(location_id: str, runner: RunnerDep) -> Response:
    """The cleaned building.json (same format as the dataset's, after the pipeline's fixes)."""
    _location(runner, location_id)
    path = runner.workspace.locations.path(location_id, "building.json")
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cleaned building for this location yet")
    return Response(path.read_bytes(), media_type="application/json")


@router.get("/locations/{location_id}/files/{path:path}", response_class=FileResponse)
def get_location_file(location_id: str, path: str, runner: RunnerDep) -> FileResponse:
    """An image or model the pipeline wrote, e.g. a review's preview (`3d/previews/...png`)."""
    _location(runner, location_id)
    root = runner.workspace.locations.dir(location_id).resolve()
    file = (root / path).resolve()
    media_type = ARTIFACT_TYPES.get(file.suffix.lower())
    if media_type is None or not file.is_relative_to(root) or not file.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"File '{path}' not found")
    return FileResponse(file, media_type=media_type)
