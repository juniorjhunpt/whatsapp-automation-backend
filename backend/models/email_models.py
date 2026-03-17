"""
Email models — tabelas NOVAS, não altera nada existente.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Boolean, DateTime, Integer
from database import Base


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email_address = Column(String, nullable=False)
    display_name = Column(String)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    token_expiry = Column(DateTime, nullable=True)
    status = Column(String, default="active")   # active | expired | revoked
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Email(Base):
    __tablename__ = "emails"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, nullable=False)
    gmail_id = Column(String, nullable=False)
    thread_id = Column(String, nullable=True)
    from_address = Column(String, nullable=False)
    from_name = Column(String, nullable=True)
    to_address = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    body_text = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    direction = Column(String, nullable=False)    # incoming | outgoing
    is_read = Column(Boolean, default=False)
    is_replied = Column(Boolean, default=False)
    replied_by = Column(String, nullable=True)    # agent | manual | null
    reply_body = Column(Text, nullable=True)
    replied_at = Column(DateTime, nullable=True)
    labels = Column(Text, nullable=True)
    received_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EmailAgent(Base):
    __tablename__ = "email_agents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    account_id = Column(String, nullable=True)
    prompt = Column(Text, nullable=False)
    api_provider = Column(String, nullable=False)
    ai_model = Column(String, nullable=False)
    ai_api_key = Column(String, nullable=True)
    mode = Column(String, default="auto")           # auto | draft | notify
    is_active = Column(Boolean, default=True)
    auto_reply = Column(Boolean, default=True)
    reply_delay_minutes = Column(Integer, default=2)
    filter_senders = Column(Text, nullable=True)    # JSON list
    block_senders = Column(Text, nullable=True)     # JSON list
    filter_subjects = Column(Text, nullable=True)   # JSON list
    ignore_categories = Column(Text, nullable=True) # JSON list
    signature = Column(Text, nullable=True)
    max_emails_per_hour = Column(Integer, default=20)
    created_at = Column(DateTime, default=datetime.utcnow)
