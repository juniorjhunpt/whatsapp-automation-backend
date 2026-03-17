import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Agent
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

router = APIRouter(prefix="/api/agents", tags=["agents"])

class AgentIn(BaseModel):
    name: str
    prompt: str
    # Support both frontend and backend field names
    api_provider: Optional[str] = None
    ai_provider: Optional[str] = None
    model: Optional[str] = None
    ai_model: Optional[str] = None
    api_key: Optional[str] = None
    ai_api_key: Optional[str] = None
    instance_id: Optional[str] = None
    connection_id: Optional[str] = None  # UUID of Connection to link
    is_active: Optional[bool] = True
    context_memory: Optional[int] = 15
    delay_min: Optional[int] = 3
    delay_max: Optional[int] = 15

class AgentOut(BaseModel):
    id: str
    name: str
    prompt: str
    api_provider: str
    model: str
    is_active: bool
    connection_id: Optional[str] = None
    instance_id: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

def agent_to_out(a: Agent) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "prompt": a.prompt,
        "api_provider": a.ai_provider or "openai",
        "model": a.ai_model or "gpt-4o",
        "is_active": a.is_active,
        "connection_id": getattr(a, 'connection_id', None),
        "instance_id": a.instance_id,
        "has_api_key": bool(a.ai_api_key),   # indica se tem key guardada sem expô-la
        "created_at": a.created_at,
    }

@router.get("")
async def list_agents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).order_by(Agent.created_at.desc()))
    return [agent_to_out(a) for a in result.scalars().all()]

@router.post("")
async def create_agent(body: AgentIn, db: AsyncSession = Depends(get_db)):
    agent = Agent(
        id=str(uuid.uuid4()),
        name=body.name,
        prompt=body.prompt,
        ai_provider=body.api_provider or body.ai_provider or "openai",
        ai_model=body.model or body.ai_model or "gpt-4o",
        ai_api_key=body.api_key or body.ai_api_key,
        instance_id=body.instance_id,
        is_active=body.is_active if body.is_active is not None else True,
        context_memory=body.context_memory or 15,
        delay_min=body.delay_min or 3,
        delay_max=body.delay_max or 15,
    )
    # Guardar connection_id e resolver instance_id
    if body.connection_id:
        from models import Connection
        r = await db.execute(select(Connection).where(Connection.id == body.connection_id))
        conn = r.scalar_one_or_none()
        if conn:
            agent.instance_id = conn.instance_id
            agent.connection_id = body.connection_id
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent_to_out(agent)

@router.get("/{agent_id}")
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent_to_out(agent)

@router.put("/{agent_id}")
async def update_agent(agent_id: str, body: AgentIn, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    
    if body.name: agent.name = body.name
    if body.prompt: agent.prompt = body.prompt
    if body.api_provider or body.ai_provider:
        agent.ai_provider = body.api_provider or body.ai_provider
    if body.model or body.ai_model:
        agent.ai_model = body.model or body.ai_model
    if body.api_key or body.ai_api_key:
        agent.ai_api_key = body.api_key or body.ai_api_key
    if body.is_active is not None:
        agent.is_active = body.is_active

    # Link WhatsApp connection
    if body.connection_id:
        from models import Connection
        r = await db.execute(select(Connection).where(Connection.id == body.connection_id))
        conn = r.scalar_one_or_none()
        if conn:
            agent.instance_id = conn.instance_id
            agent.connection_id = body.connection_id

    await db.commit()
    await db.refresh(agent)
    return agent_to_out(agent)

@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    await db.delete(agent)
    await db.commit()
    return {"ok": True}

@router.post("/{agent_id}/link")
async def link_connection(agent_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Link or unlink a WhatsApp connection to an agent."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    connection_id = body.get("connection_id")
    if connection_id:
        from models import Connection
        r = await db.execute(select(Connection).where(Connection.id == connection_id))
        conn = r.scalar_one_or_none()
        if not conn:
            raise HTTPException(404, "Connection not found")
        agent.instance_id = conn.instance_id
    else:
        agent.instance_id = None
    await db.commit()
    return {"ok": True, "instance_id": agent.instance_id}

@router.post("/{agent_id}/toggle")
async def toggle_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    agent.is_active = not agent.is_active
    await db.commit()
    return {"ok": True, "is_active": agent.is_active}
