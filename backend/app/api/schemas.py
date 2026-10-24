from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    environment: str
    llm_enabled: bool
    managed_agent_enabled: bool
    tracing_enabled: bool


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, description="Omit to start a new conversation.")
    building_id: str | None = Field(
        default=None, description="Ingested building the agent reasons about; omit for plain chat."
    )


class ChatResponse(BaseModel):
    reply: str
    thread_id: str


class DatasetList(BaseModel):
    datasets: list[str]


class IngestionRequest(BaseModel):
    dataset: str = Field(
        min_length=1,
        max_length=200,
        description="Folder name under DATASETS_DIR, e.g. 'proc-factory-dev-003'.",
        examples=["proc-factory-dev-003"],
    )


class IngestionResponse(BaseModel):
    dataset: str
    building_id: str
    building_name: str
    floors: int
    spaces: int
    openings: int
    vertical_connectors: int
    assets: int
    replaced: bool


class FloorOut(BaseModel):
    id: str
    level: int
    name: str
    elevation_m: float
    height_m: float


class BuildingSummary(BaseModel):
    id: str
    name: str
    dataset: str
    source: str
    license: str
    ingested_at: datetime


class BuildingDetail(BuildingSummary):
    attribution: str
    floors: list[FloorOut]
    space_count: int
    asset_count: int
