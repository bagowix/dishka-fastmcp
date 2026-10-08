# Lifecycle and scopes

dishka-fastmcp separates FastMCP registration from operation execution. The
decorator cleans the public signature during registration. At execution time,
`@inject` resolves the active FastMCP application and owns the request scope.

## Scope boundaries

| Scope | Boundary | Owner |
|---|---|---|
| `Scope.APP` | Server lifetime | Root container, closed by the FastMCP lifespan |
| `Scope.REQUEST` | One tool call, resource read, or prompt render | `@inject` |

Use `dishka_lifespan` when the FastMCP server owns the container:

```python
container = make_async_container(AppProvider())
mcp = FastMCP('app', lifespan=dishka_lifespan(container))
```

If the FastMCP server already has a lifespan, combine it with
`dishka_lifespan`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans


@asynccontextmanager
async def application_lifespan(
    app: FastMCP,
) -> AsyncIterator[dict[str, object]]:
    resource = await create_resource()
    try:
        yield {'resource': resource}
    finally:
        await resource.close()


mcp = FastMCP(
    'app',
    lifespan=combine_lifespans(
        application_lifespan,
        dishka_lifespan(container),
    ),
)
setup_dishka(container, mcp)
```

`combine_lifespans` enters lifespans in argument order and exits them in reverse
order. Placing `dishka_lifespan` last closes the Dishka container before the
other lifespan releases its resources.

The combined lifespan above belongs to a `FastMCP` instance. When composing
FastMCP with FastAPI or Starlette, keep `dishka_lifespan` on the FastMCP server
and combine the lifespan exposed by `mcp.http_app()` at the ASGI layer. This is
also the correct pattern for hosting multiple independent FastMCP servers:

```python
from fastapi import FastAPI


first = FastMCP('first', lifespan=dishka_lifespan(first_container))
second = FastMCP('second', lifespan=dishka_lifespan(second_container))
setup_dishka(first_container, first)
setup_dishka(second_container, second)

first_app = first.http_app()
second_app = second.http_app()
app = FastAPI(
    lifespan=combine_lifespans(
        first_app.lifespan,
        second_app.lifespan,
    ),
)
app.mount('/first', first_app)
app.mount('/second', second_app)
```

Do not combine `dishka_lifespan(first_container)` and
`dishka_lifespan(second_container)` directly at the ASGI layer:
`combine_lifespans` passes the same ASGI application to every lifespan. Each
`mcp.http_app().lifespan` adapter preserves the corresponding FastMCP instance.

## Mounted servers

A server built from routers needs one container, on the server you serve. Give
that server `dishka_lifespan(container)` and `setup_dishka(container, server)`;
routers mounted into it at any depth use its container. This includes routers
mounted later and mounts with `namespace` or `tool_names`:

```python
container = make_async_container(AppProvider())
server = FastMCP('app', lifespan=dishka_lifespan(container))
setup_dishka(container, server)

users = FastMCP('users')


@users.tool
@inject
async def get_user(user_id: int, repo: FromDishka[UserRepository]) -> User:
    return await repo.get(user_id)


server.mount(users, namespace='users')
```

`dishka_lifespan` puts the container into the server's lifespan state. FastMCP
hands that state to every MCP request the server serves, mounted components
included, so a component whose own server has no container takes it from there.
The rules that follow from it:

- A mounted server with its own `setup_dishka` or `dishka_lifespan` keeps its own
  container. Servers mounted under it without a container of their own use the
  serving root's, never the nearest set-up server's.
- A router mounted into several servers uses the container of the server that
  serves the request, so one router module can back several applications.
- A server without `dishka_lifespan` hands no container to its routers, even when
  the same router is mounted into another server that has one. The same holds when
  the serving server's lifespan state is not a mapping, so combine `dishka_lifespan`
  with dict-yielding lifespans only.
- The container travels with MCP requests (a `Client`, HTTP, stdio). A direct
  `await server.call_tool(...)` outside a request reaches only the server's own
  components; test mounted routers through `Client(server)`.
- A server behind `create_proxy(...)` runs its own MCP session and needs its own
  `setup_dishka`.

`Context` and `FastMCP` from `FastMCPProvider` still describe the mounted server
that owns the executing component.

## Sync request finalization

FastMCP offloads regular sync handlers to worker threads. `@inject` opens and
closes the Dishka request container inside the wrapped handler, so thread-affine
resources stay in one thread:

```python
import sqlite3
from collections.abc import Iterator

from dishka import Provider, Scope, provide


class DatabaseProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect('app.db')
        try:
            yield connection
        finally:
            connection.close()
```

Sync `Scope.APP` dependencies must still be thread-safe because calls may run on
different workers and APP cleanup happens during server shutdown.

## Limitations

`Scope.SESSION` is not supported because FastMCP does not provide a deterministic
teardown boundary for a Dishka session container.

Handlers registered with `task=True` are also unsupported. FastMCP 4 runs them
in a `fastmcp-tasks` worker, outside the request that queued them, and `@inject`
raises `DishkaFastMCPError` there. Resolve dependencies during the request and
pass plain values to background work.
