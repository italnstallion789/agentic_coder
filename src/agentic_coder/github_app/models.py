from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class NormalizedGithubTask:
    event_name: str
    source_repository: str
    target_repository: str
    installation_id: int | None
    title: str
    body: str
    issue_number: int | None
    sender: str
    raw_event: dict[str, Any]
