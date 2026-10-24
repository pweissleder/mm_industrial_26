"""Create the Managed Agents environment and agent once, then print their IDs.

Run from backend/:  uv run python -m scripts.setup_managed_agent
Copy the printed IDs into backend/.env. Re-running creates *new* resources, so
to change the agent later, update it (`client.beta.agents.update`) instead.
"""

import anthropic

from app.agent.graph import SYSTEM_PROMPT
from app.config import get_settings


def main() -> None:
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    environment = client.beta.environments.create(
        name="mm-industrial-26",
        config={
            "type": "cloud",
            "networking": {"type": "limited", "allow_package_managers": True},
        },
    )
    agent = client.beta.agents.create(
        name="Talk to Your Building",
        model=settings.anthropic_model,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {"enabled": True, "permission_policy": {"type": "auto"}},
                # Web tools are off; enable them only if the agent needs the web.
                "configs": [
                    {"name": "web_fetch", "enabled": False},
                    {"name": "web_search", "enabled": False},
                ],
            }
        ],
    )

    print(f"MANAGED_AGENT_ID={agent.id}")
    print(f"MANAGED_AGENT_ENVIRONMENT_ID={environment.id}")


if __name__ == "__main__":
    main()
