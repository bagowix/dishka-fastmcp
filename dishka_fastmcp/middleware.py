"""FastMCP middleware that opens one dishka REQUEST scope for a whole MCP request."""

import asyncio
from typing import Any, Final

from dishka import AsyncContainer, Scope
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from dishka_fastmcp._container import (
    RequestScope,
    current_request,
    provide_context,
    request_scope,
    require_container,
    set_request_scope,
)
from dishka_fastmcp.exceptions import DishkaFastMCPError

__all__ = ('DishkaMiddleware',)

_SYNC_CONTAINER: Final[str] = (
    'DishkaMiddleware needs an AsyncContainer: it opens the REQUEST scope on the '
    'event loop, where a sync Container would block, and sync handlers run in '
    'worker threads that must not share it. Use make_async_container(...), or '
    'leave the middleware out and keep the per-handler scope of @inject.'
)


class _TaskReentrantLock:
    """An ``asyncio.Lock`` the task holding it may enter again.

    One request resolves from the shared scope in several tasks at once, so the
    scope needs a lock. A factory that resolves through the container it was
    given re-enters the lock of the lookup that called it, which deadlocks a
    plain ``asyncio.Lock``.
    """

    # ponytail: ownership is per task, so a factory that resolves through its
    # container from a task of its own (asyncio.gather) still deadlocks, as at
    # dishka's APP scope. Lifting that needs per-dependency locks inside dishka.

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[Any] | None = None
        self._depth = 0

    async def __aenter__(self) -> None:
        task = asyncio.current_task()
        if self._owner is not task:
            await self._lock.acquire()
            self._owner = task
        self._depth += 1

    async def __aexit__(self, *exc_info: object) -> None:
        self._depth -= 1
        if not self._depth:
            self._owner = None
            self._lock.release()


class DishkaMiddleware(Middleware):
    """Open ``Scope.REQUEST`` around every MCP request the server handles.

    Put it first in ``FastMCP(middleware=[...])`` so that the middleware after it
    sees the scope. List requests, reads and prompt renders get one as well, so
    dynamic component providers and other middleware can resolve REQUEST
    dependencies through ``get_request_container()``. ``@inject`` handlers share
    the scope instead of opening their own, and it is finalized once, when the
    rest of the request is done and before the response goes out. Notifications
    get no scope.

    The root container is found as for ``@inject``: registered by
    ``setup_dishka`` on the server, or put into the lifespan state of the server
    serving the request by ``dishka_lifespan``. It must be an ``AsyncContainer``.
    The scope's ``Context`` and ``FastMCP`` describe the request as this
    middleware sees it.

    A mounted server's ``DishkaMiddleware`` does nothing when its parent already
    opened a scope, and a direct ``server.call_tool()`` outside an MCP request
    gets no scope: ``@inject`` then opens one per handler.
    """

    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> object:
        """Run the rest of the request inside one REQUEST scope."""
        request = current_request()
        if request is None or request_scope(request) is not None:
            return await call_next(context)
        root = require_container()
        if not isinstance(root, AsyncContainer):
            raise DishkaFastMCPError(_SYNC_CONTAINER)
        async with root(
            provide_context((), {}),
            lock_factory=_TaskReentrantLock,
            scope=Scope.REQUEST,
        ) as container:
            set_request_scope(request, RequestScope(root, container))
            try:
                return await call_next(context)
            finally:
                set_request_scope(request, None)
