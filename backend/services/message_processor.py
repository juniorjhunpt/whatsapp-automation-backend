import asyncio
import logging
import random
import uuid
from datetime import datetime, time as dtime

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models import Agent, Conversation, Message, Connection
from services.ai_service import get_ai_response
from services.redis_service import publish
from services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)


def _in_schedule(agent: Agent) -> bool:
    if not agent.schedule_enabled:
        return True
    now = datetime.now().time()
    days_raw = agent.schedule_days or "1,2,3,4,5"
    active_days = [int(d) for d in days_raw.split(",") if d.strip()]
    weekday = datetime.now().isoweekday()  # 1=Mon … 7=Sun
    if weekday not in active_days:
        return False
    try:
        sh, sm = map(int, agent.schedule_start.split(":"))
        eh, em = map(int, agent.schedule_end.split(":"))
        start_t = dtime(sh, sm)
        end_t = dtime(eh, em)
        return start_t <= now <= end_t
    except Exception:
        return True


async def process_incoming(data: dict) -> None:
    """
    Called when a message arrives on whatsapp:incoming Redis channel.
    Finds the right agent, checks all conditions, calls AI, sends reply.
    """
    instance_id = data.get("instanceId")
    from_jid: str = data.get("from", "")
    from_name: str = data.get("fromName", "Unknown")
    message: str = data.get("message", "")
    is_group: bool = data.get("isGroup", False)
    group_id = data.get("groupId")
    timestamp = data.get("timestamp", int(datetime.now().timestamp()))
    from_me: bool = data.get("fromMe", False)
    image_base64: str = data.get("imageBase64", "")
    image_mime: str = data.get("imageMime", "image/jpeg")

    if not instance_id or not message:
        return

    # Ignorar mensagens enviadas pelo próprio bot — evita loop infinito
    if from_me:
        logger.debug(f"Ignoring own message from {instance_id}")
        return

    # Ignorar mensagens de status/broadcast
    if from_jid in ("status@broadcast", "") or from_jid.endswith("@broadcast"):
        return

    async with AsyncSessionLocal() as db:
        # 1. Find agent for this instance
        result = await db.execute(select(Agent).where(Agent.instance_id == instance_id))
        agent: Agent | None = result.scalar_one_or_none()
        if not agent:
            logger.debug(f"No agent for instance {instance_id}")
            return

        # 2. Agent active?
        if not agent.is_active:
            return

        # 3. Schedule check
        if not _in_schedule(agent):
            if agent.offline_message:
                to = from_jid if not is_group else group_id
                await publish("whatsapp:outgoing", {"instanceId": instance_id, "to": to, "message": agent.offline_message})
            return

        # 4. Groups?
        if is_group and not agent.respond_groups:
            return

        # 5. Blocked numbers?
        contact_phone = from_jid.split("@")[0]
        blocked = [b.strip() for b in (agent.blocked_numbers or "").split("\n") if b.strip()]
        if any(contact_phone.endswith(b.lstrip("+")) or from_jid.startswith(b) for b in blocked):
            logger.info(f"Blocked number: {from_jid}")
            return

        # 6. Allow list
        allowed = [a.strip() for a in (agent.allowed_numbers or "").split("\n") if a.strip()]
        if allowed and not any(contact_phone.endswith(a.lstrip("+")) for a in allowed):
            return

        # 7. Get or create conversation
        conv_result = await db.execute(
            select(Conversation).where(
                and_(Conversation.instance_id == instance_id, Conversation.contact_phone == from_jid)
            )
        )
        conversation: Conversation | None = conv_result.scalar_one_or_none()
        if not conversation:
            conversation = Conversation(
                id=str(uuid.uuid4()),
                instance_id=instance_id,
                contact_phone=from_jid,
                contact_name=from_name,
                is_group=is_group,
                group_id=group_id,
                last_message_at=datetime.utcnow(),
            )
            db.add(conversation)
            await db.flush()

        # 8. Manual takeover?
        if conversation.is_manual_takeover:
            # Just save message, don't auto-reply
            msg = Message(id=str(uuid.uuid4()), conversation_id=conversation.id,
                          direction="incoming", sender=from_name, content=message)
            db.add(msg)
            await db.commit()
            return

        # 9. Save incoming message
        incoming_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            direction="incoming",
            sender=from_name,
            content=message,
        )
        db.add(incoming_msg)
        conversation.last_message_at = datetime.utcnow()
        if conversation.contact_name != from_name:
            conversation.contact_name = from_name
        await db.flush()

        # 10. Build message history for context
        history_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(agent.context_memory)
        )
        history_rows = list(reversed(history_result.scalars().all()))
        message_history = [
            {"role": "assistant" if m.direction == "outgoing" else "user", "content": m.content[:500]}
            for m in history_rows[:-1]  # exclude the message we just added
        ]

        await db.commit()

    # 11. Apply humanization delay
    delay = random.uniform(agent.delay_min, agent.delay_max)
    await asyncio.sleep(delay)

    # 12. Call AI with retry
    ai_result = None
    for attempt in range(2):
        try:
            ai_result = await get_ai_response(
                provider=agent.ai_provider,
                model=agent.ai_model,
                api_key=agent.ai_api_key or "",
                system_prompt=agent.prompt,
                message_history=message_history,
                user_message=message,
                image_base64=image_base64 or None,
                image_mime=image_mime or None,
            )
            break
        except Exception as exc:
            logger.warning(f"AI call attempt {attempt + 1} failed: {exc}")
            if attempt == 0:
                await asyncio.sleep(5)

    if not ai_result:
        logger.error(f"AI failed after retries for instance {instance_id}")
        return

    response_text = ai_result["response"]

    # 13. Send reply via WhatsApp
    to = from_jid if not is_group else (group_id or from_jid)
    await publish("whatsapp:outgoing", {"instanceId": instance_id, "to": to, "message": response_text})

    # 14. Save outgoing message
    async with AsyncSessionLocal() as db:
        outgoing_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            direction="outgoing",
            sender="agent",
            content=response_text,
            tokens_used=ai_result.get("tokens_used", 0),
            ai_cost=ai_result.get("cost", 0.0),
            response_time_ms=ai_result.get("response_time_ms", 0),
        )
        db.add(outgoing_msg)
        await db.commit()

    # 15. Notify frontend via WebSocket
    await ws_manager.broadcast("agent_response", {
        "conversationId": conversation.id,
        "instanceId": instance_id,
        "contact": from_name,
        "message": response_text,
    })
    logger.info(f"Agent replied to {from_jid} ({agent.context_memory} ctx, {ai_result['tokens_used']} tokens)")
