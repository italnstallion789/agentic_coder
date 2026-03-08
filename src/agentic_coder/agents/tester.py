from dataclasses import dataclass


@dataclass(slots=True)
class ExecutionTestPlan:
    commands: list[str]


class TestAgent:
    def build_plan(self) -> ExecutionTestPlan:
        return ExecutionTestPlan(commands=["pytest -q"])
