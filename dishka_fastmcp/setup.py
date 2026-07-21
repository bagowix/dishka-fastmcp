"""Wire a dishka container into a FastMCP app."""

from dishka import AsyncContainer, Container
from fastmcp import FastMCP

from dishka_fastmcp._middleware import DishkaMiddleware

__all__ = ('setup_dishka',)


def setup_dishka(container: AsyncContainer | Container, app: FastMCP) -> None:
    """Register the dishka middleware on ``app``.

    Call once before the server starts. Accepts an ``AsyncContainer`` (for async
    handlers) or a ``Container`` (for sync handlers run in a worker thread).
    """
    app.add_middleware(DishkaMiddleware(container))
