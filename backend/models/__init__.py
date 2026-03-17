import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, Float, DateTime, Text, ForeignKey
from database import Base

def gen_id():
    return str(uuid.uuid4())

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_id)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    totp_secret = Column(String, nullable=True)
    totp_enabled = Column(Boolean, default=False)
    recovery_codes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Agent(Base):
    __tablename__ = "agents"
    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    instance_id = Column(String, nullable=True)
    connection_id = Column(String, nullable=True)   # UUID da Connection vinculada
    prompt = Column(Text, nullable=False)
    ai_provider = Column(String, default="openai")
    ai_model = Column(String, default="gpt-4o")
    ai_api_key = Column(String, nullable=True)
    context_memory = Column(Integer, default=5)
    delay_min = Column(Integer, default=3)
    delay_max = Column(Integer, default=15)
    is_active = Column(Boolean, default=True)
    respond_groups = Column(Boolean, default=False)
    respond_only_mentioned = Column(Boolean, default=False)
    schedule_enabled = Column(Boolean, default=False)
    schedule_start = Column(String, default="08:00")
    schedule_end = Column(String, default="22:00")
    schedule_days = Column(String, default="1,2,3,4,5")
    offline_message = Column(Text, default="No momento não posso responder, retorno em breve!")
    blocked_numbers = Column(Text, default="")
    allowed_numbers = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Connection(Base):
    __tablename__ = "connections"
    id = Column(String, primary_key=True, default=gen_id)
    instance_id = Column(String, unique=True, nullable=False)
    phone = Column(String, nullable=True)
    status = Column(String, default="disconnected")
    last_connected_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True, default=gen_id)
    instance_id = Column(String, nullable=False)
    contact_phone = Column(String, nullable=False)
    contact_name = Column(String, nullable=True)
    is_group = Column(Boolean, default=False)
    group_id = Column(String, nullable=True)
    is_manual_takeover = Column(Boolean, default=False)
    last_message_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        __import__('sqlalchemy').UniqueConstraint('instance_id', 'contact_phone', name='uq_conv'),
    )

class Message(Base):
    __tablename__ = "messages"
    id = Column(String, primary_key=True, default=gen_id)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    direction = Column(String, nullable=False)  # incoming | outgoing
    sender = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    message_type = Column(String, default="text")
    tokens_used = Column(Integer, default=0)
    ai_cost = Column(Float, default=0.0)
    response_time_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
