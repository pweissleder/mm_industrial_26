"""LangSmith tracing setup.

LangChain and LangGraph read LANGSMITH_* from os.environ, while our settings may
come from `.env`. Exporting them here makes tracing work however the app starts.
"""

import os

from langchain_core.runnables import RunnableConfig

from app.config import Settings


def configure_tracing(settings: Settings) -> bool:
    """Enable LangSmith tracing if configured. Returns whether tracing is on."""
    enabled = settings.langsmith_tracing and settings.langsmith_api_key is not None
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    if enabled:
        assert settings.langsmith_api_key is not None
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    return enabled


def graph_config(run_name: str, thread_id: str, **metadata: str) -> RunnableConfig:
    """Config for a graph run: thread for the checkpointer, name/metadata for LangSmith."""
    return {
        "run_name": run_name,
        "configurable": {"thread_id": thread_id},
        "metadata": {"thread_id": thread_id, **metadata},
    }
