from pathlib import Path

from agentic_coder.retrieval.service import InMemoryRetriever, RetrievalDocument

IGNORED_PATH_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}


def _is_ignored_path(path: Path) -> bool:
    return any(part in IGNORED_PATH_PARTS for part in path.parts)


class ContextRetrievalAgent:
    def __init__(self, retriever: InMemoryRetriever) -> None:
        self.retriever = retriever

    def index_workspace(self, workspace_root: Path) -> int:
        indexed = 0
        for file_path in workspace_root.rglob("*.py"):
            if _is_ignored_path(file_path):
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            self.retriever.add(
                RetrievalDocument(
                    doc_id=str(file_path),
                    text=text,
                    metadata={"path": str(file_path.relative_to(workspace_root))},
                )
            )
            indexed += 1
        return indexed

    def retrieve(self, query: str, limit: int = 8) -> list[RetrievalDocument]:
        return self.retriever.search(query, limit=limit)
