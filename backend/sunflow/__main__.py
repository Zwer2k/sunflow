"""Entry point: python -m sunflow."""

import logging

import uvicorn

from .api import create_app
from .settings import load_settings


def main() -> None:
    """Start the backend."""
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
