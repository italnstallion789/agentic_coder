from dataclasses import dataclass


@dataclass(slots=True)
class SecurityResult:
    passed: bool
    findings: list[str]


class SecurityAgent:
    def scan_request(self, body: str) -> SecurityResult:
        risky_markers = ["rm -rf", "drop table", "disable auth"]
        findings = [marker for marker in risky_markers if marker in body.lower()]
        return SecurityResult(passed=not findings, findings=findings)
