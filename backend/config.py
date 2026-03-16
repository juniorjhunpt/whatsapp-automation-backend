import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/wahub.db")
    whatsapp_service_url: str = os.getenv("WHATSAPP_SERVICE_URL", "http://localhost:3001")
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")

    class Config:
        env_file = ".env"

settings = Settings()
