from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


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

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        raise NotImplementedError("GitHub-hosted model calls are not wired yet")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError("GitHub-hosted embeddings are not wired yet")


class OllamaProvider(ModelProvider):
    provider_name = "ollama"

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        raise NotImplementedError("Ollama chat calls are not wired yet")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError("Ollama embeddings are not wired yet")


class ModelRouter:
    def __init__(self, providers: dict[str, ModelProvider]) -> None:
        self.providers = providers

    def get(self, name: str) -> ModelProvider:
        try:
            return self.providers[name]
        except KeyError as exc:
            raise ValueError(f"Unknown model provider: {name}") from exc
