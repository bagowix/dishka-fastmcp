"""Wire a dishka container into a FastMCP app."""

from dishka import AsyncContainer, Container
from fastmcp import FastMCP

from dishka_fastmcp._container import register_container

__all__ = ('setup_dishka',)


def setup_dishka(container: AsyncContainer | Container, app: FastMCP) -> None:
    """Associate a root container with ``app``.

    Call once before the server starts. Servers mounted into ``app`` take the
    container from the lifespan state that ``dishka_lifespan`` sets up, during the
    MCP requests ``app`` serves.
    """
    register_container(container, app)
