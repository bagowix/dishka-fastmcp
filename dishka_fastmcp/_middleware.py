"""Middleware that opens a dishka REQUEST scope around each MCP operation.

The middleware is the only place that sees request boundaries for tools,
resources and prompts alike, so scopes are opened here — never by @inject.
``Scope.SESSION`` is intentionally skipped: FastMCP has no session-teardown
hook, so a session-lifetime scope cannot be finalized deterministically.
"""

from typing import Any, TypeVar

from dishka import AsyncContainer, Container, Scope
from fastmcp import FastMCP
from fastmcp.prompts import PromptResult
from fastmcp.resources import ResourceResult
from fastmcp.server.context import Context
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp import types as mt

from dishka_fastmcp._container import REQUEST_CONTAINER

__all__ = ('DishkaMiddleware',)

MessageT = TypeVar('MessageT')
ResultT = TypeVar('ResultT')


class DishkaMiddleware(Middleware):
    """Opens a REQUEST-scoped container per call and exposes it via ContextVar."""

    def __init__(self, container: AsyncContainer | Container) -> None:
        self._container = container

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        return await self._dispatch(context, call_next)

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        return await self._dispatch(context, call_next)

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        return await self._dispatch(context, call_next)

    async def _dispatch(
        self,
        context: MiddlewareContext[MessageT],
        call_next: CallNext[MessageT, ResultT],
    ) -> ResultT:
        data = self._context_data(context)
        container = self._container
        if isinstance(container, AsyncContainer):
            async with container(context=data, scope=Scope.REQUEST) as request_container:
                return await self._call(request_container, context, call_next)
        else:
            with container(context=data, scope=Scope.REQUEST) as request_container:
                return await self._call(request_container, context, call_next)

    @staticmethod
    async def _call(
        request_container: AsyncContainer | Container,
        context: MiddlewareContext[MessageT],
        call_next: CallNext[MessageT, ResultT],
    ) -> ResultT:
        token = REQUEST_CONTAINER.set(request_container)
        try:
            return await call_next(context)
        finally:
            REQUEST_CONTAINER.reset(token)

    @staticmethod
    def _context_data(context: MiddlewareContext[MessageT]) -> dict[Any, Any]:
        data: dict[Any, Any] = {type(context.message): context.message}
        fastmcp_context = context.fastmcp_context
        if fastmcp_context is not None:
            data[Context] = fastmcp_context
            data[FastMCP] = fastmcp_context.fastmcp
        return data
