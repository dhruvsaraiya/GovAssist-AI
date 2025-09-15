from pydantic import BaseModel, Field
from typing import List, Optional, Literal
import time

MessageRole = Literal['user', 'assistant', 'system']
MessageType = Literal['text', 'image', 'audio', 'file']

class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(time.time()))
    role: MessageRole
    content: str
    type: MessageType = 'text'
    media_uri: Optional[str] = None

class ChatRequest(BaseModel):
    messages: List[ChatMessage]

class ChatResponse(BaseModel):
    messages: List[ChatMessage]
