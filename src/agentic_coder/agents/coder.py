import asyncio
import json
from dataclasses import dataclass

from agentic_coder.agents.planner import PlanResult
from agentic_coder.models.providers import ChatMessage, ModelProvider
from agentic_coder.retrieval.service import RetrievalDocument


@dataclass(slots=True)
class PatchProposal:
    summary: str
    target_files: list[str]


class CodingAgent:
    def __init__(self, model: ModelProvider | None = None) -> None:
        self.model = model

    def propose(
        self,
        plan: PlanResult,
        context_docs: list[RetrievalDocument],
        *,
        repository: str = "",
    ) -> PatchProposal:
        if self.model is not None:
            try:
                return self._propose_with_model(plan, context_docs, repository=repository)
            except Exception:
                pass
        return self._propose_stub(plan, context_docs)

    def _propose_stub(self, plan: PlanResult, context_docs: list[RetrievalDocument]) -> PatchProposal:
        target_files = [doc.metadata.get("path", doc.doc_id) for doc in context_docs[:5]]
        summary = (
            f"Objective: {plan.objective}. "
            f"Prepared implementation path across {len(target_files)} candidate files."
        )
        return PatchProposal(summary=summary, target_files=target_files)

    def _propose_with_model(
        self,
        plan: PlanResult,
        context_docs: list[RetrievalDocument],
        *,
        repository: str,
    ) -> PatchProposal:
        file_context = "\n".join(
            f"- {doc.metadata.get('path', doc.doc_id)}: {doc.text[:400]}"
            for doc in context_docs[:10]
        )
        steps_text = "\n".join(f"  {i + 1}. {step}" for i, step in enumerate(plan.steps))
        prompt = (
            "You are an expert software engineer. Given a task plan and relevant repository files, "
            "identify which files need to be changed and describe the implementation approach.\n"
            "Only reference file paths that exist in the provided file list.\n"
            "Return ONLY compact JSON with keys:\n"
            "  - summary (string): concise description of what changes will be made\n"
            "  - target_files (array of strings): relative file paths that need modification\n\n"
            f"Repository: {repository or 'unknown'}\n"
            f"Objective: {plan.objective}\n"
            f"Steps:\n{steps_text}\n\n"
            f"Relevant files:\n{file_context or '(none indexed)'}\n"
        )
        messages = [
            ChatMessage(role="system", content="You are an expert software engineer. Respond only with valid JSON."),
            ChatMessage(role="user", content=prompt),
        ]
        response = asyncio.run(self.model.chat(messages))
        parsed = json.loads(response)
        summary = str(parsed.get("summary") or plan.objective)
        files = parsed.get("target_files") or []
        target_files = [str(f) for f in files if str(f).strip()]
        return PatchProposal(summary=summary, target_files=target_files)
