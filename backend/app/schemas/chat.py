from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from uuid import uuid4

MessageRole = Literal['user', 'assistant', 'system']
MessageType = Literal['text', 'image', 'audio', 'file']

class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    role: MessageRole
    content: str
    type: MessageType = 'text'
    media_uri: Optional[str] = None
    # Optional URL to a government or assistance form that the client can render in a WebView.
    form_url: Optional[str] = None

class ChatRequest(BaseModel):
    messages: List[ChatMessage]

class ChatResponse(BaseModel):
    messages: List[ChatMessage]
