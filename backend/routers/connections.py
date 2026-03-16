import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Connection
from schemas import ConnectionCreate, ConnectionOut
from services.redis_service import publish

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/connections", tags=["connections"])

@router.get("", response_model=list[ConnectionOut])
async def list_connections(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Connection).order_by(Connection.created_at.desc()))
    return result.scalars().all()

@router.post("", response_model=ConnectionOut)
async def create_connection(body: ConnectionCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Connection).where(Connection.instance_id == body.instance_id))
    conn = existing.scalar_one_or_none()
    if conn:
        await publish("whatsapp:command", {"action": "connect", "instanceId": conn.instance_id})
        conn.status = "connecting"
        await db.commit()
        return conn

    conn = Connection(id=str(uuid.uuid4()), instance_id=body.instance_id, status="connecting")
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    await publish("whatsapp:command", {"action": "connect", "instanceId": body.instance_id})
    return conn

@router.delete("/{conn_id}")
async def delete_connection(conn_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Connection).where(Connection.id == conn_id))
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    await publish("whatsapp:command", {"action": "disconnect", "instanceId": conn.instance_id})
    await db.delete(conn)
    await db.commit()
    return {"ok": True}

@router.get("/qr/{instance_id}")
async def get_qr(instance_id: str):
    """Poll endpoint — returns latest QR code for an instance from Redis."""
    from services.redis_service import get_redis
    r = await get_redis()
    qr = await r.get(f"wahub:qr:{instance_id}")
    status = await r.get(f"wahub:status:{instance_id}")
    return {"instanceId": instance_id, "qr": qr, "status": status or "connecting"}

@router.post("/{conn_id}/reconnect")
async def reconnect(conn_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Connection).where(Connection.id == conn_id))
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    conn.status = "connecting"
    await db.commit()
    await publish("whatsapp:command", {"action": "connect", "instanceId": conn.instance_id})
    return {"ok": True}
