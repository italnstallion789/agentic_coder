from dataclasses import dataclass

import redis

from agentic_coder.config import get_settings


@dataclass(slots=True)
class QueuedTask:
    task_id: str


class RedisTaskQueue:
    def __init__(self, redis_url: str, queue_name: str) -> None:
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.queue_name = queue_name

    @classmethod
    def from_settings(cls) -> "RedisTaskQueue":
        settings = get_settings()
        return cls(redis_url=settings.redis_url, queue_name=settings.queue_name)

    def enqueue(self, task_id: str) -> None:
        self.client.rpush(self.queue_name, task_id)

    def dequeue(self, timeout_seconds: int = 5) -> QueuedTask | None:
        item = self.client.blpop(self.queue_name, timeout=timeout_seconds)
        if item is None:
            return None
        _, task_id = item
        return QueuedTask(task_id=task_id)
