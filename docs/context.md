# FastMCP context

Add `FastMCPProvider()` to expose FastMCP operation objects through Dishka's
normal `from_context` mechanism.

```python
from dishka import make_async_container
from fastmcp import Context, FastMCP

from dishka_fastmcp import (
    FastMCPProvider,
    FromDishka,
    dishka_lifespan,
    inject,
    setup_dishka,
)

container = make_async_container(FastMCPProvider())
mcp = FastMCP('app', lifespan=dishka_lifespan(container))
setup_dishka(container, mcp)


@mcp.tool
@inject
async def request_id(ctx: FromDishka[Context]) -> str:
    return ctx.request_id
```

## Available objects

All entries are request-scoped and resolved only when requested:

| Type | Value |
|---|---|
| `fastmcp.Context` | Current FastMCP operation context |
| `fastmcp.FastMCP` | Current server instance |

Use `Context` for request IDs, progress reporting, and resource access. When a
child server is mounted, the injected `FastMCP` value is the server that owns the
executing component. Its dependencies come from the container selected as
described in [Mounted servers](lifecycle.md#mounted-servers).

## Using context in providers

Dependencies can consume the same context without coupling the handler to the
FastMCP decorator:

```python
from dishka import Provider, Scope, provide
from fastmcp import Context


class RequestProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def request_id(self, context: Context) -> str:
        return context.request_id
```

## Operation arguments

`FastMCPProvider` does not register `CallToolRequestParams`,
`ReadResourceRequestParams`, or `GetPromptRequestParams`. Receive tool and
prompt arguments in the handler signature, receive resource parameters from the
resource URI template, and use `Context` for request metadata.
