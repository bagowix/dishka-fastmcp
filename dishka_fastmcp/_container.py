"""Look up containers through FastMCP's active application.

FastMCP owns the request context and propagates it when it offloads synchronous
handlers to a worker thread. The container is stored as an attribute on the
application itself, so the pair shares one lifetime and is garbage-collected
together; ``@inject`` still opens and finalizes ``Scope.REQUEST`` in the thread
where the handler actually runs.
"""

from threading import RLock
from typing import Any, Final

from dishka import AsyncContainer, Container
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import get_context, get_server, get_task_context

from dishka_fastmcp.exceptions import DishkaFastMCPError

__all__ = (
    'get_registered_container',
    'provide_context',
    'register_container',
    'unregister_container',
)

_ATTR: Final[str] = '__dishka_fastmcp_container__'
_register_lock = RLock()

_MISSING_SETUP: Final[str] = (
    'No dishka container for the active FastMCP application. Did you call '
    'setup_dishka(container, mcp) and place @inject below the FastMCP decorator? '
    'Note: task=True handlers run outside the request and are not supported.'
)
_BACKGROUND_TASK: Final[str] = (
    'FastMCP background tasks are not supported because their request scope has '
    'already ended. Resolve dependencies during the original request and pass '
    'plain values to the task instead.'
)


def get_registered_container(app: FastMCP) -> AsyncContainer | Container | None:
    """Return the container registered for ``app``, if any."""
    container: AsyncContainer | Container | None = getattr(app, _ATTR, None)
    return container


def register_container(container: AsyncContainer | Container, app: FastMCP) -> None:
    """Associate a root container with a FastMCP application."""
    with _register_lock:
        existing = get_registered_container(app)
        if existing is not None and existing is not container:
            raise DishkaFastMCPError(
                'A different dishka container is already registered for this FastMCP application.',
            )
        setattr(app, _ATTR, container)


def unregister_container(
    container: AsyncContainer | Container,
    app: FastMCP,
) -> None:
    """Drop ``container`` from ``app`` if it is still the registered instance."""
    with _register_lock:
        if get_registered_container(app) is container:
            delattr(app, _ATTR)


def _require_container() -> AsyncContainer | Container:
    if get_task_context() is not None:
        raise DishkaFastMCPError(_BACKGROUND_TASK)

    try:
        app = get_server()
    except RuntimeError as exc:
        raise DishkaFastMCPError(_MISSING_SETUP) from exc

    container = get_registered_container(app)
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
    """Populate the REQUEST scope with FastMCP request objects."""
    del args, kwargs
    return {Context: get_context(), FastMCP: get_server()}
