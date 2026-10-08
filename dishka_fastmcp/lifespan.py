"""Lifespan helper that publishes the dishka container for a FastMCP server."""

from collections.abc import AsyncGenerator
from typing import Any

from dishka import AsyncContainer, Container
from fastmcp import FastMCP
from fastmcp.server.lifespan import Lifespan, lifespan

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
    finalize_container: bool = True,
) -> Lifespan:
    """Build a FastMCP lifespan that publishes ``container`` for the server's lifetime.

    Pass the result to ``FastMCP(lifespan=...)``. On startup the lifespan
    registers ``container`` for the app, as ``setup_dishka`` does, and its
    lifespan state carries the container, so servers mounted into this one
    resolve their dependencies from it during the MCP requests it serves. The
    result is a FastMCP ``Lifespan``: compose it with ``@lifespan`` functions
    through ``|``, or with lifespans that yield a mapping or ``None`` through
    ``combine_lifespans``.

    By default the lifespan also closes the root container on shutdown,
    finalizing every ``Scope.APP`` provider, and drops the registration. Works
    with both an ``AsyncContainer`` and a sync ``Container``. APP-scoped
    dependencies in a sync container must be thread-safe and have
    thread-independent cleanup; thread-affine resources belong in
    ``Scope.REQUEST``.

    Pass ``finalize_container=False`` when something else owns the container,
    such as a web application, a worker or a test fixture that outlives the
    server. The owner then closes the container, and a registration that
    ``setup_dishka`` made before startup survives the shutdown.

    On startup the lifespan raises :class:`DishkaFastMCPError` if a *different*
    container is already registered for the app, by ``setup_dishka`` or by
    another ``dishka_lifespan`` — otherwise the registered one would silently
    outlive the shutdown.
    """

    @lifespan
    async def dishka(app: FastMCP[Any]) -> AsyncGenerator[dict[str, Any], None]:
        registered = get_registered_container(app)
        if registered is not None and registered is not container:
            raise DishkaFastMCPError(
                'dishka_lifespan received a different container than the one '
                'already registered for this FastMCP application.',
            )
        register_container(container, app)
        try:
            yield {LIFESPAN_STATE_KEY: container}
        finally:
            try:
                if finalize_container:
                    if isinstance(container, AsyncContainer):
                        await container.close()
                    else:
                        container.close()
            finally:
                if finalize_container or registered is None:
                    unregister_container(app)

    return dishka
