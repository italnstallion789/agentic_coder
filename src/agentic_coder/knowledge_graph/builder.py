import ast
from pathlib import Path

from agentic_coder.knowledge_graph.models import GraphEdge, GraphNode, GraphNodeType
from agentic_coder.knowledge_graph.service import InMemoryKnowledgeGraph


class KnowledgeGraphBuilder:
    def build_from_workspace(self, workspace_root: Path) -> InMemoryKnowledgeGraph:
        graph = InMemoryKnowledgeGraph()
        repo_node = GraphNode(node_id="repo:local", node_type=GraphNodeType.REPO, label="local")
        graph.add_node(repo_node)

        for file_path in workspace_root.rglob("*.py"):
            if "/." in str(file_path):
                continue
            rel = str(file_path.relative_to(workspace_root))
            file_node_id = f"file:{rel}"
            graph.add_node(
                GraphNode(node_id=file_node_id, node_type=GraphNodeType.FILE, label=rel)
            )
            graph.add_edge(
                GraphEdge(source_id="repo:local", target_id=file_node_id, edge_type="contains")
            )

            try:
                module = ast.parse(file_path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue

            for node in ast.walk(module):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbol_name = node.name
                    symbol_id = f"symbol:{rel}:{symbol_name}"
                    graph.add_node(
                        GraphNode(
                            node_id=symbol_id,
                            node_type=GraphNodeType.SYMBOL,
                            label=symbol_name,
                        )
                    )
                    graph.add_edge(
                        GraphEdge(source_id=file_node_id, target_id=symbol_id, edge_type="defines")
                    )

                if isinstance(node, ast.Import):
                    for alias in node.names:
                        import_id = f"symbol:import:{alias.name}"
                        graph.add_node(
                            GraphNode(
                                node_id=import_id,
                                node_type=GraphNodeType.SYMBOL,
                                label=alias.name,
                            )
                        )
                        graph.add_edge(
                            GraphEdge(
                                source_id=file_node_id,
                                target_id=import_id,
                                edge_type="imports",
                            )
                        )

                if isinstance(node, ast.ImportFrom) and node.module:
                    import_id = f"symbol:import:{node.module}"
                    graph.add_node(
                        GraphNode(
                            node_id=import_id,
                            node_type=GraphNodeType.SYMBOL,
                            label=node.module,
                        )
                    )
                    graph.add_edge(
                        GraphEdge(source_id=file_node_id, target_id=import_id, edge_type="imports")
                    )

        return graph
