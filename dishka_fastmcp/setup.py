"""Wire a dishka container into a FastMCP app."""

from dishka import AsyncContainer, Container
from fastmcp import FastMCP

from dishka_fastmcp._container import register_container

__all__ = ('setup_dishka',)


def setup_dishka(container: AsyncContainer | Container, app: FastMCP) -> None:
    """Associate a root container with ``app``.

    Call once before the server starts.
    """
    register_container(container, app)
