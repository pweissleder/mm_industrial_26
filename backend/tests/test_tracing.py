import asyncio
import os
import uuid
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import Client, tracing_context
from langsmith._exceptions import NotFoundError

from app.agent import build_graph
from app.config import BACKEND_DIR, Settings
from app.ingestion import ingest_dataset
from app.storage import InMemoryBuildingRepository
from app.tracing import configure_tracing, graph_config


@pytest.fixture(autouse=True)
def _restore_env() -> object:
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def test_tracing_off_without_api_key(settings: Settings) -> None:
    settings.langsmith_tracing = True

    assert configure_tracing(settings) is False
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_tracing_exports_langsmith_env(settings: Settings) -> None:
    settings.langsmith_tracing = True
    settings.langsmith_api_key = "lsv2_test"
    settings.langsmith_project = "unit-tests"

    assert configure_tracing(settings) is True
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_API_KEY"] == "lsv2_test"
    assert os.environ["LANGSMITH_PROJECT"] == "unit-tests"


async def test_graph_run_is_sent_to_langsmith() -> None:
    client = MagicMock(spec=Client)
    tracer = LangChainTracer(client=client, project_name="unit-tests")
    config = graph_config("chat", "thread-1")
    config["callbacks"] = [tracer]

    await build_graph(None).ainvoke({"messages": [HumanMessage("hi")]}, config)
    tracer.wait_for_futures()

    runs = [c.kwargs for c in client.create_run.call_args_list]
    names = [r["name"] for r in runs]
    # The input guardrail runs (and is traced) before the assistant.
    assert names[:3] == ["chat", "guard", "input_guardrail"]
    assert names[-2:] == ["assistant", "next_step"]
    assert runs[0]["extra"]["metadata"]["thread_id"] == "thread-1"


def test_ingestion_is_traced_with_dataset_name_only() -> None:
    client = MagicMock(spec=Client)
    repository = InMemoryBuildingRepository()

    with tracing_context(enabled=True, client=client, project_name="unit-tests"):
        ingest_dataset(repository, BACKEND_DIR / "data" / "datasets", "proc-factory-dev-003")
    client.flush()

    created = client.create_run.call_args.kwargs
    assert created["name"] == "ingest_dataset"
    assert created["inputs"] == {"dataset": "proc-factory-dev-003"}


@pytest.mark.live
async def test_live_trace_reaches_langsmith() -> None:
    """Sends one real trace. Runs only with `pytest -m live` and a key in the env/.env."""
    settings = Settings()
    if settings.langsmith_api_key is None:
        pytest.skip("LANGSMITH_API_KEY not set")
    settings.langsmith_tracing = True
    configure_tracing(settings)

    client = Client()
    run_id = uuid.uuid4()
    config = graph_config("smoke_test", f"smoke-{run_id}")
    config["run_id"] = run_id

    with tracing_context(enabled=True, client=client, project_name=settings.langsmith_project):
        await build_graph(None).ainvoke({"messages": [HumanMessage("smoke test")]}, config)
    client.flush()

    # Ingestion into LangSmith is asynchronous, so poll briefly for the run.
    project_id = str(client.read_project(project_name=settings.langsmith_project).id)
    for _ in range(10):
        try:
            run = await client.runs.retrieve(
                str(run_id), project_id=project_id, selects=["NAME", "METADATA"]
            )
            break
        except NotFoundError:
            await asyncio.sleep(2)
    else:
        pytest.fail("trace did not show up in LangSmith")
    assert run.name == "smoke_test"
    assert run.metadata is not None
    assert run.metadata["thread_id"] == f"smoke-{run_id}"
