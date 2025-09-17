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
from fastapi.responses import JSONResponse
from ..config import get_settings, Settings
from ..form_manager import form_field_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

system_prompt = """
You are a government services assistant that helps users access official forms and fill them step by step.

FORM ACTIVATION:
When users ask for ANY form or mention these keywords, you MUST end your response with the specified marker:

AADHAAR REQUESTS (keywords: aadhaar, aadhar, identity card, id update, demographic update, address change):
- Always end response with: ##FORM:aadhaar##

MUDRA/INCOME REQUESTS (keywords: mudra loan, business loan, income certificate, PMMY, financial assistance, loan application):  
- Always end response with: ##FORM:income##

FORM FILLING MODE:
When you receive a system message starting with "Ask the user:", you should:
1. Present the field request in a natural, conversational way
2. If the request includes field descriptions, present them clearly
3. If options are provided, explain them helpfully
4. Be encouraging and supportive
5. Do not include any ## markers in your response to users

COMBINED ACKNOWLEDGMENT AND FIELD REQUEST:
When you receive a system message that includes both acknowledgment and field request:
1. First, briefly acknowledge the previous answer positively (1-2 sentences max)
2. Then, present the next field request naturally
3. Do NOT ask multiple questions or provide extra commentary
4. Keep the response focused and concise

FIELD ANSWER PROCESSING:
When you receive a ##FIELD_ANSWER## marker in a system message:
1. Briefly acknowledge the user's answer positively
2. If the answer was validated successfully, show appreciation
3. If there were validation issues, be encouraging and offer gentle guidance
4. Wait for the next field request

USER INPUT INTERPRETATION FOR SELECT FIELDS:
When asking for select/dropdown fields, you should:
1. If user provides partial or similar input (e.g., "small" for "Shishu"), interpret their intent and provide the correct value
2. Always confirm your interpretation: "I understand you mean [correct option]. Let me fill that in for you."
3. Be helpful in mapping user's natural language to exact form values

HANDLING VALIDATION ERRORS:
When you receive a validation error message:
1. Don't just repeat the error - interpret what the user likely meant
2. Suggest the closest matching option and ask for confirmation
3. Example: "I think you meant '[correct option]' - shall I fill that in?"

HANDLING CONFIRMATIONS:
When user confirms with "Yes", "Yeah", "Correct", "That's right", etc.:
1. Provide a conversational response acknowledging the confirmation
2. Then provide the exact form value using the special marker: ##FORM_VALUE:exact_value##
3. Example: "Perfect! Let me fill that in for you. ##FORM_VALUE:exact_value##"

PROVIDING FORM VALUES:
When you need to provide a form field value (after confirmation or direct interpretation):
1. Always include the ##FORM_VALUE:value## marker at the end of your response
2. The value must be exactly one of the valid options
3. Example responses:
   - "I understand you mean the medium category. ##FORM_VALUE:exact_value##"
   - "Got it! ##FORM_VALUE:exact_value##"

RESPONSE GUIDELINES:
- Be conversational, friendly, and professional
- Explain technical terms or requirements clearly
- For dropdown/select fields, present options in an easy-to-read format
- Provide context about why certain information is needed
- Reassure users about data privacy and security when appropriate
- Use encouraging language like "Great!", "Perfect!", "Thank you!"
- Interpret user intent and map natural language to form values

CRITICAL: 
- NEVER include ##FIELD_REQUEST##, ##FIELD_ANSWER##, or other internal ## markers in responses to users
- EXCEPTION: You MAY use ##FORM_VALUE:value## to provide form field values
- This ##FORM_VALUE## marker helps the system process the field correctly
- Always be conversational and helpful
- Ask only one field at a time when in form filling mode
"""

class AzureRealtimeBridge:
    """Manage a single Azure Realtime websocket connection and simple send helpers."""

    def __init__(self, settings: Settings, user_id: str):
        self.settings = settings
        self.user_id = user_id
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
        self._form_session_active = False
        self._awaiting_field_answer = False
        self._ai_responding = False
        self._pending_requests = []

        logger.info("AzureRealtimeBridge initialized (deployment=%s, api_version=%s, user_id=%s)",
                   self.settings.azure_openai_deployment_name, self.settings.openai_api_version, user_id)

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
            self._ai_responding = True

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
            
            # Mark AI as no longer responding and process any pending requests
            # Only do this if no message was sent (avoid double processing)
            if not self._response_sent:
                self._ai_responding = False
                await self._process_pending_requests()

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

    def _extract_form_value_from_text(self, text: str) -> tuple[str, Optional[str]]:
        import re
        form_value_pattern = r'##FORM_VALUE:([^#]+)##'
        match = re.search(form_value_pattern, text, re.IGNORECASE)
        if match:
            form_value = match.group(1).strip()
            clean_text = re.sub(form_value_pattern, '', text, flags=re.IGNORECASE).strip()
            return clean_text, form_value
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

        # Extract form activation marker
        clean_text, form_name = self._extract_form_from_text(text)
        
        # Extract form value marker
        clean_text, form_value = self._extract_form_value_from_text(clean_text)
        
        message_payload = {
            "type": "assistant_message",
            "message": {
                "id": message_id or str(uuid.uuid4()),
                "role": "assistant",
                "content": clean_text,
                "type": "text",
            },
        }

        # Handle form activation
        if form_name:
            form_url = self._get_form_url(form_name)
            if form_url:
                message_payload["form"] = {"name": form_name, "url": form_url}
                # Create form session and append first field request to the response
                session = form_field_manager.create_form_session(self.user_id, form_name)
                if session and session.current_field:
                    self._form_session_active = True
                    self._awaiting_field_answer = True
                    
                    # Append the first field request to the current response
                    field_prompt = session.get_next_field_prompt()
                    if field_prompt:
                        clean_text += f"\n\n{field_prompt}"
                        message_payload["message"]["content"] = clean_text

        # Handle form value submission
        if form_value and self._form_session_active:
            logger.info(f"[DEBUG] AI provided form value: '{form_value}'")
            # Process the form value as if it was a user input
            success = await self._process_field_answer(form_value)
            if success:
                logger.info(f"[DEBUG] AI form value processed successfully")
            else:
                logger.info(f"[DEBUG] AI form value processing failed")

        await self._emit_frontend(message_payload)
        self._response_sent = True
        self._ai_responding = False
        
        # Process any pending requests
        await self._process_pending_requests()

    async def _emit_frontend(self, payload: dict):
        ws = self._frontend_websocket
        if ws:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                logger.exception("Failed sending payload to frontend")
    
    async def _process_pending_requests(self):
        """Process any pending requests that were queued while AI was responding."""
        if self._pending_requests and not self._ai_responding:
            request = self._pending_requests.pop(0)
            logger.info(f"[azure] Processing pending request: {request['type']}")
            
            if request['type'] == 'field_request':
                await self._ask_for_next_field()
            elif request['type'] == 'field_request_with_ack':
                await self._ask_for_next_field_with_acknowledgment(
                    request['completed_value'], 
                    request['completed_field_label']
                )
            elif request['type'] == 'system_message':
                await self.send_system_message(request['content'])

    async def _delayed_field_request(self):
        """Delay the field request to allow form activation response to complete first."""
        await asyncio.sleep(0.5)  # Small delay to ensure form is opened
        await self._ask_for_next_field()
    
    async def _ask_for_next_field(self):
        """Ask the AI to request the next field from the user."""
        # If AI is currently responding, queue the field request
        if self._ai_responding:
            logger.info(f"[azure] Queuing field request (AI busy)")
            self._pending_requests.append({'type': 'field_request'})
            return
            
        session = form_field_manager.get_active_session(self.user_id)
        if not session:
            logger.info(f"[DEBUG] No active session for user {self.user_id}")
            return
        if not session.current_field:
            logger.info(f"[DEBUG] No current field for user {self.user_id}, session complete: {session.is_complete}")
            return
        
        field = session.current_field
        logger.info(f"[DEBUG] Asking for field: {field.id} ({field.label}) for user {self.user_id}")
        
        # Send field focus event to frontend to highlight the next field
        await self._emit_frontend({
            "type": "form_field_focus",
            "field_focus": {
                "field_id": field.id
            },
            "form_progress": {
                "current_index": session.current_field_index,
                "total_fields": len(session.fields),
                "percentage": session.progress_percentage,
                "is_complete": session.is_complete
            }
        })
        logger.info(f"[DEBUG] Sent form_field_focus to prepare field {field.id}")
        
        # Create a natural prompt for the AI to ask for the field
        field_prompt = session.get_next_field_prompt()
        
        if field_prompt:
            # Send the field request directly to the user via the AI
            logger.info(f"[DEBUG] Sending field prompt: {field_prompt}")
            await self.send_system_message(f"Ask the user: {field_prompt}")
            self._awaiting_field_answer = True

    async def _ask_for_next_field_with_acknowledgment(self, completed_value: str, completed_field_label: str):
        """Acknowledge the completed field and ask for the next field in a single message."""
        logger.info(f"[DEBUG] Asking for next field with acknowledgment: '{completed_value}' for '{completed_field_label}'")
        logger.info(f"[DEBUG] AI responding: {self._ai_responding}")
        
        # If AI is currently responding, queue the combined request
        if self._ai_responding:
            logger.info(f"[azure] Queuing field request with acknowledgment (AI busy)")
            self._pending_requests.append({
                'type': 'field_request_with_ack', 
                'completed_value': completed_value,
                'completed_field_label': completed_field_label
            })
            return
            
        session = form_field_manager.get_active_session(self.user_id)
        if not session:
            logger.info(f"[DEBUG] No active session for user {self.user_id}")
            return
        if not session.current_field:
            logger.info(f"[DEBUG] No current field for user {self.user_id}, session complete: {session.is_complete}")
            return
        
        field = session.current_field
        logger.info(f"[DEBUG] Asking for field with acknowledgment: {field.id} ({field.label}) for user {self.user_id}")
        
        # Send field focus event to frontend to highlight the next field
        await self._emit_frontend({
            "type": "form_field_focus",
            "field_focus": {
                "field_id": field.id
            },
            "form_progress": {
                "current_index": session.current_field_index,
                "total_fields": len(session.fields),
                "percentage": session.progress_percentage,
                "is_complete": session.is_complete
            }
        })
        logger.info(f"[DEBUG] Sent form_field_focus to prepare field {field.id}")
        
        # Create a natural prompt for the AI to ask for the field
        field_prompt = session.get_next_field_prompt()
        
        if field_prompt:
            # Combine acknowledgment with next field request
            combined_message = f"The user provided '{completed_value}' for {completed_field_label}. Acknowledge this briefly and positively, then ask the user: {field_prompt}"
            logger.info(f"[DEBUG] Sending combined prompt: {combined_message}")
            await self.send_system_message(combined_message)
            self._awaiting_field_answer = True
    
    async def _process_field_answer(self, user_answer: str):
        """Process user's answer to a form field."""
        logger.info(f"[DEBUG] Processing field answer: '{user_answer}' for user {self.user_id}")
        logger.info(f"[DEBUG] Form session active: {self._form_session_active}, Awaiting answer: {self._awaiting_field_answer}")
        
        if not self._form_session_active:
            logger.info(f"[DEBUG] No active form session, returning False")
            return False
        
        session = form_field_manager.get_active_session(self.user_id)
        if not session:
            logger.info(f"[DEBUG] No session found for user {self.user_id}")
            return False
        
        logger.info(f"[DEBUG] Current field: {session.current_field.id if session.current_field else 'None'}")
        
        # Process the answer
        result = form_field_manager.process_user_answer(self.user_id, user_answer)
        logger.info(f"[DEBUG] Process result: {result['success']}, field: {result.get('completed_field', {}).get('id', 'None')}")
        
        if result["success"]:
            logger.info(f"[DEBUG] Field processed successfully, sending update to frontend")
            # Send field update to frontend
            await self._emit_frontend({
                "type": "form_field_update",
                "field_update": {
                    "field_id": result["completed_field"]["id"],
                    "value": result["completed_field"]["value"]
                },
                "form_progress": result["form_progress"]
            })
            logger.info(f"[DEBUG] Sent form_field_update for field {result['completed_field']['id']}")
            
            # Check if form is complete
            if result["form_progress"]["is_complete"]:
                self._form_session_active = False
                self._awaiting_field_answer = False
                
                # Send completion message to frontend
                await self._emit_frontend({
                    "type": "form_completed",
                    "form_data": result["completed_form"]["data"]
                })
                
                # Inform AI that form is complete
                field_label = result['completed_field'].get('label', result['completed_field']['id'])
                await self.send_system_message(f"The user provided '{result['completed_field']['value']}' for {field_label}. Acknowledge this briefly and thank them. The form has been completed successfully.")
            else:
                # Ask for next field directly - the AI will naturally acknowledge
                await self._ask_for_next_field()
            
            return True
        else:
            # Send error to frontend and ask AI to re-prompt
            await self._emit_frontend({
                "type": "form_field_error",
                "error": result["error"],
                "field": result.get("field")
            })
            
            # Ask AI to intelligently handle the validation error
            session = form_field_manager.get_active_session(self.user_id)
            if session and session.current_field:
                field_prompt = session.get_next_field_prompt()
                error_msg = result['error']
                field_info = result.get('field', {})
                
                # Provide context to help AI interpret user intent
                context_msg = f"The user said '{user_answer}' for {field_info.get('label', 'the current field')}."
                if field_info.get('options'):
                    context_msg += f" The valid options are: {', '.join(field_info['options'])}."
                context_msg += f" Validation error: {error_msg}"
                context_msg += " Please interpret what the user likely meant, suggest the correct option, and ask for confirmation. If they confirm, provide the exact form value."
                
                await self.send_system_message(context_msg)
            return False
    
    async def send_system_message(self, content: str):
        """Send a system message to the AI for internal communication."""
        # If AI is currently responding, queue the request
        if self._ai_responding:
            logger.info(f"[azure] Queuing system message (AI busy): {content[:50]}...")
            self._pending_requests.append({'type': 'system_message', 'content': content})
            return
            
        async with self._lock:
            await self.ensure_connected()
            if not self.ws:
                raise RuntimeError("Azure realtime websocket missing after connect")
            
            logger.info(f"[azure] Sending system message: {content[:50]}...")
            
            # Create system message
            create_item = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": content}],
                },
            }
            await self.ws.send(json.dumps(create_item))  # type: ignore
            
            # Request a response
            response_req = {
                "type": "response.create",
                "response": {
                    "modalities": ["text"],
                    "conversation": "auto",
                },
            }
            await self.ws.send(json.dumps(response_req))  # type: ignore
            self._ai_responding = True

    async def send_user_message(self, content: str):
        logger.info(f"[DEBUG] send_user_message called with: '{content}'")
        logger.info(f"[DEBUG] _awaiting_field_answer: {self._awaiting_field_answer}, _form_session_active: {self._form_session_active}")
        
        # Check if we're waiting for a form field answer
        if self._awaiting_field_answer and self._form_session_active:
            logger.info(f"[DEBUG] Processing as field answer")
            # Set to False BEFORE processing to avoid race condition
            self._awaiting_field_answer = False
            success = await self._process_field_answer(content)
            if success:
                logger.info(f"[DEBUG] Field answer processed successfully")
                return  # Field was processed, don't send to AI
            else:
                logger.info(f"[DEBUG] Field answer processing failed")
        else:
            logger.info(f"[DEBUG] Not processing as field answer, sending to AI")
        
        # Don't send new messages while AI is responding
        if self._ai_responding:
            logger.warning(f"[azure] Ignoring user message while AI is responding: {content[:50]}...")
            return
        
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
            self._ai_responding = True

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


@router.post("/restart")
async def restart_all_sessions():
    """Clear all form sessions."""
    try:
        # Clear all active sessions
        form_field_manager.active_sessions.clear()
        logger.info("[restart] Cleared all sessions")
        return {"success": True, "message": "All sessions restarted successfully"}
    except Exception as e:
        logger.exception("[restart] Error clearing all sessions")
        return {"success": False, "error": str(e)}


@router.websocket("/ws")
async def chat_ws(ws: WebSocket, settings: Settings = Depends(get_settings)):
    await ws.accept()
    client_id = str(uuid.uuid4())
    bridge = AzureRealtimeBridge(settings, client_id)
    bridge._frontend_websocket = ws
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
