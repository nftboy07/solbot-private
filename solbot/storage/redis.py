import json
import logging
import redis.asyncio as redis
from typing import Any, Dict

logger = logging.getLogger(__name__)

class RedisManager:
    """Async Redis manager for stream operations."""
    
    def __init__(self, host: str = "localhost", port: int = 6379):
        self.host = host
        self.port = port
        self.client: redis.Redis = None

    async def connect(self):
        self.client = redis.Redis(host=self.host, port=self.port, decode_responses=True)
        await self.client.ping()
        logger.info(f"Connected to Redis at {self.host}:{self.port}")

    async def publish(self, stream_name: str, message: Dict[str, Any]):
        """Publish message to a Redis stream."""
        if self.client:
            await self.client.xadd(stream_name, {"data": json.dumps(message)})

    async def disconnect(self):
        if self.client:
            await self.client.close()
            logger.info("Disconnected from Redis.")
