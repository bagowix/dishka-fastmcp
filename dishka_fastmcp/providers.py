"""Provider exposing FastMCP's public request objects to dependencies."""

from dishka import Provider, Scope, from_context
from fastmcp import Context, FastMCP

__all__ = ('FastMCPProvider',)


class FastMCPProvider(Provider):
    """Exposes FastMCP request-scoped objects via ``from_context``."""

    context = from_context(Context, scope=Scope.REQUEST)
    server = from_context(FastMCP, scope=Scope.REQUEST)
