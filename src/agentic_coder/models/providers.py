from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str


class ModelProvider(ABC):
    provider_name: str

    @abstractmethod
    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        raise NotImplementedError

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError


class GitHubHostedProvider(ModelProvider):
    provider_name = "github"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float = 40.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        return str((((body.get("choices") or [{}])[0]).get("message") or {}).get("content") or "")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": self.model, "input": list(texts)}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json().get("data") or []
        return [list(item.get("embedding") or []) for item in data]


class OllamaProvider(ModelProvider):
    provider_name = "ollama"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        timeout_seconds: float = 40.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, json=payload)
        response.raise_for_status()
        body = response.json()
        return str((body.get("message") or {}).get("content") or "")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for text in texts:
                url = f"{self.base_url}/api/embeddings"
                payload = {"model": self.model, "prompt": text}
                response = await client.post(url, json=payload)
                response.raise_for_status()
                embeddings.append(list(response.json().get("embedding") or []))
        return embeddings


class ModelRouter:
    def __init__(self, providers: dict[str, ModelProvider]) -> None:
        self.providers = providers

    def get(self, name: str) -> ModelProvider:
        try:
            return self.providers[name]
        except KeyError as exc:
            raise ValueError(f"Unknown model provider: {name}") from exc

    def has(self, name: str) -> bool:
        return name in self.providers
