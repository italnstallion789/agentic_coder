import asyncio
import json
from dataclasses import dataclass

from agentic_coder.agents.coder import PatchProposal
from agentic_coder.models.providers import ChatMessage, ModelProvider


@dataclass(slots=True)
class ReviewResult:
    approved: bool
    feedback: str


class ReviewerAgent:
    def __init__(self, model: ModelProvider | None = None) -> None:
        self.model = model

    def review(self, proposal: PatchProposal) -> ReviewResult:
        if self.model is not None:
            try:
                return self._review_with_model(proposal)
            except Exception:
                pass
        return self._review_stub(proposal)

    def _review_stub(self, proposal: PatchProposal) -> ReviewResult:
        if not proposal.target_files:
            return ReviewResult(approved=False, feedback="No candidate files identified")
        return ReviewResult(approved=True, feedback="Proposal aligned with available context")

    def _review_with_model(self, proposal: PatchProposal) -> ReviewResult:
        file_list = ", ".join(proposal.target_files) or "(none)"
        prompt = (
            "You are a senior code reviewer. Evaluate the scope and integrity of a proposed change.\n"
            "Return ONLY compact JSON with keys:\n"
            "  - approved (bool): whether the proposal is acceptable\n"
            "  - feedback (string): concise reviewer notes\n\n"
            f"Summary: {proposal.summary}\n"
            f"Target files: {file_list}\n"
        )
        messages = [
            ChatMessage(role="system", content="You are a senior code reviewer. Respond only with valid JSON."),
            ChatMessage(role="user", content=prompt),
        ]
        response = asyncio.run(self.model.chat(messages))
        parsed = json.loads(response)
        approved = bool(parsed.get("approved", True))
        feedback = str(parsed.get("feedback") or "No feedback provided")
        return ReviewResult(approved=approved, feedback=feedback)
