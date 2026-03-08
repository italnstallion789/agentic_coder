from pathlib import Path

import yaml

from agentic_coder.policy.models import AgenticPolicy


class PolicyLoader:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AgenticPolicy:
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return AgenticPolicy.model_validate(data)


def resolve_policy_path(start: Path) -> Path:
    candidate = start / "agentic.yaml"
    if candidate.exists():
        return candidate

    for parent in start.parents:
        candidate = parent / "agentic.yaml"
        if candidate.exists():
            return candidate

    raise FileNotFoundError("Could not locate agentic.yaml")
