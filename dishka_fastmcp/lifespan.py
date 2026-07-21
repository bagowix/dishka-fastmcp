"""Lifespan helper that closes the dishka container on server shutdown.

FastMCP takes its lifespan at construction and passes the server to it, so the
container must exist first. ``setup_dishka`` cannot own the server lifecycle
(FastMCP wraps the lifespan in private machinery), hence a helper rather than
automatic wiring.
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from dishka import AsyncContainer, Container
from fastmcp import FastMCP

__all__ = ('dishka_lifespan',)


def dishka_lifespan(
    container: AsyncContainer | Container,
) -> Callable[[FastMCP[Any]], AbstractAsyncContextManager[None]]:
    """Build a FastMCP lifespan that closes ``container`` on shutdown.

    Pass the result to ``FastMCP(lifespan=...)``. On shutdown the root container
    is closed, finalizing every ``Scope.APP`` provider. Works with both an
    ``AsyncContainer`` and a sync ``Container``.
    """

    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]) -> AsyncGenerator[None, None]:
        try:
            yield
        finally:
            if isinstance(container, AsyncContainer):
                await container.close()
            else:
                container.close()

    return lifespan
