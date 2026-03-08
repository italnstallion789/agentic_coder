from dataclasses import dataclass
from uuid import uuid4


@dataclass(slots=True)
class TraceContext:
    trace_id: str

    @classmethod
    def new(cls) -> "TraceContext":
        return cls(trace_id=str(uuid4()))
