from fastapi import APIRouter, UploadFile, File, Form
from typing import Optional
from ..schemas.chat import ChatResponse, ChatMessage
import time

router = APIRouter(prefix="/chat", tags=["chat"])

@router.post("", response_model=ChatResponse, summary="Send chat message (text/image/audio)")
async def chat(
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
        id=str(time.time()),
        role='user',
        content=content,
        type=detected_type,  # type: ignore
        media_uri=media_uri
    )

    assistant_msg = ChatMessage(
        id=str(time.time()),
        role='assistant',
        content=f"Echo: {content}",
        type='text'
    )
    return ChatResponse(messages=[user_msg, assistant_msg])
