"""Transport for the root container and request context via ContextVars we own.

The REQUEST scope is entered by ``@inject`` (``wrap_injection(manage_scope=True)``),
not by the middleware. This matters for sync tools: FastMCP runs them in a worker
thread, so the scope must be entered, used and finalized in that same thread —
otherwise a thread-affine resource (e.g. a ``sqlite3`` connection) created in the
worker is finalized on the event loop and errors. The middleware only publishes
the root container and the per-operation context here; ``@inject`` opens the scope
where the handler actually runs. ContextVars propagate into ``run_in_thread``
worker threads because anyio copies the current context when offloading.
"""

from contextvars import ContextVar
from typing import Any, Final

from dishka import AsyncContainer, Container

from dishka_fastmcp.exceptions import DishkaFastMCPError

__all__ = ('CONTEXT_DATA', 'ROOT_CONTAINER')

ROOT_CONTAINER: ContextVar[AsyncContainer | Container | None] = ContextVar(
    'dishka_fastmcp_root_container',
    default=None,
)
CONTEXT_DATA: ContextVar[dict[Any, Any] | None] = ContextVar(
    'dishka_fastmcp_context_data',
    default=None,
)

_MISSING_SETUP: Final[str] = (
    'No dishka container in context. Did you call setup_dishka(container, mcp) '
    'and place @inject below the FastMCP decorator? Note: task=True handlers '
    'run outside the request and are not supported.'
)


def _require_container() -> AsyncContainer | Container:
    container = ROOT_CONTAINER.get()
    if container is None:
        raise DishkaFastMCPError(_MISSING_SETUP)
    return container


def get_async_container(args: tuple[Any, ...], kwargs: dict[str, Any]) -> AsyncContainer:
    """Return the root async container to open a REQUEST scope from."""
    del args, kwargs
    container = _require_container()
    if not isinstance(container, AsyncContainer):
        raise DishkaFastMCPError(
            'Async handler needs an AsyncContainer; got a sync Container. '
            'Use make_async_container(...) for async tools.',
        )
    return container


def get_sync_container(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Container:
    """Return the root sync container to open a REQUEST scope from."""
    del args, kwargs
    container = _require_container()
    if not isinstance(container, Container):
        raise DishkaFastMCPError(
            'Sync handler needs a Container; got an AsyncContainer. '
            'Use make_container(...) for sync tools.',
        )
    return container


def provide_context(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[Any, Any]:
    """Populate the REQUEST scope with the current operation's FastMCP objects."""
    del args, kwargs
    data = CONTEXT_DATA.get()
    if data is None:
        return {}
    return data
