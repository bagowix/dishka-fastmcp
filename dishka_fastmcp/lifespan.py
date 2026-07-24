"""Lifespan helper that closes the dishka container on server shutdown."""

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from dishka import AsyncContainer, Container
from fastmcp import FastMCP

from dishka_fastmcp._container import get_registered_container, unregister_container
from dishka_fastmcp.exceptions import DishkaFastMCPError

__all__ = ('dishka_lifespan',)


def dishka_lifespan(
    container: AsyncContainer | Container,
) -> Callable[[FastMCP[Any]], AbstractAsyncContextManager[None]]:
    """Build a FastMCP lifespan that closes ``container`` on shutdown.

    Pass the result to ``FastMCP(lifespan=...)``. On shutdown the root container
    is closed, finalizing every ``Scope.APP`` provider. Works with both an
    ``AsyncContainer`` and a sync ``Container``. APP-scoped dependencies in a
    sync container must be thread-safe and have thread-independent cleanup;
    thread-affine resources belong in ``Scope.REQUEST``.

    On startup the lifespan raises :class:`DishkaFastMCPError` if ``setup_dishka``
    registered a *different* container for the app — otherwise the registered one
    would silently outlive the shutdown.
    """

    @asynccontextmanager
    async def lifespan(app: FastMCP[Any]) -> AsyncGenerator[None, None]:
        registered = get_registered_container(app)
        if registered is not None and registered is not container:
            raise DishkaFastMCPError(
                'dishka_lifespan received a different container than the one '
                'registered via setup_dishka for this FastMCP application.',
            )
        try:
            yield
        finally:
            try:
                if isinstance(container, AsyncContainer):
                    await container.close()
                else:
                    container.close()
            finally:
                unregister_container(container, app)

    return lifespan
