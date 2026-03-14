import json
from dataclasses import dataclass, field

from agentic_coder.models.providers import ChatMessage, ModelProvider


@dataclass(slots=True)
class DispatchPlan:
    ready: bool
    summary: str
    issue_title: str
    issue_body_markdown: str
    custom_instructions: str
    clarification_questions: list[str] = field(default_factory=list)


class ChatManagerAgent:
    def __init__(self, model: ModelProvider | None = None) -> None:
        self.model = model

    async def prepare_dispatch(
        self,
        *,
        session_title: str,
        target_repository: str,
        transcript: str,
        base_branch: str,
    ) -> DispatchPlan:
        if self.model is not None:
            try:
                return await self._prepare_with_model(
                    session_title=session_title,
                    target_repository=target_repository,
                    transcript=transcript,
                    base_branch=base_branch,
                )
            except Exception:
                pass
        return self._prepare_stub(
            session_title=session_title,
            target_repository=target_repository,
            transcript=transcript,
            base_branch=base_branch,
        )

    def _prepare_stub(
        self,
        *,
        session_title: str,
        target_repository: str,
        transcript: str,
        base_branch: str,
    ) -> DispatchPlan:
        issue_title = (session_title or "Agentic request").strip()
        body = (
            "## Objective\n"
            f"- Work on `{target_repository}` from `{base_branch}`.\n\n"
            "## Transcript\n"
            "```text\n"
            f"{transcript.strip()}\n"
            "```\n"
        )
        return DispatchPlan(
            ready=True,
            summary=f"Dispatch `{issue_title}` to GitHub coding agent.",
            issue_title=issue_title,
            issue_body_markdown=body,
            custom_instructions="Follow the transcript, keep changes scoped, and open a draft PR.",
            clarification_questions=[],
        )

    async def _prepare_with_model(
        self,
        *,
        session_title: str,
        target_repository: str,
        transcript: str,
        base_branch: str,
    ) -> DispatchPlan:
        prompt = (
            "You are an engineering manager preparing work for GitHub Copilot coding agent.\n"
            "Given a chat transcript, decide if the request is ready to dispatch or if the user "
            "must answer clarification questions first.\n"
            "Return ONLY compact JSON with keys:\n"
            "  - ready (boolean)\n"
            "  - summary (string)\n"
            "  - issue_title (string)\n"
            "  - issue_body_markdown (string)\n"
            "  - custom_instructions (string)\n"
            "  - clarification_questions (array of strings, max 3)\n"
            "Rules:\n"
            "  - If the request is ambiguous or missing decision-making context, set ready=false.\n"
            "  - Ask only the minimum questions needed to unblock the work.\n"
            "  - If ready=true, issue_body_markdown must include sections titled Objective, "
            "Context, Constraints, and Acceptance Criteria.\n"
            "  - custom_instructions should be short operational guidance for the coding agent.\n\n"
            f"Session title: {session_title or 'Untitled'}\n"
            f"Target repository: {target_repository}\n"
            f"Base branch: {base_branch}\n\n"
            f"Transcript:\n{transcript}\n"
        )
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are an engineering manager. Respond only with valid JSON and be precise."
                ),
            ),
            ChatMessage(role="user", content=prompt),
        ]
        response = await self.model.chat(messages)
        parsed = json.loads(response)
        questions = [
            str(item).strip()
            for item in (parsed.get("clarification_questions") or [])
            if str(item).strip()
        ][:3]
        return DispatchPlan(
            ready=bool(parsed.get("ready", True)),
            summary=str(parsed.get("summary") or session_title or "Dispatch request"),
            issue_title=str(parsed.get("issue_title") or session_title or "Agentic request"),
            issue_body_markdown=str(parsed.get("issue_body_markdown") or transcript),
            custom_instructions=str(parsed.get("custom_instructions") or ""),
            clarification_questions=questions,
        )
