import asyncio
import logging
import sys

from core.config import load_config
from core.daemon import run_daemon


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    config = load_config()
    _setup_logging(config.daemon.log_level)

    try:
        asyncio.run(run_daemon(config))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Daemon stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
