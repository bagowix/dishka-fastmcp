"""Provider exposing FastMCP request objects to dishka dependencies.

Add ``FastMCPProvider()`` to the container to let dependencies declare the
current ``Context``, the ``FastMCP`` server, or the raw request params of the
operation in flight. Every entry is ``Scope.REQUEST`` and resolved lazily, so
only the object that actually exists for the current operation is required —
a resource handler asking for ``ReadResourceRequestParams`` works; asking for
``CallToolRequestParams`` during a resource read does not.
"""

from dishka import Provider, Scope, from_context
from fastmcp import FastMCP
from fastmcp.server.context import Context
from mcp import types as mt

__all__ = ('FastMCPProvider',)


class FastMCPProvider(Provider):
    """Exposes FastMCP request-scoped objects via ``from_context``."""

    context = from_context(Context, scope=Scope.REQUEST)
    server = from_context(FastMCP, scope=Scope.REQUEST)
    call_tool_params = from_context(mt.CallToolRequestParams, scope=Scope.REQUEST)
    read_resource_params = from_context(mt.ReadResourceRequestParams, scope=Scope.REQUEST)
    get_prompt_params = from_context(mt.GetPromptRequestParams, scope=Scope.REQUEST)
