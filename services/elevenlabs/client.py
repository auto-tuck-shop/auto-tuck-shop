import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class ElevenLabsError(Exception):
    """Exception raised for Eleven Labs API errors."""

    pass


class ElevenLabsClient:
    """Client for interacting with the Eleven Labs API."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.ELEVENLABS_API_KEY

        if not self.api_key:
            raise ElevenLabsError("ELEVENLABS_API_KEY is not configured")

    def _get_headers(self) -> dict[str, str]:
        return {
            "xi-api-key": self.api_key,
        }

    async def transcribe_audio(self, audio_data: bytes, filename: str) -> str:
        """
        Transcribe audio using Eleven Labs Speech-to-Text API.

        Args:
            audio_data: Binary audio data
            filename: Filename with extension (e.g., "audio.ogg")

        Returns:
            Transcribed text from the audio
        """
        files = {
            "file": (filename, audio_data),
        }
        data = {
            "model_id": "scribe_v1",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.post(
                    ELEVENLABS_API_URL,
                    headers=self._get_headers(),
                    files=files,
                    data=data,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(f"Eleven Labs HTTP error: {e.response.status_code} - {e.response.text}")
                raise ElevenLabsError(f"API request failed: {e.response.status_code}") from e
            except httpx.RequestError as e:
                logger.error(f"Eleven Labs request error: {e}")
                raise ElevenLabsError(f"Request failed: {e}") from e

        response_data = response.json()

        try:
            return response_data["text"]
        except KeyError as e:
            logger.error(f"Unexpected Eleven Labs response format: {response_data}")
            raise ElevenLabsError("Unexpected response format") from e
