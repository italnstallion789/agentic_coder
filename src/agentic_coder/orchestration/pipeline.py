from dataclasses import dataclass
from pathlib import Path

from agentic_coder.agents.coder import CodingAgent, PatchProposal
from agentic_coder.agents.context import ContextRetrievalAgent
from agentic_coder.agents.planner import PlannerAgent, PlanResult
from agentic_coder.agents.pr_generator import PullRequestDraft, PullRequestGenerator
from agentic_coder.agents.reviewer import ReviewerAgent, ReviewResult
from agentic_coder.agents.security import SecurityAgent, SecurityResult
from agentic_coder.agents.tester import ExecutionTestPlan, TestAgent
from agentic_coder.config import get_settings
from agentic_coder.domain.tasks import TaskRecord
from agentic_coder.knowledge_graph.builder import KnowledgeGraphBuilder
from agentic_coder.models.providers import (
    GitHubHostedProvider,
    ModelProvider,
    ModelRouter,
    OllamaProvider,
)
from agentic_coder.policy.loader import PolicyLoader
from agentic_coder.retrieval.service import InMemoryRetriever


@dataclass(slots=True)
class PipelineResult:
    plan: PlanResult
    proposal: PatchProposal
    review: ReviewResult
    security: SecurityResult
    test_plan: ExecutionTestPlan
    pr_draft: PullRequestDraft
    graph_summary: dict[str, int]
    indexed_files: int
    model_used: str | None


class TaskPipeline:
    def __init__(
        self,
        workspace_root: Path,
        *,
        model_provider_override: str | None = None,
        model_name_override: str | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.retriever = InMemoryRetriever()
        self.context_agent = ContextRetrievalAgent(self.retriever)
        self.graph_builder = KnowledgeGraphBuilder()
        self.settings = get_settings()
        self.policy = PolicyLoader(path=Path("agentic.yaml")).load()
        self.model_provider_override = str(model_provider_override or "").strip() or None
        self.model_name_override = str(model_name_override or "").strip() or None
        self.router = self._build_model_router()
        provider_name, model_name = self._select_provider()
        self._model_used: str | None = f"{provider_name}:{model_name}" if provider_name else None
        _model = self.router.get(provider_name) if provider_name else None
        self.planner = PlannerAgent(model=_model)
        self.coder = CodingAgent(model=_model)
        self.reviewer = ReviewerAgent(model=_model)
        self.security = SecurityAgent(model=_model)
        self.tester = TestAgent(model=_model)
        self.pr_generator = PullRequestGenerator(model=_model)

    def _build_model_router(self) -> ModelRouter:
        providers: dict[str, ModelProvider] = {}
        github_model = (
            self.model_name_override
            if self.model_provider_override == "github" and self.model_name_override
            else self.settings.github_models_chat_model
        )
        ollama_model = (
            self.model_name_override
            if self.model_provider_override == "ollama" and self.model_name_override
            else self.settings.ollama_chat_model
        )
        if self.settings.github_models_api_key:
            providers["github"] = GitHubHostedProvider(
                api_key=self.settings.github_models_api_key,
                model=github_model,
                base_url=self.settings.github_models_base_url,
                timeout_seconds=self.settings.model_request_timeout_seconds,
            )
        providers["ollama"] = OllamaProvider(
            model=ollama_model,
            base_url=self.settings.ollama_base_url,
            timeout_seconds=self.settings.model_request_timeout_seconds,
        )
        return ModelRouter(providers=providers)

    def _select_provider(self) -> tuple[str | None, str | None]:
        if (
            self.model_provider_override
            and self.model_name_override
            and self.router.has(self.model_provider_override)
        ):
            return self.model_provider_override, self.model_name_override

        primary = self.policy.models.primary_provider
        fallback = self.policy.models.fallback_provider

        if primary == "auto":
            if self.router.has("github"):
                return "github", self.settings.github_models_chat_model
            if self.router.has("ollama"):
                return "ollama", self.settings.ollama_chat_model
            return None, None

        if self.router.has(primary):
            if primary == "github":
                return "github", self.settings.github_models_chat_model
            if primary == "ollama":
                return "ollama", self.settings.ollama_chat_model

        if fallback and self.router.has(fallback):
            if fallback == "github":
                return "github", self.settings.github_models_chat_model
            if fallback == "ollama":
                return "ollama", self.settings.ollama_chat_model

        return None, None

    def run(self, task: TaskRecord) -> PipelineResult:
        payload = task.payload
        title = str(payload.get("title") or task.title)
        body = str(payload.get("body") or "")
        repository = str(payload.get("repository") or "unknown/unknown")

        plan = self.planner.create_plan(title=title, body=body)
        indexed_files = self.context_agent.index_workspace(self.workspace_root)
        context_docs = self.context_agent.retrieve(query=f"{title} {body}")
        graph = self.graph_builder.build_from_workspace(self.workspace_root)
        proposal = self.coder.propose(plan, context_docs, repository=repository)
        review = self.reviewer.review(proposal)
        security = self.security.scan_request(body)
        test_plan = self.tester.build_plan(plan=plan, proposal=proposal)
        pr_draft = self.pr_generator.generate(repository, plan, proposal)

        return PipelineResult(
            plan=plan,
            proposal=proposal,
            review=review,
            security=security,
            test_plan=test_plan,
            pr_draft=pr_draft,
            graph_summary=graph.summary(),
            indexed_files=indexed_files,
            model_used=self._model_used,
        )
