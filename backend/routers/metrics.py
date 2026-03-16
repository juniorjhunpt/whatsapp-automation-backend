from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from database import get_db
from models import Message, Conversation, Agent

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

PERIOD_SQL = {
    "today":    "date('now')",
    "month":    "date('now', 'start of month')",
    "3months":  "date('now', '-3 months')",
    "6months":  "date('now', '-6 months')",
    "year":     "date('now', 'start of year')",
}

@router.get("")
async def get_metrics(
    period: str = Query("today", enum=["today", "month", "3months", "6months", "year"]),
    db: AsyncSession = Depends(get_db)
):
    since = PERIOD_SQL.get(period, PERIOD_SQL["today"])

    msg_count = await db.execute(
        text(f"SELECT COUNT(*) FROM messages WHERE created_at >= {since}")
    )
    tokens = await db.execute(
        text(f"SELECT COALESCE(SUM(tokens_used), 0) FROM messages WHERE created_at >= {since}")
    )
    cost = await db.execute(
        text(f"SELECT COALESCE(SUM(ai_cost), 0.0) FROM messages WHERE created_at >= {since}")
    )
    avg_rt = await db.execute(
        text(f"SELECT COALESCE(AVG(response_time_ms), 0) FROM messages WHERE direction='outgoing' AND created_at >= {since}")
    )
    errors = await db.execute(
        text(f"SELECT COUNT(*) FROM messages WHERE direction='outgoing' AND tokens_used=0 AND created_at >= {since}")
    )
    total_convs = await db.execute(select(func.count(Conversation.id)))
    total_agents = await db.execute(select(func.count(Agent.id)))

    return {
        "messages_today": msg_count.scalar() or 0,
        "tokens_today": int(tokens.scalar() or 0),
        "cost_today": round(float(cost.scalar() or 0.0), 6),
        "avg_response_time": int(avg_rt.scalar() or 0),
        "failed_messages": errors.scalar() or 0,
        "total_conversations": total_convs.scalar() or 0,
        "total_agents": total_agents.scalar() or 0,
        "period": period,
    }

@router.get("/chart")
async def get_chart(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(text(f"""
        SELECT date(created_at) as day,
               COUNT(*) as messages,
               COALESCE(SUM(tokens_used), 0) as tokens,
               COALESCE(SUM(ai_cost), 0.0) as cost,
               SUM(CASE WHEN direction='incoming' THEN 1 ELSE 0 END) as received,
               SUM(CASE WHEN direction='outgoing' THEN 1 ELSE 0 END) as responded
        FROM messages
        WHERE created_at >= date('now', '-{days} days')
        GROUP BY day
        ORDER BY day
    """))
    rows = result.fetchall()
    return [{"date": r[0], "messages": r[1], "tokens": int(r[2]), "cost": round(float(r[3]), 6), "received": r[4], "responded": r[5]} for r in rows]
