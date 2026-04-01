"""Entry point for `python -m cli`."""

from core.config import load_config, load_dotenv
from cli.main import LMAgentTUI


def main() -> None:
    load_dotenv()
    config = load_config()
    LMAgentTUI(config).run()


if __name__ == "__main__":
    main()
