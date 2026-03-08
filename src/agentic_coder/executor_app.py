import time
from pathlib import Path

from structlog import get_logger

from agentic_coder.logging import configure_logging
from agentic_coder.policy.loader import PolicyLoader

configure_logging()
logger = get_logger(__name__)


def main() -> None:
    policy = PolicyLoader(path=Path("agentic.yaml")).load()
    logger.info("executor.started", sandbox_profile=policy.sandbox.profile)
    while True:
        logger.info("executor.idle")
        time.sleep(30)


if __name__ == "__main__":
    main()
