from typing import Literal

from pydantic import BaseModel, Field, model_validator

AutonomyMode = Literal["gated", "autonomous"]


class SystemPolicy(BaseModel):
    name: str = "agentic-coder"
    environment: str = "development"
    control_repository: str | None = None
    allow_any_target_repository: bool = False
    allowed_target_repositories: list[str] = Field(default_factory=list)


class AutonomyPolicy(BaseModel):
    mode: AutonomyMode = "gated"
    allowed_actions: list[str] = Field(default_factory=list)
    approval_required_actions: list[str] = Field(default_factory=list)


class SandboxPolicy(BaseModel):
    profile: str = "compose"
    network_enabled: bool = False
    network_allowlist: list[str] = Field(default_factory=list)
    max_runtime_seconds: int = 900
    max_cpu_cores: int = 2
    max_memory_mb: int = 2048


class ModelPolicy(BaseModel):
    primary_provider: str = "github"
    fallback_provider: str | None = "ollama"
    embedding_provider: str = "github"


class KnowledgeGraphPolicy(BaseModel):
    enabled: bool = True
    storage: str = "postgres"
    node_types: list[str] = Field(default_factory=list)
    edge_types: list[str] = Field(default_factory=list)


class BudgetPolicy(BaseModel):
    max_prompt_tokens: int = 32000
    max_completion_tokens: int = 8000
    max_parallel_candidates: int = 1


class AgenticPolicy(BaseModel):
    version: int = 1
    system: SystemPolicy = Field(default_factory=SystemPolicy)
    autonomy: AutonomyPolicy = Field(default_factory=AutonomyPolicy)
    sandbox: SandboxPolicy = Field(default_factory=SandboxPolicy)
    models: ModelPolicy = Field(default_factory=ModelPolicy)
    knowledge_graph: KnowledgeGraphPolicy = Field(default_factory=KnowledgeGraphPolicy)
    budgets: BudgetPolicy = Field(default_factory=BudgetPolicy)

    @model_validator(mode="after")
    def validate_autonomy_mode(self) -> "AgenticPolicy":
        if self.autonomy.mode == "gated" and not self.autonomy.approval_required_actions:
            raise ValueError("gated autonomy requires at least one approval-required action")
        if self.budgets.max_parallel_candidates < 1:
            raise ValueError("max_parallel_candidates must be at least 1")
        if (
            not self.system.allow_any_target_repository
            and not self.system.allowed_target_repositories
        ):
            raise ValueError(
                "set allowed_target_repositories or enable allow_any_target_repository"
            )
        return self
