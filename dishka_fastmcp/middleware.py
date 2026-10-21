"""FastMCP middleware that opens one dishka REQUEST scope for a whole MCP request."""

import asyncio
from typing import Any, Final

from dishka import AsyncContainer, Scope
from fastmcp.server.dependencies import FastMCPRequestContext
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.server.connection import Connection

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
_SESSION_KEY: Final[str] = 'dishka_fastmcp.session'


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


def _connection(request: FastMCPRequestContext) -> Connection:
    # The mcp SDK keeps the per-client Connection only on the private
    # ServerSession._connection. FastMCP itself reads it there (PingMiddleware,
    # StatefulProxyClient, Context.session_id); tests/test_session.py fails on a
    # lock bump that drops it.
    connection: Connection = request.session._connection  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    return connection


def _session_container(root: AsyncContainer, request: FastMCPRequestContext) -> AsyncContainer:
    """Return the SESSION container of ``root`` for the connection serving ``request``.

    Opened on the connection's first request and closed by the SDK when the
    connection closes. Concurrent requests share it, hence the lock. Mounted
    servers with containers of their own get sessions of their own.
    """
    connection = _connection(request)
    sessions: dict[AsyncContainer, AsyncContainer] = connection.state.setdefault(_SESSION_KEY, {})
    session = sessions.get(root)
    if session is None:
        # Entering a dishka scope does not await, so no other request can slip
        # in between this lookup and the assignment.
        session = sessions[root] = root(lock_factory=_TaskReentrantLock, scope=Scope.SESSION)
        connection.exit_stack.push_async_callback(session.close)
    return session


class DishkaMiddleware(Middleware):
    """Open ``Scope.REQUEST`` around every MCP request the server handles.

    Put it first in ``FastMCP(middleware=[...])`` so that the middleware after it
    sees the scope. List requests, reads and prompt renders get one as well, so
    dynamic component providers and other middleware can resolve REQUEST
    dependencies through ``get_request_container()``. Async ``@inject`` handlers
    share the scope instead of opening their own, and it is finalized once, when
    the rest of the request is done and before the response goes out.
    Notifications get no scope.

    The root container is found as for ``@inject``: registered by
    ``setup_dishka`` on the server, or put into the lifespan state of the server
    serving the request by ``dishka_lifespan``. It must be an ``AsyncContainer``.
    The scope's ``Context`` and ``FastMCP`` describe the request as this
    middleware sees it.

    A mounted server's ``DishkaMiddleware`` does nothing when its parent already
    opened a scope, and a direct ``server.call_tool()`` outside an MCP request
    gets no scope: ``@inject`` then opens one per handler.

    With ``session_scope=True`` the REQUEST scope is opened from a
    ``Scope.SESSION`` scope that lives as long as the MCP connection: opened on
    its first request, shared by its concurrent requests, and finalized by the
    MCP SDK when the connection closes. Only handshake-era (2025) connections
    over stdio, stateful Streamable HTTP, SSE or an in-memory client outlive a
    request. A 2026-07-28 connection and a stateless HTTP request live for one
    request, and so does their SESSION scope. The SDK logs and swallows
    errors from SESSION finalizers and gives them five seconds.
    """

    def __init__(self, *, session_scope: bool = False) -> None:
        """Open REQUEST scopes from a SESSION scope per MCP connection if ``session_scope``."""
        self._session_scope = session_scope

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
        parent = _session_container(root, request) if self._session_scope else root
        async with parent(
            provide_context((), {}),
            lock_factory=_TaskReentrantLock,
            scope=Scope.REQUEST,
        ) as container:
            set_request_scope(request, RequestScope(root, container))
            try:
                return await call_next(context)
            finally:
                set_request_scope(request, None)
