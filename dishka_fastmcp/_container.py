"""Look up containers through FastMCP's active application.

FastMCP owns the request context and propagates it when it offloads synchronous
handlers to a worker thread. The container is stored as an attribute on the
application itself, so the pair shares one lifetime and is garbage-collected
together; ``@inject`` still opens and finalizes ``Scope.REQUEST`` in the thread
where the handler actually runs.

FastMCP makes a mounted server the active application while its component runs.
A server without a container of its own takes the one ``dishka_lifespan`` put
into the lifespan state of the server serving the MCP request, which FastMCP
exposes per request the way Starlette exposes ``request.app.state``.
"""

import sys
from collections.abc import Mapping
from threading import RLock
from typing import Any, Final, cast

from dishka import AsyncContainer, Container
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import get_context, get_server

from dishka_fastmcp.exceptions import DishkaFastMCPError

__all__ = (
    'LIFESPAN_STATE_KEY',
    'get_registered_container',
    'provide_context',
    'register_container',
    'unregister_container',
)

_ATTR: Final[str] = '__dishka_fastmcp_container__'
_register_lock = RLock()
LIFESPAN_STATE_KEY: Final[str] = 'dishka_fastmcp.container'

_MISSING_SETUP: Final[str] = (
    'No dishka container for the active FastMCP application. Call '
    'setup_dishka(container, mcp) on it, or serve it through a server whose '
    'lifespan includes dishka_lifespan(container): mounted servers find the '
    'container there during MCP requests. Also check that @inject sits below the '
    'FastMCP decorator.'
)
_BACKGROUND_TASK: Final[str] = (
    'FastMCP background tasks (task=True) are not supported. Resolve dependencies '
    'during the original request and pass plain values to the task instead.'
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


def unregister_container(app: FastMCP) -> None:
    """Drop the container registered for ``app``."""
    with _register_lock:
        delattr(app, _ATTR)


def _serving_container() -> AsyncContainer | Container | None:
    """Return the container in the lifespan state of the server serving this request."""
    try:
        request = get_context().request_context
    except RuntimeError:  # a server is active outside any operation, e.g. in a lifespan
        return None
    if request is None:
        return None
    state: object = request.lifespan_context
    if not isinstance(state, Mapping):
        return None
    container: AsyncContainer | Container | None = cast('Mapping[str, Any]', state).get(
        LIFESPAN_STATE_KEY,
    )
    return container


def _in_background_task() -> bool:
    # fastmcp-tasks imports this module before it starts a task worker
    # (fastmcp_tasks.lifespan.docket_lifespan). Looking it up instead of importing
    # it keeps fastmcp-tasks optional and skips the client-side extension its
    # package registers on import.
    tasks = sys.modules.get('fastmcp_tasks.context')
    return tasks is not None and tasks.get_task_context() is not None


def _require_container() -> AsyncContainer | Container:
    if _in_background_task():
        raise DishkaFastMCPError(_BACKGROUND_TASK)

    try:
        app = get_server()
    except RuntimeError as exc:
        raise DishkaFastMCPError(_MISSING_SETUP) from exc

    container = get_registered_container(app)
    if container is None:
        container = _serving_container()
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
