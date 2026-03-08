from pathlib import Path

from agentic_coder.policy.loader import PolicyLoader, resolve_policy_path


def test_policy_loader_reads_default_file() -> None:
    policy_path = resolve_policy_path(Path.cwd())
    policy = PolicyLoader(policy_path).load()

    assert policy.autonomy.mode == "gated"
    assert policy.knowledge_graph.enabled is True
