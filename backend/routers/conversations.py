import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from database import get_db
from models import Conversation, Message
from schemas import ConversationOut, MessageOut, SendMessageRequest
from services.redis_service import publish

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conversations", tags=["conversations"])

@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    instance_id: str | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Conversation).order_by(Conversation.last_message_at.desc())
    if instance_id:
        q = q.where(Conversation.instance_id == instance_id)
    if search:
        q = q.where(
            Conversation.contact_name.ilike(f"%{search}%") |
            Conversation.contact_phone.ilike(f"%{search}%")
        )
    result = await db.execute(q)
    return result.scalars().all()

@router.get("/{conv_id}/messages", response_model=list[MessageOut])
async def get_messages(conv_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at)
    )
    return result.scalars().all()

@router.post("/{conv_id}/send")
async def send_message(conv_id: str, body: SendMessageRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")

    await publish("whatsapp:outgoing", {
        "instanceId": conv.instance_id,
        "to": conv.contact_phone,
        "message": body.message,
    })

    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        direction="outgoing",
        sender="manual",
        content=body.message,
    )
    db.add(msg)
    await db.commit()
    return {"ok": True}

@router.post("/{conv_id}/takeover")
async def takeover(conv_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    conv.is_manual_takeover = True
    await db.commit()
    return {"ok": True}

@router.post("/{conv_id}/release")
async def release(conv_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    conv.is_manual_takeover = False
    await db.commit()
    return {"ok": True}
