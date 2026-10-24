from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.ingestion import DatasetNotFoundError
from app.pipeline.runner import PipelineRunner, RunNotFoundError, RunStateError
from app.pipeline.state import ReviewDecision, RunRecord
from app.pipeline.store import LocationRecord

router = APIRouter(tags=["pipeline"])


def get_runner(request: Request) -> PipelineRunner:
    runner: PipelineRunner = request.app.state.pipeline_runner
    return runner


RunnerDep = Annotated[PipelineRunner, Depends(get_runner)]


class PipelineRunRequest(BaseModel):
    dataset: str = Field(min_length=1, max_length=200, examples=["proc-factory-dev-003"])
    force: bool = Field(default=False, description="Re-run even if the input is unchanged.")


class LocationSummary(BaseModel):
    id: str
    name: str
    dataset: str
    source: str
    pillars: dict[str, str]


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
        return await runner.start(body.dataset, force=body.force)
    except DatasetNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Dataset '{body.dataset}' not found"
        ) from None
    except RunStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.get("/pipeline-runs", response_model=list[RunRecord])
def list_runs(runner: RunnerDep) -> list[RunRecord]:
    return runner.list()


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
