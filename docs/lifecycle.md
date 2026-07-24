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

Handlers registered with `task=True` are also unsupported. Background execution
starts after the originating request context has ended. Resolve dependencies
during the request and pass plain values to background work.
