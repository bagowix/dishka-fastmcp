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

``DishkaMiddleware`` keeps the REQUEST scope it opens on FastMCP's per-request
context object, the way dishka's Starlette integration keeps it on
``request.state``. Every component of that request, mounted or not, reaches the
same object through ``get_context().request_context``, on the event loop and in
worker threads alike.
"""

import sys
from collections.abc import Mapping
from threading import RLock
from typing import Any, Final, NamedTuple, cast

from dishka import AsyncContainer, Container
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import FastMCPRequestContext, get_context, get_server

from dishka_fastmcp.exceptions import DishkaFastMCPError

__all__ = (
    'LIFESPAN_STATE_KEY',
    'RequestScope',
    'current_request',
    'get_registered_container',
    'get_request_container',
    'get_shared_container',
    'provide_context',
    'register_container',
    'request_scope',
    'require_container',
    'set_request_scope',
    'shared_request_container',
    'unregister_container',
)

_ATTR: Final[str] = '__dishka_fastmcp_container__'
_SCOPE_ATTR: Final[str] = '__dishka_fastmcp_request_scope__'
_register_lock = RLock()
LIFESPAN_STATE_KEY: Final[str] = 'dishka_fastmcp.container'

_MISSING_SETUP: Final[str] = (
    'No dishka container for the active FastMCP application. Call '
    'setup_dishka(container, mcp) on it, or serve it through a server whose '
    'lifespan includes dishka_lifespan(container): mounted servers find the '
    'container there during MCP requests. Also check that @inject sits below the '
    'FastMCP decorator.'
)
_NO_REQUEST_SCOPE: Final[str] = (
    'No dishka request scope is open. Add DishkaMiddleware() as the first middleware '
    'of the FastMCP server that serves the request. Even then there is no scope '
    'outside an MCP request: FastMCP lists components on startup, runs completion '
    'and extension-method handlers outside the scope, and a task a handler spawns '
    'can outlive its request.'
)
_FOREIGN_SCOPE: Final[str] = (
    'The active FastMCP server was set up with a dishka container of its own, but '
    'the open request scope comes from another container. A mounted server shares '
    'the request scope only when it has no container of its own; its @inject '
    'handlers open a REQUEST scope from its own container instead.'
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
    """Drop the container registered for ``app``, if any."""
    with _register_lock:
        vars(app).pop(_ATTR, None)


class RequestScope(NamedTuple):
    """A REQUEST container ``DishkaMiddleware`` opened, and the root it came from."""

    root: AsyncContainer
    container: AsyncContainer


def current_request() -> FastMCPRequestContext | None:
    """Return FastMCP's context object for the MCP request being served, if any."""
    try:
        return get_context().request_context
    except RuntimeError:  # a server is active outside any operation, e.g. in a lifespan
        return None


def request_scope(request: FastMCPRequestContext) -> RequestScope | None:
    """Return the REQUEST scope ``DishkaMiddleware`` opened for ``request``, if any."""
    scope: RequestScope | None = getattr(request, _SCOPE_ATTR, None)
    return scope


def set_request_scope(request: FastMCPRequestContext, scope: RequestScope | None) -> None:
    """Make ``scope`` the REQUEST scope of ``request``, or forget it with ``None``."""
    if scope is None:
        vars(request).pop(_SCOPE_ATTR, None)
    else:
        setattr(request, _SCOPE_ATTR, scope)


def _request_container() -> AsyncContainer | str:
    """Return the REQUEST container the active server shares, or why there is none.

    A server set up with a container of its own shares only a scope opened from
    that container, so its own container keeps priority as without the middleware.
    """
    if _in_background_task():
        return _BACKGROUND_TASK
    request = current_request()
    scope = None if request is None else request_scope(request)
    if scope is None:
        return _NO_REQUEST_SCOPE
    own = get_registered_container(get_server())
    if own is not None and own is not scope.root:
        return _FOREIGN_SCOPE
    return scope.container


def shared_request_container() -> AsyncContainer | None:
    """Return the REQUEST container the active server shares for this MCP request."""
    container = _request_container()
    return None if isinstance(container, str) else container


def get_request_container() -> AsyncContainer:
    """Return the REQUEST container ``DishkaMiddleware`` opened for this MCP request.

    It is the scope ``@inject`` handlers of the request share. Raises
    :class:`DishkaFastMCPError` where there is none: without the middleware,
    outside an MCP request, in a ``task=True`` worker, or on a mounted server set
    up with a container of its own.
    """
    container = _request_container()
    if isinstance(container, str):
        raise DishkaFastMCPError(container)
    return container


def _serving_container() -> AsyncContainer | Container | None:
    """Return the container in the lifespan state of the server serving this request."""
    request = current_request()
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


def require_container() -> AsyncContainer | Container:
    """Return the root container for the active FastMCP application."""
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
    container = require_container()
    if not isinstance(container, AsyncContainer):
        raise DishkaFastMCPError(
            'Async handler needs an AsyncContainer; got a sync Container. '
            'Use make_async_container(...) for async tools.',
        )
    return container


def get_shared_container(args: tuple[Any, ...], kwargs: dict[str, Any]) -> AsyncContainer:
    """Return the open REQUEST container for ``@inject`` to share."""
    del args, kwargs
    return get_request_container()


def get_sync_container(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Container:
    """Return the root sync container to open a REQUEST scope from."""
    del args, kwargs
    container = require_container()
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
