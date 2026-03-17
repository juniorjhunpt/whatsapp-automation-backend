"""
Email worker — sincroniza e-mails a cada 60s e processa com agentes de IA.
NOVO arquivo, não altera nada existente.
"""
import asyncio
import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from database import AsyncSessionLocal
from models.email_models import EmailAccount, Email, EmailAgent
from services.gmail_service import (
    list_new_emails, get_email_detail, parse_email,
    send_reply, refresh_access_token
)
from services.ai_service import get_ai_response

logger = logging.getLogger(__name__)

SYNC_INTERVAL = 60  # segundos


async def process_account(account: EmailAccount):
    """Sincroniza e processa e-mails de uma conta Gmail."""
    try:
        # Renovar token se necessário
        if account.token_expiry and datetime.utcnow() > account.token_expiry:
            logger.info(f"Renovando token para {account.email_address}")
            tokens = await refresh_access_token(account.refresh_token)
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(EmailAccount).where(EmailAccount.id == account.id))
                acc = result.scalar_one_or_none()
                if acc:
                    acc.access_token = tokens["access_token"]
                    from datetime import timedelta
                    acc.token_expiry = datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600))
                    await db.commit()
            account.access_token = tokens["access_token"]

        # Buscar novos e-mails
        new_ids = await list_new_emails(account.access_token, since=account.last_sync_at, max_results=10)
        if not new_ids:
            return

        logger.info(f"[Email] {account.email_address}: {len(new_ids)} e-mail(s) novos")

        async with AsyncSessionLocal() as db:
            # Buscar agente ativo para esta conta
            agent_result = await db.execute(
                select(EmailAgent).where(
                    EmailAgent.account_id == account.id,
                    EmailAgent.is_active == True,
                )
            )
            agent = agent_result.scalar_one_or_none()

            for msg_ref in new_ids:
                gmail_id = msg_ref["id"]

                # Verificar se já existe no banco
                existing = await db.execute(select(Email).where(Email.gmail_id == gmail_id))
                if existing.scalar_one_or_none():
                    continue

                # Buscar detalhes
                try:
                    raw = await get_email_detail(account.access_token, gmail_id)
                    parsed = parse_email(raw)
                except Exception as e:
                    logger.warning(f"Erro ao buscar e-mail {gmail_id}: {e}")
                    continue

                # Salvar no banco
                email = Email(
                    id=str(uuid.uuid4()),
                    account_id=account.id,
                    gmail_id=gmail_id,
                    thread_id=parsed.get("thread_id"),
                    from_address=parsed["from_address"],
                    from_name=parsed.get("from_name"),
                    to_address=parsed["to_address"],
                    subject=parsed.get("subject", ""),
                    body_text=parsed.get("body_text", ""),
                    body_html=parsed.get("body_html", ""),
                    direction="incoming",
                    labels=parsed.get("labels", ""),
                    received_at=parsed.get("received_at"),
                )
                db.add(email)
                await db.flush()

                # Processar com agente se existir
                if agent and agent.auto_reply and parsed.get("body_text"):
                    # Delay configurado
                    if agent.reply_delay_minutes > 0:
                        await asyncio.sleep(agent.reply_delay_minutes * 60)

                    try:
                        ai_result = await get_ai_response(
                            provider=agent.api_provider,
                            model=agent.ai_model,
                            api_key=agent.ai_api_key or "",
                            system_prompt=agent.prompt,
                            message_history=[],
                            user_message=f"Assunto: {parsed.get('subject', '')}\n\nConteúdo:\n{parsed.get('body_text', '')}",
                        )
                        reply_text = ai_result["response"]
                        if agent.signature:
                            reply_text += f"\n\n{agent.signature}"

                        if agent.mode == "auto":
                            ok = await send_reply(
                                account.access_token,
                                parsed["from_address"],
                                parsed.get("subject", ""),
                                reply_text,
                                parsed.get("thread_id"),
                            )
                            if ok:
                                email.is_replied = True
                                email.replied_by = "agent"
                                email.reply_body = reply_text
                                email.replied_at = datetime.utcnow()
                                logger.info(f"[Email] Respondido automaticamente para {parsed['from_address']}")
                    except Exception as e:
                        logger.warning(f"[Email] Erro no agente: {e}")

            # Atualizar last_sync
            acc_result = await db.execute(select(EmailAccount).where(EmailAccount.id == account.id))
            acc = acc_result.scalar_one_or_none()
            if acc:
                acc.last_sync_at = datetime.utcnow()
            await db.commit()

    except ValueError as e:
        if "token_expired" in str(e):
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(EmailAccount).where(EmailAccount.id == account.id))
                acc = result.scalar_one_or_none()
                if acc:
                    acc.status = "expired"
                    await db.commit()
            logger.warning(f"[Email] Token expirado para {account.email_address}")
    except Exception as e:
        logger.error(f"[Email] Erro ao processar conta {account.email_address}: {e}")


async def email_sync_loop():
    """Loop principal do worker de e-mail."""
    logger.info("[Email Worker] Iniciado — sincronização a cada 60s")
    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(EmailAccount).where(EmailAccount.status == "active")
                )
                accounts = result.scalars().all()

            for account in accounts:
                await process_account(account)

        except Exception as e:
            logger.error(f"[Email Worker] Erro no loop: {e}")

        await asyncio.sleep(SYNC_INTERVAL)
