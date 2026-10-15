import logging

import uvicorn

from src.config.logging import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    try:
        configure_logging("api")
        uvicorn.run(
            "src.main:app",
            host="0.0.0.0",
            port=8000,
            log_config=None,
            access_log=False,
        )
    except Exception:
        logger.exception("api_server_failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
