from agentic_coder.agents.coder import CodingAgent
from agentic_coder.agents.planner import PlanResult
from agentic_coder.retrieval.service import RetrievalDocument


class _FakeModel:
    async def chat(self, messages) -> str:  # noqa: ANN001
        _ = messages
        return (
            '{"summary":"Update cache logic","target_files":["src/cache.py"],'
            '"file_changes":[{"path":"src/cache.py","content":"def cache():\\n    return 42\\n"}]}'
        )


def test_coding_agent_parses_file_changes_from_model_response() -> None:
    agent = CodingAgent(model=_FakeModel())
    proposal = agent.propose(
        PlanResult(objective="Update cache", steps=["edit file"]),
        [
            RetrievalDocument(
                doc_id="src/cache.py",
                text="def cache():\n    return 1\n",
                metadata={"path": "src/cache.py"},
            )
        ],
        repository="acme/widgets",
    )

    assert proposal.summary == "Update cache logic"
    assert proposal.target_files == ["src/cache.py"]
    assert len(proposal.file_changes) == 1
    assert proposal.file_changes[0].path == "src/cache.py"
    assert "return 42" in proposal.file_changes[0].content
