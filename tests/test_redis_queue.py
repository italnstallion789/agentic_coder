from agentic_coder.queue.redis_queue import RedisTaskQueue


class _FakeRedis:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def rpush(self, queue_name: str, task_id: str) -> None:
        self.items.append((queue_name, task_id))

    def blpop(self, queue_name: str, timeout: int = 0) -> tuple[str, str] | None:
        if not self.items:
            return None
        _, task_id = self.items.pop(0)
        return queue_name, task_id


def test_redis_queue_enqueue_dequeue(monkeypatch) -> None:
    fake = _FakeRedis()

    def _from_url(*args, **kwargs):  # noqa: ANN002, ANN003
        return fake

    monkeypatch.setattr("agentic_coder.queue.redis_queue.redis.Redis.from_url", _from_url)

    queue = RedisTaskQueue(redis_url="redis://example", queue_name="agentic:tasks")
    queue.enqueue("abc123")
    queued = queue.dequeue(timeout_seconds=1)

    assert queued is not None
    assert queued.task_id == "abc123"
