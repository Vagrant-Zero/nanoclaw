"""Application entry point — runs the uvicorn server."""

import uvicorn

from nanoclaw.config import settings
from nanoclaw.server.app import create_app


def main() -> None:
    """Start the Nanoclaw server."""
    app = create_app()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
