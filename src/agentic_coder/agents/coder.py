from dataclasses import dataclass

from agentic_coder.agents.planner import PlanResult
from agentic_coder.retrieval.service import RetrievalDocument


@dataclass(slots=True)
class PatchProposal:
    summary: str
    target_files: list[str]


class CodingAgent:
    def propose(self, plan: PlanResult, context_docs: list[RetrievalDocument]) -> PatchProposal:
        target_files = [doc.metadata.get("path", doc.doc_id) for doc in context_docs[:5]]
        summary = (
            f"Objective: {plan.objective}. "
            f"Prepared implementation path across {len(target_files)} candidate files."
        )
        return PatchProposal(summary=summary, target_files=target_files)
