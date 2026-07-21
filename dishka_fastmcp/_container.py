"""Transport for the per-request container via a ContextVar we own.

The container cannot ride on a function argument (FastMCP binds tool arguments
to the client-facing schema) nor on a FastMCP-private attribute. A ContextVar
we own is public-API-stable and still propagates into ``run_in_thread`` worker
threads, because anyio copies the current context when offloading sync tools.
"""

from contextvars import ContextVar
from typing import Any

from dishka import AsyncContainer, Container

from dishka_fastmcp.exceptions import DishkaFastMCPError

__all__ = ('REQUEST_CONTAINER',)

REQUEST_CONTAINER: ContextVar[AsyncContainer | Container | None] = ContextVar(
    'dishka_fastmcp_request_container',
    default=None,
)

_MISSING_SETUP = (
    'No dishka container in context. Did you call setup_dishka(container, mcp) '
    'and place @inject below the FastMCP decorator?'
)


def _require_container() -> AsyncContainer | Container:
    container = REQUEST_CONTAINER.get()
    if container is None:
        raise DishkaFastMCPError(_MISSING_SETUP)
    return container


def get_async_container(args: tuple[Any, ...], kwargs: dict[str, Any]) -> AsyncContainer:
    """Return the request-scoped async container for an async handler."""
    del args, kwargs
    container = _require_container()
    if not isinstance(container, AsyncContainer):
        raise DishkaFastMCPError(
            'Async handler needs an AsyncContainer; got a sync Container. '
            'Use make_async_container(...) for async tools.',
        )
    return container


def get_sync_container(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Container:
    """Return the request-scoped sync container for a sync handler."""
    del args, kwargs
    container = _require_container()
    if not isinstance(container, Container):
        raise DishkaFastMCPError(
            'Sync handler needs a Container; got an AsyncContainer. '
            'Use make_container(...) for sync tools.',
        )
    return container
