import asyncio
import json
from dataclasses import dataclass

from agentic_coder.models.providers import ChatMessage, ModelProvider


@dataclass(slots=True)
class PlanResult:
    objective: str
    steps: list[str]


class PlannerAgent:
    def __init__(self, model: ModelProvider | None = None) -> None:
        self.model = model

    def create_plan(self, title: str, body: str) -> PlanResult:
        if self.model is not None:
            try:
                return self._create_plan_with_model(title, body)
            except Exception:
                pass
        return self._create_plan_stub(title, body)

    def _create_plan_stub(self, title: str, body: str) -> PlanResult:
        objective = title.strip() or "Implement requested change"
        steps = [
            "Locate relevant files and symbols",
            "Build context from repository and knowledge graph",
            "Produce implementation proposal",
            "Run policy and quality checks",
            "Generate PR draft summary",
        ]
        if "test" in body.lower() or "bug" in body.lower():
            steps.insert(3, "Prioritize failing test scenarios")
        return PlanResult(objective=objective, steps=steps)

    def _create_plan_with_model(self, title: str, body: str) -> PlanResult:
        prompt = (
            "You are a senior software engineer planning implementation work. "
            "Given a GitHub issue or task request, produce a concise engineering plan.\n"
            "Return ONLY compact JSON with keys:\n"
            "  - objective (string): one-sentence goal\n"
            "  - steps (array of strings): ordered implementation steps, 3-7 items\n\n"
            f"Title: {title}\n"
            f"Body: {body or '(none)'}\n"
        )
        messages = [
            ChatMessage(role="system", content="You are a senior software engineer. Respond only with valid JSON."),
            ChatMessage(role="user", content=prompt),
        ]
        response = asyncio.run(self.model.chat(messages))
        parsed = json.loads(response)
        objective = str(parsed.get("objective") or title.strip() or "Implement requested change")
        steps = [str(s) for s in (parsed.get("steps") or []) if str(s).strip()]
        if not steps:
            steps = ["Implement the requested change"]
        return PlanResult(objective=objective, steps=steps)
