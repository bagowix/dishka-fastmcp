"""Middleware that publishes the container and context for each MCP operation.

The middleware runs for tools, resources and prompts alike and stashes the root
container plus the operation's FastMCP objects in ContextVars. It does NOT open
the dishka scope — ``@inject`` does, via ``wrap_injection(manage_scope=True)``, so
the REQUEST scope is entered and finalized in the thread where the handler runs
(critical for sync tools offloaded to a worker thread). ``Scope.SESSION`` is
intentionally unsupported: FastMCP has no session-teardown hook, so a
session-lifetime scope cannot be finalized deterministically.
"""

from typing import Any, TypeVar

from dishka import AsyncContainer, Container
from fastmcp import FastMCP
from fastmcp.prompts import PromptResult
from fastmcp.resources import ResourceResult
from fastmcp.server.context import Context
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp import types as mt

from dishka_fastmcp._container import CONTEXT_DATA, ROOT_CONTAINER

__all__ = ('DishkaMiddleware',)

MessageT = TypeVar('MessageT')
ResultT = TypeVar('ResultT')


class DishkaMiddleware(Middleware):
    """Publishes the root container and per-operation context via ContextVars."""

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
        container_token = ROOT_CONTAINER.set(self._container)
        context_token = CONTEXT_DATA.set(self._context_data(context))
        try:
            return await call_next(context)
        finally:
            ROOT_CONTAINER.reset(container_token)
            CONTEXT_DATA.reset(context_token)

    @staticmethod
    def _context_data(context: MiddlewareContext[MessageT]) -> dict[Any, Any]:
        data: dict[Any, Any] = {type(context.message): context.message}
        fastmcp_context = context.fastmcp_context
        if fastmcp_context is not None:
            data[Context] = fastmcp_context
            data[FastMCP] = fastmcp_context.fastmcp
        return data
