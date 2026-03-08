from dataclasses import dataclass

from agentic_coder.policy.models import AgenticPolicy


@dataclass(slots=True)
class SandboxCommand:
    argv: list[str]
    needs_network: bool = False


@dataclass(slots=True)
class SandboxResult:
    allowed: bool
    reason: str


class SandboxPolicyEnforcer:
    def __init__(self, policy: AgenticPolicy) -> None:
        self.policy = policy

    def check(self, command: SandboxCommand) -> SandboxResult:
        if not command.argv:
            return SandboxResult(allowed=False, reason="empty command")
        if command.needs_network and not self.policy.sandbox.network_enabled:
            return SandboxResult(allowed=False, reason="network access disabled by policy")
        return SandboxResult(allowed=True, reason="allowed")
