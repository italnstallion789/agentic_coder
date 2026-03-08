from dataclasses import dataclass


@dataclass(slots=True)
class PlanResult:
    objective: str
    steps: list[str]


class PlannerAgent:
    def create_plan(self, title: str, body: str) -> PlanResult:
        objective = title.strip() or "Implement requested change"
        steps = [
            "Locate relevant files and symbols",
            "Build context from repository and knowledge graph",
            "Produce implementation proposal",
            "Run policy and quality checks",
            "Generate PR draft summary",
        ]
        if "test" in body.lower() or "bug" in body.lower():
            steps.insert(3, "Prioritize failing test scenarios")
        return PlanResult(objective=objective, steps=steps)
