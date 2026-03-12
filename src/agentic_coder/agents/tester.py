import asyncio
import json
from dataclasses import dataclass

from agentic_coder.models.providers import ChatMessage, ModelProvider


@dataclass(slots=True)
class ExecutionTestPlan:
    commands: list[str]


class TestAgent:
    def __init__(self, model: ModelProvider | None = None) -> None:
        self.model = model

    def build_plan(
        self,
        plan: object | None = None,
        proposal: object | None = None,
    ) -> ExecutionTestPlan:
        if self.model is not None:
            try:
                return self._build_plan_with_model(plan, proposal)
            except Exception:
                pass
        return ExecutionTestPlan(commands=["pytest -q"])

    def _build_plan_with_model(
        self,
        plan: object | None,
        proposal: object | None,
    ) -> ExecutionTestPlan:
        objective = getattr(plan, "objective", "") if plan is not None else ""
        target_files = getattr(proposal, "target_files", []) if proposal is not None else []
        file_list = ", ".join(str(f) for f in target_files) or "(none)"
        prompt = (
            "You are a QA engineer. Given a task objective and the files being changed, "
            "produce a minimal ordered list of shell commands to validate the change.\n"
            "Return ONLY compact JSON with keys:\n"
            "  - commands (array of strings): ordered shell commands to run\n\n"
            f"Objective: {objective or '(unknown)'}\n"
            f"Affected files: {file_list}\n"
        )
        messages = [
            ChatMessage(
                role="system",
                content="You are a QA engineer. Respond only with valid JSON.",
            ),
            ChatMessage(role="user", content=prompt),
        ]
        response = asyncio.run(self.model.chat(messages))
        parsed = json.loads(response)
        commands = [str(c) for c in (parsed.get("commands") or []) if str(c).strip()]
        return ExecutionTestPlan(commands=commands or ["pytest -q"])
