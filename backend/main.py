import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, and_

from config import settings
from database import init_db, AsyncSessionLocal
from models import Connection
from routers import agents, connections, conversations, metrics, settings as settings_router
from services.redis_service import subscribe_forever
from services.websocket_manager import ws_manager
from services.message_processor import process_incoming

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("main")


async def redis_event_loop():
    """
    Listens to all Redis channels that come from the WhatsApp service
    and routes them to the right handlers (WebSocket broadcast or message processor).
    """
    async def handler(channel: str, data: dict) -> None:
        if channel == "whatsapp:qr":
            instance_id = data.get("instanceId")
            logger.info(f"QR code received for {instance_id}")
            await ws_manager.broadcast("qr_code", data)

        elif channel == "whatsapp:status":
            instance_id = data.get("instanceId")
            status = data.get("status")
            phone = data.get("phone")
            logger.info(f"Status update: {instance_id} → {status}")

            # Update DB
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Connection).where(Connection.instance_id == instance_id)
                )
                conn = result.scalar_one_or_none()
                if conn:
                    conn.status = status
                    if phone:
                        conn.phone = phone
                    if status == "connected":
                        conn.last_connected_at = datetime.utcnow()
                    await db.commit()

            await ws_manager.broadcast("connection_status", data)

        elif channel == "whatsapp:incoming":
            await ws_manager.broadcast("new_message", data)
            # Process and auto-reply in background so we don't block the listener
            asyncio.create_task(process_incoming(data))

        elif channel == "whatsapp:sent":
            await ws_manager.broadcast("message_sent", data)

        elif channel == "whatsapp:error":
            logger.error(f"WhatsApp error: {data}")
            await ws_manager.broadcast("whatsapp_error", data)

    channels = ["whatsapp:qr", "whatsapp:status", "whatsapp:incoming", "whatsapp:sent", "whatsapp:error"]
    await subscribe_forever(channels, handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing database...")
    await init_db()
    logger.info("Starting Redis event listener...")
    task = asyncio.create_task(redis_event_loop())
    yield
    # Shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="WA Hub API", version="1.0.0", lifespan=lifespan)

# CORS — allow everything (personal project)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(agents.router)
app.include_router(connections.router)
app.include_router(conversations.router)
app.include_router(metrics.router)
app.include_router(settings_router.router)


@app.get("/api/health")
async def health():
    try:
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {
        "status": "ok",
        "redis": "connected" if redis_ok else "disconnected",
        "version": "1.0.0",
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive — client can send pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.error(f"WebSocket error: {exc}")
        ws_manager.disconnect(websocket)
