from fastapi import APIRouter, Form, UploadFile, File, Request
from typing import Optional
from ..schemas.chat import ChatResponse, ChatMessage
from ..services.azure_realtime import generate_assistant_reply

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
