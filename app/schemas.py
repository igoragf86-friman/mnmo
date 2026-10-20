from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class UploaderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    role: str

class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    mime_type: str | None
    file_size: int | None
    status: str
    created_at: datetime
    uploaded_by: UploaderOut
    can_delete: bool


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int

class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    system_prompt: str = Field(default="", max_length=4000)
    model_name: str = Field(default="qwen", max_length=100)
    icon: str = Field(default="bot", max_length=40)
    color: str = Field(default="#4f46e5", max_length=20)
    document_ids: list[int] = Field(default_factory=list)


class AgentDocOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    file_size: int | None
    mime_type: str | None
    is_enabled: bool = True
    is_inherited: bool = False      
    owner_id: int | None = None
    owner_name: str | None = None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    share_user_ids: list[int] = Field(default_factory=list) 
    name: str
    description: str | None
    system_prompt: str
    model_name: str
    icon: str
    color: str
    is_active: bool
    is_public: bool = False
    created_by: int | None
    created_at: datetime
    owner_name: str | None = None
    document_ids: list[int] = Field(default_factory=list)
    documents: list[AgentDocOut] = Field(default_factory=list) 
    document_count: int = 0
    enabled_document_ids: list[int] = Field(default_factory=list)
    is_owner: bool = False
    can_edit: bool = False
    can_share: bool = False
    can_share_all: bool = False  
    is_shared_with_me: bool = False


class AgentListOut(BaseModel):
    items: list[AgentOut]
    total: int


class AgentShareIn(BaseModel):
    user_id: int


class AgentShareOut(BaseModel):
    user_id: int
    name: str
    role: str
    
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    system_prompt: str | None = None
    agent_id: int | None = None
    
class ChatOut(BaseModel):
    answer: str
    
class UserCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=6, max_length=72)
    role: Literal["admin", "user"] = "user"