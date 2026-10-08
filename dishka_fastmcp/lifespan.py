"""Lifespan helper that publishes the dishka container for a FastMCP server."""

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from dishka import AsyncContainer, Container
from fastmcp import FastMCP

from dishka_fastmcp._container import (
    LIFESPAN_STATE_KEY,
    get_registered_container,
    register_container,
    unregister_container,
)
from dishka_fastmcp.exceptions import DishkaFastMCPError

__all__ = ('dishka_lifespan',)


def dishka_lifespan(
    container: AsyncContainer | Container,
    *,
    close: bool = True,
) -> Callable[[FastMCP[Any]], AbstractAsyncContextManager[dict[str, Any]]]:
    """Build a FastMCP lifespan that publishes ``container`` for the server's lifetime.

    Pass the result to ``FastMCP(lifespan=...)``. On startup the lifespan
    registers ``container`` for the app, as ``setup_dishka`` does, and its
    lifespan state carries the container, so servers mounted into this one
    resolve their dependencies from it during the MCP requests it serves. Combine
    it through ``combine_lifespans`` with lifespans that yield a mapping or
    ``None``.

    By default the lifespan also closes the root container on shutdown,
    finalizing every ``Scope.APP`` provider, and drops the registration. Works
    with both an ``AsyncContainer`` and a sync ``Container``. APP-scoped
    dependencies in a sync container must be thread-safe and have
    thread-independent cleanup; thread-affine resources belong in
    ``Scope.REQUEST``.

    Pass ``close=False`` when something else owns the container, such as a web
    application, a worker or a test fixture that outlives the server. The owner
    then closes the container, and a registration that ``setup_dishka`` made
    before startup survives the shutdown.

    On startup the lifespan raises :class:`DishkaFastMCPError` if ``setup_dishka``
    registered a *different* container for the app — otherwise the registered one
    would silently outlive the shutdown.
    """

    @asynccontextmanager
    async def lifespan(app: FastMCP[Any]) -> AsyncGenerator[dict[str, Any], None]:
        registered = get_registered_container(app)
        if registered is not None and registered is not container:
            raise DishkaFastMCPError(
                'dishka_lifespan received a different container than the one '
                'registered via setup_dishka for this FastMCP application.',
            )
        register_container(container, app)
        try:
            yield {LIFESPAN_STATE_KEY: container}
        finally:
            try:
                if close:
                    if isinstance(container, AsyncContainer):
                        await container.close()
                    else:
                        container.close()
            finally:
                if close or registered is None:
                    unregister_container(app)

    return lifespan
