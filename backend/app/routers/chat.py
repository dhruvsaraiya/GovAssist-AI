"""Chat websocket router.

Provides a minimal streaming bridge:
 frontend <-> (FastAPI WS) <-> Azure OpenAI Realtime WebSocket API.

Flow (happy path):
1. Client connects to /api/chat/ws
2. Backend opens (or lazily opens) a websocket to Azure Realtime
3. For each user message: create conversation item + request response
4. Stream response.output_text.delta events to client as assistant_delta
5. When response completes, send consolidated assistant_message

Deliberately minimal: no audio, no tool calls, no function execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends

from ..config import get_settings, Settings

try:
	import websockets  # type: ignore
except ImportError:  # pragma: no cover - runtime dependency
	websockets = None  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


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
		if self.ws and not self.ws.close_code:  # Connection is alive if close_code is None
			logger.info("Azure realtime websocket already connected")
			return
		if websockets is None:
			raise RuntimeError("websockets package not installed")
		url = self._build_url()
		key = self.settings.azure_openai_key or self.settings.azure_openai_api_key
		if not key:
			raise RuntimeError("Azure OpenAI key not configured (AZURE_OPENAI_KEY or AZURE_OPENAI_API_KEY)")
		
		# Prepare headers for websockets.connect() - not for create_connection()
		headers = [
			("api-key", key),
			("OpenAI-Beta", "realtime=v1"),
		]
		
		logger.info("[azure] Connecting realtime websocket -> %s", url)
		connect_started = time.perf_counter()
		
		try:
			# Use additional_headers (list of tuples) for older websockets versions
			# or extra_headers (dict) for newer versions
			self.ws = await websockets.connect(
				url, 
				additional_headers=headers,
				max_size=2**23,
				open_timeout=15,
				close_timeout=5
			)
		except TypeError as te:
			logger.warning("[azure] additional_headers failed (%s), trying extra_headers", te)
			# Fallback to extra_headers with dict format
			headers_dict = {k: v for k, v in headers}
			self.ws = await websockets.connect(
				url, 
				extra_headers=headers_dict,
				max_size=2**23,
				open_timeout=15,
				close_timeout=5
			)
		
		logger.info("[azure] Connected (%.2f ms)", (time.perf_counter() - connect_started) * 1000)

		# Configure session (minimal)
		session_cfg = {
			"type": "session.update",
			"session": {
				"modalities": ["text"],
				"instructions": """You are a helpful government forms assistant. When users request forms, you MUST include the exact form marker at the end of your response:

For Aadhaar card requests: Always end with ##FORM:aadhaar##
For Mudra loan or income certificate requests: Always end with ##FORM:income##

Examples:
User: "I need aadhaar form"
Assistant: "I'll help you with the Aadhaar card application form. ##FORM:aadhaar##"

User: "mudra loan form please" 
Assistant: "Here's the Mudra loan application form for small business financing. ##FORM:income##"

IMPORTANT: Always include the ##FORM:## marker exactly as shown above.""",
				"tool_choice": "none",
			},
		}
		logger.info("[azure->] session.update: %s", json.dumps(session_cfg, ensure_ascii=False))
		await self.ws.send(json.dumps(session_cfg))  # type: ignore

		# Start background receiver
		self._recv_task = asyncio.create_task(self._receiver_loop())
		logger.info("[azure] Session configured & receiver loop started")

	async def _receiver_loop(self):
		if not self.ws:
			return
		try:
			async for raw in self.ws:  # type: ignore
				logger.info("[azure<-raw] %s", (raw[:500] + "…") if isinstance(raw, str) and len(raw) > 500 else raw)
				try:
					event = json.loads(raw)
				except Exception:  # skip malformed
					logger.warning("[azure] Received non-JSON frame (ignored)")
					continue
				await self._handle_event(event)
		except Exception as e:  # noqa: BLE001
			if not self._closing:
				logger.warning("[azure] Receiver loop stopped: %s", e)

	async def _handle_event(self, event: dict):
		etype = event.get("type")
		response_id = event.get("response_id")
		logger.info("[azure<-event] type=%s keys=%s", etype, list(event.keys()))
		
		# Reset response tracking for new responses
		if response_id and response_id != self._current_response_id:
			self._current_response_id = response_id
			self._response_sent = False
			logger.info("[azure] New response started: %s", response_id)
		
		# Handle streaming text deltas
		if etype == "response.output_text.delta":
			delta = event.get("delta", "")
			if delta:
				self._response_buffer.append(delta)
				await self._emit_frontend({"type": "assistant_delta", "delta": delta})
		
		# Handle output item done events (contains the complete message item) - PRIMARY
		elif etype == "response.output_item.done" and not self._response_sent:
			text = self._extract_text_from_output_item(event.get("item", {}))
			if text:
				message_id = event.get("item", {}).get("id")
				await self._send_assistant_message(text, message_id, "output_item")
				return
		
		# Handle content part done events (contains the full text) - FALLBACK ONLY
		elif etype == "response.content_part.done" and not self._response_sent:
			text = self._extract_text_from_content_part(event.get("part", {}))
			if text:
				await self._send_assistant_message(text, event_type="content_part_fallback")
		
		# Handle traditional completion events (fallback for buffered content)
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
			# Log but ignore other event types for now.
			logger.info("[azure] Ignored event type=%s", etype)

	def _calculate_response_duration(self) -> Optional[float]:
		"""Calculate response duration in milliseconds if request start time is available."""
		if self._last_request_started is not None:
			return (time.perf_counter() - self._last_request_started) * 1000
		return None

	def _extract_text_from_output_item(self, item: dict) -> Optional[str]:
		"""Extract text content from an output item (message)."""
		if item.get("type") == "message" and item.get("role") == "assistant":
			content_list = item.get("content", [])
			for content_item in content_list:
				if content_item.get("type") == "text":
					text = content_item.get("text", "")
					if text:
						return text
		return None

	def _extract_text_from_content_part(self, part: dict) -> Optional[str]:
		"""Extract text content from a content part."""
		if part.get("type") == "text":
			return part.get("text", "") or None
		return None

	def _log_response_completion(self, text: str, event_type: str = ""):
		"""Log response completion with duration and character count."""
		duration_ms = self._calculate_response_duration()
		if duration_ms is not None:
			logger.info("[azure] Response completed in %.2f ms, chars=%d%s", 
					   duration_ms, len(text), f" ({event_type})" if event_type else "")
		else:
			logger.info("[azure] Response completed (no start time recorded) chars=%d%s", 
					   len(text), f" ({event_type})" if event_type else "")

	def _extract_form_from_text(self, text: str) -> tuple[str, Optional[str]]:
		"""Extract form marker from text and return clean text + form name."""
		import re
		logger.info("[form-debug] Checking text for form markers: %r", text[-300:])  # Check end of text
		
		# Look for ##FORM:formname## pattern (case insensitive)
		form_pattern = r'##FORM:(\w+)##'
		match = re.search(form_pattern, text, re.IGNORECASE)
		
		if match:
			form_name = match.group(1).lower()
			clean_text = re.sub(form_pattern, '', text, flags=re.IGNORECASE).strip()
			logger.info("[form-debug] Found form marker: %s", form_name)
			return clean_text, form_name
			
		# Fallback: keyword detection in the text itself
		text_lower = text.lower()
		detected_form = None
		
		if any(keyword in text_lower for keyword in ['aadhaar', 'aadhar', 'adhaar']):
			detected_form = 'aadhaar'
		elif any(keyword in text_lower for keyword in ['mudra', 'income', 'loan']):
			detected_form = 'income'
			
		if detected_form:
			logger.info("[form-debug] Detected form via keyword matching: %s", detected_form)
			return text, detected_form
			
		logger.info("[form-debug] No form marker or keywords found in text")
		return text, None

	def _get_form_url(self, form_name: str) -> str:
		"""Get the URL for a specific form."""
		form_urls = {
			"aadhaar": "/forms/formAadhaar.html",
			"aadhar": "/forms/formAadhaar.html",  # Alternative spelling
			"income": "/forms/formIncome.html",
			"mudra": "/forms/formIncome.html"  # Mudra loan uses income form
		}
		url = form_urls.get(form_name, "")
		logger.info("[form-debug] Form URL mapping: %s -> %s", form_name, url)
		return url

	async def _send_assistant_message(self, text: str, message_id: Optional[str] = None, event_type: str = ""):
		"""Send assistant message to frontend and mark response as sent."""
		self._log_response_completion(text, event_type)
		
		# Check if the message contains a form marker
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
		
		# Add form information if detected
		if form_name:
			form_url = self._get_form_url(form_name)
			if form_url:
				message_payload["form"] = {
					"name": form_name,
					"url": form_url
				}
				logger.info("[azure] Form detected in response: %s -> %s", form_name, form_url)
		
		await self._emit_frontend(message_payload)
		self._response_sent = True

	async def _emit_frontend(self, payload: dict):
		ws = self._frontend_websocket
		if ws:
			try:
				logger.info("[fe->] %s", payload.get("type"))
				await ws.send_text(json.dumps(payload))
			except Exception:
				logger.exception("Failed sending payload to frontend")

	async def send_user_message(self, content: str):
		async with self._lock:
			await self.ensure_connected()
			if not self.ws:
				raise RuntimeError("Azure realtime websocket missing after connect")
			self._last_request_started = time.perf_counter()
			logger.info("[user->azure] Sending user message chars=%d preview=%r", len(content), content[:80])
			# 1. Create a conversation item (user message)
			create_item = {
				"type": "conversation.item.create",
				"item": {
					"type": "message",
					"role": "user",
					"content": [
						{"type": "input_text", "text": content},
					],
				},
			}
			logger.info("[azure->] conversation.item.create len=%d", len(json.dumps(create_item)))
			await self.ws.send(json.dumps(create_item))  # type: ignore
			# 2. Request a new response
			response_req = {
				"type": "response.create",
				"response": {
					"modalities": ["text"],
					"instructions": "Respond helpfully and concisely.",
				},
			}
			logger.info("[azure->] response.create")
			await self.ws.send(json.dumps(response_req))  # type: ignore

	async def close(self):
		self._closing = True
		logger.info("[azure] Closing bridge")
		if self._recv_task:
			self._recv_task.cancel()
			try:
				await self._recv_task
			except Exception:
				logger.info("Receiver task cancelled with exception (ignored)")
		if self.ws:
			try:
				await self.ws.close()  # type: ignore
				logger.info("[azure] Underlying websocket closed")
			except Exception:
				logger.exception("Error while closing azure websocket")
			self.ws = None


@router.websocket("/ws")
async def chat_ws(ws: WebSocket, settings: Settings = Depends(get_settings)):
	await ws.accept()
	# Immediately inform client connection accepted so frontend can move from 'connecting' state.
	try:
		await ws.send_text(json.dumps({"type": "ready"}))
		logger.info("Sent ready event to client")
	except Exception:
		logger.exception("Failed to send ready event right after accept")
	bridge = AzureRealtimeBridge(settings)
	bridge._frontend_websocket = ws
	client_id = str(uuid.uuid4())
	logger.info("[client %s] Connected", client_id)
	try:
		while True:
			raw = await ws.receive_text()
			logger.info("[client %s ->] raw=%s", client_id, (raw[:300] + "…") if len(raw) > 300 else raw)
			try:
				msg = json.loads(raw)
			except Exception:
				logger.warning("[client %s] invalid JSON", client_id)
				await ws.send_text(json.dumps({"type": "error", "error": "invalid_json"}))
				continue
			mtype = msg.get("type")
			if mtype == "ping":
				await ws.send_text(json.dumps({"type": "pong"}))
				logger.info("[client %s] pong sent", client_id)
				continue
			if mtype == "user_message":
				content = (msg.get("content") or "").strip()
				logger.info("[client %s] user_message len=%d", client_id, len(content))
				if not content:
					await ws.send_text(json.dumps({"type": "error", "error": "empty_message"}))
					logger.info("[client %s] empty message rejected", client_id)
					continue
				mid = str(uuid.uuid4())
				await ws.send_text(json.dumps({"type": "ack", "message_id": mid}))
				logger.info("[client %s] ack sent message_id=%s", client_id, mid)
				try:
					await bridge.send_user_message(content)
				except Exception as e:  # noqa: BLE001
					logger.exception("[client %s] Failed sending to Azure realtime", client_id)
					await ws.send_text(json.dumps({"type": "error", "error": str(e)}))
			else:
				await ws.send_text(json.dumps({"type": "error", "error": "unknown_event"}))
				logger.info("[client %s] unknown event type=%s", client_id, mtype)
	except WebSocketDisconnect:
		logger.info("[client %s] Disconnected", client_id)
	except Exception as e:  # noqa: BLE001
		logger.exception("[client %s] Websocket error: %s", client_id, e)
	finally:
		try:
			await bridge.close()
			logger.info("[client %s] Bridge closed", client_id)
		except Exception:
			logger.exception("[client %s] Error during bridge close", client_id)

