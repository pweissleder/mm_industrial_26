"""The only paid Claude call site: Messages API, no retries, no SDK logging, key from settings."""

import logging
from typing import Any

import anthropic

from app.assistant.service import AssistantError


class LiveTransport:
    def __init__(self, api_key: str) -> None:
        for name in ("anthropic", "httpx", "httpx2", "httpcore", "httpcore2"):
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=90.0)

    def create(self, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.messages.create(**kwargs)
            result: dict[str, Any] = response.model_dump(mode="json", exclude_unset=True)
            return result
        except anthropic.APITimeoutError:
            raise AssistantError("Claude did not answer in time. Please try again.") from None
        except anthropic.APIConnectionError:
            raise AssistantError("Could not reach Claude (network). Please try again.") from None
        except anthropic.RateLimitError:
            raise AssistantError("Claude is busy (rate limit). Please wait a moment.") from None
        except anthropic.APIStatusError as error:
            body = error.body if isinstance(error.body, dict) else {}
            raw = body.get("error")
            info: dict[str, Any] = raw if isinstance(raw, dict) else {}
            message = str(info.get("message") or "")[:200]
            raise AssistantError(
                f"Claude API error (HTTP {error.status_code}): {message} No retry was made."
            ) from None
