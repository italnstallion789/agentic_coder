from dataclasses import dataclass


@dataclass(slots=True)
class RetrievalDocument:
    doc_id: str
    text: str
    metadata: dict[str, str]


class InMemoryRetriever:
    def __init__(self) -> None:
        self._documents: list[RetrievalDocument] = []

    def add(self, document: RetrievalDocument) -> None:
        self._documents.append(document)

    def search(self, query: str, limit: int = 5) -> list[RetrievalDocument]:
        query_terms = {term.lower() for term in query.split() if term.strip()}
        scored: list[tuple[int, RetrievalDocument]] = []
        for document in self._documents:
            doc_terms = {term.lower() for term in document.text.split() if term.strip()}
            score = len(query_terms & doc_terms)
            if score:
                scored.append((score, document))
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored:
            return [document for _, document in scored[:limit]]
        return self._documents[:limit]
