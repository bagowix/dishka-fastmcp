# FastMCP context

Add `FastMCPProvider()` to expose FastMCP operation objects through Dishka's
normal `from_context` mechanism.

```python
from dishka import make_async_container
from fastmcp import FastMCP
from fastmcp.server.context import Context

from dishka_fastmcp import (
    FastMCPProvider,
    FromDishka,
    dishka_lifespan,
    inject,
    setup_dishka,
)

container = make_async_container(AppProvider(), FastMCPProvider())
mcp = FastMCP('app', lifespan=dishka_lifespan(container))
setup_dishka(container, mcp)


@mcp.tool
@inject
async def notify(message: str, ctx: FromDishka[Context]) -> None:
    await ctx.info(message)
```

## Available objects

All entries are request-scoped and resolved only when requested:

| Type | Value |
|---|---|
| `fastmcp.server.context.Context` | Current FastMCP operation context |
| `fastmcp.FastMCP` | Current server instance |
| `CallToolRequestParams` | Raw parameters for the current tool call |
| `ReadResourceRequestParams` | Raw parameters for the current resource read |
| `GetPromptRequestParams` | Raw parameters for the current prompt render |

Request parameter types are operation-specific. For example, resolving
`CallToolRequestParams` during a resource read fails because no tool call exists
in that request scope.

Dependencies can consume the same context without coupling the handler to
FastMCP:

```python
class RequestProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def request_uri(self, params: ReadResourceRequestParams) -> str:
        return str(params.uri)
```
