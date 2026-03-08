from collections import defaultdict

from agentic_coder.knowledge_graph.models import GraphEdge, GraphNode


class InMemoryKnowledgeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._adjacency: dict[str, list[GraphEdge]] = defaultdict(list)

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.node_id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        self.edges.append(edge)
        self._adjacency[edge.source_id].append(edge)

    def neighbors(self, node_id: str) -> list[GraphNode]:
        return [
            self.nodes[edge.target_id]
            for edge in self._adjacency.get(node_id, [])
            if edge.target_id in self.nodes
        ]

    def summary(self) -> dict[str, int]:
        return {"nodes": len(self.nodes), "edges": len(self.edges)}
