from dataclasses import dataclass
from enum import StrEnum


class GraphNodeType(StrEnum):
    REPO = "repo"
    COMMIT = "commit"
    FILE = "file"
    SYMBOL = "symbol"
    TEST = "test"
    ISSUE = "issue"
    PR = "pr"
    TASK = "task"
    ARTIFACT = "artifact"
    DECISION = "decision"


@dataclass(slots=True)
class GraphNode:
    node_id: str
    node_type: GraphNodeType
    label: str


@dataclass(slots=True)
class GraphEdge:
    source_id: str
    target_id: str
    edge_type: str
