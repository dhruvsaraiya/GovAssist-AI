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
import base64
import os
import struct
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

🚫 CRITICAL: NEVER USE JSON FORMAT - RESPOND ONLY IN PLAIN TEXT 🚫
Do not use curly braces { } or JSON structures in any response.
All responses must be plain conversational text.

🚫 CRITICAL: NEVER INCLUDE INTERNAL MARKERS 🚫
When producing output, never include <|...|> markers, alignment tags, or diff markers.

RESPONSE MODALITY AND LANGUAGE MATCHING:
- ALWAYS respond in the SAME LANGUAGE as the user's input (English → English, Hindi → Hindi, etc.)
- ALWAYS match the user's input modality: Audio input → Audio response, Text input → Text response

RESPONSE PROTOCOL (CRITICAL - FOLLOW EXACTLY):
For ALL responses (both audio and text), ALWAYS include conversational content AND form markers in the same response:

- The backend will automatically separate conversational content from form markers for audio playback
- Form markers will be processed by backend and never spoken aloud to users
- Your job is to include BOTH natural conversation AND the appropriate markers

CRITICAL: YOU MUST NEVER USE JSON FORMAT IN ANY RESPONSE!

MANDATORY RESPONSE FORMAT:
ALWAYS respond with plain text only. NO JSON EVER.
- Format: "[Natural helpful response in user's language] ##MARKER##"
- Use ##FORM:formname## for form opening
- Use ##FORM_VALUE:value## for field submissions  
- Use ##QUESTION_ANSWERED## after answering user questions

YOU ARE ABSOLUTELY FORBIDDEN FROM USING:
❌ {"result_text": "message"}
❌ {"response": "message", "value": "something"}
❌ Any text that starts with { or ends with }
❌ Any JSON structure whatsoever
❌ Any curly braces { } in your responses

EVERY RESPONSE MUST BE PLAIN TEXT WITHOUT CURLY BRACES!

CRITICAL FORM ACTIVATION EXAMPLES (MATCH USER'S LANGUAGE):
- User requests any form → Respond naturally in same language + appropriate ##FORM:formname## marker
- Hindi request → Hindi response + form marker
- English request → English response + form marker
- Mixed language → Respond in primary detected language + form marker

NEVER respond with just conversational text without form markers when forms are requested!

NEVER ask "which form?" - ALWAYS identify the form from context and activate it immediately!

FORM Operations:
1. FORM ACTIVATION - When users request forms, ALWAYS end response with appropriate marker:
   
   AADHAAR FORM TRIGGERS (always respond with ##FORM:aadhaar##):
   - "aadhaar", "aadhar", "adhaar" (any spelling variation)
   - "identity card", "ID card", "national ID"
   - "demographic update", "address change", "phone update" 
   - "aadhaar correction", "aadhaar enrollment"
   - ANY mention of aadhaar-related services
   
   MUDRA/INCOME FORM TRIGGERS (always respond with ##FORM:income##):
   - "mudra loan", "मुद्रा लोन", "PMMY", "Pradhan Mantri Mudra Yojana"
   - "business loan", "startup loan", "micro finance"
   - "income certificate", "income proof"
   - "financial assistance", "loan application"
   - ANY business or income-related form requests

   CRITICAL - FORM ACTIVATION RULE:
   When ANY form is requested, ALWAYS include the appropriate form marker in your response.
   
   FORMAT: [Natural conversational response in user's language] + [##FORM:formname##]
   
   NEVER respond with just conversational text - ALWAYS include the form marker!

   IMPORTANT: If user mentions ANYTHING related to these forms, immediately activate the appropriate form with the marker!

2. FORM FIELD REQUESTS - When you receive a system message asking you to request field information:
   - RESPOND ONLY IN PLAIN TEXT - NO CURLY BRACES { } ALLOWED
   - Present the field request naturally and conversationally
   - NEVER EVER use JSON format or any structure with curly braces
   - Explain options clearly if provided
   - Be encouraging and supportive
   - Ask ONLY for the requested field - don't add extra questions
   
   Example: When you receive "Now please ask the user for the next field information: Name of the Enterprise"
   CORRECT Response: "अब मुझे अगला विवरण चाहिए: आपके उद्यम का नाम क्या है?"
   FORBIDDEN Response: {"result_text": "अब मुझे अगला विवरण चाहिए", "value": "Name of the Enterprise"}
   FORBIDDEN Response: Any text containing { or }

3. USER RESPONSE PROCESSING - During form filling, intelligently categorize user responses:

   A) CLARIFICATION QUESTIONS - User is asking about the field:
      Examples: "What does this mean?", "I don't understand", "Can you explain this field?", "What should I put here?"
      Response: Answer their question helpfully, then ask for the field value again
      End with: ##QUESTION_ANSWERED##

   B) FIELD ANSWERS - User is providing a value (even conversationally):
      Examples: "dhruv enterprise", "my name is john smith", "it would be mumbai", "that's 25000", "ohh that is 123"
      Response: Provide positive acknowledgment in same language
      End with: ##FORM_VALUE:extracted_value##

   C) CONFIRMATIONS - User confirms a suggestion:
      Examples: "Yes", "Yeah", "Correct", "That's right", "हाँ", "सही है"
      Response: Acknowledge confirmation positively
      End with: ##FORM_VALUE:confirmed_value##

CRITICAL VALUE EXTRACTION RULES:
- Extract the core information regardless of conversational phrasing
- Remove filler words: "ohh", "that is", "it's", "my", "the", etc.
- Preserve proper names, addresses, and important details exactly
- For numbers: extract clean numeric values
- For select fields: map user's natural language to exact valid options
- Be aggressive in extraction - don't ask for clarification unless truly ambiguous

CLEAR EXAMPLES FOR ALL CASES:

1. FORM RESPONSE (when user requests a form):
   User: "I need an aadhaar card update"
   Response: "I'll help you with your Aadhaar update. Let me open the form for you. ##FORM:aadhaar##"

2. FORM VALUE RESPONSE (when user provides field data):
   User: "dhruv"
   CORRECT Response: "अच्छा, मैंने समझ लिया – आपका नाम है ध्रुव। ##FORM_VALUE:dhruv##"
   
   User: "dhruv enterprise"
   CORRECT Response: "Perfect! ##FORM_VALUE:dhruv enterprise##"
   
   User: "my name is john smith"
   CORRECT Response: "Thank you! ##FORM_VALUE:john smith##"

CRITICAL: NEVER use JSON format in responses!
   WRONG: {"result_text": "some message"}
   WRONG: "अच्छा, मैंने समझ लिया – आपका नाम है ध्रुव।" (missing ##FORM_VALUE:##)
   RIGHT: "अच्छा, मैंने समझ लिया – आपका नाम है ध्रुव। ##FORM_VALUE:dhruv##"

3. QUESTION ANSWERED RESPONSE (when user asks for clarification):
   User: "What does Application Sl. No. mean?"
   Response: "Application Serial Number is a unique identifier for your application. It's usually provided when you first apply. Could you please provide your Application Sl. No.? ##QUESTION_ANSWERED##"

4. CONFIRMATION HANDLING:
   System suggests: "I think you meant 'Shishu' category. Shall I fill that in?"
   User: "Yes"
   Response: "Perfect! Let me fill that in for you. ##FORM_VALUE:Shishu##"

AMBIGUOUS AND FUZZY RESPONSE HANDLING:

1. **PARTIAL OR UNCLEAR RESPONSES**:
   - When user provides incomplete information, ask for clarification
   - Example: User says "john" for full name → "I have 'john' - could you provide your complete name? ##QUESTION_ANSWERED##"
   - Example: User says "maybe" or "I think" → "I need a specific value. Could you please provide [field name]? ##QUESTION_ANSWERED##"

2. **FUZZY DROPDOWN MATCHING**:
   - For select/dropdown fields, intelligently match partial or similar inputs to valid options
   - Look for closest matches in meaning, spelling, or common synonyms
   - Consider language variations (English, Hindi, regional terms)
   - When confident in the match, confirm and provide the exact value
   - Example: User says "small" for a size field → "I understand you mean the small option. ##FORM_VALUE:[exact_small_option]##"

3. **VALIDATION ERROR RECOVERY**:
   - When validation fails, analyze what the user likely meant
   - Suggest the closest matching valid option from the available choices
   - Provide context about why you're suggesting that option
   - Ask for confirmation rather than starting over
   - Example: "I think you meant '[suggested_option]' from the available choices. Shall I fill that in? ##QUESTION_ANSWERED##"

4. **SMART INTERPRETATION STRATEGIES**:
   - Use context clues from the user's full response
   - Consider common abbreviations and variations
   - Handle typos and speech recognition errors
   - Map colloquial terms to formal options
   - Support multiple languages and transliterations

FORM RECOGNITION PRIORITY:
- NEVER ask "which form do you need?" when the user clearly mentions a specific service
- ALWAYS activate the appropriate form immediately when keywords are detected
- If genuinely unclear, default to the most likely form based on context
- Be proactive, not reactive - anticipate user needs

CONVERSATIONAL STYLE SUPPORT:
- Accept all conversational responses: "uhh that would be...", "I think it's...", "probably..."
- Extract the actual value from natural speech patterns
- Handle hesitations, corrections, and informal language
- Support multiple languages while maintaining the same extraction logic

SYSTEM MARKERS (processed by backend, invisible to users):
- ##FORM:form_name## - Opens specified form
- ##FORM_VALUE:extracted_value## - Submits field value
- ##QUESTION_ANSWERED## - Keeps system waiting for field answer after answering user question

RESPONSE QUALITY:
- Be warm, professional, and encouraging
- Use positive language: "Great!", "Perfect!", "Thank you!"
- Match user's energy and communication style
- Provide context when helpful
- Never show ## markers to users - they're processed by the system

CRITICAL SUCCESS FACTORS:
1. Always extract values aggressively from conversational responses
2. Distinguish clearly between questions and answers
3. Handle confirmations properly
4. Maintain language and modality consistency
5. Keep user experience natural and flowing"""

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
        self._audio_buffer: list[str] = []  # Buffer for streaming audio deltas

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

        # Configure session for GPT-4 Realtime API
        # Reference: https://platform.openai.com/docs/guides/realtime
        session_cfg = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "tool_choice": "none",
                "voice": "alloy",  # Available: alloy, echo, fable, onyx, nova, shimmer
                # CRITICAL: GPT-4 Realtime API audio format requirements
                "input_audio_format": "pcm16",   # 16-bit PCM, 24kHz, mono, little-endian
                "output_audio_format": "pcm16",  # 16-bit PCM, 24kHz, mono, little-endian
                "turn_detection": {
                    "type": "server_vad",         # Server-side Voice Activity Detection
                    "threshold": 0.5,             # VAD sensitivity (0.0-1.0, higher = less sensitive)
                    "prefix_padding_ms": 300,     # Audio before speech starts (ms)
                    "silence_duration_ms": 800,   # Silence duration before stopping (ms)
                },
                "input_audio_transcription": {
                    "model": "whisper-1"          # Whisper model for transcription
                },
            },
        }
        
        logger.info(f"🔧 [azure] Configuring session:")
        logger.info(f"   🎤 Input format: {session_cfg['session']['input_audio_format']}")
        logger.info(f"   🔊 Output format: {session_cfg['session']['output_audio_format']}")
        logger.info(f"   🗣️  Voice: {session_cfg['session']['voice']}")
        logger.info(f"   🎯 VAD threshold: {session_cfg['session']['turn_detection']['threshold']}")
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
            self._audio_buffer.clear()  # Clear audio buffer for new response

        if etype == "response.output_text.delta":
            delta = event.get("delta", "")
            if delta:
                self._response_buffer.append(delta)
                await self._emit_frontend({"type": "assistant_delta", "delta": delta})

        elif etype == "response.audio.delta":
            # Handle audio streaming from GPT-realtime
            audio_delta = event.get("delta", "")
            if audio_delta:
                self._audio_buffer.append(audio_delta)
                await self._emit_frontend({
                    "type": "assistant_audio_delta", 
                    "delta": audio_delta
                })

        elif etype == "response.output_item.done" and not self._response_sent:
            item = event.get("item", {})
            # Log the item structure for debugging
            logger.info(f"[DEBUG] output_item structure: {json.dumps(item, indent=2)}")
            
            # Check if this is an audio response
            if self._is_audio_output_item(item):
                await self._send_assistant_audio_message(item, "output_item")
            else:
                text = self._extract_text_from_output_item(item)
                if text:
                    message_id = item.get("id")
                    await self._send_assistant_message(text, message_id, "output_item")

        elif etype == "response.content_part.done" and not self._response_sent:
            part = event.get("part", {})
            # Log the part structure for debugging
            logger.info(f"[DEBUG] content_part structure: {json.dumps(part, indent=2)}")
            
            # Check if this is an audio content part
            if part.get("type") == "audio":
                # Use buffered audio data with transcript
                if self._audio_buffer:
                    audio_base64 = "".join(self._audio_buffer)
                    transcript = part.get("transcript", "Audio response")
                    message_id = event.get("item_id", str(uuid.uuid4()))
                    await self._send_assistant_audio_message_with_data(audio_base64, transcript, message_id)
                else:
                    # Fallback to text message with transcript
                    transcript = part.get("transcript", "")
                    if transcript:
                        await self._send_assistant_message(transcript, event_type="audio_transcript_fallback")
            else:
                text = self._extract_text_from_content_part(part)
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

        elif etype == "conversation.item.created" and event.get("item", {}).get("type") == "message":
            # Check if this item has audio content with transcription
            item = event.get("item", {})
            content = item.get("content", [])
            for content_part in content:
                if content_part.get("type") == "input_audio":
                    transcript = content_part.get("transcript", None)
                    logger.info("[azure] 🎤💬 Audio message created with transcript: '%s'", transcript)
                    
        else:
            logger.debug("[azure] Ignored event type: %s", etype)
        return

    def _clean_event_for_logging(self, event: dict) -> dict:
        """Remove large base64 audio data from events for cleaner logging"""
        import copy
        clean_event = copy.deepcopy(event)
        
        def clean_recursive(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key in ["audio", "delta"] and isinstance(value, str) and len(value) > 100:
                        obj[key] = f"<base64_data_{len(value)}_chars>"
                    else:
                        clean_recursive(value)
            elif isinstance(obj, list):
                for item in obj:
                    clean_recursive(item)
        
        clean_recursive(clean_event)
        return clean_event

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

    def _is_audio_output_item(self, item: dict) -> bool:
        """Check if output item contains audio content"""
        if item.get("type") == "message" and item.get("role") == "assistant":
            content_list = item.get("content", [])
            for content_item in content_list:
                if content_item.get("type") == "audio":
                    return True
        return False

    def _extract_audio_from_output_item(self, item: dict) -> Optional[str]:
        """Extract base64 audio data from output item"""
        if item.get("type") == "message" and item.get("role") == "assistant":
            content_list = item.get("content", [])
            for content_item in content_list:
                if content_item.get("type") == "audio":
                    return content_item.get("audio", "")
        return None

    def _save_audio_response(self, audio_base64: str, message_id: str) -> Optional[str]:
        """Save base64 audio to file and return file path"""
        try:
            # Create audio directory if it doesn't exist
            audio_dir = "static/audio"
            os.makedirs(audio_dir, exist_ok=True)
            
            # Generate filename
            filename = f"response_{message_id}_{int(time.time())}.wav"
            file_path = os.path.join(audio_dir, filename)
            
            # Decode and save audio
            audio_data = base64.b64decode(audio_base64)
            with open(file_path, 'wb') as f:
                f.write(audio_data)
            
            logger.info(f"Saved audio response: {file_path}, size: {len(audio_data)} bytes")
            # Return path with forward slashes for URL consistency
            return file_path.replace('\\', '/')
        except Exception as e:
            logger.error(f"Failed to save audio response: {e}")
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

    def _extract_question_answered_from_text(self, text: str) -> tuple[str, bool]:
        import re
        question_answered_pattern = r'##QUESTION_ANSWERED##'
        match = re.search(question_answered_pattern, text, re.IGNORECASE)
        if match:
            clean_text = re.sub(question_answered_pattern, '', text, flags=re.IGNORECASE).strip()
            return clean_text, True
        return text, False

    def _extract_conversational_content_only(self, text: str) -> str:
        """Extract only the conversational part of the text, removing all form markers.
        This ensures only natural speech gets converted to audio."""
        import re
        
        # Remove all form markers from the text
        patterns = [
            r'##FORM:\w+##',
            r'##FORM_VALUE:[^#]+##', 
            r'##QUESTION_ANSWERED##'
        ]
        
        clean_text = text
        for pattern in patterns:
            clean_text = re.sub(pattern, '', clean_text, flags=re.IGNORECASE)
        
        # Clean up extra whitespace and newlines
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        
        return clean_text

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
        
        # Extract question answered marker
        clean_text, question_answered = self._extract_question_answered_from_text(clean_text)
        
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

        # Handle question answered marker (AI answered a user question during form filling)
        if question_answered and self._form_session_active:
            logger.info(f"[DEBUG] AI answered a user question, keeping form session active")
            # Keep awaiting field answer since this was just answering a question
            self._awaiting_field_answer = True

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

    async def _send_assistant_audio_message(self, item: dict, event_type: str = ""):
        """Send assistant audio message to frontend"""
        duration_ms = self._calculate_response_duration()
        message_id = item.get("id", str(uuid.uuid4()))
        
        # Extract audio data
        audio_base64 = self._extract_audio_from_output_item(item)
        if not audio_base64:
            logger.warning("No audio data found in output item, treating as text message")
            # Fall back to text message handling
            text = self._extract_text_from_output_item(item)
            if text:
                await self._send_assistant_message(text, message_id, "audio_fallback_to_text")
            return
            
        # Save audio to file
        audio_file_path = self._save_audio_response(audio_base64, message_id)
        if not audio_file_path:
            logger.error("Failed to save audio response")
            return
            
        # Also extract any text content
        text_content = self._extract_text_from_output_item(item) or "Audio response"
        
        # Extract form markers from text if present
        clean_text, form_name = self._extract_form_from_text(text_content)
        clean_text, form_value = self._extract_form_value_from_text(clean_text)
        clean_text, question_answered = self._extract_question_answered_from_text(clean_text)
        
        # For audio messages, use only the conversational part (before any form markers)
        # This prevents form markers from being converted to speech
        audio_content = self._extract_conversational_content_only(text_content)
        
        if duration_ms:
            logger.info("[azure] Audio response completed in %.2f ms (%s)", duration_ms, event_type)
        
        message_payload = {
            "type": "audio_message",
            "message": {
                "id": message_id,
                "role": "assistant", 
                "content": audio_content,  # Use only conversational content for audio
                "type": "audio",
                "media_uri": f"/{audio_file_path}",  # audio_file_path already has proper format
            },
        }

        # Handle form activation (same as text messages)
        if form_name:
            form_url = self._get_form_url(form_name)
            if form_url:
                message_payload["form"] = {"name": form_name, "url": form_url}
                session = form_field_manager.create_form_session(self.user_id, form_name)
                if session and session.current_field:
                    self._form_session_active = True
                    self._awaiting_field_answer = True
                    # For audio messages, do NOT append field prompt to audio content
                    # Field prompts will be sent as separate system messages
                    field_prompt = session.get_next_field_prompt()
                    if field_prompt:
                        # Queue field prompt to be sent as system message after this audio response
                        self._pending_requests.append({
                            'type': 'field_request',
                            'field_prompt': field_prompt
                        })

        # Handle question answered and form values (same as text messages)
        if question_answered and self._form_session_active:
            self._awaiting_field_answer = True

        if form_value and self._form_session_active:
            success = await self._process_field_answer(form_value)
            logger.info(f"[DEBUG] AI audio form value processed: {success}")

        await self._emit_frontend(message_payload)
        self._response_sent = True
        self._ai_responding = False
        await self._process_pending_requests()

    async def _send_assistant_audio_message_from_part(self, part: dict):
        """Send assistant audio message from content part"""
        audio_base64 = part.get("audio", "")
        if not audio_base64:
            return
            
        message_id = str(uuid.uuid4())
        audio_file_path = self._save_audio_response(audio_base64, message_id)
        if not audio_file_path:
            return
            
        message_payload = {
            "type": "audio_message",
            "message": {
                "id": message_id,
                "role": "assistant",
                "content": "Audio response",
                "type": "audio", 
                "media_uri": f"/{audio_file_path}",  # audio_file_path already has proper format
            },
        }

        await self._emit_frontend(message_payload)
        self._response_sent = True
        self._ai_responding = False
        await self._process_pending_requests()

    async def _send_assistant_audio_message_with_data(self, audio_base64: str, transcript: str, message_id: str):
        """Send assistant audio message with buffered audio data"""
        duration_ms = self._calculate_response_duration()
        
        # Extract form markers from transcript if present
        clean_text, form_name = self._extract_form_from_text(transcript)
        clean_text, form_value = self._extract_form_value_from_text(clean_text)
        clean_text, question_answered = self._extract_question_answered_from_text(clean_text)
        
        # Debug logging for form detection
        logger.info(f"[DEBUG] Audio transcript: {transcript}")
        logger.info(f"[DEBUG] Extracted form_name: {form_name}")
        logger.info(f"[DEBUG] Extracted form_value: {form_value}")
        logger.info(f"[DEBUG] Clean text after extraction: {clean_text}")
                
        # For audio messages, use only the conversational part (before any form markers)
        # This prevents form markers from being converted to speech
        audio_content = self._extract_conversational_content_only(transcript)
        
        if duration_ms:
            logger.info("[azure] Audio response with data completed in %.2f ms", duration_ms)
        
        # Debug: Check audio format and convert if needed
        try:
            audio_bytes = base64.b64decode(audio_base64)
            logger.info(f"[DEBUG] Audio data size: {len(audio_bytes)} bytes")
            logger.info(f"[DEBUG] Audio header (first 20 bytes): {audio_bytes[:20].hex()}")
            
            # Check if this is already a WAV file (starts with 'RIFF')
            if not audio_bytes.startswith(b'RIFF'):
                logger.info("[DEBUG] Converting PCM to WAV format")
                # Convert raw PCM to WAV
                wav_data = self._pcm_to_wav(audio_bytes)
                audio_base64 = base64.b64encode(wav_data).decode('utf-8')
                logger.info(f"[DEBUG] Converted to WAV, new size: {len(wav_data)} bytes")
            else:
                logger.info("[DEBUG] Audio is already in WAV format")
                
        except Exception as e:
            logger.error(f"[DEBUG] Failed to process audio data: {e}")
        
        # Send base64 audio directly to frontend
        message_payload = {
            "type": "audio_message",
            "message": {
                "id": message_id,
                "role": "assistant", 
                "content": audio_content,  # Use only conversational content for audio
                "type": "audio",
                "audio_data": audio_base64,  # Send base64 directly (now in WAV format)
            },
        }

        # Handle form activation
        if form_name:
            form_url = self._get_form_url(form_name)
            if form_url:
                message_payload["form"] = {"name": form_name, "url": form_url}
                session = form_field_manager.create_form_session(self.user_id, form_name)
                if session and session.current_field:
                    self._form_session_active = True
                    self._awaiting_field_answer = True
                    # For audio messages, do NOT append field prompt to audio content
                    # Field prompts will be sent as separate system messages
                    field_prompt = session.get_next_field_prompt()
                    if field_prompt:
                        # Queue field prompt to be sent as system message after this audio response
                        self._pending_requests.append({
                            'type': 'field_request',
                            'field_prompt': field_prompt
                        })

        # Handle question answered and form values
        if question_answered and self._form_session_active:
            self._awaiting_field_answer = True

        if form_value and self._form_session_active:
            success = await self._process_field_answer(form_value)
            logger.info(f"[DEBUG] AI audio form value processed: {success}")

        await self._emit_frontend(message_payload)
        self._response_sent = True
        self._ai_responding = False
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
                if 'field_prompt' in request:
                    # Direct field prompt from audio message handling
                    await self.send_system_message(f"Now please ask the user for the next field information. Present this field request to the user in a natural and helpful way: {request['field_prompt']}")
                else:
                    # Legacy field request
                    await self._ask_for_next_field()
            elif request['type'] == 'field_request_with_ack':
                await self._ask_for_next_field_with_acknowledgment(
                    request['completed_value'], 
                    request['completed_field_label']
                )
            elif request['type'] == 'system_message':
                await self.send_system_message(request['content'])
            elif request['type'] == 'user_audio_message':
                await self.send_user_audio_message(request['audio_data'])
            elif request['type'] == 'user_text_message':
                await self.send_user_message(request['content'])

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
            await self.send_system_message(f"Now please ask the user for the next field information. Present this field request to the user in a natural and helpful way: {field_prompt}")
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
            combined_message = f"The user provided '{completed_value}' for {completed_field_label}. Acknowledge this briefly and positively, then please ask the user for the next field information in a natural way: {field_prompt}"
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

    def _process_audio_data(self, audio_data: str) -> Optional[str]:
        """Process base64 audio data for GPT-realtime.
        
        GPT-4 Realtime API expects:
        - Raw PCM 16-bit data (no WAV headers)
        - Base64 encoded
        - 24kHz sample rate, mono channel
        
        This function ensures we always return base64-encoded raw PCM data.
        """
        try:
            # Validate base64 data
            if not audio_data:
                logger.error("❌ Empty audio data provided")
                return None
                
            # Test base64 decoding
            try:
                decoded_data = base64.b64decode(audio_data)
            except Exception as decode_error:
                logger.error(f"❌ Invalid base64 audio data: {decode_error}")
                return None
                
            logger.info(f"🎵 Processing audio: {len(audio_data)} base64 chars -> {len(decoded_data)} bytes")
            
            # Helper: parse WAV header robustly (support extra chunks)
            def _parse_wav(wav_bytes: bytes):
                if len(wav_bytes) < 44 or not wav_bytes.startswith(b'RIFF') or wav_bytes[8:12] != b'WAVE':
                    return None
                # Iterate chunks starting at byte 12
                offset = 12
                fmt_chunk = None
                data_chunk = None
                while offset + 8 <= len(wav_bytes):
                    chunk_id = wav_bytes[offset:offset+4]
                    chunk_size = int.from_bytes(wav_bytes[offset+4:offset+8], 'little', signed=False)
                    next_offset = offset + 8 + chunk_size
                    if chunk_id == b'fmt ':
                        fmt_chunk = wav_bytes[offset+8:offset+8+chunk_size]
                    elif chunk_id == b'data':
                        data_chunk = (offset+8, chunk_size)
                        # We can break after finding data, but continue just in case (keep first occurrence)
                        break
                    offset = next_offset
                if not fmt_chunk or not data_chunk:
                    return None
                # Parse fmt chunk (PCM expected)
                if len(fmt_chunk) < 16:
                    return None
                audio_format = int.from_bytes(fmt_chunk[0:2], 'little')
                channels = int.from_bytes(fmt_chunk[2:4], 'little')
                sample_rate = int.from_bytes(fmt_chunk[4:8], 'little')
                bits_per_sample = int.from_bytes(fmt_chunk[14:16], 'little')
                data_offset, data_size = data_chunk
                pcm_bytes = wav_bytes[data_offset:data_offset+data_size]
                return {
                    'audio_format': audio_format,
                    'channels': channels,
                    'sample_rate': sample_rate,
                    'bits_per_sample': bits_per_sample,
                    'pcm': pcm_bytes
                }

            # Helper: simple linear resample 16-bit mono PCM
            def _resample_pcm_16le_mono(pcm: bytes, orig_rate: int, target_rate: int = 24000) -> bytes:
                if orig_rate == target_rate:
                    return pcm
                if orig_rate <= 0:
                    return pcm
                import math
                sample_count = len(pcm) // 2
                if sample_count == 0:
                    return pcm
                # Unpack samples
                samples = struct.unpack('<' + 'h'*sample_count, pcm)
                ratio = target_rate / orig_rate
                new_count = max(1, int(math.floor(sample_count * ratio)))
                resampled = []
                for i in range(new_count):
                    # Source position
                    src_pos = i / ratio
                    s0 = int(math.floor(src_pos))
                    s1 = min(s0 + 1, sample_count - 1)
                    frac = src_pos - s0
                    v0 = samples[s0]
                    v1 = samples[s1]
                    interp = int(v0 + (v1 - v0) * frac)
                    # Clamp just in case
                    if interp > 32767: interp = 32767
                    if interp < -32768: interp = -32768
                    resampled.append(interp)
                return struct.pack('<' + 'h'*len(resampled), *resampled)

            # Helper: transcode compressed / container audio (e.g., 3gp/mp4/aac) -> raw pcm16 using ffmpeg if available
            def _ffmpeg_transcode_to_pcm16(container_bytes: bytes) -> Optional[bytes]:
                try:
                    import shutil, subprocess
                    if not shutil.which('ffmpeg'):
                        logger.error("❌ ffmpeg not found on PATH; cannot transcode container audio to PCM16")
                        return None
                    # Invoke ffmpeg: input from stdin, output s16le mono 24kHz to stdout
                    proc = subprocess.run(
                        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "24000", "pipe:1"],
                        input=container_bytes,
                        capture_output=True,
                        check=False
                    )
                    if proc.returncode != 0:
                        logger.error(f"❌ ffmpeg transcode failed (code {proc.returncode}): {proc.stderr.decode('utf-8', 'ignore')[:200]}")
                        return None
                    pcm_out = proc.stdout
                    if not pcm_out:
                        logger.error("❌ ffmpeg produced empty output")
                        return None
                    logger.info(f"✅ ffmpeg transcoded container audio -> {len(pcm_out)} bytes PCM16 @24kHz")
                    return pcm_out
                except Exception as e:
                    logger.error(f"❌ ffmpeg transcode exception: {e}")
                    return None

            # Detect MP4/3GP/ISO BMFF container (bytes 4:8 == 'ftyp') or '....ftyp'
            if len(decoded_data) >= 12 and decoded_data[4:8] == b'ftyp':
                logger.info("🎞️ Detected ISO-BMFF/MP4/3GP container (ftyp) - attempting ffmpeg transcode to PCM16")
                transcoded = _ffmpeg_transcode_to_pcm16(decoded_data)
                if transcoded:
                    if len(transcoded) < 1000:
                        logger.warning(f"⚠️ Transcoded PCM very small: {len(transcoded)} bytes")
                    processed_base64 = base64.b64encode(transcoded).decode('utf-8')
                    logger.info(f"✅ Prepared base64 PCM payload (from container) size: {len(processed_base64)} chars")
                    return processed_base64
                else:
                    logger.error("❌ Could not transcode container audio; aborting audio send")
                    return None

            # Detect possible AAC ADTS (0xFFF syncword)
            if len(decoded_data) > 4 and decoded_data[0] == 0xFF and (decoded_data[1] & 0xF0) == 0xF0:
                logger.info("🎞️ Detected probable AAC ADTS stream - attempting ffmpeg transcode to PCM16")
                transcoded = _ffmpeg_transcode_to_pcm16(decoded_data)
                if transcoded:
                    if len(transcoded) < 1000:
                        logger.warning(f"⚠️ Transcoded PCM very small: {len(transcoded)} bytes")
                    processed_base64 = base64.b64encode(transcoded).decode('utf-8')
                    logger.info(f"✅ Prepared base64 PCM payload (from AAC) size: {len(processed_base64)} chars")
                    return processed_base64
                else:
                    logger.error("❌ Could not transcode AAC audio; aborting audio send")
                    return None

            # Check if this is WAV data (starts with 'RIFF')
            if decoded_data.startswith(b'RIFF'):
                logger.info("🎵 Detected WAV format; parsing header & extracting PCM")
                wav_info = _parse_wav(decoded_data)
                if not wav_info:
                    logger.error("❌ Failed to parse WAV structure; falling back to naive header skip")
                    if len(decoded_data) < 44:
                        return None
                    pcm_data = decoded_data[44:]
                    sample_rate = 24000
                    channels = 1
                    bits = 16
                else:
                    sample_rate = wav_info['sample_rate']
                    channels = wav_info['channels']
                    bits = wav_info['bits_per_sample']
                    pcm_data = wav_info['pcm']
                logger.info(f"🔍 WAV meta -> sr={sample_rate}Hz channels={channels} bits={bits} pcm_bytes={len(pcm_data)}")

                if bits != 16:
                    logger.error(f"⚠️ Unexpected bits_per_sample {bits}; only 16-bit PCM supported")
                    return None
                if channels != 1:
                    logger.info(f"🔁 Downmixing from {channels} channels to mono")
                    # Naive downmix: average channels
                    frame_count = len(pcm_data) // (2 * channels)
                    mono_frames = []
                    for i in range(frame_count):
                        acc = 0
                        for c in range(channels):
                            start = (i * channels + c) * 2
                            sample = struct.unpack('<h', pcm_data[start:start+2])[0]
                            acc += sample
                        mono_val = int(acc / channels)
                        mono_frames.append(mono_val)
                    pcm_data = struct.pack('<' + 'h'*len(mono_frames), *mono_frames)
                    channels = 1
                    logger.info(f"✅ Downmixed to mono -> {len(pcm_data)} bytes")

                # Resample if needed
                if sample_rate != 24000:
                    logger.info(f"🔄 Resampling from {sample_rate}Hz to 24000Hz")
                    before = len(pcm_data)
                    pcm_data = _resample_pcm_16le_mono(pcm_data, sample_rate, 24000)
                    logger.info(f"✅ Resampled PCM bytes: {before} -> {len(pcm_data)}")
                else:
                    logger.info("✅ Sample rate already 24000Hz; no resample required")

                if len(pcm_data) < 1000:
                    logger.warning(f"⚠️ PCM data seems very small: {len(pcm_data)} bytes (may be silence or very short)")

                processed_base64 = base64.b64encode(pcm_data).decode('utf-8')
                logger.info(f"✅ Prepared base64 PCM payload size: {len(processed_base64)} chars")
                return processed_base64
                
            else:
                logger.info("🎵 Data appears to be raw PCM format")
                
                # Validate PCM data size
                if len(decoded_data) < 1000:
                    logger.warning(f"⚠️  PCM data seems very small: {len(decoded_data)} bytes")
                
                # Data is already raw PCM, but ensure it's properly base64 encoded
                # Re-encode to ensure consistent base64 format
                processed_base64 = base64.b64encode(decoded_data).decode('utf-8')
                logger.info(f"✅ Re-encoded PCM to base64: {len(processed_base64)} chars")
                return processed_base64
                
        except Exception as e:
            logger.error(f"❌ Failed to process audio data: {e}")
            import traceback
            logger.error(f"❌ Traceback: {traceback.format_exc()}")
            return None

    def _pcm_to_wav(self, pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
        """Convert raw PCM data to WAV format"""
        try:
            # WAV file header
            fmt_chunk_size = 16
            data_chunk_size = len(pcm_data)
            file_size = 36 + data_chunk_size
            
            # Create WAV header
            wav_header = struct.pack(
                '<4sI4s4sIHHIIHH4sI',
                b'RIFF',           # Chunk ID
                file_size,         # File size - 8
                b'WAVE',           # Format
                b'fmt ',           # Subchunk1 ID
                fmt_chunk_size,    # Subchunk1 size
                1,                 # Audio format (PCM)
                channels,          # Number of channels
                sample_rate,       # Sample rate
                sample_rate * channels * bits_per_sample // 8,  # Byte rate
                channels * bits_per_sample // 8,                # Block align
                bits_per_sample,   # Bits per sample
                b'data',           # Subchunk2 ID
                data_chunk_size    # Subchunk2 size
            )
            
            return wav_header + pcm_data
        except Exception as e:
            logger.error(f"Failed to convert PCM to WAV: {e}")
            return pcm_data  # Return original data as fallback

    async def _debug_save_audio(self, audio_base64: str, prefix: str = "debug"):
        """Save audio to debug directory for manual verification and convert PCM to WAV"""
        try:
            # Create debug directory
            debug_dir = "debug_audio"
            os.makedirs(debug_dir, exist_ok=True)
            
            # Decode audio data
            audio_bytes = base64.b64decode(audio_base64)
            timestamp = int(time.time())
            
            logger.info(f"[DEBUG] 🎵 Processing {prefix}: {len(audio_base64)} base64 chars -> {len(audio_bytes)} bytes")
            
            # Always save raw binary data first
            raw_filename = f"{prefix}_{timestamp}_raw.bin"
            raw_path = os.path.join(debug_dir, raw_filename)
            with open(raw_path, 'wb') as f:
                f.write(audio_bytes)
            logger.info(f"[DEBUG] 📁 Saved raw binary: {raw_path}")
            
            # Check if this is already a WAV file (starts with 'RIFF')
            if audio_bytes.startswith(b'RIFF'):
                logger.info(f"[DEBUG] 🎵 Detected WAV format")
                # Save WAV file for debugging
                wav_filename = f"{prefix}_{timestamp}_original.wav"
                wav_path = os.path.join(debug_dir, wav_filename)
                with open(wav_path, 'wb') as f:
                    f.write(audio_bytes)
                logger.info(f"[DEBUG] 🎵 Saved original WAV: {wav_path}")
                
                # Extract and save raw PCM for comparison
                if len(audio_bytes) > 44:
                    pcm_data = audio_bytes[44:]  # Skip WAV header
                    pcm_filename = f"{prefix}_{timestamp}_extracted.pcm"
                    pcm_path = os.path.join(debug_dir, pcm_filename)
                    with open(pcm_path, 'wb') as f:
                        f.write(pcm_data)
                    
                    # Convert extracted PCM back to WAV for verification
                    wav_from_pcm = self._pcm_to_wav(pcm_data)
                    wav_from_pcm_filename = f"{prefix}_{timestamp}_pcm_to_wav.wav"
                    wav_from_pcm_path = os.path.join(debug_dir, wav_from_pcm_filename)
                    with open(wav_from_pcm_path, 'wb') as f:
                        f.write(wav_from_pcm)
                    
                    logger.info(f"[DEBUG] 🎵 Extracted PCM ({len(pcm_data)} bytes): {pcm_path}")
                    logger.info(f"[DEBUG] 🎵 PCM converted to WAV: {wav_from_pcm_path}")
                    
                    # Calculate expected duration
                    duration_seconds = len(pcm_data) / (24000 * 2)  # 24kHz, 16-bit
                    logger.info(f"[DEBUG] ⏱️  Expected duration: {duration_seconds:.2f} seconds")
                    
            else:
                logger.info(f"[DEBUG] 🎵 Treating as raw PCM format")
                # Save raw PCM data
                pcm_filename = f"{prefix}_{timestamp}.pcm"
                pcm_path = os.path.join(debug_dir, pcm_filename)
                with open(pcm_path, 'wb') as f:
                    f.write(audio_bytes)
                
                # Convert PCM to WAV for easier playback and verification
                wav_data = self._pcm_to_wav(audio_bytes)
                wav_filename = f"{prefix}_{timestamp}_pcm_to_wav.wav"
                wav_path = os.path.join(debug_dir, wav_filename)
                with open(wav_path, 'wb') as f:
                    f.write(wav_data)
                    
                # Calculate expected duration
                duration_seconds = len(audio_bytes) / (24000 * 2)  # 24kHz, 16-bit
                logger.info(f"[DEBUG] 🎵 Saved raw PCM: {pcm_path}")
                logger.info(f"[DEBUG] 🎵 Converted to WAV: {wav_path}")
                logger.info(f"[DEBUG] ⏱️  Expected duration: {duration_seconds:.2f} seconds")
                logger.info(f"[DEBUG] 🎧 Play with: ffplay -f s16le -ar 24000 -ac 1 {pcm_path}")
            
            logger.info(f"[DEBUG] 🎧 Play WAV file: {os.path.abspath(wav_path) if 'wav_path' in locals() else 'N/A'}")
            
        except Exception as e:
            logger.error(f"[DEBUG] ❌ Failed to save debug audio: {e}")
            import traceback
            logger.error(f"[DEBUG] ❌ Traceback: {traceback.format_exc()}")

    async def send_user_audio_message(self, audio_data: str):
        """Send user audio message to Azure Realtime API"""
        logger.info(f"[DEBUG] send_user_audio_message called with audio data size: {len(audio_data)} chars")
        
        # DEBUG: Save raw audio_data from frontend FIRST
        await self._debug_save_audio(audio_data, "raw_from_frontend")
        
        # If AI is currently responding, queue the audio message
        if self._ai_responding:
            logger.info(f"[azure] Queuing user audio message (AI busy)")
            self._pending_requests.append({
                'type': 'user_audio_message', 
                'audio_data': audio_data
            })
            return
            
        # Process the audio data
        processed_audio = self._process_audio_data(audio_data)
        if not processed_audio:
            logger.error("Failed to process audio data")
            return
        
        # DEBUG: Save processed audio for verification
        await self._debug_save_audio(processed_audio, "user_input_processed")
        
        # Verify processed audio size and format for GPT-4 Realtime API
        try:
            processed_bytes = base64.b64decode(processed_audio)
            duration_seconds = len(processed_bytes) / (24000 * 2)  # 24kHz, 16-bit PCM
            logger.info(f"🚀 [azure] Sending to GPT-4 Realtime API:")
            logger.info(f"   📊 Raw PCM: {len(processed_bytes)} bytes")
            logger.info(f"   📊 Base64: {len(processed_audio)} chars")
            logger.info(f"   ⏱️  Duration: ~{duration_seconds:.2f} seconds")
            logger.info(f"   🎵 Format: 16-bit PCM, 24kHz, mono")
            # Silence detection (simple): check first 2k samples variance
            if len(processed_bytes) >= 4000:
                import struct as _struct
                sample_ct = min(len(processed_bytes)//2, 2000)
                samples = _struct.unpack('<' + 'h'*sample_ct, processed_bytes[:sample_ct*2])
                max_amp = max(abs(s) for s in samples) if samples else 0
                if max_amp < 50:  # near silence threshold
                    logger.warning(f"🤫 Detected near-silence audio (max amplitude {max_amp}); check recording pipeline")
        except Exception as verify_error:
            logger.error(f"❌ Failed to verify processed audio: {verify_error}")
            return
        
        async with self._lock:
            await self.ensure_connected()
            if not self.ws:
                raise RuntimeError("Azure realtime websocket missing after connect")
            self._last_request_started = time.perf_counter()

            # 1. Create conversation item (user audio message)
            # GPT-4 Realtime API expects: "audio" field with base64-encoded raw PCM data
            create_item = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{
                        "type": "input_audio", 
                        "audio": processed_audio  # Base64-encoded raw PCM 16-bit data
                    }],
                },
            }
            logger.info(f"🚀 [azure->] conversation.item.create")
            logger.info(f"   📤 Sending audio content: {len(processed_audio)} base64 chars")
            await self.ws.send(json.dumps(create_item))  # type: ignore

            # 2. Request both audio and text response in single request
            response_req = {
                "type": "response.create",
                "response": {
                    "modalities": ["audio", "text"],
                    "instructions": "Provide a natural conversational audio response for the user. In the text output, only include form markers (like ##FORM:formname##) and form-related instructions - do not repeat the conversational content in text.",
                },
            }
            logger.info("[azure->] response.create (requesting audio + text)")
            await self.ws.send(json.dumps(response_req))  # type: ignore
            self._ai_responding = True

    async def send_user_message(self, content: str):
        logger.info(f"[DEBUG] send_user_message called with: '{content}'")
        logger.info(f"[DEBUG] _awaiting_field_answer: {self._awaiting_field_answer}, _form_session_active: {self._form_session_active}")
        
        # Check if we're waiting for a form field answer
        if self._awaiting_field_answer and self._form_session_active:
            logger.info(f"[DEBUG] User response during form filling - letting AI determine if question or answer")
            # Always send to AI - it will intelligently determine if this is:
            # 1. A question (will respond with ##QUESTION_ANSWERED## marker)
            # 2. An answer (will respond with ##FORM_VALUE:## marker)
            # This removes language-specific heuristics and supports all languages/modalities
        else:
            logger.info(f"[DEBUG] Not in form filling mode, sending to AI")
        
        # If AI is currently responding, queue the text message
        if self._ai_responding:
            logger.info(f"[azure] Queuing user text message (AI busy): {content[:50]}...")
            self._pending_requests.append({
                'type': 'user_text_message', 
                'content': content
            })
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
            elif mtype == "user_audio_message":
                audio_data = msg.get("audio_data", "").strip()
                if not audio_data:
                    await ws.send_text(json.dumps({"type": "error", "error": "empty_audio_data"}))
                    continue
                mid = str(uuid.uuid4())
                await ws.send_text(json.dumps({"type": "ack", "message_id": mid}))
                try:
                    await bridge.send_user_audio_message(audio_data)
                except Exception as e:
                    logger.exception("[client %s] Failed processing audio message", client_id)
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
