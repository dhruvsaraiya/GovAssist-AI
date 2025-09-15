from fastapi import APIRouter, UploadFile, File, Form, Request
from typing import Optional
from ..schemas.chat import ChatResponse, ChatMessage
from urllib.parse import urlparse
import logging
from ..services.azure_realtime import generate_assistant_reply  # type: ignore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

def extract_form_url(text: str, request: Request) -> Optional[str]:
    """Naive trigger detection for Phase 1.

    Rules:
      - If text contains pattern `form:` followed by a URL, extract it.
      - Else if certain keywords appear, return a canned sample URL.
    Apply minimal allowlist host check.
    """
    if not text:
        return None
    lowered = text.lower().strip()
    # Direct pattern form:https://example
    if 'form:' in lowered:
        after = lowered.split('form:', 1)[1].strip().split()[0]
        candidate = after
    else:
        candidate = None
    # Keyword heuristics - map a few demo keywords to local demo HTML forms
    if candidate is None:
        keyword_map = {
            'aadhaar': '/forms/formAadhaar.html',
            'aadhaar form': '/forms/formAadhaar.html',
            'aadhaar update': '/forms/formAadhaar.html',
            'income': '/forms/formIncome.html',
            'income certificate': '/forms/formIncome.html',
            'form': '/forms/formAadhaar.html',
        }
        for k, v in keyword_map.items():
            if k in lowered:
                # Build absolute URL using incoming request base
                base = str(request.base_url).rstrip('/')
                candidate = base + v
                break
    if not candidate:
        return None
    # Basic validation
    try:
        parsed = urlparse(candidate)
        if parsed.scheme not in {'http', 'https'}:
            return None
        return candidate
    except Exception:
        return None


@router.post("", response_model=ChatResponse, summary="Send chat message (text/image/audio)")
async def chat(
    request: Request,
    text: Optional[str] = Form(None, description="Text content of the message"),
    media_type: Optional[str] = Form(None, description="Explicit media type if sending file: image|audio"),
    file: Optional[UploadFile] = File(None, description="Image or audio file upload")
):
    """Accept a single user message (text or media) and return an assistant echo.

    Future: integrate LLM, store conversation context, process modalities.
    """
    detected_type = 'text'
    content = text or ''
    media_uri = None
    if file:
        filename_lower = file.filename.lower()
        if filename_lower.endswith(( '.png', '.jpg', '.jpeg', '.gif', '.webp')):
            detected_type = 'image'
        elif filename_lower.endswith(('.wav', '.mp3', '.m4a', '.aac', '.ogg')):
            detected_type = 'audio'
        else:
            detected_type = 'file'
        media_uri = f"uploaded://{file.filename}"  # placeholder reference
        content = file.filename
    if media_type in {'image','audio'}:
        detected_type = media_type

    user_msg = ChatMessage(
        role='user',
        content=content,
        type=detected_type,  # type: ignore
        media_uri=media_uri
    )

    # Log the detected message type and context
    logger.info(
        "[chat] received message detected_type=%s param_media_type=%s has_file=%s file_name=%s text_len=%d",
        detected_type,
        media_type,
        bool(file),
        (file.filename if file else None),
        len(content or "")
    )

    form_url = extract_form_url(content, request)
    # If a form URL was detected, prioritize that actionable response.
    if form_url:
        assistant_content = f"Opening form: {form_url}"
    else:
        # Attempt realtime model generation for plain text user messages.
        assistant_content = await generate_assistant_reply(content) if detected_type == 'text' else ''
        logger.info(
            "[chat] realtime model reply length=%d empty=%s", len(assistant_content or ""), not bool(assistant_content)
        )
        if not assistant_content:
            assistant_content = f"Echo: {content}"

    # Always log final assistant content (truncated) and whether a form URL is attached
    logger.info(
        "[chat] assistant response form_url=%s preview=%s", form_url, (assistant_content[:120] + ('…' if len(assistant_content) > 120 else ''))
    )

    assistant_msg = ChatMessage(
        role='assistant',
        content=assistant_content,
        type='text',
        form_url=form_url
    )
    return ChatResponse(messages=[user_msg, assistant_msg])
