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


class GuardrailInfo(BaseModel):
    category: str = Field(description="off_topic, prompt_injection or unsafe.")
    reason: str
    source: str = Field(description="Which layer blocked the message: rules or classifier.")


class ChatResponse(BaseModel):
    reply: str
    thread_id: str
    guardrail: GuardrailInfo | None = Field(
        default=None, description="Set when the input guardrail rejected the message."
    )
    building_changed: bool = Field(
        default=False, description="The agent edited the building; refetch its model and graph."
    )


class DatasetList(BaseModel):
    datasets: list[str]
    needs_reconstruction: list[str] = Field(
        default_factory=list,
        description="Datasets without a building.json but with floor plans to rebuild it from.",
    )


class DatasetAvailability(BaseModel):
    dataset: str
    available: bool = Field(description="True if a new dataset can be created under this name.")
    reason: str | None = Field(default=None, description="Why the name can't be used.")


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
    loaded: bool = Field(
        default=True,
        description="False when the dataset was already loaded and opening kept its live version.",
    )


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


class DatasetFileOut(BaseModel):
    name: str
    size_bytes: int


class DatasetFileList(BaseModel):
    dataset: str
    files: list[DatasetFileOut]


class UploadResponse(DatasetFileList):
    ingestible: bool = Field(description="True once the dataset has a building.json.")
    reconstructable: bool = Field(
        description="True when the dataset has no building.json but floor plans the pipeline "
        "can reconstruct it from."
    )
