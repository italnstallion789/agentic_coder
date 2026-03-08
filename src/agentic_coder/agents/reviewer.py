from dataclasses import dataclass

from agentic_coder.agents.coder import PatchProposal


@dataclass(slots=True)
class ReviewResult:
    approved: bool
    feedback: str


class ReviewerAgent:
    def review(self, proposal: PatchProposal) -> ReviewResult:
        if not proposal.target_files:
            return ReviewResult(approved=False, feedback="No candidate files identified")
        return ReviewResult(approved=True, feedback="Proposal aligned with available context")
