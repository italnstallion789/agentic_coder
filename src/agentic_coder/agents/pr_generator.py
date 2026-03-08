from dataclasses import dataclass

from agentic_coder.agents.coder import PatchProposal
from agentic_coder.agents.planner import PlanResult


@dataclass(slots=True)
class PullRequestDraft:
    title: str
    body: str


class PullRequestGenerator:
    def generate(
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
