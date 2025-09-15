"""Azure OpenAI Realtime (WebSocket) helper.

Currently implements a simplified utility to send a single user text message
to the `gpt-realtime` deployment and gather the aggregated textual response.

Design notes:
 - Uses keyless (Microsoft Entra ID) auth via DefaultAzureCredential.
 - Keeps a single connection per invocation for now (low traffic prototype).
 - Output modalities restricted to text (audio disabled until frontend usage).
 - Returns assistant text; caller wraps into ChatMessage.

Environment variables required:
  AZURE_OPENAI_ENDPOINT            e.g. https://<your-resource-name>.openai.azure.com/
  AZURE_OPENAI_DEPLOYMENT_NAME     (deployment name; assumed 'gpt-realtime' if unset)

Optional:
  OPENAI_API_VERSION (defaults to 2025-08-28 for realtime models)

If you later want streaming per-token UI updates, expose an async generator
that yields deltas instead of aggregating.
"""

from __future__ import annotations

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncAzureOpenAI
from ..config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()
REALTIME_API_VERSION = _settings.openai_api_version
DEFAULT_DEPLOYMENT = _settings.azure_openai_deployment_name


class RealtimeClient:
    """Thin wrapper around Azure OpenAI realtime connection.

    Not pooling connections yet; each `complete_once` call will open & close.
    """

    def __init__(self, endpoint: str, deployment: str = DEFAULT_DEPLOYMENT):
        self.endpoint = endpoint.rstrip("/")
        self.deployment = deployment
        self._credential: Optional[DefaultAzureCredential] = None
        self._client: Optional[AsyncAzureOpenAI] = None

    async def _ensure_client(self) -> AsyncAzureOpenAI:
        if self._client:
            return self._client
        # Initialize underlying AsyncAzureOpenAI client
        logger.info(
            "[realtime] Initializing RealtimeClient endpoint=%s deployment=%s api_version=%s",
            self.endpoint,
            self.deployment,
            REALTIME_API_VERSION,
        )

        self._credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            self._credential, "https://cognitiveservices.azure.com/.default"
        )
        self._client = AsyncAzureOpenAI(
            azure_endpoint=self.endpoint,
            azure_ad_token_provider=token_provider,
            api_version=REALTIME_API_VERSION,
        )
        return self._client


    @asynccontextmanager
    async def connect(self):  # type: ignore[override]
        client = await self._ensure_client()
        async with client.beta.realtime.connect(model=self.deployment) as connection:  # type: ignore[attr-defined]
            await connection.session.update(session={"output_modalities": ["text", "audio"]})
            yield connection

    async def complete_once(self, user_text: str, timeout: float = 30.0) -> str:
        """Send a single user message and aggregate the full text reply.

        Returns the concatenated assistant response text.
        """
        if not user_text.strip():
            return ""
        full_text_parts: list[str] = []
        try:
            async with asyncio.timeout(timeout):  # Python 3.11+ timeout context
                async with self.connect() as connection:
                    await connection.conversation.item.create(
                        item={
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": user_text}],
                        }
                    )
                    await connection.response.create()
                    async for event in connection:  # stream events
                        etype = getattr(event, "type", None)
                        if etype == "response.output_text.delta":
                            delta = getattr(event, "delta", "")
                            if delta:
                                full_text_parts.append(delta)
                        elif etype == "response.output_text.done":
                            # Completed all text segments
                            continue
                        elif etype == "response.done":
                            break
        except Exception as exc:  # pragma: no cover - log and surface empty response
            logger.exception("Realtime completion failed (no fallback) error=%s", exc)
            return ""
        return "".join(full_text_parts).strip()

    async def aclose(self):
        if self._credential:
            await self._credential.close()


_singleton: Optional[RealtimeClient] = None


def get_realtime_client() -> RealtimeClient:
    global _singleton
    if _singleton is None:
        settings = get_settings()
        endpoint = settings.azure_openai_endpoint
        if not endpoint:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT env var not set")
        deployment = settings.azure_openai_deployment_name or DEFAULT_DEPLOYMENT
        _singleton = RealtimeClient(endpoint=str(endpoint), deployment=deployment)
    return _singleton


async def generate_assistant_reply(prompt: str) -> str:
    """Public helper used by router to fetch assistant response.

    Returns empty string if service not properly configured or on error.
    """
    try:
        client = get_realtime_client()
    except Exception as e:  # configuration issue
        logger.warning("Realtime client unavailable: %s", e)
        return ""
    return await client.complete_once(prompt)
