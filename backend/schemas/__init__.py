from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class AgentCreate(BaseModel):
    name: str
    instance_id: Optional[str] = None
    prompt: str
    ai_provider: str = "openai"
    ai_model: str = "gpt-4o"
    ai_api_key: Optional[str] = None
    context_memory: int = 15
    delay_min: int = 3
    delay_max: int = 15
    is_active: bool = True
    respond_groups: bool = False
    respond_only_mentioned: bool = False
    schedule_enabled: bool = False
    schedule_start: str = "08:00"
    schedule_end: str = "22:00"
    schedule_days: str = "1,2,3,4,5"
    offline_message: str = "No momento não posso responder, retorno em breve!"
    blocked_numbers: str = ""
    allowed_numbers: str = ""

class AgentUpdate(AgentCreate):
    pass

class AgentOut(AgentCreate):
    id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class ConnectionCreate(BaseModel):
    instance_id: str

class ConnectionOut(BaseModel):
    id: str
    instance_id: str
    phone: Optional[str] = None
    status: str
    last_connected_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class MessageOut(BaseModel):
    id: str
    conversation_id: str
    direction: str
    sender: str
    content: str
    message_type: str
    tokens_used: int
    ai_cost: float
    response_time_ms: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class ConversationOut(BaseModel):
    id: str
    instance_id: str
    contact_phone: str
    contact_name: Optional[str] = None
    is_group: bool
    is_manual_takeover: bool
    last_message_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class SendMessageRequest(BaseModel):
    message: str
