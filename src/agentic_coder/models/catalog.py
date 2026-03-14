from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatModelOption:
    provider: str
    model: str
    display_name: str
    cost_tier: str
    recommended: bool = False

    @property
    def selection_key(self) -> str:
        return f"{self.provider}::{self.model}"

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "displayName": self.display_name,
            "costTier": self.cost_tier,
            "recommended": self.recommended,
            "selectionKey": self.selection_key,
        }


_GITHUB_CHAT_MODELS: tuple[ChatModelOption, ...] = (
    ChatModelOption("github", "Phi-4", "Phi-4", "0x", recommended=True),
    ChatModelOption("github", "Phi-4-mini", "Phi-4 mini", "0x"),
    ChatModelOption("github", "gpt-4.1-mini", "GPT-4.1 mini", "0x"),
    ChatModelOption("github", "gpt-4o-mini", "GPT-4o mini", "0x"),
    ChatModelOption(
        "github",
        "Meta-Llama-3.3-70B-Instruct",
        "Meta Llama 3.3 70B",
        "0x",
    ),
    ChatModelOption("github", "gpt-4.1", "GPT-4.1", "1x"),
    ChatModelOption("github", "claude-3-7-sonnet", "Claude 3.7 Sonnet", "1x"),
)


def available_chat_models(settings: object) -> list[dict[str, object]]:
    options: list[ChatModelOption] = []

    if getattr(settings, "github_models_api_key", ""):
        options.extend(_GITHUB_CHAT_MODELS)
        configured_model = str(getattr(settings, "github_models_chat_model", "") or "").strip()
        if configured_model and configured_model not in {option.model for option in options}:
            options.insert(
                0,
                ChatModelOption(
                    "github",
                    configured_model,
                    configured_model,
                    "custom",
                ),
            )

    ollama_model = str(getattr(settings, "ollama_chat_model", "") or "").strip()
    if ollama_model:
        options.append(
            ChatModelOption(
                "ollama",
                ollama_model,
                ollama_model,
                "local",
            )
        )

    return [option.to_dict() for option in options]


def find_chat_model_option(
    settings: object,
    *,
    provider: str | None,
    model: str | None,
) -> dict[str, object] | None:
    normalized_provider = str(provider or "").strip()
    normalized_model = str(model or "").strip()
    if not normalized_provider or not normalized_model:
        return None

    for option in available_chat_models(settings):
        if (
            option["provider"] == normalized_provider
            and option["model"] == normalized_model
        ):
            return option
    return None


def default_chat_model_selection(policy: object, settings: object) -> dict[str, object] | None:
    primary = str(getattr(policy.models, "primary_provider", "") or "").strip()
    fallback = str(getattr(policy.models, "fallback_provider", "") or "").strip()

    if primary == "auto":
        if getattr(settings, "github_models_api_key", ""):
            return find_chat_model_option(
                settings,
                provider="github",
                model=getattr(settings, "github_models_chat_model", ""),
            )
        return find_chat_model_option(
            settings,
            provider="ollama",
            model=getattr(settings, "ollama_chat_model", ""),
        )

    if primary == "github" and getattr(settings, "github_models_api_key", ""):
        return find_chat_model_option(
            settings,
            provider="github",
            model=getattr(settings, "github_models_chat_model", ""),
        )

    if primary == "ollama":
        return find_chat_model_option(
            settings,
            provider="ollama",
            model=getattr(settings, "ollama_chat_model", ""),
        )

    if fallback == "github" and getattr(settings, "github_models_api_key", ""):
        return find_chat_model_option(
            settings,
            provider="github",
            model=getattr(settings, "github_models_chat_model", ""),
        )

    if fallback == "ollama":
        return find_chat_model_option(
            settings,
            provider="ollama",
            model=getattr(settings, "ollama_chat_model", ""),
        )

    return None
