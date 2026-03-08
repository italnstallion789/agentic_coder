from dataclasses import dataclass
from pathlib import Path

from agentic_coder.agents.coder import CodingAgent, PatchProposal
from agentic_coder.agents.context import ContextRetrievalAgent
from agentic_coder.agents.planner import PlannerAgent, PlanResult
from agentic_coder.agents.pr_generator import PullRequestDraft, PullRequestGenerator
from agentic_coder.agents.reviewer import ReviewerAgent, ReviewResult
from agentic_coder.agents.security import SecurityAgent, SecurityResult
from agentic_coder.agents.tester import ExecutionTestPlan, TestAgent
from agentic_coder.domain.tasks import TaskRecord
from agentic_coder.knowledge_graph.builder import KnowledgeGraphBuilder
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


class TaskPipeline:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.retriever = InMemoryRetriever()
        self.context_agent = ContextRetrievalAgent(self.retriever)
        self.graph_builder = KnowledgeGraphBuilder()
        self.planner = PlannerAgent()
        self.coder = CodingAgent()
        self.reviewer = ReviewerAgent()
        self.security = SecurityAgent()
        self.tester = TestAgent()
        self.pr_generator = PullRequestGenerator()

    def run(self, task: TaskRecord) -> PipelineResult:
        payload = task.payload
        title = str(payload.get("title") or task.title)
        body = str(payload.get("body") or "")
        repository = str(payload.get("repository") or "unknown/unknown")

        plan = self.planner.create_plan(title=title, body=body)
        indexed_files = self.context_agent.index_workspace(self.workspace_root)
        context_docs = self.context_agent.retrieve(query=f"{title} {body}")
        graph = self.graph_builder.build_from_workspace(self.workspace_root)
        proposal = self.coder.propose(plan, context_docs)
        review = self.reviewer.review(proposal)
        security = self.security.scan_request(body)
        test_plan = self.tester.build_plan()
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
        )
