"""Chat websocket router.

Provides a minimal streaming bridge:
 frontend <-> (FastAPI WS) <-> Azure OpenAI Realtime WebSocket API.

Flow (happy path):
1. Client connects to /api/chat/ws
2. Backend opens (or lazily opens) a websocket to Azure Realtime
3. On connect: send system instructions once (conversation.item.create role=system)
4. For each user message: create conversation item + request response
5. Stream response.output_text.delta events to client as assistant_delta
6. When response completes, send consolidated assistant_message

Deliberately minimal: no audio, no tool calls, no function execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import time
import websockets
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from ..config import get_settings, Settings


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

system_prompt = """
You are a government services assistant that helps users access official forms.

CRITICAL: When users ask for ANY form or mention these keywords, you MUST end your response with the specified marker:

AADHAAR REQUESTS (keywords: aadhaar, aadhar, identity card, id update, demographic update, address change):
- Always end response with: ##FORM:aadhaar##

MUDRA/INCOME REQUESTS (keywords: mudra loan, business loan, income certificate, PMMY, financial assistance, loan application):  
- Always end response with: ##FORM:income##

REQUIRED FORMAT:
1. Give a helpful 1-2 sentence response about the form
2. Add the exact marker: ##FORM:formname##

EXAMPLES:
User: "give me mudra loan form"
You: "I'll provide the Mudra loan application form for small business financing. ##FORM:income##"

User: "need aadhaar update form"  
You: "Here's the Aadhaar update form to modify your demographic details. ##FORM:aadhaar##"

User: "income certificate form"
You: "I'll open the income certificate application form for you. ##FORM:income##"

MANDATORY: The ##FORM:## marker is REQUIRED for all form requests - never skip it!
"""

class AzureRealtimeBridge:
    """Manage a single Azure Realtime websocket connection and simple send helpers."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.ws: Optional[websockets.WebSocketClientProtocol] = None  # type: ignore
        self._lock = asyncio.Lock()
        self._recv_task: Optional[asyncio.Task] = None
        self._response_buffer: list[str] = []
        self._frontend_websocket: Optional[WebSocket] = None
        self._closing = False
        self._last_request_started: Optional[float] = None
        self._current_response_id: Optional[str] = None
        self._response_sent: bool = False
        self._system_sent = False  # track if system instructions sent once

        logger.info("AzureRealtimeBridge initialized (deployment=%s, api_version=%s)",
                   self.settings.azure_openai_deployment_name, self.settings.openai_api_version)

    def _build_url(self) -> str:
        endpoint = (self.settings.azure_openai_endpoint or "").rstrip("/")
        if not endpoint:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT not configured")
        # Convert https -> wss, http -> ws
        if endpoint.startswith("https://"):
            endpoint = "wss://" + endpoint[len("https://") :]
        elif endpoint.startswith("http://"):
            endpoint = "ws://" + endpoint[len("http://") :]
        url = (
            f"{endpoint}/openai/realtime?api-version={self.settings.openai_api_version}"
            f"&deployment={self.settings.azure_openai_deployment_name}"
        )
        logger.info("Constructed Azure Realtime URL: %s", url)
        return url

    async def ensure_connected(self):
        if self.ws and not self.ws.close_code:
            logger.info("Azure realtime websocket already connected")
            return
        if websockets is None:
            raise RuntimeError("websockets package not installed")
        url = self._build_url()
        key = self.settings.azure_openai_key or self.settings.azure_openai_api_key
        if not key:
            raise RuntimeError("Azure OpenAI key not configured (AZURE_OPENAI_KEY or AZURE_OPENAI_API_KEY)")

        headers = [
            ("api-key", key),
            ("OpenAI-Beta", "realtime=v1"),
        ]

        logger.info("[azure] Connecting realtime websocket -> %s", url)
        connect_started = time.perf_counter()

        try:
            self.ws = await websockets.connect(
                url,
                additional_headers=headers,
                max_size=2**23,
                open_timeout=15,
                close_timeout=5,
            )
        except TypeError as te:
            logger.warning("[azure] additional_headers failed (%s), trying extra_headers", te)
            headers_dict = {k: v for k, v in headers}
            self.ws = await websockets.connect(
                url,
                extra_headers=headers_dict,
                max_size=2**23,
                open_timeout=15,
                close_timeout=5,
            )

        logger.info("[azure] Connected (%.2f ms)", (time.perf_counter() - connect_started) * 1000)

        # Configure session (minimal)
        session_cfg = {
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "tool_choice": "none",
            },
        }
        logger.info("[azure->] session.update: %s", json.dumps(session_cfg, ensure_ascii=False))
        await self.ws.send(json.dumps(session_cfg))  # type: ignore

        # Send system instructions once
        sys_msg = {
            "type": "conversation.item.create",
            "item": {
                "role": "system",
                "type": "message",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
        }
        await self.ws.send(json.dumps(sys_msg))
        self._system_sent = True
        logger.info("[azure->] Sent system instructions once")

        # Start background receiver
        self._recv_task = asyncio.create_task(self._receiver_loop())
        logger.info("[azure] Session configured & receiver loop started")

    async def _receiver_loop(self):
        if not self.ws:
            return
        try:
            async for raw in self.ws:  # type: ignore
                try:
                    event = json.loads(raw)
                except Exception:
                    logger.warning("[azure] Received non-JSON frame (ignored)")
                    continue
                await self._handle_event(event)
        except Exception as e:
            if not self._closing:
                logger.warning("[azure] Receiver loop stopped: %s", e)

    async def _handle_event(self, event: dict):
        etype = event.get("type")
        response_id = event.get("response_id")
        logger.info("[azure<-event] type=%s keys=%s", etype, list(event.keys()))

        if response_id and response_id != self._current_response_id:
            self._current_response_id = response_id
            self._response_sent = False

        if etype == "response.output_text.delta":
            delta = event.get("delta", "")
            if delta:
                self._response_buffer.append(delta)
                await self._emit_frontend({"type": "assistant_delta", "delta": delta})

        elif etype == "response.output_item.done" and not self._response_sent:
            text = self._extract_text_from_output_item(event.get("item", {}))
            if text:
                message_id = event.get("item", {}).get("id")
                await self._send_assistant_message(text, message_id, "output_item")

        elif etype == "response.content_part.done" and not self._response_sent:
            text = self._extract_text_from_content_part(event.get("part", {}))
            if text:
                await self._send_assistant_message(text, event_type="content_part_fallback")

        elif etype in {"response.output_text.done", "response.completed", "response.done"} and not self._response_sent:
            if self._response_buffer:
                full = "".join(self._response_buffer)
                self._response_buffer.clear()
                await self._send_assistant_message(full, event_type="buffered_fallback")

        elif etype == "error":
            err_msg = event.get("error", {}).get("message", "unknown_error")
            logger.error("[azure] Error event: %s", err_msg)
            await self._emit_frontend({"type": "error", "error": err_msg})
        else:
            logger.debug("[azure] Ignored event type: %s", etype)
        return

    def _calculate_response_duration(self) -> Optional[float]:
        if self._last_request_started is not None:
            return (time.perf_counter() - self._last_request_started) * 1000
        return None

    def _extract_text_from_output_item(self, item: dict) -> Optional[str]:
        if item.get("type") == "message" and item.get("role") == "assistant":
            content_list = item.get("content", [])
            for content_item in content_list:
                if content_item.get("type") == "text":
                    return content_item.get("text", "") or None
        return None

    def _extract_text_from_content_part(self, part: dict) -> Optional[str]:
        if part.get("type") == "text":
            return part.get("text", "") or None
        return None

    def _extract_form_from_text(self, text: str) -> tuple[str, Optional[str]]:
        import re
        form_pattern = r'##FORM:(\w+)##'
        match = re.search(form_pattern, text, re.IGNORECASE)
        if match:
            form_name = match.group(1).lower()
            clean_text = re.sub(form_pattern, '', text, flags=re.IGNORECASE).strip()
            return clean_text, form_name
        return text, None

    def _get_form_url(self, form_name: str) -> str:
        form_urls = {
            "aadhaar": "/forms/formAadhaar.html",
            "aadhar": "/forms/formAadhaar.html",
            "income": "/forms/formIncome.html",
            "mudra": "/forms/formIncome.html",
        }
        return form_urls.get(form_name, "")

    async def _send_assistant_message(self, text: str, message_id: Optional[str] = None, event_type: str = ""):
        duration_ms = self._calculate_response_duration()
        if duration_ms:
            logger.info("[azure] Response completed in %.2f ms, chars=%d (%s)",
                        duration_ms, len(text), event_type)
        else:
            logger.info("[azure] Response completed chars=%d (%s)", len(text), event_type)

        clean_text, form_name = self._extract_form_from_text(text)
        message_payload = {
            "type": "assistant_message",
            "message": {
                "id": message_id or str(uuid.uuid4()),
                "role": "assistant",
                "content": clean_text,
                "type": "text",
            },
        }

        if form_name:
            form_url = self._get_form_url(form_name)
            if form_url:
                message_payload["form"] = {"name": form_name, "url": form_url}

        await self._emit_frontend(message_payload)
        self._response_sent = True

    async def _emit_frontend(self, payload: dict):
        ws = self._frontend_websocket
        if ws:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                logger.exception("Failed sending payload to frontend")

    async def send_user_message(self, content: str):
        async with self._lock:
            await self.ensure_connected()
            if not self.ws:
                raise RuntimeError("Azure realtime websocket missing after connect")
            self._last_request_started = time.perf_counter()

            # 1. Create conversation item (user message)
            create_item = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": content}],
                },
            }
            await self.ws.send(json.dumps(create_item))  # type: ignore

            # 2. Request a response (no need to repeat system prompt)
            response_req = {
                "type": "response.create",
                "response": {
                    "modalities": ["text"],
                    "conversation": "auto",
                },
            }
            await self.ws.send(json.dumps(response_req))  # type: ignore

    async def close(self):
        self._closing = True
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except Exception:
                pass
        if self.ws:
            try:
                await self.ws.close()  # type: ignore
            except Exception:
                logger.exception("Error while closing azure websocket")
            self.ws = None


@router.websocket("/ws")
async def chat_ws(ws: WebSocket, settings: Settings = Depends(get_settings)):
    await ws.accept()
    bridge = AzureRealtimeBridge(settings)
    bridge._frontend_websocket = ws
    client_id = str(uuid.uuid4())
    logger.info("[client %s] Connected", client_id)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                await ws.send_text(json.dumps({"type": "error", "error": "invalid_json"}))
                continue
            mtype = msg.get("type")
            if mtype == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
                continue
            if mtype == "user_message":
                content = (msg.get("content") or "").strip()
                if not content:
                    await ws.send_text(json.dumps({"type": "error", "error": "empty_message"}))
                    continue
                mid = str(uuid.uuid4())
                await ws.send_text(json.dumps({"type": "ack", "message_id": mid}))
                try:
                    await bridge.send_user_message(content)
                except Exception as e:
                    logger.exception("[client %s] Failed sending to Azure realtime", client_id)
                    await ws.send_text(json.dumps({"type": "error", "error": str(e)}))
            else:
                await ws.send_text(json.dumps({"type": "error", "error": "unknown_event"}))
    except WebSocketDisconnect:
        logger.info("[client %s] Disconnected", client_id)
    except Exception as e:
        logger.exception("[client %s] Websocket error: %s", client_id, e)
    finally:
        try:
            await bridge.close()
        except Exception:
            logger.exception("[client %s] Error during bridge close", client_id)
