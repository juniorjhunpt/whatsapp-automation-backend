"""
Email router — NOVO arquivo, não altera nada existente.
Prefix: /api/email
"""
import logging
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models.email_models import EmailAccount, Email, EmailAgent
from services.gmail_service import (
    get_auth_url, exchange_code, get_user_email,
    list_new_emails, get_email_detail, parse_email, send_reply
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/email", tags=["email"])


# ─── Schemas ────────────────────────────────────────────────────────────────

class EmailAgentCreate(BaseModel):
    name: str
    account_id: Optional[str] = None
    prompt: str
    api_provider: str = "openrouter"
    model: str = "openai/gpt-4o-mini"
    api_key: Optional[str] = None
    mode: str = "auto"
    auto_reply: bool = True
    reply_delay_minutes: int = 2
    signature: Optional[str] = None
    max_emails_per_hour: int = 20

class EmailAgentUpdate(EmailAgentCreate):
    pass

class SendEmailBody(BaseModel):
    account_id: str
    to: str
    subject: str
    body: str
    thread_id: Optional[str] = None


# ─── OAuth ──────────────────────────────────────────────────────────────────

@router.get("/auth/url")
async def get_gmail_auth_url():
    url = get_auth_url()
    if not url or "client_id=" == url.split("client_id=")[-1]:
        raise HTTPException(400, "GOOGLE_CLIENT_ID não configurado. Adicione nas variáveis de ambiente.")
    return {"url": url}


@router.get("/callback")
async def gmail_callback(code: str = Query(...), db: AsyncSession = Depends(get_db)):
    try:
        tokens = await exchange_code(code)
        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token", "")
        expires_in = tokens.get("expires_in", 3600)
        email = await get_user_email(access_token)

        # Verificar se já existe
        result = await db.execute(select(EmailAccount).where(EmailAccount.email_address == email))
        existing = result.scalar_one_or_none()
        if existing:
            existing.access_token = access_token
            existing.refresh_token = refresh_token or existing.refresh_token
            existing.token_expiry = datetime.utcnow().replace(second=0) + __import__('datetime').timedelta(seconds=expires_in)
            existing.status = "active"
        else:
            account = EmailAccount(
                id=str(uuid.uuid4()),
                email_address=email,
                display_name=email.split("@")[0],
                access_token=access_token,
                refresh_token=refresh_token,
                token_expiry=datetime.utcnow(),
                status="active",
            )
            db.add(account)
        await db.commit()
        # Redirecionar para frontend
        return RedirectResponse(url="/email/accounts?connected=1")
    except Exception as e:
        logger.error(f"Gmail OAuth error: {e}")
        return RedirectResponse(url="/email/accounts?error=1")


# ─── Contas ─────────────────────────────────────────────────────────────────

@router.get("/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EmailAccount).order_by(EmailAccount.created_at.desc()))
    accounts = result.scalars().all()
    return [
        {
            "id": a.id,
            "email": a.email_address,
            "display_name": a.display_name,
            "status": a.status,
            "last_sync": a.last_sync_at.isoformat() if a.last_sync_at else None,
            "created_at": a.created_at.isoformat(),
        }
        for a in accounts
    ]


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Conta não encontrada")
    await db.delete(account)
    await db.commit()
    return {"ok": True}


@router.post("/accounts/{account_id}/sync")
async def sync_account(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Conta não encontrada")
    account.last_sync_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "message": "Sincronização iniciada"}


# ─── Inbox ──────────────────────────────────────────────────────────────────

@router.get("/inbox")
async def list_inbox(
    account_id: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db)
):
    q = select(Email).where(Email.direction == "incoming").order_by(Email.received_at.desc()).limit(limit)
    if account_id:
        q = q.where(Email.account_id == account_id)
    result = await db.execute(q)
    emails = result.scalars().all()
    return [
        {
            "id": e.id,
            "account_id": e.account_id,
            "from_email": e.from_address,
            "from_name": e.from_name,
            "subject": e.subject,
            "body_text": (e.body_text or "")[:200],
            "received_at": e.received_at.isoformat() if e.received_at else None,
            "replied": e.is_replied,
            "replied_by": e.replied_by,
            "reply_body": e.reply_body,
            "replied_at": e.replied_at.isoformat() if e.replied_at else None,
        }
        for e in emails
    ]


@router.get("/inbox/{email_id}")
async def get_email(email_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Email).where(Email.id == email_id))
    email = result.scalar_one_or_none()
    if not email:
        raise HTTPException(404, "E-mail não encontrado")
    return {
        "id": email.id,
        "from_email": email.from_address,
        "from_name": email.from_name,
        "subject": email.subject,
        "body_text": email.body_text,
        "body_html": email.body_html,
        "received_at": email.received_at.isoformat() if email.received_at else None,
        "replied": email.is_replied,
        "reply_body": email.reply_body,
        "replied_at": email.replied_at.isoformat() if email.replied_at else None,
    }


@router.post("/send")
async def send_email(body: SendEmailBody, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EmailAccount).where(EmailAccount.id == body.account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Conta não encontrada")
    ok = await send_reply(account.access_token, body.to, body.subject, body.body, body.thread_id)
    if not ok:
        raise HTTPException(500, "Erro ao enviar e-mail")
    return {"ok": True}


# ─── Agentes de E-mail ───────────────────────────────────────────────────────

@router.get("/agents")
async def list_email_agents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EmailAgent).order_by(EmailAgent.created_at.desc()))
    agents = result.scalars().all()
    return [
        {
            "id": a.id,
            "name": a.name,
            "account_id": a.account_id,
            "model": a.ai_model,
            "api_provider": a.api_provider,
            "mode": a.mode,
            "is_active": a.is_active,
            "auto_reply": a.auto_reply,
            "reply_delay_minutes": a.reply_delay_minutes,
            "created_at": a.created_at.isoformat(),
        }
        for a in agents
    ]


@router.post("/agents")
async def create_email_agent(body: EmailAgentCreate, db: AsyncSession = Depends(get_db)):
    agent = EmailAgent(
        id=str(uuid.uuid4()),
        name=body.name,
        account_id=body.account_id or None,
        prompt=body.prompt,
        api_provider=body.api_provider,
        ai_model=body.model,
        ai_api_key=body.api_key,
        mode=body.mode,
        auto_reply=body.auto_reply,
        reply_delay_minutes=body.reply_delay_minutes,
        signature=body.signature,
        max_emails_per_hour=body.max_emails_per_hour,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return {"id": agent.id, "name": agent.name, "model": agent.ai_model, "is_active": agent.is_active}


@router.put("/agents/{agent_id}")
async def update_email_agent(agent_id: str, body: EmailAgentUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EmailAgent).where(EmailAgent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agente não encontrado")
    agent.name = body.name
    agent.account_id = body.account_id or None
    agent.prompt = body.prompt
    agent.api_provider = body.api_provider
    agent.ai_model = body.model
    agent.mode = body.mode
    agent.auto_reply = body.auto_reply
    agent.reply_delay_minutes = body.reply_delay_minutes
    agent.signature = body.signature
    if body.api_key:
        agent.ai_api_key = body.api_key
    await db.commit()
    return {"id": agent.id, "name": agent.name, "model": agent.ai_model}


@router.delete("/agents/{agent_id}")
async def delete_email_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EmailAgent).where(EmailAgent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agente não encontrado")
    await db.delete(agent)
    await db.commit()
    return {"ok": True}
