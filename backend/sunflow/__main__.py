"""Entry point: python -m sunflow."""

import logging

import uvicorn

from .api import create_app
from .log_buffer import LOG_BUFFER
from .settings import load_settings


def main() -> None:
    """Start the backend."""
    logging.basicConfig(level=logging.INFO)
    logging.getLogger().addHandler(LOG_BUFFER)
    settings = load_settings()
    # The UI polls every few seconds; a log line per request only fills the host's journal
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
