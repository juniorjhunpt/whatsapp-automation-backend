from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from database import get_db
from models import Message, Conversation, Agent

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

@router.get("")
async def get_metrics(db: AsyncSession = Depends(get_db)):
    # Messages today
    today_count = await db.execute(
        select(func.count(Message.id)).where(
            Message.created_at >= func.date('now')
        )
    )
    # Total tokens today
    tokens_today = await db.execute(
        select(func.coalesce(func.sum(Message.tokens_used), 0)).where(
            Message.created_at >= func.date('now')
        )
    )
    # Total cost today
    cost_today = await db.execute(
        select(func.coalesce(func.sum(Message.ai_cost), 0.0)).where(
            Message.created_at >= func.date('now')
        )
    )
    # Avg response time (outgoing only)
    avg_rt = await db.execute(
        select(func.coalesce(func.avg(Message.response_time_ms), 0)).where(
            Message.direction == 'outgoing',
            Message.created_at >= func.date('now'),
        )
    )
    # Failed (messages with 0 tokens but outgoing — approximation)
    errors = await db.execute(
        select(func.count(Message.id)).where(
            Message.direction == 'outgoing',
            Message.tokens_used == 0,
            Message.created_at >= func.date('now'),
        )
    )
    total_convs = await db.execute(select(func.count(Conversation.id)))
    total_agents = await db.execute(select(func.count(Agent.id)))

    return {
        "messages_today": today_count.scalar() or 0,
        "tokens_today": tokens_today.scalar() or 0,
        "cost_today": round((cost_today.scalar() or 0.0) * 100, 2),  # in cents
        "avg_response_time": int(avg_rt.scalar() or 0),
        "failed_messages": errors.scalar() or 0,
        "total_conversations": total_convs.scalar() or 0,
        "total_agents": total_agents.scalar() or 0,
    }

@router.get("/chart")
async def get_chart(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("""
        SELECT date(created_at) as day,
               SUM(CASE WHEN direction='incoming' THEN 1 ELSE 0 END) as received,
               SUM(CASE WHEN direction='outgoing' THEN 1 ELSE 0 END) as responded
        FROM messages
        WHERE created_at >= date('now', '-7 days')
        GROUP BY day
        ORDER BY day
    """))
    rows = result.fetchall()
    return [{"date": r[0], "received": r[1], "responded": r[2]} for r in rows]
