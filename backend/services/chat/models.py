from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class ConversationCreate(BaseModel):
    channel: str = "xianyu"
    channel_conversation_id: Optional[str] = None
    customer_name: Optional[str] = None
    customer_id: Optional[str] = None
    agent_key: Optional[str] = None
    assigned_to: Optional[str] = None


class ConversationUpdate(BaseModel):
    status: Optional[str] = None
    agent_key: Optional[str] = None
    assigned_to: Optional[str] = None
    customer_name: Optional[str] = None


class ConversationResponse(BaseModel):
    id: int
    tenant_id: int
    channel: str
    channel_conversation_id: Optional[str]
    customer_name: Optional[str]
    customer_id: Optional[str]
    status: str
    agent_key: Optional[str]
    assigned_to: Optional[str]
    session_id: Optional[str] = None  # 最近一次 chat 使用的 Agent Session（轨迹回放定位用）
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    role: str  # customer | agent | system
    sender_name: Optional[str] = None
    content: str = ""
    content_type: str = "text"
    metadata: dict = Field(default_factory=dict)


class MessageResponse(BaseModel):
    id: int
    tenant_id: int
    conversation_id: int
    role: str
    sender_name: Optional[str]
    content: str
    content_type: str
    metadata: dict
    created_at: datetime
