"""
Gmail API service — NOVO arquivo, não altera nada existente.
"""
import base64
import logging
import os
from datetime import datetime, timedelta
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "https://72-62-74-215.sslip.io/api/email/callback")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
]


def get_auth_url() -> str:
    """Gera a URL de autorização OAuth2 do Google."""
    scope = " ".join(SCOPES)
    return (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={GOOGLE_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&access_type=offline"
        f"&prompt=consent"
    )


async def exchange_code(code: str) -> dict:
    """Troca o código de autorização por access_token + refresh_token."""
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        res.raise_for_status()
        return res.json()


async def refresh_access_token(refresh_token: str) -> dict:
    """Renova o access_token usando o refresh_token."""
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "refresh_token": refresh_token,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "grant_type": "refresh_token",
            },
        )
        res.raise_for_status()
        return res.json()


async def get_user_email(access_token: str) -> str:
    """Obtém o email da conta autenticada."""
    async with httpx.AsyncClient() as client:
        res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        res.raise_for_status()
        return res.json().get("email", "")


async def list_new_emails(access_token: str, since: Optional[datetime] = None, max_results: int = 20) -> list:
    """Lista IDs de e-mails novos desde a última sincronização."""
    query = "in:inbox"
    if since:
        ts = int(since.timestamp())
        query += f" after:{ts}"

    async with httpx.AsyncClient() as client:
        res = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"q": query, "maxResults": max_results},
        )
        if res.status_code == 401:
            raise ValueError("token_expired")
        res.raise_for_status()
        data = res.json()
        return data.get("messages", [])


async def get_email_detail(access_token: str, gmail_id: str) -> dict:
    """Obtém detalhes completos de um e-mail."""
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{gmail_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": "full"},
        )
        res.raise_for_status()
        return res.json()


def parse_email(raw: dict) -> dict:
    """Extrai campos úteis de um e-mail raw da Gmail API."""
    headers = {h["name"].lower(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
    
    # Extrair corpo
    body_text = ""
    body_html = ""
    payload = raw.get("payload", {})

    def extract_body(part):
        nonlocal body_text, body_html
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if data:
            decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            if mime == "text/plain":
                body_text = decoded
            elif mime == "text/html":
                body_html = decoded
        for p in part.get("parts", []):
            extract_body(p)

    extract_body(payload)

    return {
        "gmail_id": raw["id"],
        "thread_id": raw.get("threadId"),
        "from_address": headers.get("from", "").split("<")[-1].rstrip(">"),
        "from_name": headers.get("from", "").split("<")[0].strip().strip('"'),
        "to_address": headers.get("to", ""),
        "subject": headers.get("subject", "(sem assunto)"),
        "body_text": body_text[:5000],   # limite para não explodir tokens
        "body_html": body_html[:10000],
        "labels": ",".join(raw.get("labelIds", [])),
        "received_at": datetime.fromtimestamp(int(raw.get("internalDate", 0)) / 1000),
    }


async def send_reply(access_token: str, to: str, subject: str, body: str, thread_id: Optional[str] = None) -> bool:
    """Envia uma resposta de e-mail via Gmail API."""
    message = f"To: {to}\nSubject: Re: {subject}\nContent-Type: text/plain; charset=utf-8\n\n{body}"
    encoded = base64.urlsafe_b64encode(message.encode()).decode()

    payload: dict = {"raw": encoded}
    if thread_id:
        payload["threadId"] = thread_id

    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
        )
        if res.status_code == 200:
            return True
        logger.error(f"Gmail send error: {res.status_code} {res.text}")
        return False
