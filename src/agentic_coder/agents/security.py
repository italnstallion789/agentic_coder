import asyncio
import json
from dataclasses import dataclass

from agentic_coder.models.providers import ChatMessage, ModelProvider


@dataclass(slots=True)
class SecurityResult:
    passed: bool
    findings: list[str]


class SecurityAgent:
    _RISKY_MARKERS = ("rm -rf", "drop table", "disable auth")

    def __init__(self, model: ModelProvider | None = None) -> None:
        self.model = model

    def scan_request(self, body: str) -> SecurityResult:
        if self.model is not None:
            try:
                return self._scan_with_model(body)
            except Exception:
                pass
        return self._scan_stub(body)

    def _scan_stub(self, body: str) -> SecurityResult:
        findings = [marker for marker in self._RISKY_MARKERS if marker in body.lower()]
        return SecurityResult(passed=not findings, findings=findings)

    def _scan_with_model(self, body: str) -> SecurityResult:
        stub = self._scan_stub(body)
        prompt = (
            "You are an application security engineer performing a threat review of a code"
            " change request.\n"
            "Identify security risks such as injection, privilege escalation, data exposure,"
            " or unsafe operations.\n"
            "Return ONLY compact JSON with keys:\n"
            "  - passed (bool): true if no serious risks found\n"
            "  - findings (array of strings): brief description of each risk found (empty array"
            " if none)\n\n"
            f"Change request:\n{body}\n"
        )
        messages = [
            ChatMessage(
                role="system",
                content="You are a security engineer. Respond only with valid JSON.",
            ),
            ChatMessage(role="user", content=prompt),
        ]
        response = asyncio.run(self.model.chat(messages))
        parsed = json.loads(response)
        model_passed = bool(parsed.get("passed", True))
        model_findings = [str(f) for f in (parsed.get("findings") or []) if str(f).strip()]
        # Merge: fail if either check fails; deduplicated union of findings
        all_findings = list(dict.fromkeys(stub.findings + model_findings))
        return SecurityResult(passed=stub.passed and model_passed, findings=all_findings)
