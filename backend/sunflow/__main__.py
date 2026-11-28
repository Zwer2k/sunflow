"""Entry point: python -m sunflow."""

import logging

import uvicorn

from .api import create_app
from .log_buffer import LOG_BUFFER
from .settings import load_settings

LOG_FORMAT = "%(asctime)s %(levelname)s (%(name)s) %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def main() -> None:
    """Start the backend."""
    # The App log in Home Assistant shows the lines as they are: time first, like Home Assistant's own
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    logging.getLogger().addHandler(LOG_BUFFER)
    settings = load_settings()
    # The UI polls every few seconds; a log line per request only fills the host's journal.
    # No log config of its own: uvicorn's lines go through the root logger with the same format
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port, access_log=False, log_config=None)


if __name__ == "__main__":
    main()
