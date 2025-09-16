from fastapi import APIRouter, Form, UploadFile, File, Request, WebSocket, WebSocketDisconnect
from typing import Optional, Dict, Any
from ..schemas.chat import ChatResponse, ChatMessage
from ..services.azure_realtime import generate_assistant_reply, stream_assistant_reply
import json, asyncio, logging

router = APIRouter(prefix="/chat", tags=["chat"])

FORM_KEYWORDS = {
    "aadhaar": "/forms/formAadhaar.html",
    "income": "/forms/formIncome.html",
    "form": "/forms/formAadhaar.html",
}

def _maybe_form_url(text: str, request: Request) -> Optional[str]:
    if not text:
        return None
    lowered = text.lower()
    for k, path in FORM_KEYWORDS.items():
        if k in lowered:
            return str(request.base_url).rstrip('/') + path
    return None

@router.post("", response_model=ChatResponse, summary="Send chat message")
async def chat(
    request: Request,
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    content = text or ''
    msg_type = 'text'
    media_uri = None
    if file:
        name = file.filename.lower()
        if name.endswith(( '.png', '.jpg', '.jpeg', '.gif', '.webp')):
            msg_type = 'image'
        elif name.endswith(('.wav', '.mp3', '.m4a', '.aac', '.ogg')):
            msg_type = 'audio'
        else:
            msg_type = 'file'
        media_uri = f"uploaded://{file.filename}"
        content = file.filename

    user = ChatMessage(role='user', content=content, type=msg_type, media_uri=media_uri)  # type: ignore
    form_url = _maybe_form_url(content, request)
    if form_url:
        assistant_text = f"Opening form: {form_url}"
    else:
        assistant_text = await generate_assistant_reply(content) if msg_type == 'text' else ''
        if not assistant_text:
            assistant_text = f"Echo: {content}"
    assistant = ChatMessage(role='assistant', content=assistant_text, type='text', form_url=form_url)
    return ChatResponse(messages=[user, assistant])


# --------------------------- WebSocket Realtime API ---------------------------
# Protocol (JSON messages):
# Client -> Server:
#   {"type":"user_message", "content": "text"}
#   {"type":"ping"}
# Server -> Client events:
#   {"type":"ack", "message_id": "..."}
#   {"type":"assistant_delta", "delta": "partial text"}
#   {"type":"assistant_message", "message": {ChatMessage JSON}}
#   {"type":"form_open", "url": "..."}
#   {"type":"error", "error": "..."}
#   {"type":"pong"}

logger = logging.getLogger(__name__)


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket):  # type: ignore
    await ws.accept()
    base_url = str(ws.base_url).rstrip('/') if hasattr(ws, 'base_url') else ''
    # Simple connection state
    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.warning("[ws] receive error %s", e)
                break
            try:
                msg = json.loads(raw)
            except Exception:
                await _safe_send(ws, {"type": "error", "error": "invalid_json"})
                continue
            mtype = msg.get("type")
            if mtype == "ping":
                await _safe_send(ws, {"type": "pong"})
                continue
            if mtype != "user_message":
                await _safe_send(ws, {"type": "error", "error": "unsupported_type"})
                continue
            content = str(msg.get("content") or "")
            logger.info("[ws] >> user_message len=%d preview=%r", len(content), content[:120])
            if not content.strip():
                await _safe_send(ws, {"type": "error", "error": "empty_content"})
                continue
            form_url = _maybe_form_url(content, _WSRequestShim(base_url))
            user_msg = ChatMessage(role='user', content=content)  # type: ignore
            await _safe_send(ws, {"type": "ack", "message_id": user_msg.id})
            # If form identified, short-circuit with form open + assistant acknowledgement
            if form_url:
                assistant_msg = ChatMessage(role='assistant', content=f"Opening form: {form_url}", form_url=form_url)  # type: ignore
                await _safe_send(ws, {"type": "form_open", "url": form_url})
                await _safe_send(ws, {"type": "assistant_message", "message": assistant_msg.dict()})
                logger.info("[ws] << form_open url=%s", form_url)
                continue
            # Streaming reply from model
            accum: list[str] = []

            async def on_delta(delta: str):
                if delta:
                    accum.append(delta)
                    await _safe_send(ws, {"type": "assistant_delta", "delta": delta})

            async def on_tool(name: str, args: dict):
                # Basic mapping of tool calls to websocket events
                if name == 'open_form':
                    slug = (args or {}).get('slug', '')
                    path = None
                    for k, p in FORM_KEYWORDS.items():
                        if k.lower() == slug.lower():
                            path = p
                            break
                    if path:
                        form_url = base_url.rstrip('/') + path
                        await _safe_send(ws, {"type": "form_open", "url": form_url, "source": "tool"})
                elif name == 'list_forms':
                    forms = [{"slug": k, "url": base_url.rstrip('/') + v} for k, v in FORM_KEYWORDS.items()]
                    await _safe_send(ws, {"type": "forms_list", "forms": forms})
                elif name == 'set_field_value':
                    await _safe_send(ws, {"type": "field_set", "field": (args or {}).get('field'), "value": (args or {}).get('value')})
                elif name == 'get_next_field':
                    # Placeholder: in future consult form schema progression.
                    await _safe_send(ws, {"type": "next_field", "field": "<unimplemented>"})

            full_text = await stream_assistant_reply(content, on_delta=on_delta, on_tool=on_tool)
            if not full_text:
                full_text = f"Echo: {content}" if content else "(no response)"
            assistant_msg = ChatMessage(role='assistant', content=full_text)  # type: ignore
            await _safe_send(ws, {"type": "assistant_message", "message": assistant_msg.dict()})
            logger.info("[ws] << assistant_message len=%d preview=%r", len(full_text), full_text[:120])
    finally:
        try:
            await ws.close()
        except Exception:
            pass


async def _safe_send(ws: WebSocket, payload: Dict[str, Any]):  # type: ignore
    try:
        await ws.send_text(json.dumps(payload))
    except Exception as e:
        logger.debug("[ws] send failed %s", e)


class _WSRequestShim:
    """Shim to reuse _maybe_form_url which expects FastAPI Request."""
    def __init__(self, base_url: str):
        self._base_url = base_url + '/'
    @property
    def base_url(self):  # type: ignore
        return self._base_url
