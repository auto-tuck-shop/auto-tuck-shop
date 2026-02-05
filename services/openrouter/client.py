import json
import logging
from typing import Any

import httpx
from django.conf import settings

from utils.timing import track

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(Exception):
    """Exception raised for OpenRouter API errors."""

    pass


class OpenRouterClient:
    """Client for interacting with the OpenRouter API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        self.model = model or settings.OPENROUTER_MODEL

        if not self.api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is not configured")

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://autotuckshop.site",
            "X-Title": "Auto Tuck Shop",
        }

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """
        Send a chat completion request to OpenRouter.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            response_format: Optional response format specification for JSON mode

        Returns:
            The assistant's response content
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }

        if response_format:
            payload["response_format"] = response_format

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    OPENROUTER_API_URL,
                    headers=self._get_headers(),
                    json=payload,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(f"OpenRouter HTTP error: {e.response.status_code} - {e.response.text}")
                raise OpenRouterError(f"API request failed: {e.response.status_code}") from e
            except httpx.RequestError as e:
                logger.error(f"OpenRouter request error: {e}")
                raise OpenRouterError(f"Request failed: {e}") from e

        data = response.json()

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            logger.error(f"Unexpected OpenRouter response format: {data}")
            raise OpenRouterError("Unexpected response format") from e

    async def parse_json_response(
        self,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        """
        Send a chat completion request and parse the response as JSON.

        Args:
            messages: List of message dicts with 'role' and 'content' keys

        Returns:
            Parsed JSON response as a dictionary
        """
        async with track("openrouter_llm"):
            content = await self.chat_completion(
                messages,
                response_format={"type": "json_object"},
            )

            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {content}")
                raise OpenRouterError(f"Invalid JSON response: {e}") from e
