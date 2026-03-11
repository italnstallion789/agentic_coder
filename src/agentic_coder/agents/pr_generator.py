import asyncio
import json
from dataclasses import dataclass

from agentic_coder.agents.coder import PatchProposal
from agentic_coder.agents.planner import PlanResult
from agentic_coder.models.providers import ChatMessage, ModelProvider


@dataclass(slots=True)
class PullRequestDraft:
    title: str
    body: str


class PullRequestGenerator:
    def __init__(self, model: ModelProvider | None = None) -> None:
        self.model = model

    def generate(
        self,
        repo_name: str,
        plan: PlanResult,
        proposal: PatchProposal,
    ) -> PullRequestDraft:
        if self.model is not None:
            try:
                return self._generate_with_model(repo_name, plan, proposal)
            except Exception:
                pass
        return self._generate_stub(repo_name, plan, proposal)

    def _generate_stub(
        self,
        repo_name: str,
        plan: PlanResult,
        proposal: PatchProposal,
    ) -> PullRequestDraft:
        title = f"[{repo_name}] {plan.objective}"
        file_list = "\n".join(f"- {path}" for path in proposal.target_files)
        if not file_list:
            file_list = "- (none identified)"
        body = (
            f"## Objective\n{plan.objective}\n\n"
            f"## Proposed Changes\n{proposal.summary}\n\n"
            f"## Candidate Files\n{file_list}\n"
        )
        return PullRequestDraft(title=title[:240], body=body)

    def _generate_with_model(
        self,
        repo_name: str,
        plan: PlanResult,
        proposal: PatchProposal,
    ) -> PullRequestDraft:
        file_list = ", ".join(proposal.target_files) or "(none)"
        steps_text = "\n".join(f"  {i + 1}. {step}" for i, step in enumerate(plan.steps))
        prompt = (
            "You are a software engineer writing a GitHub pull request description.\n"
            "Return ONLY compact JSON with keys:\n"
            "  - title (string): concise PR title, max 72 characters\n"
            "  - body (string): markdown PR body with sections: ## Summary, ## Changes, ## Testing\n\n"
            f"Repository: {repo_name}\n"
            f"Objective: {plan.objective}\n"
            f"Implementation steps:\n{steps_text}\n"
            f"Proposed changes: {proposal.summary}\n"
            f"Files to modify: {file_list}\n"
        )
        messages = [
            ChatMessage(role="system", content="You are a software engineer. Respond only with valid JSON."),
            ChatMessage(role="user", content=prompt),
        ]
        response = asyncio.run(self.model.chat(messages))
        parsed = json.loads(response)
        title = str(parsed.get("title") or plan.objective)[:240]
        body = str(parsed.get("body") or "")
        if not body:
            return self._generate_stub(repo_name, plan, proposal)
        return PullRequestDraft(title=title, body=body)
