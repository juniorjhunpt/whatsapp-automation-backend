import json
import logging
import asyncio
from typing import Callable, Optional
import redis.asyncio as aioredis
from config import settings

logger = logging.getLogger(__name__)

_redis: Optional[aioredis.Redis] = None

async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis

async def publish(channel: str, data: dict) -> None:
    r = await get_redis()
    await r.publish(channel, json.dumps(data))

async def subscribe_forever(channels: list[str], handler: Callable[[str, dict], None]) -> None:
    """Subscribe to Redis channels and call handler for each message. Runs forever."""
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(*channels)
    logger.info(f"Subscribed to Redis channels: {channels}")
    try:
        async for raw in pubsub.listen():
            if raw["type"] != "message":
                continue
            try:
                data = json.loads(raw["data"])
                await handler(raw["channel"], data)
            except Exception as exc:
                logger.error(f"Error in Redis handler for {raw['channel']}: {exc}")
    except asyncio.CancelledError:
        await pubsub.unsubscribe()
        await r.aclose()
