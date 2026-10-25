"""Starts, tracks and resumes pipeline runs.

A run is one LangGraph thread (`thread_id = run_id`). The graph itself is synchronous
(stages are CPU-bound tools plus blocking SDK calls), so API calls drive it in a worker
thread and return at once; the `RunRecord` JSON is the source of truth for progress.
"""

import asyncio
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from langgraph.types import Command

from app.config import Settings
from app.ingestion.service import DATASET_NAME, DatasetNotFoundError
from app.pipeline.agents.base import AgentModels
from app.pipeline.graph import PipelineGraph, build_pipeline_graph
from app.pipeline.state import (
    MODE_STAGES,
    PipelineMode,
    ReviewDecision,
    RunRecord,
    RunStatus,
    StageRun,
)
from app.pipeline.store import Pillar, Workspace
from app.storage import BuildingRepository
from app.tracing import graph_config

TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.ABORTED}


# Distinguishes this process from an earlier one that had the same pid (containers).
BOOT_ID = uuid.uuid4().hex


def _alive(pid: int | None, boot: str | None) -> bool:
    if pid is None:
        return False
    if pid == os.getpid():
        return boot == BOOT_ID
    if os.name == "nt":
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _alive_windows(pid: int) -> bool:
    """Read-only check. On Windows `os.kill(pid, 0)` is TerminateProcess, not a probe: it
    killed whatever process held that pid (e.g. the backend that owns a paused run)."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited_information, still_active, access_denied = 0x1000, 259, 5
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == access_denied
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and (
            code.value == still_active
        )
    finally:
        kernel32.CloseHandle(handle)


class RunNotFoundError(LookupError):
    pass


class RunStateError(RuntimeError):
    pass


class PipelineRunner:
    def __init__(
        self,
        settings: Settings,
        workspace: Workspace,
        client: Anthropic | None,
        repository: BuildingRepository | None = None,
    ) -> None:
        self.settings = settings
        self.workspace = workspace
        self.offline = client is None
        self.graph: PipelineGraph = build_pipeline_graph(
            workspace,
            client,
            AgentModels.from_settings(settings),
            repository=repository,
            max_retries=settings.pipeline_max_retries,
        )
        self._tasks: set[asyncio.Task[RunRecord]] = set()
        self._active: set[str] = set()  # run ids whose graph is executing in this process
        self._lock = threading.Lock()
        self._fail_orphans()

    def _fail_orphans(self) -> None:
        """Unfinished runs of a process that is gone can never finish (the checkpoint was
        in its memory). Runs of another live process (backend vs CLI) are left alone."""
        for record in self.workspace.runs.all():
            if record.status not in TERMINAL and not _alive(record.owner_pid, record.owner_boot):
                self._mark_failed(record, "The process driving this run has stopped.")

    def _mark_failed(self, record: RunRecord, error: str) -> RunRecord:
        record.status, record.error = RunStatus.FAILED, error
        if record.current_stage is not None:
            record.stages[record.current_stage].status = "failed"
        record.current_stage = None
        self.workspace.runs.save(record)
        if record.location_id:
            location = self.workspace.locations.get(record.location_id)
            if location is not None:
                for name, pillar in location.pillars.items():
                    if pillar.status in ("pending", "running"):
                        location.pillars[name] = Pillar(status="failed", note=error)
                self.workspace.locations.save(location)
        return record

    @classmethod
    def from_settings(
        cls, settings: Settings, repository: BuildingRepository | None = None, offline: bool = False
    ) -> "PipelineRunner":
        client = (
            None
            if offline or not settings.anthropic_api_key
            else Anthropic(api_key=settings.anthropic_api_key)
        )
        workspace = Workspace(settings.workspace_dir, settings.asset_library_seed_dir)
        return cls(settings, workspace, client, repository)

    def dataset_dir(self, dataset: str, datasets_dir: Path | None = None) -> Path:
        root = datasets_dir or self.settings.datasets_dir
        if not DATASET_NAME.match(dataset) or not (root / dataset / "building.json").is_file():
            raise DatasetNotFoundError(dataset)
        return root / dataset

    def get(self, run_id: str) -> RunRecord:
        try:
            record = self.workspace.runs.get(run_id)
        except ValueError:
            record = None
        if record is None:
            raise RunNotFoundError(run_id)
        return record

    def list(self) -> list[RunRecord]:
        return self.workspace.runs.all()

    def create(
        self, dataset: str, datasets_dir: Path | None = None, mode: PipelineMode = "full"
    ) -> tuple[RunRecord, Path]:
        dataset_dir = self.dataset_dir(dataset, datasets_dir)
        with self._lock:  # one unfinished run per dataset: runs rewrite the same location
            for other in self.workspace.runs.all():
                if other.dataset == dataset and other.status not in TERMINAL:
                    raise RunStateError(
                        f"Run {other.run_id} for {dataset} is still {other.status}; "
                        "finish or resolve it first."
                    )
            record = RunRecord(
                run_id=uuid.uuid4().hex[:12],
                dataset=dataset,
                mode=mode,
                offline=self.offline,
                owner_pid=os.getpid(),
                owner_boot=BOOT_ID,
            )
            for stage in record.stages:
                if stage not in MODE_STAGES[mode]:
                    record.stages[stage] = StageRun(status="skipped", summary=f"{mode}-only run")
            self.workspace.runs.save(record)
            self._active.add(record.run_id)
        return record, dataset_dir

    # --- driving the graph -------------------------------------------------------------

    def _config(self, record: RunRecord) -> Any:
        return graph_config("ingestion_pipeline", record.run_id, dataset=record.dataset)

    def _drive(self, record: RunRecord, payload: Any) -> RunRecord:
        """Run the graph until it ends or pauses for review; returns the final record."""
        config = self._config(record)
        try:
            self.graph.invoke(payload, config)
        except Exception as exc:
            return self._mark_failed(self.get(record.run_id), repr(exc))
        finally:
            with self._lock:
                self._active.discard(record.run_id)

        current = self.get(record.run_id)
        snapshot = self.graph.get_state(config)
        if snapshot.interrupts:
            current.status = RunStatus.NEEDS_REVIEW
        elif snapshot.values.get("skipped"):
            current.status, current.error = RunStatus.COMPLETED, None
            current.decisions.append(
                {"stage": None, "action": "skip", "reason": "Input unchanged."}
            )
        self.workspace.runs.save(current)
        return current

    def run_sync(
        self,
        dataset: str,
        force: bool = False,
        datasets_dir: Path | None = None,
        mode: PipelineMode = "full",
    ) -> RunRecord:
        record, dataset_dir = self.create(dataset, datasets_dir, mode)
        return self._drive(record, self._initial(record, dataset_dir, force))

    def resume_sync(self, run_id: str, decision: ReviewDecision) -> RunRecord:
        record = self._check_review(run_id, decision)
        return self._drive(record, Command(resume=decision.model_dump(mode="json")))

    async def start(
        self, dataset: str, force: bool = False, mode: PipelineMode = "full"
    ) -> RunRecord:
        record, dataset_dir = self.create(dataset, mode=mode)
        self._spawn(
            asyncio.to_thread(self._drive, record, self._initial(record, dataset_dir, force))
        )
        return record

    async def resume(self, run_id: str, decision: ReviewDecision) -> RunRecord:
        record = self._check_review(run_id, decision)
        record.status, record.pending_review = RunStatus.RUNNING, None
        self.workspace.runs.save(record)
        self._spawn(
            asyncio.to_thread(self._drive, record, Command(resume=decision.model_dump(mode="json")))
        )
        return record

    async def wait(self) -> None:
        """Wait for all background runs (tests, shutdown)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # --- helpers -------------------------------------------------------------------------

    @staticmethod
    def _initial(record: RunRecord, dataset_dir: Path, force: bool) -> dict[str, Any]:
        return {
            "run_id": record.run_id,
            "dataset": record.dataset,
            "dataset_dir": str(dataset_dir),
            "force": force,
            "mode": record.mode,
        }

    def _check_review(self, run_id: str, decision: ReviewDecision) -> RunRecord:
        record = self.get(run_id)
        with self._lock:
            if run_id in self._active:
                raise RunStateError(f"Run {run_id} is already being resumed.")
        if record.status != RunStatus.NEEDS_REVIEW or record.pending_review is None:
            raise RunStateError(f"Run {run_id} is not waiting for a review.")
        if not self.graph.get_state(self._config(record)).interrupts:
            # The checkpoint lives in memory; it is gone after a backend restart.
            raise RunStateError(f"Run {run_id} can no longer be resumed (backend restarted).")
        if decision.review_id != record.pending_review.review_id:
            raise RunStateError("review_id does not match the pending review.")
        with self._lock:
            self._active.add(run_id)
        return record

    def _spawn(self, coro: Any) -> None:
        task: asyncio.Task[RunRecord] = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
