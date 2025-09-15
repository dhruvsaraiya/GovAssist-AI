"""Azure OpenAI Realtime helper (persistent websocket variant).

Adds a lightweight connection manager so repeated messages during an active
chat reuse a single websocket instead of reconnecting every request.

Design goals:
 - Simple: single global manager instance.
 - Safe: serialize sends with a lock (model expects linear prompt/response flow).
 - Resilient: auto-reconnect if server closes; close after idle timeout.
 - Backwards compatible: `generate_assistant_reply(text)` signature unchanged.
"""

from __future__ import annotations

import asyncio, logging, json, time
from typing import Optional
from azure.identity.aio import DefaultAzureCredential
import websockets
from websockets.exceptions import InvalidStatusCode  # type: ignore
from ..config import get_settings

logger = logging.getLogger(__name__)

_cred: Optional[DefaultAzureCredential] = None


def _build_url(endpoint: str, deployment: str, api_version: str) -> str:
    base = endpoint.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif not base.startswith("wss://"):
        base = "wss://" + base
    if base.endswith("/openai/realtime"):
        base = base[: -len("/openai/realtime")].rstrip("/")
    return f"{base}/openai/realtime?api-version={api_version}&deployment={deployment}"


class RealtimeConnectionManager:
    def __init__(self, idle_seconds: float = 60.0):
        self.idle_seconds = idle_seconds
        self._ws: Optional[websockets.WebSocketClientProtocol] = None  # type: ignore
        self._url: Optional[str] = None
        self._last_used: float = 0.0
        self._lock = asyncio.Lock()

    async def _ensure_credential(self):
        global _cred
        if _cred is None:
            _cred = DefaultAzureCredential()
        return _cred

    async def _build_headers(self) -> dict:
        # Prefer API key if provided through settings (either alias) else AAD token
        settings = get_settings()
        api_key = settings.azure_openai_key or settings.azure_openai_api_key
        if api_key:
            headers: dict[str, str] = {"api-key": api_key}
        else:
            cred = await self._ensure_credential()
            token = await cred.get_token("https://cognitiveservices.azure.com/.default")
            headers = {"Authorization": f"Bearer {token.token}"}
        # headers["OpenAI-Beta"] = "realtime=v1"
        return headers

    async def _ensure_connection(self) -> websockets.WebSocketClientProtocol:  # type: ignore
        settings = get_settings()
        endpoint = settings.azure_openai_endpoint
        deployment = settings.azure_openai_deployment_name
        api_version = settings.openai_api_version
        if not endpoint or not deployment:
            raise RuntimeError("Realtime endpoint/deployment not configured")
        url = _build_url(str(endpoint), deployment, api_version)
        now = time.time()
        # Close idle connection
        if self._ws and (now - self._last_used > self.idle_seconds):
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        # Reconnect if URL changed or ws closed
        if self._ws is None or self._ws.closed or url != self._url:
            headers = await self._build_headers()
            logger.info("[realtime] connect url=%s auth=%s", url, "api-key" if "api-key" in headers else "aad-token")
            try:
                self._ws = await websockets.connect(url, additional_headers=headers)  # type: ignore[arg-type]
            except Exception as e:
                logger.error("[realtime] connect_failed url=%s err=%s", url, e)
                raise
            self._url = url
            # Initialize session once per connection
            await self._ws.send(json.dumps({
                "type": "session.update",
                "session": {"output_modalities": ["text"], "modalities": ["text"]}
            }))
        self._last_used = now
        return self._ws

    async def send_prompt(self, text: str, timeout: float) -> str:
        async with self._lock:  # serialize interactions
            ws = await self._ensure_connection()
            parts: list[str] = []
            try:
                await ws.send(json.dumps({"type": "response.create", "response": {"instructions": text}}))
                async with asyncio.timeout(timeout):
                    async for raw in ws:
                        try:
                            evt = json.loads(raw)
                        except Exception:
                            continue
                        et = evt.get("type")
                        if et == "response.output_text.delta":
                            d = evt.get("delta")
                            if d:
                                parts.append(d)
                        elif et == "response.done":
                            break
            except InvalidStatusCode as isc:
                logger.error("[realtime] http_status=%s during send url=%s", getattr(isc, 'status_code', 'n/a'), self._url)
            except Exception as e:
                logger.info("[realtime] send_failed err=%s url=%s", e, self._url)
                # Force reconnect next time
                try:
                    if ws and not ws.closed:
                        await ws.close()
                except Exception:
                    pass
                self._ws = None
                return ""
            self._last_used = time.time()
            return "".join(parts).strip()

_manager = RealtimeConnectionManager()


async def generate_assistant_reply(text: str, timeout: float = 30.0) -> str:
    if not text.strip():
        return ""
    try:
        return await _manager.send_prompt(text, timeout=timeout)
    except Exception as e:  # configuration or auth error
        logger.info("[realtime] unavailable %s", e)
        return ""
